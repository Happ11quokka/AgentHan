"""측정 요약 (티켓 07): 가짜 휴대폰 통화 기록 → 녹음별 줄, 모드별 요약, 통화별 주문 결과.

녹음별 줄의 시간(ms)은 가짜 휴대폰 시계 하나로 잰다.
- `end_to_result_ms`: 기준 말 끝(녹음 안 말 끝, `audio.speech_end_s`) → 그 녹음의 마지막 `stt.final`
- `end_to_first_sound_ms`: 기준 말 끝 → 그 턴 첫 문장 `playback.started`
  (가짜 휴대폰이 재생을 시작한 때)
- `engine_ms`·`tts_first_ms`: 서버 `metrics`의 `engine_end - engine_start`, `tts_first - tts_start`
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from agenthan.voice.fake_phone.client import CallRun

# 숫자 쓰는 법만 다른 것(STT는 "2개", 대본은 "두 개")은 같게 본다. 양쪽에 똑같이 바꾼다
_NUMBERS = (("하나", "1"), ("여섯", "6"), ("일곱", "7"), ("한", "1"), ("두", "2"), ("둘", "2"),
            ("세", "3"), ("셋", "3"))
YES_RATIO = 0.9  # 이 이상 비슷하면 맞음(띄어쓰기·문장부호·숫자 표기 차이 정도)
PARTIAL_RATIO = 0.5  # 이 이상이면 일부 맞음(메뉴 이름 하나 틀림, 앞말 빠짐 등)


def _norm(text: str) -> str:
    text = re.sub(r"[\s\W_]+", "", text)
    for word, digit in _NUMBERS:
        text = text.replace(word, digit)
    return text


def match_script(heard: str, script: str) -> str:
    """받아 적은 글이 대본과 맞나: `yes` · `partial` · `no`."""
    a, b = _norm(heard), _norm(script)
    if not a:
        return "no"
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= YES_RATIO:
        return "yes"
    return "partial" if ratio >= PARTIAL_RATIO else "no"


def clip_rows(run: CallRun, script: dict[str, str]) -> list[dict[str, Any]]:
    finals = {e["turn"]: e["t"] for e in run.events if e.get("type") == "stt.final"}
    first_sound: dict[int, float] = {}
    for e in run.sent:
        if e.get("type") == "playback.started":
            first_sound.setdefault(e["turn"], e["t"])
    metrics = {m["turn"]: m for m in run.metrics}
    rows = []
    for clip in run.clips:
        text = " ".join(clip.texts)
        last = clip.turns[-1] if clip.turns else None
        m = metrics.get(last, {}) if last is not None else {}
        marks = m.get("marks", {})

        def since_end(at: float | None, end: float | None = clip.speech_end_ms) -> int | None:
            return None if at is None or end is None else round(at - end)

        rows.append({
            "clip": clip.id,
            "text": text,
            "match": match_script(text, script.get(clip.id, "")),
            "turns": len(clip.turns),
            "fragments": sum(metrics.get(t, {}).get("fragments", 0) for t in clip.turns),
            "end_by": m.get("end_by", "-") if last is not None else "-",
            "end_to_result_ms": since_end(finals.get(last)) if last is not None else None,
            "end_to_first_sound_ms": (since_end(first_sound.get(last))
                                      if last is not None else None),
            "engine_ms": _span(marks, "engine_start", "engine_end"),
            "tts_first_ms": _span(marks, "tts_start", "tts_first"),
            "muted_ms": clip.muted_ms,
        })
    return rows


def _span(marks: dict[str, int], start: str, end: str) -> int | None:
    if start in marks and end in marks:
        return marks[end] - marks[start]
    return None


def overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """말 끝 판정 경로 수, 대본 일치 수, 중앙값."""

    def median(key: str) -> float | None:
        values = [r[key] for r in rows if r[key] is not None]
        return statistics.median(values) if values else None

    return {
        "end_by": dict(sorted(Counter(r["end_by"] for r in rows if r["end_by"] != "-").items())),
        "match": dict(Counter(r["match"] for r in rows)),
        "median_end_to_result_ms": median("end_to_result_ms"),
        "median_end_to_first_sound_ms": median("end_to_first_sound_ms"),
        "median_engine_ms": median("engine_ms"),
        "median_tts_first_ms": median("tts_first_ms"),
    }


def call_outcome(run: CallRun) -> dict[str, Any]:
    """통화가 어떤 주문서로 끝났나. 없으면 어디서 멈췄나(마지막 봇 대사)."""
    order = next((e for e in reversed(run.events) if e.get("type") == "order"), None)
    error = next((e for e in reversed(run.events) if e.get("type") == "error"), None)
    says = [e["text"] for e in run.events if e.get("type") == "bot.say"]
    if order is not None:
        status = order["order"]["status"]
    elif error is not None:
        status = f"error:{error.get('code')}"
    else:
        status = "unfinished"
    return {
        "status": status,
        "total": order.get("total") if order else None,
        "pickup_time": order["order"].get("pickup_time") if order else None,
        "items": (", ".join(_item(line) for line in order.get("cart", [])) if order else ""),
        "last_bot": says[-1] if says else "",
    }


def _item(line: dict[str, Any]) -> str:
    options = "·".join(line.get("options") or [])
    return f"{line['name']}{f'({options})' if options else ''} {line['qty']}"


def markdown_table(rows: list[dict[str, Any]]) -> str:
    """녹음별 요약 표 (받아 적은 글 원문은 넣지 않는다)."""
    head = ("| 녹음 | 대본 일치 | 턴 | 조각 | 말 끝 판정 | 말 끝→결과(ms) | 말 끝→첫 소리(ms) "
            "| 엔진(ms) | TTS 첫 문장(ms) |")
    lines = [head, "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cells = [r["clip"], r["match"], r["turns"], r["fragments"], r["end_by"],
                 r["end_to_result_ms"], r["end_to_first_sound_ms"], r["engine_ms"],
                 r["tts_first_ms"]]
        lines.append("| " + " | ".join("-" if c is None else str(c) for c in cells) + " |")
    return "\n".join(lines)
