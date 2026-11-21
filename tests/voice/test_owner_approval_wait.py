"""사장님 허브: 기다리는 중에 손님이 취소한 확인 요청 (주문 승인 티켓 07, 이음매 3).

손님이 "취소할게요"라고 하면 통화가 "주문 취소할까요?"를 물으며 요청을 거두고(고치는 중), 손님이
취소를 확정하면 같은 번호를 취소로 한 번 더 알린다. 카드는 취소 표시가 되고 결정을 받지 않으며,
통화가 끝나도 통화 뒤 대기로 넘어가지 않는다. 손님 문자도 없다.
"""

from __future__ import annotations

import asyncio

import pytest
from test_owner_approval import Screen, _hub, _order

from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner.hub import CardBusy


def test_a_request_cancelled_while_waiting_ends_as_a_cancelled_card() -> None:
    decisions: list[OwnerDecision] = []

    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        screen = Screen(hub)
        await screen.take()  # snapshot (통화 카드 포함)
        hub.open_approval("call-1", ApprovalRequest(1, _order()), decisions.append)
        await screen.take()
        hub.withdraw_approval("call-1", 1, "changed")  # "주문 취소할까요?"
        (changing,) = await screen.take()
        hub.withdraw_approval("call-1", 1, "cancelled")  # "네"
        (cancelled,) = await screen.take()
        with pytest.raises(CardBusy):
            hub.accept("call-1")
        hub.call_ended("call-1", "cancelled")
        ended = await screen.take()
        await screen.nothing_more()
        await screen.close()
        return changing.data, cancelled.data, ended, hub.customer_texts("call-1")

    changing, cancelled, ended, texts = asyncio.run(scenario())
    assert (changing["status"], changing["withdrawn"]) == ("new", "changed")
    assert (cancelled["status"], cancelled["withdrawn"], cancelled["stage"]) == (
        "new", "cancelled", "in_call")
    # 주문 카드는 통화 뒤 대기가 되지 않는다(통화 카드만 바뀐다)
    assert [(e.name, e.data["status"]) for e in ended] == [("call.update", "missed")]
    assert decisions == [] and texts == []
