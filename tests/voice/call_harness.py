"""통화 세션 테스트 도구: 나간 이벤트·오디오를 모으는 송신 포트, 합성 PCM, 손님 흉내.

오디오는 합성 PCM(무음·톤 100ms 프레임)으로 결정적으로 만든다 (spec Testing Decisions).
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import Callable
from concurrent.futures import Executor, Future
from datetime import datetime
from pathlib import Path
from typing import Any

from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice.call.fakes import FakeClock, FakeSttStream, FakeTts, RecordingOwnerChannel
from agenthan.voice.call.frames import AudioFrame, unpack_audio_frame
from agenthan.voice.call.session import CallSession, SessionSettings
from agenthan.voice.store_config import StoreConfig

FRAME_MS = 100
FRAME_SAMPLES = 1_600
NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
WAIT = 3.0  # 이벤트를 기다리는 최대 초. 넘으면 테스트 실패


def silence(ms: int = FRAME_MS) -> list[bytes]:
    return [bytes(FRAME_SAMPLES * 2) for _ in range(ms // FRAME_MS)]


def tone(ms: int = FRAME_MS, amplitude: int = 4_000) -> list[bytes]:
    one = struct.pack(
        f"<{FRAME_SAMPLES}h",
        *(int(amplitude * math.sin(2 * math.pi * 440 * i / 16_000)) for i in range(FRAME_SAMPLES)),
    )
    return [one for _ in range(ms // FRAME_MS)]


class InlineExecutor(Executor):
    """부른 자리에서 바로 돌리는 실행기. 턴 모델 점수가 말 끝 순간에 바로 나온다(결정적 시험)."""

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as err:  # noqa: BLE001 - 모델 실패 흉내도 그대로 담는다
            future.set_exception(err)
        return future


class StalledExecutor(Executor):
    """받기만 하고 돌리지 않는 실행기. 턴 모델 결과가 판정 때까지 안 오는 경우(늦음)."""

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        return Future()


class RecordingOutbound:
    """송신 포트 가짜. 보낸 순서대로 `sent`에 쌓는다 (이벤트는 dict, 오디오는 `AudioFrame`)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any] | AudioFrame] = []
        self._changed = asyncio.Condition()

    async def send_event(self, event: dict[str, Any]) -> None:
        await self._add(event)

    async def send_audio(self, frame: bytes) -> None:
        await self._add(unpack_audio_frame(frame))

    async def _add(self, item: dict[str, Any] | AudioFrame) -> None:
        async with self._changed:
            self.sent.append(item)
            self._changed.notify_all()

    @property
    def events(self) -> list[dict[str, Any]]:
        return [m for m in self.sent if isinstance(m, dict)]

    @property
    def frames(self) -> list[AudioFrame]:
        return [m for m in self.sent if isinstance(m, AudioFrame)]

    def of(self, type_: str, turn: int | None = None) -> list[dict[str, Any]]:
        return [
            e for e in self.events if e["type"] == type_ and (turn is None or e["turn"] == turn)
        ]

    async def wait_for(self, type_: str, turn: int | None = None) -> dict[str, Any]:
        def found() -> bool:
            return bool(self.of(type_, turn))

        async with self._changed:
            await asyncio.wait_for(self._changed.wait_for(found), WAIT)
        return self.of(type_, turn)[-1]


class Call:
    """세션 하나와 그 가짜 포트들. 손님 쪽 동작(말하기·재생 보고)을 흉내 낸다."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        stt: FakeSttStream | None = None,
        tts: FakeTts | None = None,
        nlu: Any = "fake",
        clock: FakeClock | None = None,
        settings: SessionSettings | None = None,
        store_factory: Callable[[], Any] = FakeStoreKnowledge.from_fixture,
        executor: Executor | None = None,
        turn_model: Any = None,
        turn_executor: Executor | None = None,
        owner: RecordingOwnerChannel | None = None,
        knowledge: Any = None,
        no_owner: bool = False,
    ) -> None:
        """`knowledge`는 보충 지식 읽기 포트(4단계 티켓 13). `no_owner`면 사장님 쪽 없이 띄운다."""
        self.out = RecordingOutbound()
        self.sink = MemoryOrderSink()
        self.owner = owner or RecordingOwnerChannel()
        self.stt = stt or FakeSttStream()
        self.tts = tts or FakeTts()
        self.clock = clock or FakeClock(NOW)
        self.log_dir = tmp_path / "logs"
        self.session = CallSession(
            store=store_factory(),
            config=StoreConfig.from_fixture(),
            nlu=nlu,
            stt=self.stt,
            tts=self.tts,
            clock=self.clock,
            outbound=self.out,
            order_sink=self.sink,
            owner=None if no_owner else self.owner,
            knowledge=knowledge,
            log_dir=self.log_dir,
            call_id="test-call",
            settings=settings or SessionSettings(),
            executor=executor,
            turn_model=turn_model,
            turn_executor=turn_executor,
        )

    async def __aenter__(self) -> Call:
        await self.session.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.session.close()

    async def start(self, **client: Any) -> None:
        """`call.start` → 인사 턴(0)이 다 나가고, 재생 끝까지 보고한다."""
        await self.session.feed_event({"type": "call.start", "turn": 0, **client})
        await self.out.wait_for("bot.done", turn=0)
        await self.played(0)

    async def feed(self, frames: list[bytes]) -> None:
        """프레임을 넣고, 세션의 STT 쓰기 작업이 그것을 다 쓸 때까지 기다린다."""
        for frame in frames:
            await self.session.feed_audio(frame)
        await self.session.drain_audio()

    async def say(self, speech_ms: int = 800, after_ms: int = 1_200) -> None:
        """말소리 뒤 무음. 무음 감지가 `epFlag`를 붙이고 가짜 STT가 대본의 다음 글을 낸다."""
        await self.feed(tone(speech_ms) + silence(after_ms))

    async def played(self, turn: int, output_latency: float = 0.02) -> None:
        """그 턴의 봇 문장을 모두 재생했다고 보고한다 (`playback.started` → `playback.done`)."""
        says = self.out.of("bot.say", turn)
        for say in says:
            await self.session.feed_event({"type": "playback.started", "turn": turn,
                                           "seq": say["seq"]})
            await self.session.feed_event({"type": "playback.done", "turn": turn,
                                           "seq": say["seq"], "output_latency": output_latency})

    async def turn(self, turn: int, speech_ms: int = 800) -> dict[str, Any]:
        """손님이 한 번 말하고, 봇 대답이 다 나오고, 재생 끝까지. `bot.done`을 돌려준다."""
        await self.say(speech_ms)
        done = await self.out.wait_for("bot.done", turn=turn)
        await self.played(turn)
        return done
