"""음성 끼어들기 (3단계 티켓 06, `PHONE_VOICE_BARGE_IN`).

통화 세션의 바깥 행동만 본다. 손님 소리는 봇이 말하는 동안 넣는 합성 PCM(톤·무음)이다.
브라우저가 끼어들기를 할 수 있다고 알리면(`call.start.voice_barge_in`) 봇 말 중에도 진짜 마이크
소리가 올라온다.
"""

from __future__ import annotations

import asyncio
import json

from call_harness import Call, silence, tone

from agenthan.voice.call.barge import BargeSettings
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.frames import BYTES_PER_MS
from agenthan.voice.call.session import SessionSettings

# 봇 문장 하나를 4초로 둔다. 남은 봇 말이 2초보다 짧으면 소리만으로는 멈추지 않기 때문이다
LONG_MS = 4_000
LONG = bytes(LONG_MS * BYTES_PER_MS)
ON = SessionSettings(barge=BargeSettings())


def run(coro):
    return asyncio.run(coro)


def barge_call(tmp_path, replies, *, settings=ON, tts=None, **kw) -> Call:
    return Call(tmp_path, stt=FakeSttStream(replies=replies, voiced_start=True),
                tts=tts or FakeTts(pcm=LONG), settings=settings, **kw)


async def start(c: Call, *, capable: bool = True) -> None:
    await c.start(voice_barge_in=capable)


async def to_speaking(c: Call, turn: int = 1) -> None:
    """손님이 한 번 말하고 봇이 그 턴 대답을 다 보낼 때까지(아직 재생 중)."""
    await c.say()
    await c.out.wait_for("bot.done", turn=turn)


def kinds(c: Call) -> list[tuple[str, int]]:
    return [(e["type"], e["turn"]) for e in c.out.events
            if e["type"] in ("bot.duck", "bot.unduck", "bot.stop")]


def lines(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(s) for s in path.read_text().splitlines()]


def test_the_server_turns_voice_barge_in_on_only_for_a_capable_browser(tmp_path) -> None:
    async def call(capable: bool) -> dict:
        async with barge_call(tmp_path, ["메도빅 하나요"]) as c:
            await start(c, capable=capable)
            return c.out.of("call.ready")[0]

    assert run(call(True))["voice_barge_in"] == "on"
    assert "voice_barge_in" not in run(call(False))


def test_sound_while_the_bot_speaks_ducks_the_voice(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요"]) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(200))
            assert kinds(c) == []  # 0.2초는 아직
            await c.feed(tone(100))
            await c.out.wait_for("bot.duck", turn=1)
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 1)]


def test_with_the_switch_off_a_capable_browser_still_gets_half_duplex(tmp_path) -> None:
    # 꺼져 있으면 2단계·티켓 05와 똑같다: 줄이기·멈춤 없음, 봇 말 중에 한 말은 버린다
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "아니 블린 주세요"],
                              settings=SessionSettings()) as c:
            await start(c)
            await to_speaking(c)
            await c.say(speech_ms=1_500)
            await asyncio.sleep(0.05)
            await c.played(1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert "voice_barge_in" not in c.out.of("call.ready")[0]
    assert kinds(c) == []
    assert [e["text"] for e in c.out.of("stt.final")] == ["메도빅 하나요"]
    (record,) = [line for line in lines(c) if line["turn"] == 1]
    assert [d["text"] for d in record["dropped"]] == ["아니 블린 주세요"]
    assert "barge" not in record and "barge" not in record["dropped"][0]


def test_a_long_sound_stops_the_bot_and_becomes_the_next_turn(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "아니 블린 두 개 주세요"]) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(900))
            assert kinds(c) == [("bot.duck", 1)]
            await c.feed(tone(200))  # 1.0초가 넘었다
            await c.out.wait_for("bot.stop", turn=1)
            assert c.out.of("state", 1)[-1]["phase"] == "listening"
            # 브라우저가 소리를 끄고 들은 자리를 알린다 (서버 어림 1,100ms보다 이것이 정확하다)
            await c.session.feed_event({"type": "playback.interrupted", "turn": 1, "seq": 0,
                                        "played_ms": 1_050})
            await c.feed(silence(1_200))
            await c.out.wait_for("stt.final", turn=2)
            await c.out.wait_for("bot.done", turn=2)
            await c.session.feed_event({"type": "call.end", "turn": 2})
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 1), ("bot.stop", 1)]
    assert c.out.of("stt.final", 2)[0]["text"] == "아니 블린 두 개 주세요"
    record = next(line for line in lines(c) if line["turn"] == 1)
    cut = record["interrupt"]
    assert (cut["by"], cut["reason"], cut["seq"], cut["played_ms"]) == ("voice", "sound", 0, 1_050)
    assert [h["heard"] for h in cut["heard"]] == ["partial", "unheard"]
    assert "est" not in cut and cut["report_ms"] >= 0
    assert [e["act"] for e in record["barge"]["events"]] == ["duck", "stop"]


