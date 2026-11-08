"""가짜 STT·TTS·시계. 테스트와 키 없는 개발 서버(티켓 04 가짜 모드)가 쓴다.

- `FakeSttStream`: 오디오 시각 기준으로 결과를 낸다. 두 가지를 섞어 쓸 수 있다.
  - `replies`: `epFlag`가 붙은 DATA가 올 때마다 다음 글을 `lag_ms` 뒤(오디오 시각)에
    `endPoint` 결과로 낸다. 다 쓰면 빈 글 + `epFlag` 응답(CLOVA 실측과 같음)
  - `script`: `Scripted(at_ms, result)` — 세션이 `at_ms`만큼 오디오를 쓴 순간 그 결과를 낸다
  - `voiced_start`: 참(기본)이면 `epFlag` 응답의 `start_ms`가 지난 `epFlag` 뒤 처음 0이 아닌
    프레임의 시각이다(CLOVA처럼 말한 시각). 말소리 프레임이 없으면 그 `epFlag` 프레임의 시각이다.
    거짓이면 지난 `epFlag`의 시각이다(3단계 리뷰 P1 전 기본값. 복창 앞 "네"를 말한 시각으로
    거르므로 기본을 말한 시각으로 바꿨다)
- `FakeTts`: 고정 PCM을 돌려준다. 문장별 지연·실패를 줄 수 있고 동시 호출 수를 센다
- `FakeClock`: 고정 가게 시각 + (기본) 실제 단조 시각
- `FakeTurnModel`: 턴 모델(Smart Turn) 가짜. 정해 둔 점수를 차례로 내고, 본 오디오 길이를 남긴다
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from agenthan.voice.call.frames import BYTES_PER_MS, CHUNK_BYTES
from agenthan.voice.call.ports import SttResult

STT_LAG_MS = 600  # CLOVA 실측: epFlag 보낸 뒤 결과까지 0.5~0.6초


@dataclass(frozen=True)
class Scripted:
    at_ms: int  # 세션이 이만큼(ms)의 오디오를 쓴 뒤에 낸다
    result: SttResult


@dataclass(frozen=True)
class SttWrite:
    """세션이 쓴 DATA 하나의 기록."""

    at_ms: int  # 이 프레임이 시작하는 오디오 시각
    size: int
    ep_flag: bool
    seq_id: int


class FakeSttStream:
    def __init__(
        self,
        replies: Iterable[str] = (),
        script: Iterable[Scripted] = (),
        *,
        lag_ms: int = STT_LAG_MS,
        fail_start: Exception | None = None,
        voiced_start: bool = True,
    ) -> None:
        self.writes: list[SttWrite] = []
        self.started = False
        self.closed = False
        self._replies = deque(replies)
        self._pending: list[Scripted] = list(script)
        self._lag_ms = lag_ms
        self._fail_start = fail_start
        self._offset = 0
        self._utter_start = 0
        self._voiced_start = voiced_start
        self._first_voiced: int | None = None
        self._queue: asyncio.Queue[SttResult | Exception | None] = asyncio.Queue()

    @property
    def audio_ms(self) -> int:
        return self._offset

    async def start(self) -> None:
        if self._fail_start is not None:
            raise self._fail_start
        self.started = True

    async def write(self, pcm: bytes, *, ep_flag: bool = False, seq_id: int = 0) -> None:
        self.writes.append(SttWrite(self._offset, len(pcm), ep_flag, seq_id))
        if self._first_voiced is None and any(pcm):
            self._first_voiced = self._offset
        self._offset += len(pcm) // BYTES_PER_MS
        if ep_flag:
            text = self._replies.popleft() if self._replies else ""
            start = self._utter_start
            if self._voiced_start:
                start = self.writes[-1].at_ms if self._first_voiced is None else self._first_voiced
            result = SttResult(text, start, self._offset, "endPoint", True, seq_id)
            self._pending.append(Scripted(self._offset + self._lag_ms, result))
            self._utter_start = self._offset
            self._first_voiced = None
        due = [s for s in self._pending if s.at_ms <= self._offset]
        self._pending = [s for s in self._pending if s.at_ms > self._offset]
        for item in due:
            self._queue.put_nowait(item.result)
        await asyncio.sleep(0)

    def push(self, result: SttResult) -> None:
        """오디오 시각과 상관없이 결과 하나를 바로 낸다."""
        self._queue.put_nowait(result)

    def fail(self, error: Exception) -> None:
        """결과 스트림이 이 예외로 깨진다 (연결 끊김 흉내)."""
        self._queue.put_nowait(error)

    async def results(self) -> AsyncIterator[SttResult]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put_nowait(None)


class FakeTts:
    def __init__(
        self,
        pcm: bytes = bytes(CHUNK_BYTES * 2 + 320),  # 약 410ms → 조각 3개
        *,
        delays: Mapping[str, float] | Callable[[str], float] | None = None,
        failures: Mapping[str, Exception] | None = None,
    ) -> None:
        self.pcm = pcm
        self.calls: list[str] = []
        self.max_active = 0
        self._active = 0
        self._delays = delays or {}
        self._failures = failures or {}

    def _delay(self, text: str) -> float:
        if callable(self._delays):
            return self._delays(text)
        return self._delays.get(text, 0.0)

    async def synth(self, text: str) -> bytes:
        self.calls.append(text)
        self._active += 1
        self.max_active = max(self.max_active, self._active)
        try:
            await asyncio.sleep(self._delay(text))
            if text in self._failures:
                raise self._failures[text]
            return self.pcm
        finally:
            self._active -= 1


class FakeClock:
    def __init__(self, now: datetime, monotonic: Callable[[], float] = time.monotonic) -> None:
        if now.tzinfo is None:
            raise ValueError("가짜 시계의 now도 시간대가 있어야 해요")
        self._now = now
        self._monotonic = monotonic

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def monotonic(self) -> float:
        return self._monotonic()


class FakeTurnModel:
    """`TurnModel` 가짜. 부를 때마다 `scores`의 다음 값(다 쓰면 마지막 값)을 낸다.

    값이 예외면 그 예외를 올린다(모델 실패 흉내).
    `heard_ms`에 부를 때 받은 오디오 길이(ms)를 쌓는다.
    """

    def __init__(self, scores: Iterable[float | Exception]) -> None:
        self._scores = list(scores)
        if not self._scores:
            raise ValueError("점수를 하나 이상 주세요")
        self.heard_ms: list[int] = []

    def p_complete(self, pcm16: bytes) -> float:
        self.heard_ms.append(len(pcm16) // BYTES_PER_MS)
        score = self._scores.pop(0) if len(self._scores) > 1 else self._scores[0]
        if isinstance(score, Exception):
            raise score
        return score
