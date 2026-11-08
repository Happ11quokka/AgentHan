"""지연 기록기: 턴별 구간 시각을 JSONL 한 줄로 (spec 모듈 · 지연 기록기, 사용자 이야기 26·27·30).

한 줄 모양 (시각은 통화 시작부터 ms, 단조 시계):

    {"call": "c1", "turn": 1, "at": "2026-11-08T16:00:03+09:00", "text": "메도빅 하나요",
     "epd_type": "endPoint", "fragments": 1, "splits": 0, "end_by": "vad",
     "marks": {"speech_end": 1200, "ep_sent": 1700, "stt_final": 2300, "engine_start": 2301,
               "engine_end": 2310, "tts_start": 2311, "tts_first": 2700, "audio_first": 2702,
               "bot_done": 2900, "play_start": 2950, "play_done": 4100},
     "output_latency": 0.02, "client": {...}}

- `marks`는 그 일이 있었을 때만 들어간다 (인사 턴 0에는 `speech_end`·`stt_final`이 없다)
- `dropped`(있을 때만): 그 턴에 버린 STT 결과 `[{text, epd_type, start_ms, end_ms, ep_flag,
  reason}]`. reason은 speaking(반이중) · terminal(확정·handoff 뒤) · ended(통화 끝) (티켓 10)
- `client`는 그 턴의 `client.metrics` 이벤트 내용을 합친 것(`turn`·`type` 뺌).
  턴 0에는 `call.start`의 값(마이크 실제 설정값 등)도 들어간다
- 줄은 다음 턴이 시작될 때나 통화가 끝날 때 쓴다.
  그래야 재생 뒤에 오는 `client.metrics`도 같은 줄에 들어간다
- 파일: `<log_dir>/phone-YYYYMMDD.jsonl` (가게 날짜). `log_dir=None`이면 파일을 쓰지 않는다
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agenthan.voice.call.ports import Clock


@dataclass
class TurnRecord:
    turn: int
    at: str
    fields: dict[str, Any] = field(default_factory=dict)
    marks: dict[str, int] = field(default_factory=dict)
    client: dict[str, Any] = field(default_factory=dict)

    def as_line(self, call_id: str) -> dict[str, Any]:
        return {
            "call": call_id,
            "turn": self.turn,
            "at": self.at,
            **self.fields,
            "marks": dict(self.marks),
            "client": dict(self.client),
        }


class LatencyLog:
    def __init__(self, log_dir: Path | None, call_id: str, clock: Clock) -> None:
        self._dir = log_dir
        self._call_id = call_id
        self._clock = clock
        self._t0 = clock.monotonic()
        self._open: dict[int, TurnRecord] = {}

    @property
    def path(self) -> Path | None:
        if self._dir is None:
            return None
        return self._dir / f"phone-{self._clock.now():%Y%m%d}.jsonl"

    def ms(self, at: float | None = None) -> int:
        """단조 시각(초) → 통화 시작부터 ms. 없으면 지금."""
        return round(((self._clock.monotonic() if at is None else at) - self._t0) * 1000)

    def begin(self, turn: int, **fields: Any) -> TurnRecord:
        record = TurnRecord(turn, self._clock.now().isoformat(), dict(fields))
        self._open[turn] = record
        return record

    def get(self, turn: int) -> TurnRecord | None:
        return self._open.get(turn)

    def mark(self, turn: int, name: str, *, at: float | None = None, first: bool = True) -> None:
        record = self._open.get(turn)
        if record is None or (first and name in record.marks):
            return
        record.marks[name] = self.ms(at)

    def flush(self, before: int | None = None) -> list[dict[str, Any]]:
        """`before`보다 앞 턴(없으면 전부)을 파일에 쓰고 닫는다. 쓴 줄들을 돌려준다."""
        done = sorted(t for t in self._open if before is None or t < before)
        lines = [self._open.pop(t).as_line(self._call_id) for t in done]
        path = self.path
        if lines and path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return lines
