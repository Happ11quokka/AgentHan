"""확인 요청을 기다리는 채 끝나는 통화 (주문 승인 티켓 03, spec "세션", 이음매 2).

통화가 어떤 이유로 끝나든(손님 끊음, 오류·통화 길이 초과, 시간 초과 문자 약속) 결정을 기다리던
요청의 주문서는 넘어가고, 사장님 쪽은 통화 끝 알림 하나로 통화 뒤 대기를 연다(요청은 거두지 않는다).
손님이 기다리는 중에 취소했으면 요청은 취소로 닫혀 주문서가 넘어가지 않는다.
"""

from __future__ import annotations

import asyncio

from call_harness import WAIT, Call

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.call.session import SessionSettings

SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]
HOLD = "사장님께 주문 확인 중이에요. 잠시만요."


def run(coro):
    return asyncio.run(coro)


def said(c: Call, turn: int) -> str:
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


async def to_yes(c: Call) -> None:
    """인사 → 메도빅 → 그게 다예요 → 6시 반 → "네"("확인 중"까지 재생)."""
    await c.start()
    for turn in (1, 2, 3, 4):
        await c.turn(turn)


def test_a_customer_who_hangs_up_while_waiting_leaves_the_order_with_the_owner(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=SessionSettings(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await c.session.feed_event({"type": "call.end", "turn": 4})
            owner.decide(1, "accept")  # 통화 뒤 결정: 세션은 버린다(문자는 허브 몫)
            await asyncio.sleep(0.1)
            return c

    c = run(call())  # 하네스가 나가며 `close()`로 한 번 더 끝내도 주문서·알림은 한 번
    assert said(c, 4) == HOLD
    assert owner.ended == ["hangup"]
    assert owner.withdrawn == []  # 거두지 않는다: 허브가 통화 뒤 대기로 넘긴다
    (order,) = c.sink.orders  # 요청에 실린 주문서가 넘어간다 (시간 초과 문자 약속과 같다)
    assert order == owner.approvals[0].order
    assert order.status == "confirmed"
    assert c.out.of("bot.say", 5) == []


def test_a_call_cut_off_by_an_error_while_waiting_also_hands_the_order_over(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        settings = SessionSettings(approval_wait_s=5.0, max_call_s=2.5)
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=settings) as c:
            await to_yes(c)
            await asyncio.wait_for(c.session.wait_done(), WAIT)  # 통화 길이 초과
            return c

    c = run(call())
    assert owner.ended == ["error"]
    assert owner.withdrawn == []
    assert [o.id for o in c.sink.orders] == [owner.approvals[0].order.id]


def test_a_request_cancelled_while_waiting_hands_nothing_over(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "취소할게요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=SessionSettings(approval_wait_s=5.0)) as c:
            await to_yes(c)
            for turn in (5, 6):  # "주문 취소할까요?" → "네"
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    # 묻는 사이 changed로 거두고 확정되면 cancelled로 한 번 더 (티켓 07)
    assert owner.withdrawn == [(1, "changed"), (1, "cancelled")]
    assert c.sink.orders == []
    assert owner.ended == ["cancelled"]


def test_a_timeout_hands_the_order_over_once(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=SessionSettings(approval_wait_s=0.5)) as c:
            await to_yes(c)
            await c.out.wait_for("bot.done", turn=5)
            await c.played(5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5).startswith("가게에서 아직 확인 중이에요.")
    assert [o.status for o in c.sink.orders] == ["confirmed"]  # 문자 약속 턴의 주문서 하나뿐
    assert owner.ended == ["confirmed"]
