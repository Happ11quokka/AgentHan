"""3단계 리뷰 고침의 회귀 테스트 (`phone-s3-turn-management/review.md`).

통화 세션의 바깥 행동만 본다. 가짜 STT·TTS·시계와 합성 PCM으로 결정적으로 만든다.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from call_harness import Call, silence, tone

from agenthan.voice.call.barge import BargeSettings
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.session import SessionSettings

READBACK = ["메도빅 하나요", "그게 다예요", "6시 반이요"]


def run(coro):
    return asyncio.run(coro)


def lines(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(s) for s in path.read_text().splitlines()]


def slow_readback(text: str) -> float:
    """최종 복창의 금액 문장 합성을 늦춘다. 그 사이(첫 소리 전)에 손님이 말한다."""
    return 0.4 if "총" in text else 0.0


# ---------------------------------------------------------------- P1


@pytest.mark.parametrize("barge", [False, True], ids=["half_duplex", "voice_barge_in"])
@pytest.mark.parametrize("early", ["네", "응"])
def test_a_yes_said_while_the_readback_is_being_synthesized_does_not_confirm(
    tmp_path, barge, early
) -> None:
    # 픽업 시각 뒤 "네… 네"의 두 번째 "네"가 복창 첫 소리 전에 나왔다. 그 말은 `_pending`에
    # 들어가 복창 재생이 끝난 뒤에 판정된다. 금액을 듣기 전에 한 말이라 확정이 아니다(Q10)
    settings = SessionSettings(barge=BargeSettings()) if barge else SessionSettings()
    stt = FakeSttStream(replies=[*READBACK, early, "네"], voiced_start=True)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=FakeTts(delays=slow_readback),
                        settings=settings) as c:
            await c.start(voice_barge_in=barge)
            await c.turn(1)
            await c.turn(2)
            await c.say()
            await c.out.wait_for("stt.final", turn=3)
            await c.say()  # 복창 첫 문장을 합성하는 중
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await asyncio.sleep(0.05)
            assert c.out.of("order") == [], "금액 전에 한 말로 확정되면 안 된다"
            assert c.out.of("stt.final", 4) == []
            await c.turn(4)  # 복창을 다 듣고 한 "네"는 확정이다
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    c = run(call())
    (order,) = c.out.of("order")
    assert order["turn"] == 4 and order["order"]["status"] == "confirmed"
    record = next(line for line in lines(c) if line["turn"] == 3)
    assert [(d["text"], d["reason"]) for d in record["dropped"]] == [(early, "early_yes")]


def test_a_correction_said_while_the_readback_is_being_synthesized_is_kept(tmp_path) -> None:
    stt = FakeSttStream(replies=[*READBACK, "메도빅 두 개로 바꿔 주세요"], voiced_start=True)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=FakeTts(delays=slow_readback)) as c:
            await c.start()
            await c.turn(1)
            await c.turn(2)
            await c.say()
            await c.out.wait_for("stt.final", turn=3)
            await c.say()
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await c.out.wait_for("bot.done", turn=4)
            return c

    c = run(call())
    assert c.out.of("order") == []
    assert "15,000원" in " ".join(e["text"] for e in c.out.of("bot.say", 4))


# ---------------------------------------------------------------- C1


def test_the_end_button_during_the_wait_keeps_the_words_whose_text_is_still_coming(
    tmp_path,
) -> None:
    # "메도빅 두 개랑"으로 이어짐 대기 중 손님이 "아메리카노 하나요"를 말하고 "말 끝"을 눌렀다.
    # 그 말의 epFlag 응답(글)은 아직 오지 않았다. 버튼이 기다리던 말만 넘기면 뒷말이 다음 턴이
    # 되어 봇이 두 번 답한다
    stt = FakeSttStream(replies=["메도빅 두 개랑", "아메리카노 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=SessionSettings(cont_wait_s=2.0)) as c:
            await c.start()
            await c.say()
            await asyncio.sleep(0.05)
            assert c.out.of("stt.final") == []  # 이어짐 대기 중
            await c.feed(tone(800) + silence(500))  # epFlag는 갔고 응답은 600ms 뒤(오디오 시각)
            await c.session.feed_event({"type": "user.end_of_speech", "turn": 0})
            await c.feed(silence(800))
            await c.out.wait_for("bot.done", turn=1)
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert [e["text"] for e in c.out.of("stt.final")] == ["메도빅 두 개랑 아메리카노 하나요"]


# ---------------------------------------------------------------- C2


def test_how_much_was_heard_stays_on_a_cut_turn_even_when_a_queued_word_opens_the_next(
    tmp_path,
) -> None:
    # 첫 소리 전에 한 말이 대기 중이면 끊는 순간 다음 턴이 열린다. 그래도 그 뒤에 온
    # `playback.interrupted`의 들은 만큼은 끊긴 턴 기록에 남는다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    tts = FakeTts(delays={"메도빅 하나요.": 0.4, "더 필요하신 거 있으세요?": 5.0})

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await c.say()  # 첫 문장 합성 중
            await c.out.wait_for("bot.say", turn=1)
            await c.session.feed_event({"type": "user.interrupt", "turn": 1})
            await c.out.wait_for("stt.final", turn=2)
            await c.session.feed_event({"type": "playback.interrupted", "turn": 1, "seq": 0,
                                        "played_ms": 200})
            await c.out.wait_for("bot.done", turn=2)
            await c.session.feed_event({"type": "call.end", "turn": 2})
            return c

    c = run(call())
    record = next(line for line in lines(c) if line["turn"] == 1)
    assert record["interrupt"]["seq"] == 0 and record["interrupt"]["played_ms"] == 200
    assert record["interrupt"]["heard"][0]["heard"] == "partial"


# ---------------------------------------------------------------- C6


def test_a_confirm_turn_without_the_readback_can_be_interrupted(tmp_path) -> None:
    # 복창 중 "잠깐만요" → "네, 천천히 말씀하세요"는 CONFIRM 상태지만 복창이 아니다
    stt = FakeSttStream(replies=[*READBACK, "잠깐만요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            return c

    c = run(call())
    speaking = {e["turn"]: e["interruptible"] for e in c.out.of("state")
                if e["phase"] == "speaking"}
    assert speaking[3] is False  # 최종 복창
    assert speaking[4] is True
    assert [e["text"] for e in c.out.of("bot.say", 4)] == ["네, 천천히 말씀하세요."]
