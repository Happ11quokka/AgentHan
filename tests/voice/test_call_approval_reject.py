"""통화 세션 → 사장님 거절 (주문 승인 티켓 05, spec "세션", 이음매 2).

사장님이 거절하면 세션은 수락과 같은 봇 턴으로 엔진에 넣는다. 손님은 사유별 사과를 듣고 통화는
취소(cancelled)로 끝난다. 주문서는 넘어가지 않는다. 손님 화면 상태는 rejected + 사유. 거절이 와
있었지만 말하기 전에 손님이 끊었어도 주문서는 넘기지 않는다.
"""

from __future__ import annotations

import asyncio

from call_harness import WAIT, Call, tone
from test_call_approval import (
    HOLD,
    SCRIPT,
    approval_steps,
    bot_turn,
    records,
    run,
    said,
    to_yes,
    view,
    waiting,
)

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel

BUSY = "죄송해요, 지금 가게가 바빠서 주문을 받기 어렵대요. 다음에 다시 주문해 주세요."
FREE = "사장님이 사정을 문자로 남기셨어요. 죄송해요."


def test_a_busy_rejection_is_apologised_for_and_the_call_ends_cancelled(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            owner.reject(1, "busy")
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 4) == HOLD
    assert said(c, 5) == BUSY
    assert c.sink.orders == []  # 주문서는 넘어가지 않는다
    assert c.out.of("order") == []
    assert owner.ended == ["cancelled"]
    assert c.out.of("call.ended")[0]["reason"] == "cancelled"
    decision = records(c)[5]["approval_decision"]
    assert (decision["kind"], decision["reason"]) == ("reject", "busy")
    assert approval_steps(c)[-1] == (5, view("rejected", 1, reason="busy"))


def test_a_written_rejection_is_not_read_out(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            owner.reject(1, "free")
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == FREE  # 사장님 글은 문자로만 (허브 몫)
    assert owner.ended == ["cancelled"]


def test_a_rejection_not_yet_said_when_the_customer_hangs_up_hands_nothing_over(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await c.feed(tone(500))  # 손님이 말하는 중: 거절 대사를 미룬다
            owner.reject(1, "closed")
            await asyncio.sleep(0.2)
            assert c.out.of("bot.say", 5) == []
            await c.session.feed_event({"type": "call.end", "turn": 4})
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert owner.ended == ["hangup"]
    assert c.sink.orders == []  # 거절된 주문서는 통화 뒤 대기로 넘기지 않는다
