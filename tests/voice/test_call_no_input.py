"""무응답·되묻기·"잠깐만요" (3단계 티켓 04). 통화 세션의 바깥 행동만 본다.

- 봇 재생 끝(`playback.done`·말 끊기)부터 `no_input_s` 동안 말이 없으면 봇이 먼저 되묻는다
  (새 턴 번호). 세 번째 무응답이면 마지막 인사 뒤 `call.ended {reason: no_input}`로 끝난다
- VAD 말 시작이면 타이머가 멈추고 그 말 끝부터 다시 센다. 받아들인 말은 횟수를 0으로
- "잠깐만요" 뒤 첫 대기는 `wait_request_s`
- 말소리 `not_heard_min_ms` 이상 + 빈 epFlag 응답 → "잘 못 들었어요". 합성 `eof`·짧은 소리는 빼고
"""

from __future__ import annotations

import asyncio
import json
import time

from call_harness import WAIT, Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream
from agenthan.voice.call.ports import SttResult
from agenthan.voice.call.session import SessionSettings

QUIET = 0.3  # 시험용 무응답 대기(초). 기본값은 6.0
HOLD_ON = 0.8  # 시험용 "잠깐만요" 뒤 대기. 기본값은 15.0
HELLO = "여보세요?"
NOT_HEARD = "죄송해요, 잘 못 들었어요. 다시 말씀해 주세요."
GOODBYE = "연결이 원활하지 않아 전화를 끊을게요. 다시 걸어 주세요."


def run(coro):
    return asyncio.run(coro)


def quiet(**kw) -> SessionSettings:
    return SessionSettings(**{"no_input_s": QUIET, "wait_request_s": HOLD_ON, **kw})


