"""말 끊기 버튼 + 들은 만큼 기록 (3단계 티켓 05).

통화 세션의 바깥 행동만 본다. 가짜 STT·TTS·시계와 합성 PCM으로 결정적으로 만든다.
"""

from __future__ import annotations

import asyncio

from call_harness import Call

from agenthan.voice.call.fakes import FakeSttStream, FakeTts

ORDER_SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]


def run(coro):
    return asyncio.run(coro)


def test_the_button_is_allowed_while_the_bot_speaks_except_readback_and_closing(tmp_path) -> None:
    # 인사·주문 중 대답은 끊을 수 있고, 최종 복창(턴 3)과 마무리(턴 4)는 막는다.
    # 듣는 중·생각 중에는 끊을 소리가 없다
    stt = FakeSttStream(replies=ORDER_SCRIPT)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    c = run(call())
    states = c.out.of("state")
    assert all("interruptible" in e for e in states)
    speaking = {e["turn"]: e["interruptible"] for e in states if e["phase"] == "speaking"}
    assert speaking == {0: True, 1: True, 2: True, 3: False, 4: False}
    assert not any(e["interruptible"] for e in states if e["phase"] != "speaking")


class CancelAwareTts(FakeTts):
    """합성 도중 취소된 문장을 `cancelled`에 남기는 가짜 TTS."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.cancelled: list[str] = []

    async def synth(self, text: str) -> bytes:
        try:
            return await super().synth(text)
        except asyncio.CancelledError:
            self.cancelled.append(text)
            raise


SLOW = "더 필요하신 거 있으세요?"  # 턴 1 대답의 두 번째 문장


async def interrupt(c: Call, turn: int, seq: int = 0, played_ms: int = 200) -> None:
    """화면의 말 끊기 버튼: `user.interrupt` 뒤 브라우저가 멈춘 자리를 알린다."""
    await c.session.feed_event({"type": "user.interrupt", "turn": turn})
    await c.session.feed_event({"type": "playback.interrupted", "turn": turn, "seq": seq,
                                "played_ms": played_ms})


def test_the_button_stops_the_turn_and_the_next_words_do_not_wait_for_the_cut_tts(
    tmp_path,
) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    tts = CancelAwareTts(delays={SLOW: 5.0})

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.say", turn=1)
            loop = asyncio.get_running_loop()
            pressed = loop.time()
            await interrupt(c, 1)
            done = await c.out.wait_for("bot.done", turn=1)
            assert done["interrupted"] is True
            await c.say()  # 버튼을 누르고 바로 한 말
            await c.out.wait_for("bot.done", turn=2)
            return c, loop.time() - pressed

    c, elapsed = run(call())
    assert elapsed < 2.0, "끼어든 말이 끊긴 문장의 TTS(5초)를 기다리면 안 된다"
    assert [e["text"] for e in c.out.of("bot.say", 1)] == ["메도빅 하나요."]
    assert tts.cancelled == [SLOW]
    after = [(e["phase"], e["interruptible"]) for e in c.out.of("state", 1)]
    assert after[-1] == ("listening", False)
    assert [(e["turn"], e["text"]) for e in c.out.of("stt.final")] == [
        (1, "메도빅 하나요"), (2, "그게 다예요")
    ]
