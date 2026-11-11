"""턴 기록 요약 (3단계 티켓 01, spec 지연 개선 묶음 · 지표 확장).

통화 세션이 쓴 JSONL(`latency.py`)을 읽어 숫자만 낸다. 손님 말·봇 말 글은 내지 않는다.

    uv run python -m agenthan.voice.call.turn_summary logs/phone-20261108.jsonl [...]

- `voice`: 말 끝 → 첫 목소리(`end_to_voice_ms`) P50·P90. 발표 기준은 이 P50이다(grilling Q5)
- `slow_turns_per_call`: 통화마다 `slow_over_ms`(2.5초, Hamming P90 기준)를 넘은 턴 수
- `thinking`: 말 끝 → 생각 중 효과음(`end_to_thinking_ms`). 목소리와 따로 센다
- `end_by`: 조각을 어떻게 끝냈나(무음 감지·버튼·gap·…)별 턴 수. `turn_end_path`: 말 끝 판정
  경로(rule·model·cap·button, 티켓 02 `turn_end.path`)별 턴 수
- `empty_turns`: 말소리는 있었는데 글이 빈 응답 수
- `stt_wait`: 무음 감지로 끝난 턴의 `ep_sent` → `stt_final`(epFlag 송신 → 글 있는 응답)
  P50·P90·P99. 세션 `hold_max_s`를 이 p99로 정한다(Pipecat `ttfs_p99`)
- `prefetch`: 미리 해석(이어짐 대기 중 NLU 선호출, 티켓 02 `nlu_prefetch`). 미리 부른 턴 수,
  적중 수·적중률, 부른 호출 수, 부르고 안 쓴 호출 수, 안 쓴 이유별 수, 엔진이 결과를 기다린 ms
- 6단계 티켓 02 (늦은 조각 턴 쪼개짐)
  - `rewind`: 되감은 턴 수(`superseded`), 되감은 턴을 뺀 손님 말 수, 되감기 전에 문지기가 첫
    소리를 붙잡은 ms(`gate_ms`). `PHONE_TURN_REWIND=on`일 때만 생긴다
  - `gate`: 헛 문지기(걸렸지만 되감지 않고 원래 답을 말한 턴) 수, 이유별 수(empty: 빈 글 ·
    backchannel: 맞장구·필러 · timeout: 상한), 첫 소리를 붙잡은 ms
  - `mic_open_extra`: 첫 소리 송신 → 재생 시작 보고 사이에 마이크를 더 열어 둔 ms
    (`PHONE_MIC_CLOSE=playback`으로 얻는 시간)
  - `more_no`: "더 필요하신 거?" 뒤 맨 "아니요"를 짧게 기다린 턴. alone: 뒷말 없이 넘어감(그만큼
    늦어진 턴) · merged: 뒷말과 한 턴이 됨

- 6단계 품질 (경로별 지연·되묻기)
  - `by_route`: 엔진이 탄 길(`route`)별 말 끝 → 첫 목소리 P50·P90과 구간별 P50. rule: NLU를 안
    부른 턴(규칙) · nlu: NLU(HCX)를 부른 턴 · answer: 답변기까지 부른 턴 · unknown: `route`가
    없는 예전 기록(답변기 결과가 있으면 answer로 센다)
  - `stages`: 손님 턴의 구간별 P50. end_to_ep: 말 끝 → epFlag 보냄 · ep_to_stt: epFlag → STT 글 ·
    stt_to_engine: 글 → 엔진 시작(말 끝 판정·이어짐 대기) · engine: 엔진(NLU·답변기 포함) ·
    tts_first: 첫 문장 TTS 첫 바이트 · send_to_play: 첫 조각 보냄 → 브라우저 재생 보고.
    그 구간의 두 시각이 다 있는 턴만 센다(`n`)
  - `reask`: 못 알아듣고 되물은 턴 수, 손님 턴당 비율, 슬롯별 수. `handoff`: 넘긴 이유별 통화 수
  - 스모크 통화(턴 0에 `smoke: true`, `python -m agenthan.voice.smoke`가 건 통화)는 빼고 센다.
    뺀 통화 수는 `smoke_calls`. 같이 보려면 `--include-smoke`

백분위는 선형 보간(numpy 기본과 같음)이다. 값이 적으면 p99는 사실상 최대값이다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

SLOW_OVER_MS = 2_500


def percentile(values: Sequence[float], q: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo))


def spread(values: list[int], qs: tuple[float, ...] = (0.5, 0.9)) -> dict[str, int | None]:
    """개수와 백분위(ms). 끼어들기 요약(`barge_summary`)도 같이 쓴다."""
    out: dict[str, int | None] = {"n": len(values)}
    for q in qs:
        out[f"p{round(q * 100)}_ms"] = percentile(values, q)
    return out


def _prefetch(guest: list[dict[str, Any]]) -> dict[str, Any]:
    """미리 해석(티켓 02 `nlu_prefetch`): 미리 부른 턴의 적중률, 부른·버린 호출 수(리뷰 P5)."""
    stats = [line["nlu_prefetch"] for line in guest if isinstance(line.get("nlu_prefetch"), dict)]
    used = sum(1 for s in stats if s.get("used"))
    return {
        "turns": len(stats),
        "used": used,
        "hit_rate": round(used / len(stats), 2) if stats else None,
        "calls": sum(s.get("prefetched", 0) for s in stats),
        "discarded": sum(s.get("discarded", 0) for s in stats),
        "miss": dict(sorted(Counter(s["miss"] for s in stats if s.get("miss")).items())),
        "wait": spread([s["wait_ms"] for s in stats if isinstance(s.get("wait_ms"), int)]),
    }


def _dicts(lines: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [line[key] for line in lines if isinstance(line.get(key), dict)]


def _ms(items: list[dict[str, Any]], key: str) -> list[int]:
    return [item[key] for item in items if isinstance(item.get(key), int)]


def _late_fragments(guest: list[dict[str, Any]]) -> dict[str, Any]:
    """6단계 티켓 02 지표: 되감기·문지기·마이크 닫는 때·맨 "아니요" 대기."""
    superseded = _dicts(guest, "superseded")
    gates = _dicts(guest, "gate")
    ends = _dicts(guest, "turn_end")
    steps = [step for line in guest for step in line.get("turn_end_steps", [])
             if isinstance(step, dict)]
    return {
        "rewind": {
            "superseded_turns": len(superseded),
            "guest_turns_net": len(guest) - len(superseded),
            "gate_wait": spread(_ms(superseded, "gate_ms")),
        },
        "gate": {
            "false_gates": len(gates),
            "by_result": dict(sorted(Counter(g["result"] for g in gates if "result" in g).items())),
            "wait": spread(_ms(gates, "wait_ms")),
        },
        "mic_open_extra": spread([line["mic_open_extra_ms"] for line in guest
                                  if isinstance(line.get("mic_open_extra_ms"), int)]),
        "more_no": {
            "alone": sum(1 for e in ends if e.get("reason") == "cap:no_after_more"),
            "merged": sum(1 for s in steps if s.get("reason") == "no_after_more"),
        },
    }


ROUTES = ("rule", "nlu", "answer", "unknown")
# 구간 이름 → (시작 mark, 끝 mark)
STAGES = {
    "end_to_ep": ("speech_end", "ep_sent"),
    "ep_to_stt": ("ep_sent", "stt_final"),
    "stt_to_engine": ("stt_final", "engine_start"),
    "engine": ("engine_start", "engine_end"),
    "tts_first": ("tts_start", "tts_first"),
    "send_to_play": ("audio_first", "play_start"),
}


def _route(line: dict[str, Any]) -> str:
    route = line.get("route")
    if route in ROUTES:
        return str(route)
    return "answer" if isinstance(line.get("answer"), dict) else "unknown"


def _stages(guest: list[dict[str, Any]]) -> dict[str, dict[str, int | None]]:
    """구간별 P50. 두 시각이 다 있는 턴만. 한 턴도 없는 구간은 뺀다."""
    out = {}
    for name, (start, end) in STAGES.items():
        values = [line["marks"][end] - line["marks"][start] for line in guest
                  if {start, end} <= set(line.get("marks", {}))]
        if values:
            out[name] = spread(values, (0.5,))
    return out


def _by_route(guest: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for route in ROUTES:
        turns = [line for line in guest if _route(line) == route]
        if turns:
            voiced = [line["end_to_voice_ms"] for line in turns if "end_to_voice_ms" in line]
            out[route] = {"voice": spread(voiced), "stages": _stages(turns)}
    return out


def _reask(lines: list[dict[str, Any]], guest_turns: int) -> dict[str, Any]:
    reasks = _dicts(lines, "reask")
    return {
        "turns": len(reasks),
        "per_guest_turn": round(len(reasks) / guest_turns, 2) if guest_turns else 0.0,
        "by_slot": dict(sorted(Counter(r["slot"] for r in reasks if "slot" in r).items())),
    }


def summarize(lines: Iterable[dict[str, Any]], *, include_smoke: bool = False) -> dict[str, Any]:
    lines = list(lines)
    smoke = {line["call"] for line in lines if line.get("smoke")}
    if not include_smoke:
        lines = [line for line in lines if line["call"] not in smoke]
    calls = sorted({line["call"] for line in lines})
    guest = [line for line in lines if "end_by" in line]  # 손님 말로 연 턴
    voiced = [line for line in guest if "end_to_voice_ms" in line]
    slow: dict[str, int] = dict.fromkeys(calls, 0)
    for line in voiced:
        if line["end_to_voice_ms"] > SLOW_OVER_MS:
            slow[line["call"]] += 1
    stt_wait = [
        line["marks"]["stt_final"] - line["marks"]["ep_sent"]
        for line in guest
        if line.get("end_by") == "vad" and {"ep_sent", "stt_final"} <= set(line.get("marks", {}))
    ]
    thinking = [line["end_to_thinking_ms"] for line in guest if "end_to_thinking_ms" in line]
    return {
        "calls": len(calls),
        "smoke_calls": len(smoke),
        "guest_turns": len(guest),
        "voice": spread([line["end_to_voice_ms"] for line in voiced]),
        "slow_over_ms": SLOW_OVER_MS,
        "slow_turns_per_call": slow,
        "calls_with_slow_turn": sum(1 for n in slow.values() if n),
        "thinking_turns": sum(1 for line in lines if "thinking" in line.get("marks", {})),
        "thinking": spread(thinking),
        "end_by": dict(sorted(Counter(line["end_by"] for line in guest).items())),
        "turn_end_path": dict(sorted(Counter(
            line["turn_end"]["path"] for line in guest
            if isinstance(line.get("turn_end"), dict) and "path" in line["turn_end"]
        ).items())),
        "empty_turns": sum(len(line.get("empty", [])) for line in lines),
        "queued_turns": sum(1 for line in guest if line.get("queued")),
        "bot_turns": sum(1 for line in lines if "bot_turn" in line),
        "stt_wait": spread(stt_wait, (0.5, 0.9, 0.99)),
        "prefetch": _prefetch(guest),
        **_late_fragments(guest),
        "by_route": _by_route(guest),
        "stages": _stages(guest),
        "reask": _reask(lines, len(guest)),
        "handoff": dict(sorted(Counter(
            h["reason"] for h in _dicts(lines, "handoff") if "reason" in h).items())),
    }


def read_lines(paths: Iterable[Path]) -> list[dict[str, Any]]:
    out = []
    for path in paths:
        with path.open(encoding="utf-8") as f:
            out.extend(json.loads(line) for line in f if line.strip())
    return out


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="통화 턴 기록 요약 (숫자만)")
    parser.add_argument("jsonl", nargs="+", type=Path)
    parser.add_argument("--include-smoke", action="store_true", help="스모크 통화도 같이 센다")
    args = parser.parse_args(argv)
    summary = summarize(read_lines(args.jsonl), include_smoke=args.include_smoke)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