def test_a_backchannel_ducks_then_restores_the_voice_and_the_bot_goes_on(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "네"]) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(500) + silence(1_000))  # "네" 0.5초
            await c.out.wait_for("bot.unduck", turn=1)
            await c.played(1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 1), ("bot.unduck", 1)]
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]  # 맞장구는 손님 턴이 아니다
    record = next(line for line in lines(c) if line["turn"] == 1)
    assert record["dropped"][0]["barge"] == "backchannel"
    assert "interrupt" not in record


def test_a_duck_with_no_text_within_two_seconds_of_the_sound_is_restored(tmp_path) -> None:
    # 기침·잡음: 글이 안 온다(여기서는 STT가 5초 늦는다). 소리가 끝나고 2초면 되돌린다
    async def call() -> Call:
        stt = FakeSttStream(replies=["메도빅 하나요"], voiced_start=True, lag_ms=5_000)
        async with Call(tmp_path, stt=stt, tts=FakeTts(pcm=LONG), settings=ON) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0, "voice_barge_in": True})
            await c.out.wait_for("bot.done", turn=0)
            await c.played(0)
            await c.feed(tone(800) + silence(6_000))
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(tone(300) + silence(1_900))
            assert kinds(c) == [("bot.duck", 1)]
            await c.feed(silence(200))
            await c.out.wait_for("bot.unduck", turn=1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    record = next(line for line in lines(c) if line["turn"] == 1)
    assert [(e["act"], e["by"]) for e in record["barge"]["events"]] == [
        ("duck", "sound"), ("unduck", "sound")
    ]


def test_a_short_non_backchannel_word_stops_the_bot_by_its_text(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "아니요"]) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(500) + silence(1_000))  # 0.5초라 소리로는 안 멈춘다
            await c.out.wait_for("bot.stop", turn=1)
            await c.out.wait_for("stt.final", turn=2)
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 1), ("bot.stop", 1)]
    assert c.out.of("stt.final", 2)[0]["text"] == "아니요"


def test_the_bots_own_words_heard_back_are_echo_and_restore_the_voice(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "더 필요하신 거 있으세요"]) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(600) + silence(1_000))
            await c.out.wait_for("bot.unduck", turn=1)
            await c.played(1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 1), ("bot.unduck", 1)]
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]
    record = next(line for line in lines(c) if line["turn"] == 1)
    assert record["dropped"][0]["barge"] == "echo"


def test_echo_text_that_arrives_after_the_bot_finished_is_judged_by_when_it_was_said(
    tmp_path,
) -> None:
    # 에코가 봇 말 끝까지 이어졌다. 재생 끝 경계에서 epFlag로 STT 구간을 끊고, 듣는 중에 도착한
    # 그 글도 말한 시각(봇 말 중)으로 판정해 버린다. 도착 시각으로 거르면 손님 턴이 됐을 것이다
    async def call() -> tuple[Call, int]:
        async with barge_call(tmp_path, ["메도빅 하나요", "있으세요"]) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(200))  # 줄이기 전, 무음 감지는 말로 본다
            boundary = c.stt.audio_ms
            await c.played(1)
            await c.feed(silence(1_200))
            await asyncio.sleep(0.05)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c, boundary

    c, boundary = run(call())
    assert [w.at_ms for w in c.stt.writes if w.ep_flag] == [1_000, boundary]  # 손님 말, 경계
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]
    record = next(line for line in lines(c) if line["turn"] == 1)
    assert [(d["text"], d["barge"]) for d in record["dropped"]] == [("있으세요", "echo")]


def test_the_greeting_cannot_be_barged_in_by_voice(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, ["여보세요 메도빅 하나 주세요"]) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0, "voice_barge_in": True})
            await c.out.wait_for("bot.done", turn=0)
            await c.feed(tone(1_500) + silence(1_000))
            await asyncio.sleep(0.05)
            await c.played(0)
            await c.session.feed_event({"type": "call.end", "turn": 0})
            return c

    c = run(call())
    assert kinds(c) == []
    assert c.out.of("stt.final") == []
    record = next(line for line in lines(c) if line["turn"] == 0)
    assert record["barge"]["kind"] == "blocked"
    assert record["dropped"][0]["barge"] == "blocked"


READBACK = ["메도빅 하나요", "그게 다예요", "6시 반이요"]


async def to_readback(c: Call) -> None:
    await start(c)
    await c.turn(1)
    await c.turn(2)
    await to_speaking(c, 3)  # 최종 복창: "…총 7,500원이에요." "맞으세요?"


def test_a_yes_before_the_amount_ends_is_ignored_in_the_readback(tmp_path) -> None:
    # 1단계 불변식: 복창(총액)을 다 듣지 않고 "네"로 확정되는 길이 없다
    async def call() -> Call:
        async with barge_call(tmp_path, [*READBACK, "네"]) as c:
            await to_readback(c)
            await c.feed(tone(500) + silence(1_200))  # 금액 문장 도중의 "네"
            await c.out.wait_for("bot.unduck", turn=3)
            await c.played(3)
            await c.session.feed_event({"type": "call.end", "turn": 3})
            return c

    c = run(call())
    assert c.out.of("order") == []
    assert c.out.of("bot.stop") == []
    record = next(line for line in lines(c) if line["turn"] == 3)
    assert record["barge"]["kind"] == "readback"
    assert record["dropped"][0]["barge"] == "early_yes"


