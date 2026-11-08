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


def _spread(values: list[int], qs: tuple[float, ...] = (0.5, 0.9)) -> dict[str, int | None]:
    out: dict[str, int | None] = {"n": len(values)}
    for q in qs:
        out[f"p{round(q * 100)}_ms"] = percentile(values, q)
    return out


def summarize(lines: Iterable[dict[str, Any]]) -> dict[str, Any]:
    lines = list(lines)
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
        "guest_turns": len(guest),
        "voice": _spread([line["end_to_voice_ms"] for line in voiced]),
        "slow_over_ms": SLOW_OVER_MS,
        "slow_turns_per_call": slow,
        "calls_with_slow_turn": sum(1 for n in slow.values() if n),
        "thinking_turns": sum(1 for line in lines if "thinking" in line.get("marks", {})),
        "thinking": _spread(thinking),
        "end_by": dict(sorted(Counter(line["end_by"] for line in guest).items())),
        "turn_end_path": dict(sorted(Counter(
            line["turn_end"]["path"] for line in guest
            if isinstance(line.get("turn_end"), dict) and "path" in line["turn_end"]
        ).items())),
        "empty_turns": sum(len(line.get("empty", [])) for line in lines),
        "queued_turns": sum(1 for line in guest if line.get("queued")),
        "bot_turns": sum(1 for line in lines if "bot_turn" in line),
        "stt_wait": _spread(stt_wait, (0.5, 0.9, 0.99)),
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
    args = parser.parse_args(argv)
    print(json.dumps(summarize(read_lines(args.jsonl)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
