"""말 끊기 버튼 + 들은 만큼 기록 (3단계 티켓 05).

통화 세션의 바깥 행동만 본다. 가짜 STT·TTS·시계와 합성 PCM으로 결정적으로 만든다.
"""

from __future__ import annotations

import asyncio
import json

from call_harness import Call, RecordingOutbound

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


def _lines(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(s) for s in path.read_text().splitlines()]


def test_the_turn_record_keeps_how_much_of_each_sentence_the_guest_heard(tmp_path) -> None:
    # 가짜 TTS 문장은 410ms. 첫 문장을 200ms 듣고 끊었고 둘째 문장은 합성 중이었다
    stt = FakeSttStream(replies=["메도빅 하나요"])
    tts = FakeTts(delays={SLOW: 5.0})

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.say", turn=1)
            await interrupt(c, 1, seq=0, played_ms=200)
            await c.out.wait_for("bot.done", turn=1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    record = next(line for line in _lines(c) if line["turn"] == 1)
    assert record["interrupt"] == {
        "by": "button", "seq": 0, "played_ms": 200,
        "heard": [
            {"seq": 0, "heard": "partial", "ms": 410, "played_ms": 200, "chars": 4},
            {"seq": 1, "heard": "unheard", "ms": None, "played_ms": 0},
        ],
    }
    assert "interrupted" in record["marks"]
    assert "play_done" not in record["marks"]


def test_a_cut_after_every_sentence_was_sent_marks_earlier_sentences_heard(tmp_path) -> None:
    # TTS가 다 끝나 문장을 모두 보낸 뒤 둘째 문장 재생 중에 끊었다
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await interrupt(c, 1, seq=1, played_ms=5_000)  # 문장 길이보다 길면 끝까지 들음
            await asyncio.sleep(0.05)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert c.out.of("state", 1)[-1]["phase"] == "listening"
    record = next(line for line in _lines(c) if line["turn"] == 1)
    assert [(h["seq"], h["heard"]) for h in record["interrupt"]["heard"]] == [
        (0, "heard"), (1, "heard")
    ]
    assert "play_done_timeout" not in record


def test_a_button_press_during_the_final_readback_cannot_lead_to_a_confirm(tmp_path) -> None:
    # 회귀: 복창(총액 문장) 중 버튼을 눌러 끊고 "네"라고 하면 총액을 못 듣고 확정됐을 것이다.
    # 브라우저가 버튼을 막지만, 서버도 복창 턴의 끊기를 받지 않는다. 봇이 계속 말하는 중이라
    # 그동안 한 "네"는 반이중으로 버린다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요", "6시 반이요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2):
                await c.turn(turn)
            await c.say()
            await c.out.wait_for("bot.say", turn=3)
            await interrupt(c, 3, seq=0, played_ms=100)
            await c.say()  # 버튼이 먹은 줄 알고 바로 "네" (마이크를 연 브라우저 흉내)
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert c.out.of("order") == []
    assert c.sink.orders == []
    assert [e["text"] for e in c.out.of("stt.final")] == ORDER_SCRIPT[:3]
    assert [(e["phase"], e["interruptible"]) for e in c.out.of("state", 3)] == [
        ("thinking", False), ("speaking", False)
    ]
    assert [e["text"] for e in c.out.of("bot.say", 3)] == [
        "메도빅 하나, 6시 반 픽업, 총 7,500원이에요.", "맞으세요?"
    ]


def test_an_interrupt_for_an_older_turn_or_while_listening_is_ignored(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await interrupt(c, 0)  # 인사 재생은 이미 끝났다
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await interrupt(c, 0)  # 지난 턴
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert c.out.of("state", 1)[-1]["phase"] == "speaking"
    assert "interrupted" not in c.out.of("bot.done", 1)[0]


class GatedOutbound(RecordingOutbound):
    """오디오 프레임을 `open_frames`개까지만 바로 보내고 그다음은 문이 열릴 때까지 붙잡는다."""

    def __init__(self, open_frames: int) -> None:
        super().__init__()
        self.left = open_frames
        self.blocked = asyncio.Event()
        self.gate = asyncio.Event()

    async def send_audio(self, frame: bytes) -> None:
        if self.left == 0:
            self.blocked.set()
            await self.gate.wait()
        else:
            self.left -= 1
        await super().send_audio(frame)


def test_audio_still_queued_for_a_cut_turn_is_never_sent(tmp_path) -> None:
    # 회선이 밀려 인사 소리가 송신 큐에 쌓여 있는 동안 끊었다. 이미 소켓에 들어간 것 말고는
    # 그 턴의 오디오·자막을 보내지 않는다 (브라우저가 버리기 전에 서버가 먼저 거른다)
    async def call() -> Call:
        c = Call(tmp_path)
        c.out = out = GatedOutbound(open_frames=1)
        c.session._outbound = out  # 테스트 도구가 만든 송신 포트를 막히는 것으로 바꾼다
        async with c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await asyncio.wait_for(out.blocked.wait(), 3)  # 둘째 프레임을 보내는 중
            await asyncio.sleep(0.05)  # 인사 두 문장이 모두 큐에 들어간다
            await interrupt(c, 0, seq=0, played_ms=50)
            out.gate.set()
            await out.wait_for("bot.done", turn=0)
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert len(c.out.frames) == 2  # 보낸 것 1 + 보내던 것 1. 인사는 문장마다 3조각
    assert [e["seq"] for e in c.out.of("bot.say", 0)] == [0]
    assert c.out.of("bot.audio.end", 0) == []


def test_a_bot_turn_after_an_interrupt_gets_a_new_turn_number(tmp_path) -> None:
    # 끊은 뒤 봇이 먼저 말하는 턴(무음 되묻기 등)은 새 번호다. 브라우저가 지난 턴 소리로 안 버린다
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> tuple[Call, int | None]:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await interrupt(c, 1)
            turn = c.session.bot_turn("test", lambda engine, now: engine.start(now))
            await c.out.wait_for("bot.done", turn=2)
            return c, turn

    c, turn = run(call())
    assert turn == 2
    assert 2 in {f.turn for f in c.out.frames}
