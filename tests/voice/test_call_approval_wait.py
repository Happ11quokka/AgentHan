"""통화 세션의 확인 대기 경험 (주문 승인 티켓 07, spec "세션", 이음매 2).

기다리는 동안: 듣는 중이 될 때마다 대기음 신호(`bot.hold`), 카드를 보낸 뒤 재안내 시간에
"조금만 더 기다려 주세요." 한 번, 무응답 되묻기 없음. 손님 말은 엔진이 받는다(그 밖의 말은
"확인 중이에요", 취소는 요청을 닫고 카드를 취소로). 타이머는 루프 시계라 짧은 실제 초로 본다.
"""

from __future__ import annotations

import asyncio
import json
import time

from call_harness import WAIT, Call, tone

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.call.session import SessionSettings

SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]
HOLD = "사장님께 주문 확인 중이에요. 잠시만요."
NUDGE = "조금만 더 기다려 주세요."
CHECKING = "확인 중이에요, 잠시만요."
CLOSING = "네, 6시 반까지 준비해 둘게요. 감사합니다."
LATER = ("가게에서 아직 확인 중이에요. 확인되는 대로 문자로 확정 알려 드릴게요. "
         "확정 문자를 받으시면 주문 완료예요. 감사합니다.")
QUIET = 0.3  # 시험용 무응답 대기. 멈추지 않았으면 "여보세요?"가 먼저 나간다
APPROVAL_WAIT = 1.6  # 시험용 통화 중 대기 (기본 20초)
REPROMPT = 0.6  # 시험용 재안내 (기본 10초)


def run(coro):
    return asyncio.run(coro)


def said(c: Call, turn: int) -> str:
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


def waiting(**kw) -> SessionSettings:
    return SessionSettings(**{"no_input_s": QUIET, "approval_wait_s": APPROVAL_WAIT,
                              "approval_reprompt_s": REPROMPT, **kw})


def records(c: Call) -> dict[int, dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return {line["turn"]: line for line in lines}


async def bot_turn(c: Call, turn: int) -> None:
    await c.out.wait_for("bot.done", turn=turn)
    await c.played(turn)


async def to_yes(c: Call) -> float:
    """인사 → 메도빅 → 그게 다예요 → 6시 반 → "네"("확인 중" 재생 끝까지). 요청을 연 때."""
    await c.start()
    for turn in (1, 2, 3):
        await c.turn(turn)
    await c.say()
    await c.out.wait_for("bot.say", turn=4)
    opened = time.monotonic()
    await bot_turn(c, 4)
    return opened


def test_the_wait_plays_the_hold_cue_nudges_once_and_then_promises_a_text(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> tuple[Call, float, float]:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting()) as c:
            opened = await to_yes(c)
            await c.out.wait_for("bot.say", turn=5)
            nudged = time.monotonic()
            await bot_turn(c, 5)
            await bot_turn(c, 6)  # 통화 중 대기 초과: 문자 약속
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c, opened, nudged

    c, opened, nudged = run(call())
    assert said(c, 4) == HOLD
    assert said(c, 5) == NUDGE
    assert REPROMPT - 0.05 <= nudged - opened < APPROVAL_WAIT
    assert said(c, 6) == LATER
    assert c.out.of("bot.say", 7) == []  # 재안내는 한 번, 무응답 되묻기 없음
    # 대기음: 확인 중 대사·재안내 재생이 끝나 듣는 중이 될 때. 마지막 대사 뒤에는 없다
    holds = c.out.of("bot.hold")
    assert [e["turn"] for e in holds] == [4, 5]
    assert 0 < holds[1]["max_s"] < holds[0]["max_s"] <= APPROVAL_WAIT + 5
    turns = records(c)
    assert turns[5]["bot_turn"] == "approval_nudge"
    assert turns[5]["approval_nudge"]["request"] == 1
    assert turns[5]["approval_nudge"]["after_ms"] >= REPROMPT * 1000 - 50


def test_no_nudge_when_switched_off_or_the_owner_decides_first(tmp_path) -> None:
    for settings, decide in ((waiting(approval_reprompt_s=0), False), (waiting(), True)):
        owner = RecordingOwnerChannel(approval="hold")

        async def call(settings=settings, decide=decide, owner=owner) -> Call:
            async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                            settings=settings) as c:
                await to_yes(c)
                if decide:
                    owner.decide(1, "accept")
                await bot_turn(c, 5)
                await asyncio.wait_for(c.session.wait_done(), WAIT)
                await asyncio.sleep(REPROMPT)
                return c

        c = run(call())
        assert said(c, 5) == (CLOSING if decide else LATER)
        assert c.out.of("bot.say", 6) == []