def records(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def said(c: Call, turn: int) -> str:
    """그 턴에 봇이 한 말 (TTS 문장으로 나뉜 것을 다시 이은 것)."""
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


async def bot_turn(c: Call, turn: int) -> float:
    """봇이 그 턴을 다 말하고 손님이 다 들었다. 첫 `bot.say`가 온 때를 돌려준다."""
    await c.out.wait_for("bot.say", turn=turn)
    at = time.monotonic()
    await c.out.wait_for("bot.done", turn=turn)
    await c.played(turn)
    return at


def test_silence_reprompts_twice_then_says_goodbye_and_ends_the_call(tmp_path) -> None:
    async def call() -> tuple[Call, float, float]:
        async with Call(tmp_path, settings=quiet()) as c:
            await c.start()
            heard = time.monotonic()
            first = await bot_turn(c, 1)
            await bot_turn(c, 2)
            await bot_turn(c, 3)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c, heard, first

    c, heard, first = run(call())
    assert first - heard >= QUIET - 0.05
    assert said(c, 1) == f"{HELLO} 어떤 걸로 드릴까요?"
    assert said(c, 2) == f"{HELLO} 어떤 걸로 드릴까요?"
    assert said(c, 3) == GOODBYE
    assert c.out.of("call.ended") == [
        {"type": "call.ended", "turn": 3, "reason": "no_input", "sms": []}]
    lines = records(c)
    assert [line.get("bot_turn") for line in lines] == [None, "no_input", "no_input", "no_input"]
    assert lines[3]["end_reason"] == "no_input"
    assert c.sink.orders == []


def test_speech_stops_the_timer_and_it_restarts_from_the_end_of_that_speech(tmp_path) -> None:
    # 짧은 소리(300ms, 글 없음)는 되묻기 대상이 아니다. 그 소리 끝부터 다시 센다
    async def call() -> tuple[Call, float, float]:
        async with Call(tmp_path, settings=quiet()) as c:
            await c.start()
            await asyncio.sleep(QUIET * 0.6)
            await c.feed(tone(300) + silence(1_200))  # 빈 epFlag 응답까지
            spoke = time.monotonic()
            first = await bot_turn(c, 1)
            return c, spoke, first

    c, spoke, first = run(call())
    assert first - spoke >= QUIET - 0.05
    assert said(c, 1) == f"{HELLO} 어떤 걸로 드릴까요?"


def test_accepted_speech_resets_the_count(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=quiet()) as c:
            await c.start()
            await bot_turn(c, 1)  # 무응답 1
            await c.turn(2)  # 받아들인 말: 횟수 0
            await bot_turn(c, 3)  # 무응답 1
            await bot_turn(c, 4)  # 무응답 2: 아직 끝내지 않는다
            await c.session.feed_event({"type": "call.end", "turn": 4})
            return c

    c = run(call())
    assert said(c, 3) == f"{HELLO} 더 필요하신 거 있으세요?"
    assert said(c, 4) == f"{HELLO} 더 필요하신 거 있으세요?"
    assert c.out.of("call.ended") == []


def test_a_wait_request_answers_and_waits_longer_before_the_first_reprompt(tmp_path) -> None:
    stt = FakeSttStream(replies=["잠깐만요"])

    async def call() -> tuple[Call, float, float, float]:
        async with Call(tmp_path, stt=stt, settings=quiet()) as c:
            await c.start()
            await c.turn(1)
            heard = time.monotonic()
            second = await bot_turn(c, 2)
            heard2 = time.monotonic()
            third = await bot_turn(c, 3)
            return c, heard, second, third - heard2

    c, heard, second, third_gap = run(call())
    assert said(c, 1) == "네, 천천히 말씀하세요."
    assert second - heard >= HOLD_ON - 0.05
    assert third_gap < HOLD_ON - 0.2  # 그다음은 평소 대기
    assert said(c, 2) == f"{HELLO} 어떤 걸로 드릴까요?"
    turn1 = records(c)[1]
    assert turn1["turn_end"]["label"] == "wait_request"
    assert "nlu_prefetch" not in turn1 or turn1["nlu_prefetch"]["prefetched"] == 0


def test_speech_with_no_text_asks_to_say_it_again(tmp_path) -> None:
    stt = FakeSttStream(replies=[""])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say(speech_ms=800)
            await bot_turn(c, 1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert said(c, 1) == NOT_HEARD
    lines = records(c)
    assert lines[1]["bot_turn"] == "not_heard"
    (empty,) = lines[0]["empty"]
    assert empty["voiced_ms"] == 800


def test_short_sounds_and_the_adapter_eof_do_not_ask_to_say_it_again(tmp_path) -> None:
    # 300ms 기침은 빈 epFlag 응답이 와도 되묻지 않는다. 재연결 때 어댑터가 만든 빈 결과(eof)는
    # 말소리가 길었어도 되묻지 않는다
    stt = FakeSttStream(replies=[""], lag_ms=600)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say(speech_ms=300)
            await c.feed(tone(800) + silence(300))  # epFlag는 썼지만 응답이 아직 없다
            stt.push(SttResult("", 0, stt.audio_ms, "eof", True))
            await asyncio.sleep(0.3)
            await c.session.feed_event({"type": "call.end", "turn": 0})
            return c

    c = run(call())
    assert c.out.of("bot.say", 1) == []


def test_the_timer_starts_when_the_guest_cuts_the_bot_off(tmp_path) -> None:
    async def call() -> tuple[Call, float, float]:
        async with Call(tmp_path, settings=quiet()) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("bot.done", turn=0)
            cut = time.monotonic()
            await c.session.feed_event({"type": "user.interrupt", "turn": 0})
            await c.session.feed_event({"type": "playback.interrupted", "turn": 0, "seq": 0,
                                        "played_ms": 200})
            first = await bot_turn(c, 1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c, cut, first

    c, cut, first = run(call())
    assert first - cut >= QUIET - 0.05
    assert said(c, 1) == f"{HELLO} 어떤 걸로 드릴까요?"


def test_a_confirmed_order_ends_the_call_with_the_confirmed_reason(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요", "6시 반이요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert c.out.of("call.ended") == [
        {"type": "call.ended", "turn": 4, "reason": "confirmed", "sms": []}]
    assert records(c)[4]["end_reason"] == "confirmed"
