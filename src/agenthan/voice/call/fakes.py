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
- `RecordingOwnerChannel`: 사장님 쪽(`OwnerChannel`) 가짜. 받은 알림을 차례로 모은다.
  `answer`를 주면 질문을 받자마자 그 답을 한다(바로 답하는 가짜 사장님, 티켓 05)
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agenthan.core.contracts import OrderLine
from agenthan.voice.call.frames import BYTES_PER_MS, CHUNK_BYTES
from agenthan.voice.call.ports import SttResult
from agenthan.voice.dialog.owner_question import (
    OwnerButton,
    OwnerNote,
    OwnerQuestion,
    OwnerReply,
    OwnerSeen,
)

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


class RecordingOwnerChannel:
    """`OwnerChannel` 가짜. 받은 알림을 `notes`에 차례로 쌓는다.

    알림 모양: `("started", id)` · `("cart", id, 줄 튜플)` · `("customer", id, 글)` ·
    `("bot", id, 글)` · `("ended", id, 이유)` · `("question", id, OwnerQuestion)` ·
    `("timeout", id, 질문 번호)`

    `answer`가 있으면 질문을 받은 자리에서 그 답을 `reply`로 돌려준다. 없으면 답하지 않는다
    (시간 초과·늦은 답 시험은 나중에 `answer`·`see`를 부른다).
    """

    def __init__(self, *, answer: OwnerButton | None = None) -> None:
        self.notes: list[tuple[Any, ...]] = []
        self._answer = answer
        self._replies: dict[int, Callable[[OwnerNote], None]] = {}  # 질문 번호 → 세션의 받는 곳

    def answer(self, number: int, answer: OwnerButton) -> None:
        """사장님이 그 질문에 버튼으로 답했다(늦은 답·낡은 답 흉내도 그대로 넘긴다)."""
        self._replies[number](OwnerReply(number, answer))

    def see(self, number: int) -> None:
        """사장님이 그 질문 카드를 봤다."""
        self._replies[number](OwnerSeen(number))

    def call_started(self, call_id: str) -> None:
        self.notes.append(("started", call_id))

    def cart_changed(self, call_id: str, lines: Sequence[OrderLine]) -> None:
        self.notes.append(("cart", call_id, tuple(lines)))

    def customer_said(self, call_id: str, text: str) -> None:
        self.notes.append(("customer", call_id, text))

    def bot_said(self, call_id: str, text: str) -> None:
        self.notes.append(("bot", call_id, text))

    def call_ended(self, call_id: str, reason: str) -> None:
        self.notes.append(("ended", call_id, reason))

    def open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerNote], None]
    ) -> None:
        self.notes.append(("question", call_id, question))
        self._replies[question.number] = reply
        if self._answer is not None:
            reply(OwnerReply(question.number, self._answer))

    def question_timed_out(self, call_id: str, number: int) -> None:
        self.notes.append(("timeout", call_id, number))

    def _of(self, kind: str) -> list[Any]:
        return [note[-1] for note in self.notes if note[0] == kind]

    @property
    def carts(self) -> list[tuple[OrderLine, ...]]:
        return self._of("cart")

    @property
    def customer_lines(self) -> list[str]:
        return self._of("customer")

    @property
    def bot_lines(self) -> list[str]:
        return self._of("bot")

    @property
    def questions(self) -> list[tuple[str, OwnerQuestion]]:
        """연 질문들 (통화 id, 질문)."""
        return [(note[1], note[2]) for note in self.notes if note[0] == "question"]

    @property
    def timeouts(self) -> list[int]:
        """시간 초과를 알린 질문 번호들."""
        return self._of("timeout")

    @property
    def ended(self) -> list[str]:
        """받은 "통화 끝" 이유들 (통화당 하나여야 한다)."""
        return self._of("ended")