def test_the_nudge_waits_while_the_customer_is_speaking(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "메도빅 달아요?"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=waiting(approval_wait_s=3.0)) as c:
            await to_yes(c)
            await c.feed(tone(900))  # 재안내 때를 넘겨 말하는 중
            assert c.out.of("bot.say", 5) == []  # 손님 말을 자르지 않는다
            await c.turn(5)  # 그 말이 턴이 된다
            await bot_turn(c, 6)  # 그 턴 뒤에 재안내
            owner.decide(1, "accept")
            await bot_turn(c, 7)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == CHECKING
    assert said(c, 6) == NUDGE
    assert said(c, 7) == CLOSING
    assert [e["turn"] for e in c.out.of("bot.hold")] == [4, 5, 6]


def test_other_speech_while_waiting_keeps_the_wait_and_no_hello(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "뭐라고요?"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=waiting(approval_wait_s=5.0, approval_reprompt_s=0)) as c:
            await to_yes(c)
            await c.turn(5)
            await asyncio.sleep(QUIET * 2)  # 그 턴 뒤에도 무응답 되묻기 없음
            assert c.out.of("bot.say", 6) == []
            owner.decide(1, "accept")
            await bot_turn(c, 6)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == CHECKING
    assert said(c, 6) == CLOSING
    assert owner.withdrawn == []


def test_cancelling_while_waiting_closes_the_request_as_cancelled(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "취소할게요", "네"])

    async def call() -> Call:
        # 거둔 뒤에는 대기가 아니라 무응답 되묻기가 돌아온다. 재안내 때보다 길게 둔다
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=waiting(no_input_s=REPROMPT * 3)) as c:
            await to_yes(c)
            await c.turn(5)  # "주문 취소할까요?": 요청을 거둔다
            owner.decide(1, "accept")  # 거둔 요청의 결정은 버린다
            await asyncio.sleep(REPROMPT)  # 거둔 요청에는 재안내도 없다
            assert c.out.of("bot.say", 6) == []
            await c.turn(6)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == "주문 취소할까요?"
    assert said(c, 6) == "네, 주문 취소했어요. 감사합니다."
    assert owner.withdrawn == [(1, "changed"), (1, "cancelled")]
    assert c.sink.orders == []
    assert owner.ended == ["cancelled"]
    assert [e["turn"] for e in c.out.of("bot.hold")] == [4]  # 거둔 뒤에는 대기음이 없다
    assert records(c)[6]["approval_withdrawn"] == {"number": 1, "reason": "cancelled"}


def test_no_nudge_when_it_would_not_come_before_the_timeout(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting(approval_reprompt_s=APPROVAL_WAIT)) as c:
            await to_yes(c)
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == LATER
    assert c.out.of("bot.say", 6) == []


def test_a_nudge_held_back_past_the_timeout_or_a_decision_is_never_said(tmp_path) -> None:
    """재안내 때에 손님이 말하는 중이라 미뤘는데 그 사이 시간 초과·결정이 왔다: 재안내는 없고
    결정이 먼저다. 결정 대사 뒤에도 재안내는 나오지 않는다(두 타이머가 다투지 않는다)."""
    for decide in (False, True):
        owner = RecordingOwnerChannel(approval="hold")
        stt = FakeSttStream(replies=[*SCRIPT, "메도빅 달아요?"])

        async def call(decide=decide, owner=owner, stt=stt) -> Call:
            async with Call(tmp_path, stt=stt, owner=owner, settings=waiting()) as c:
                await to_yes(c)
                await c.feed(tone(900))  # 재안내 때를 넘겨 말하는 중
                if decide:
                    await asyncio.sleep(REPROMPT)
                    owner.decide(1, "accept")
                else:
                    await asyncio.sleep(APPROVAL_WAIT)  # 시간 초과도 넘긴다
                await c.turn(5)  # 그 말이 턴이 된다
                await bot_turn(c, 6)
                await asyncio.wait_for(c.session.wait_done(), WAIT)
                await asyncio.sleep(REPROMPT)
                return c

        c = run(call())
        assert said(c, 5) == CHECKING
        assert said(c, 6) == (CLOSING if decide else LATER)
        assert c.out.of("bot.say", 7) == []
        assert all(r.get("bot_turn") != "approval_nudge" for r in records(c).values())
