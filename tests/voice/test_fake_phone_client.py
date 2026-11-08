"""가짜 휴대폰 (티켓 07): 녹음을 실제 WS 라우터에 흘려 넣고 봇 오디오를 '재생'하는 손님 흉내.

진짜 uvicorn 서버(같은 프로세스, 빈 포트)에 가짜 STT·TTS 라우터를 띄우고 그 위에서 돌린다.
속도(`pace`)만 줄여서 빨리 돈다. 서버 무음 감지·가짜 STT는 오디오 시각 기준이라 결과는 같다.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import struct
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.vad import rms
from agenthan.voice.fake_phone.client import Clip, run_call
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.ws import WS_PATH, phone_router

ORDER_SCRIPT = ("메도빅 하나요", "그게 다예요", "6시 반이요", "네")
NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
PACE = 0.03  # 실제 속도의 3%


def _silence(ms: int) -> bytes:
    return bytes(ms * 32)


def _tone(ms: int, amplitude: int = 4_000) -> bytes:
    n = ms * 16
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 440 * i / 16_000))
                                   for i in range(n)))


def _spoken(clip_id: str) -> Clip:
    """무음 300ms + 말 600ms + 무음 300ms. 말 끝은 0.9초."""
    return Clip(clip_id, _silence(300) + _tone(600) + _silence(300), speech_end_s=0.9)


@contextlib.asynccontextmanager
async def _server(streams: list[Any] | None = None, tts: Any = None) -> AsyncIterator[str]:
    settings = VoiceSettings(nlu="fake", stt="fake", tts="fake", log_dir=None, now=NOW,
                             fake_replies=ORDER_SCRIPT)

    def make_stt() -> FakeSttStream:
        stream = RecordingStt(replies=ORDER_SCRIPT)
        if streams is not None:
            streams.append(stream)
        return stream

    app = FastAPI()
    app.include_router(phone_router(store=FakeStoreKnowledge.from_fixture(),
                                    config=StoreConfig.from_fixture(),
                                    order_sink=MemoryOrderSink(), settings=settings,
                                    stt_factory=make_stt, tts_factory=tts))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                                           lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}{WS_PATH}"
    finally:
        server.should_exit = True
        await task


def _run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, 30))


def test_four_spoken_clips_in_one_call_end_with_the_confirmed_order() -> None:
    async def main() -> Any:
        async with _server() as url:
            return await run_call(url, [_spoken(f"A{i}") for i in range(1, 5)], pace=PACE)

    run = _run(main())

    assert [c.texts for c in run.clips] == [[t] for t in ORDER_SCRIPT]
    assert run.order is not None and run.order["order"]["status"] == "confirmed"
    assert run.error is None
    assert run.closed_by_server
    by_turn = {m["turn"]: m for m in run.metrics}
    assert sorted(by_turn) == [0, 1, 2, 3, 4]
    # 재생 보고(playback.started/done)를 보냈으니 서버 기록에 재생 시각이 있다
    assert all("play_start" in by_turn[t]["marks"] for t in range(5))


def test_each_clip_records_when_its_speech_ended_and_what_came_back() -> None:
    async def main() -> Any:
        async with _server() as url:
            return await run_call(url, [_spoken("A1")], pace=PACE)

    run = _run(main())

    (clip,) = run.clips
    assert clip.turns == [1]
    sent_ms = clip.sent_at_ms
    assert clip.speech_end_ms == pytest.approx(sent_ms + 900 * PACE, abs=5)
    final = next(e for e in run.events if e["type"] == "stt.final")
    first_sound = next(e for e in run.sent if e["type"] == "playback.started" and e["turn"] == 1)
    assert clip.speech_end_ms < final["t"] < first_sound["t"]


def test_a_clip_that_makes_no_turn_is_given_up_and_the_next_clip_still_goes() -> None:
    async def main() -> Any:
        async with _server() as url:
            clips = [_spoken("A1"), Clip("X", _silence(1_000), speech_end_s=None), _spoken("A2")]
            return await run_call(url, clips, pace=PACE, no_turn_timeout_s=3.0)

    run = _run(main())

    assert [c.texts for c in run.clips] == [["메도빅 하나요"], [], ["그게 다예요"]]
    assert [c.turns for c in run.clips] == [[1], [], [2]]


class RecordingStt(FakeSttStream):
    """가짜 STT + 받은 PCM 그대로 기록."""

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.pcm: list[bytes] = []

    async def write(self, pcm: bytes, *, ep_flag: bool = False, seq_id: int = 0) -> None:
        self.pcm.append(pcm)
        await super().write(pcm, ep_flag=ep_flag, seq_id=seq_id)


def test_mic_sends_zeros_while_the_bot_is_speaking() -> None:
    """반이중: 봇 오디오를 '재생'하는 동안 올라가는 프레임은 0으로 채운다 (spec 반이중)."""
    streams: list[RecordingStt] = []
    long_answer = FakeTts(bytes(32_000 * 10))  # 문장마다 10초: 녹음 뒤쪽 내내 봇이 말한다

    async def main() -> Any:
        async with _server(streams, tts=lambda: long_answer) as url:
            # 말이 끝나고 봇이 대답하는 동안에도 녹음은 계속된다(톤 4초). 그 부분은 0으로 가야 한다
            clip = Clip("A1", _silence(300) + _tone(600) + _silence(1_500) + _tone(4_000),
                        speech_end_s=0.9)
            return await run_call(url, [clip], pace=PACE, no_turn_timeout_s=3.0)

    run = _run(main())

    (stt,) = streams
    loud = sum(1 for frame in stt.pcm if rms(frame) > 1_000)
    assert 6 <= loud < 20, loud  # 처음 말 600ms는 가고, 봇이 말하는 동안의 톤 4초는 대부분 안 간다
    assert run.clips[0].muted_ms >= 2_000
