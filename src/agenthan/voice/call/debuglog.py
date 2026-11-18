"""통화 진단 기록 (5단계 티켓 03): STT 결과 원본과 손님 프레임 도착 간격을 통화마다 한 줄로.

2026-11-11 실기기 통화에서 통화 중에는 "메도비"·빈 글이었던 소리가, 같은 녹음을 다시 넣으면
문장 전체로 나왔다. 차이가 폰 → 서버 도착 방식인지 CLOVA 응답인지 가르려고 남긴다.

파일: `<log_dir>/stt-YYYYMMDD.jsonl`, 통화가 끝날 때 한 줄

    {"call": "c1", "stt_origin_ms": 5000,
     "frames": {"count": 300, "audio_ms": 30000, "max_gap_ms": 800, "max_behind_ms": 700,
                "late": [{"at_ms": 900, "gap_ms": 800, "behind_ms": 700}]},
     "stt": [{"ep_at_ms": 9082, "audio_ms": 3900},
             {"at_ms": 9447, "text": "메도비", "start_ms": 1200, "end_ms": 1900, "epd": "endPoint",
              "ep_flag": true, "seq": 1, "conf": 0.4}]}

- 시각(`*_at_ms`)은 턴 기록 `marks`·녹음과 같은 세션 시각
- `audio_ms`·`start_ms`·`end_ms`는 STT 오디오 시각(STT에 처음 쓴 바이트가 0). 세션 시각으로는
  대략 `stt_origin_ms` + 그 값(손님 프레임이 제때 왔다면)
- `frames.late`: 앞 프레임보다 `late_ms` 넘게 늦게 온 프레임. `behind_ms`는 그때까지 받은 소리
  길이가 벽시계보다 얼마나 모자랐는지(망이 멈췄다 몰려 오면 커진다). 최대 `MAX_ITEMS`개
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agenthan.voice.call.ports import SttResult

BYTES_PER_MS = 32
MAX_ITEMS = 500


class CallDebugLog:
    def __init__(self, *, late_ms: int = 250) -> None:
        self._late = late_ms
        self._first: int | None = None
        self._last: int | None = None
        self._audio_ms = 0
        self._count = 0
        self._max_gap = 0
        self._max_behind = 0
        self._late_items: list[dict[str, int]] = []
        self._stt: list[dict[str, Any]] = []
        self._origin: int | None = None

    def frame(self, *, at_ms: int, nbytes: int) -> None:
        if self._first is None:
            self._first = at_ms
        elif self._last is not None:
            gap = at_ms - self._last
            self._max_gap = max(self._max_gap, gap)
            behind = (at_ms - self._first) - self._audio_ms
            self._max_behind = max(self._max_behind, behind)
            if gap > self._late and len(self._late_items) < MAX_ITEMS:
                self._late_items.append({"at_ms": at_ms, "gap_ms": gap, "behind_ms": behind})
        self._last = at_ms
        self._count += 1
        self._audio_ms += nbytes // BYTES_PER_MS

    def stt_origin(self, *, at_ms: int) -> None:
        if self._origin is None:
            self._origin = at_ms

    def ep(self, *, at_ms: int, audio_ms: int) -> None:
        self._add({"ep_at_ms": at_ms, "audio_ms": audio_ms})

    def result(self, *, at_ms: int, r: SttResult) -> None:
        self._add({"at_ms": at_ms, "text": r.text, "start_ms": r.start_ms, "end_ms": r.end_ms,
                   "epd": r.epd_type, "ep_flag": r.ep_flag, "seq": r.seq_id,
                   "conf": r.confidence})

    def _add(self, item: dict[str, Any]) -> None:
        if len(self._stt) < MAX_ITEMS:
            self._stt.append(item)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stt_origin_ms": self._origin,
            "frames": {"count": self._count, "audio_ms": self._audio_ms,
                       "max_gap_ms": self._max_gap, "max_behind_ms": self._max_behind,
                       "late": list(self._late_items)},
            "stt": list(self._stt),
        }

    def write(self, path: Path, call_id: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"call": call_id, **self.as_dict()}, ensure_ascii=False) + "\n")
