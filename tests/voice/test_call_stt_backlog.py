"""STT 쓰기가 느려도 손님 이벤트 처리는 밀리지 않는다 (리뷰 1번).

세션이 STT 송신 큐와 쓰기 작업 하나를 둔다. 밀린 프레임은 `epFlag` 프레임까지 묶어 DATA 하나로
보내고 바이트는 버리지 않는다. 3초 넘게 밀리면 `stt_backlog`로 통화를 끝낸다.
"""

from __future__ import annotations

import asyncio
import json
import time

from call_harness import FRAME_MS, Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream


def run(coro):
    return asyncio.run(coro)


class SlowStt(FakeSttStream):
    """DATA 하나를 쓰는 데 `delay`초 걸린다 (CLOVA 쪽 업로드가 느린 회선)."""

    def __init__(self, *args, delay: float = 0.3, **kw) -> None:
        super().__init__(*args, **kw)
        self.delay = delay
        self.gate: asyncio.Event | None = None  # 있으면 열릴 때까지 쓰기가 멈춘다

    async def write(self, pcm: bytes, *, ep_flag: bool = False, seq_id: int = 0) -> None:
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(self.delay)
        await super().write(pcm, ep_flag=ep_flag, seq_id=seq_id)


async def _until(check, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("기다리는 일이 일어나지 않았다")
        await asyncio.sleep(0.01)


def test_playback_done_is_handled_at_once_even_if_stt_writes_are_slow(tmp_path) -> None:
    stt = SlowStt(delay=0.3)

    async def call() -> float:
        async with Call(tmp_path, stt=stt) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("bot.done", turn=0)
            # 읽기 작업 하나가 하듯: 0 프레임 20개(인사 재생 중) 뒤에 playback.done
            began = time.monotonic()
            for frame in silence(2_000):
                await c.session.feed_audio(frame)
            await c.played(0)
            await c.out.wait_for("state", turn=0)
            await _until(lambda: c.out.of("state", 0)[-1]["phase"] == "listening")
            return time.monotonic() - began

    assert run(call()) < 0.05


def test_backlogged_frames_are_joined_up_to_the_ep_flag_without_losing_bytes(tmp_path) -> None:
    stt = SlowStt(delay=0.05)

    async def call() -> None:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            before_ms, before_writes = stt.audio_ms, len(stt.writes)
            stt.gate = asyncio.Event()
            frames = tone(800) + silence(1_000)  # 무음 감지 epFlag는 1,200ms 프레임 끝
            for frame in frames:
                await c.session.feed_audio(frame)
            stt.gate.set()
            await _until(lambda: stt.audio_ms - before_ms == len(frames) * FRAME_MS)
            writes = stt.writes[before_writes:]
            assert sum(w.size for w in writes) == sum(len(f) for f in frames)
            assert len(writes) < len(frames)  # 묶여서 갔다
            (ep,) = [w for w in writes if w.ep_flag]
            assert ep.at_ms - before_ms + ep.size // 32 == 1_200  # epFlag는 같은 바이트 끝에
            assert ep.seq_id == 1

    run(call())


def test_three_seconds_of_backlog_ends_the_call_with_stt_backlog(tmp_path) -> None:
    stt = SlowStt(delay=0.0)

    async def call() -> tuple[dict, list[dict]]:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            stt.gate = asyncio.Event()  # CLOVA 쪽이 멈췄다
            for frame in silence(3_500):
                await c.session.feed_audio(frame)
            error = await c.out.wait_for("error")
            await asyncio.wait_for(c.session.wait_done(), 3)
            stt.gate.set()
        lines = (c.log_dir / "phone-20261108.jsonl").read_text().splitlines()
        return error, [json.loads(line) for line in lines]

    error, lines = run(call())
    assert (error["code"], error["retry"]) == ("stt_backlog", True)
    assert "회선" in error["message"]
    assert lines[-1]["stt_backlog_max"] >= 3_000
