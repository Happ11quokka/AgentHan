"""음성 끼어들기 측정 요약 (3단계 티켓 06, 실기기 시험 T1·T2·T3·T4·T5·T7).

통화 세션이 쓴 JSONL(`latency.py`)에서 봇 말 구간 기록(`barge`)과 끊김 기록(`interrupt`)을 읽어
숫자만 낸다. 손님 말·봇 말 글은 내지 않는다. `PHONE_VOICE_BARGE_IN=shadow`나 `on`으로 건 통화만
기록이 있다.

    uv run python -m agenthan.voice.call.barge_summary logs/phone-20261109.jsonl [...]

- `calls`: 통화마다 한 줄. 마이크 설정(에코 제거·자동 음량·컨텍스트 순서)과 그 통화의 에코 바닥,
  줄이기·되돌림·멈춤 수, 헛 멈춤 수, 마지막 멈춤 문턱. T4(컨텍스트 순서)·T5(자동 음량)는 이 줄을
  비교한다
- `echo_rms`: 구간 종류(blocked 인사·마무리 / open / readback 복창)별 봇 말 중 프레임 RMS.
  구간마다 낸 p50·p95·최대를 다시 모은다(p50은 구간 p50의 중앙값, p95는 구간 p95의 p95, 최대는
  최대). T1: 손님이 조용할 때 이 p95가 `PHONE_BARGE_THRESHOLD`보다 충분히 낮아야 한다
- `results`: 봇 말 중에 시작한 말의 STT 글 판정별 수(accept·echo·backchannel·empty·early_yes·
  blocked). T2: 손님이 조용한 통화에서 empty가 아닌 것은 에코 글이다
- `events`: 줄이기·되돌림·멈춤 수(그림자 측정이면 `shadow`로 따로). `duck_to_unduck_ms`: 줄인 뒤
  되돌리기까지(맞장구·에코일 때 손님이 듣는 울렁임 길이, `s3-verify-barge-in` 6장 10)
- `backchannel_ms`: 맞장구로 판정한 글(복창 중 이른 "네" 포함)의 길이(STT `end_ms - start_ms`).
  멈춤 문턱(`PHONE_BARGE_STOP_MS`)을 정하는 근거다(논문 값: 한국어 중앙값 0.5초, 최대 2.4초)
- `false_stops`: 멈췄는데 그 구간에 받아들인 글이 없다(헛 멈춤)
- `stop_report_ms`: `bot.stop`을 보낸 뒤 브라우저가 멈췄다고 알리기까지(T7, 판정 → 소리 꺼짐의
  위 한계. 올림 회선 지연이 들어 있다)
- `listen_rms`: 듣는 동안 손님 말소리 프레임(`voiced`, 무음 감지 문턱 이상)과 조용할 때(`quiet`,
  말 구간 밖)의 RMS. 턴마다 낸 값을 모은다(voiced p10은 턴 p10의 중앙값, quiet p95는 턴 p95의
  p95). `PHONE_VAD_THRESHOLD`는 quiet p95와 voiced p10 사이, 끼어들기 문턱은 에코 p95 위에 둔다
- `turn_model`: Smart Turn 점수 출처(model·late·error·none) 수, 걸린 ms, 점수 대역(`low` 이하·
  가운데·`high` 이상) 수. 대역은 기본값(`defaults.py`, 0.3/0.7)이고, 서버를 다른
  `PHONE_TURN_LOW/HIGH`로 띄웠으면 `--low`·`--high`로 같은 값을 준다. 실기기 소리에서 점수가
  어디에 몰리는지 본다
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from agenthan.voice.call.defaults import MODEL_HIGH, MODEL_LOW
from agenthan.voice.call.turn_summary import percentile, read_lines, spread


def _duck_to_unduck(events: list[dict[str, Any]]) -> list[int]:
    out, ducked_at = [], None
    for e in events:
        if e.get("act") == "duck":
            ducked_at = e.get("at_ms")
        elif e.get("act") == "unduck" and ducked_at is not None:
            out.append(e["at_ms"] - ducked_at)
            ducked_at = None
    return out


def _is_false_stop(barge: dict[str, Any]) -> bool:
    stopped = any(e.get("act") == "stop" for e in barge.get("events", []))
    accepted = any(r.get("verdict") == "accept" for r in barge.get("results", []))
    return stopped and not accepted


def _rms_by_kind(windows: list[dict[str, Any]]) -> dict[str, dict[str, int | None]]:
    out: dict[str, dict[str, int | None]] = {}
    for kind in sorted({w.get("kind", "?") for w in windows}):
        stats = [w["rms"] for w in windows
                 if w.get("kind") == kind and w.get("rms", {}).get("frames")]
        out[kind] = {
            "windows": len(stats),
            "p50": percentile([s["p50"] for s in stats], 0.5),
            "p95": percentile([s["p95"] for s in stats], 0.95),
            "max": max((s["max"] for s in stats), default=None),
        }
    return out


def _listen_rms(lines: list[dict[str, Any]]) -> dict[str, int | None]:
    voiced = [line["listen_rms"]["voiced"] for line in lines
              if line.get("listen_rms", {}).get("voiced", {}).get("frames")]
    quiet = [line["listen_rms"]["quiet"] for line in lines
             if line.get("listen_rms", {}).get("quiet", {}).get("frames")]
    return {
        "turns": len(voiced),
        "voiced_p10": percentile([v["p10"] for v in voiced], 0.5),
        "voiced_p50": percentile([v["p50"] for v in voiced], 0.5),
        "quiet_p95": percentile([q["p95"] for q in quiet], 0.95),
        "quiet_max": max((q["max"] for q in quiet), default=None),
    }


def _turn_model(lines: list[dict[str, Any]], low: float, high: float) -> dict[str, Any]:
    infos = [line["turn_model"] for line in lines if isinstance(line.get("turn_model"), dict)]
    scores = [line["turn_end"]["score"] for line in lines
              if isinstance(line.get("turn_end"), dict)
              and isinstance(line["turn_end"].get("score"), int | float)]
    return {
        "src": dict(sorted(Counter(i.get("src") for i in infos).items())),
        "ms": spread([round(i["ms"]) for i in infos if isinstance(i.get("ms"), int | float)]),
        "score_bands": {  # 판정과 같은 대역 (`combine_turn_end`)
            f"le_{low}": sum(s <= low for s in scores),
            "middle": sum(low < s < high for s in scores),
            f"ge_{high}": sum(s >= high for s in scores),
        },
    }


def _call_row(call: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    first = min(lines, key=lambda line: line["turn"])
    client = first.get("client", {})
    mic = client.get("mic") or {}
    windows = [line["barge"] for line in lines if isinstance(line.get("barge"), dict)]
    acts = Counter(e.get("act") for w in windows for e in w.get("events", []))
    p95s = [w["rms"]["p95"] for w in windows if w.get("rms", {}).get("frames")]
    return {
        "call": call,
        "echo_cancellation": mic.get("echoCancellation"),
        "auto_gain": mic.get("autoGainControl"),
        "ctx_order": client.get("ctx_order"),
        "barge_capable": client.get("voice_barge_in"),
        "windows": len(windows),
        "echo_p95": max(p95s, default=None),
        "duck": acts["duck"],
        "unduck": acts["unduck"],
        "stop": acts["stop"],
        "false_stops": sum(_is_false_stop(w) for w in windows),
        "last_stop_ms": windows[-1].get("stop_ms") if windows else None,
    }


def summarize(
    lines: Iterable[dict[str, Any]], *, low: float = MODEL_LOW, high: float = MODEL_HIGH
) -> dict[str, Any]:
    """`low`·`high`는 서버의 Smart Turn 대역(`PHONE_TURN_LOW`·`PHONE_TURN_HIGH`)이다."""
    lines = list(lines)
    by_call: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_call.setdefault(line["call"], []).append(line)
    windows = [line["barge"] for line in lines if isinstance(line.get("barge"), dict)]
    events = [e for w in windows for e in w.get("events", [])]
    cuts = [line["interrupt"] for line in lines if isinstance(line.get("interrupt"), dict)]
    voice_cuts = [c for c in cuts if c.get("by") == "voice"]
    return {
        "calls": [_call_row(call, by_call[call]) for call in sorted(by_call)],
        "windows": dict(sorted(Counter(w.get("kind", "?") for w in windows).items())),
        "echo_rms": _rms_by_kind(windows),
        "results": dict(sorted(Counter(
            r.get("verdict") for w in windows for r in w.get("results", [])
        ).items())),
        "events": dict(sorted(Counter(
            f"{e.get('act')}:{e.get('by')}" + (":shadow" if e.get("shadow") else "")
            for e in events
        ).items())),
        "duck_to_unduck_ms": spread([d for w in windows
                                      for d in _duck_to_unduck(w.get("events", []))]),
        "false_stops": sum(_is_false_stop(w) for w in windows),
        "backchannel_ms": spread([
            r["end_ms"] - r["start_ms"] for w in windows for r in w.get("results", [])
            if r.get("verdict") in ("backchannel", "early_yes")
            and isinstance(r.get("start_ms"), int) and isinstance(r.get("end_ms"), int)
        ]),
        "stop_report_ms": spread([c["report_ms"] for c in voice_cuts if "report_ms" in c]),
        "listen_rms": _listen_rms(lines),
        "turn_model": _turn_model(lines, low, high),
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="음성 끼어들기 측정 요약 (숫자만)")
    parser.add_argument("jsonl", nargs="+", type=Path)
    parser.add_argument("--low", type=float, default=MODEL_LOW,
                        help=f"서버의 PHONE_TURN_LOW (기본 {MODEL_LOW})")
    parser.add_argument("--high", type=float, default=MODEL_HIGH,
                        help=f"서버의 PHONE_TURN_HIGH (기본 {MODEL_HIGH})")
    args = parser.parse_args(argv)
    summary = summarize(read_lines(args.jsonl), low=args.low, high=args.high)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