def test_a_yes_said_after_the_amount_sentence_confirms(tmp_path) -> None:
    # 금액 문장(4초)을 다 들은 뒤 "맞으세요?" 도중의 "네"는 답이다
    async def call() -> Call:
        async with barge_call(tmp_path, [*READBACK, "네"]) as c:
            await to_readback(c)
            await c.feed(silence(LONG_MS + 200))
            await c.feed(tone(500) + silence(1_200))
            await c.out.wait_for("order", turn=4)
            return c

    c = run(call())
    assert kinds(c)[-1] == ("bot.stop", 3)
    assert c.out.of("order")[0]["order"]["status"] == "confirmed"


def test_a_correction_during_the_readback_stops_it_and_fixes_the_order(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, [*READBACK, "메도빅 두 개로 바꿔 주세요"]) as c:
            await to_readback(c)
            await c.feed(tone(800) + silence(1_200))
            await c.out.wait_for("bot.done", turn=4)
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 3), ("bot.stop", 3)]
    assert c.out.of("order") == []
    assert "15,000원" in " ".join(e["text"] for e in c.out.of("bot.say", 4))


def test_the_readback_is_not_stopped_by_sound_alone(tmp_path) -> None:
    async def call() -> Call:
        async with barge_call(tmp_path, [*READBACK]) as c:
            await to_readback(c)
            await c.feed(tone(1_500))
            await asyncio.sleep(0.05)
            await c.session.feed_event({"type": "call.end", "turn": 3})
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 3)]


def test_sound_near_the_end_of_the_bots_words_does_not_stop_it(tmp_path) -> None:
    # 남은 봇 말이 2초보다 짧으면 끝까지 말한다(Cao 2025). 짧은 기본 TTS(문장 0.41초)
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요"], tts=FakeTts()) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(1_500))
            await asyncio.sleep(0.05)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 1)]


def test_each_false_barge_in_raises_the_stop_threshold_for_the_call(tmp_path) -> None:
    # 턴 1에서 맞장구로 헛 끼어들기 한 번 → 멈춤 문턱 1.0 → 1.25초. 턴 2에서 1.1초는 안 멈춘다
    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "네", "그게 다예요"]) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(500) + silence(1_000))
            await c.out.wait_for("bot.unduck", turn=1)
            await c.played(1)
            await to_speaking(c, 2)
            await c.feed(tone(1_100))
            assert kinds(c)[-1] == ("bot.duck", 2)
            await c.feed(tone(200))
            await c.out.wait_for("bot.stop", turn=2)
            await c.session.feed_event({"type": "call.end", "turn": 2})
            return c

    c = run(call())
    assert kinds(c) == [("bot.duck", 1), ("bot.unduck", 1), ("bot.duck", 2), ("bot.stop", 2)]
    records = {line["turn"]: line for line in lines(c)}
    assert records[1]["barge"]["stop_ms"] == 1_000
    assert records[2]["barge"]["stop_ms"] == 1_250


def test_shadow_mode_records_decisions_but_keeps_half_duplex(tmp_path) -> None:
    shadow = SessionSettings(barge=BargeSettings(mode="shadow"))

    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "아니 블린 주세요"],
                              settings=shadow) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(1_100) + silence(1_200))
            await asyncio.sleep(0.05)
            await c.played(1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert c.out.of("call.ready")[0]["voice_barge_in"] == "shadow"
    assert kinds(c) == []
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]
    record = next(line for line in lines(c) if line["turn"] == 1)
    assert [(e["act"], e["shadow"]) for e in record["barge"]["events"]] == [
        ("duck", True), ("stop", True)
    ]
    assert record["barge"]["rms"]["frames"] == 23
    assert record["dropped"][0]["barge"] == "accept"


def test_customer_speech_after_a_voice_stop_holds_the_no_input_timer(tmp_path) -> None:
    # 음성으로 끼어든 말은 "말 시작"이다. 끊긴 때부터 무음 타이머가 돌아도 말하는 동안은 안 되묻는다
    quick = SessionSettings(barge=BargeSettings(), no_input_s=0.2, no_input_slack_s=5.0)

    async def call() -> Call:
        async with barge_call(tmp_path, ["메도빅 하나요", "블린도 두 개 주세요"],
                              settings=quick) as c:
            await start(c)
            await to_speaking(c)
            await c.feed(tone(1_100))
            await c.out.wait_for("bot.stop", turn=1)
            await asyncio.sleep(0.5)  # 손님은 아직 말하는 중
            await c.feed(tone(300) + silence(1_200))
            await c.out.wait_for("stt.final", turn=2)
            return c

    c = run(call())
    # 되묻기 봇 턴이 끼었다면 턴 2는 봇 턴이고 손님 말은 턴 3이 됐을 것이다
    assert c.out.of("stt.final", 2)[0]["text"] == "블린도 두 개 주세요"
