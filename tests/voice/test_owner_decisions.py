"""사장님 결정 기록 (주문 승인 티켓 10): 허브가 결정을 `DecisionSink`로 넘긴다. DB 없이 본다.

DB 쓰기 자체는 `tests/test_db.py`(실 DB가 있을 때만)에서 본다.
"""

from __future__ import annotations

from datetime import datetime

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest
from agenthan.voice.owner.decisions import DecisionRecord, decision_of
from agenthan.voice.owner.hub import OwnerHub

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
LATER = datetime.fromisoformat("2026-11-08T16:02:09+09:00")
MEDOVIK = OrderLine("m01", 2, name="메도빅", unit_price=7_500)


class Recorded:
    """받은 결정을 쌓아 두는 가짜 받는 곳."""

    def __init__(self) -> None:
        self.decisions: list[DecisionRecord] = []

    def record(self, decision: DecisionRecord) -> None:
        self.decisions.append(decision)


class Broken:
    def record(self, decision: DecisionRecord) -> None:
        raise RuntimeError("DB 없음")


def _hub(sink, clock: FakeClock | None = None) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock or FakeClock(NOW),
                    approval_wait_s=20.0, approval_expire_s=300.0, decision_sink=sink)


def _order(call_id: str = "call-1") -> Order:
    return Order(id=call_id, lines=[MEDOVIK], pickup_time="2026-11-08T18:30:00+09:00",
                 status="confirmed", created_at="2026-11-08T16:02:00+09:00")


def _fields(record: DecisionRecord) -> tuple[str, str, str, str, str]:
    return (record.order_id, record.owner_status, record.reject_reason, record.decided_at,
            record.owner_pickup_time)


def test_an_in_call_accept_is_recorded_once_with_the_order() -> None:
    sink, clock = Recorded(), FakeClock(NOW)
    hub = _hub(sink, clock)
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    assert sink.decisions == []  # 확인 대기는 결정이 아니다
    clock.set(LATER)
    hub.accept("call-1")
    hub.submit(_order())  # 통화 끝 주문서·통화 끝: 결정은 그대로라 다시 보내지 않는다
    hub.call_ended("call-1", "confirmed")
    (record,) = sink.decisions
    assert _fields(record) == ("call-1", "accepted", "", LATER.isoformat(), "")
    assert record.order == _order()  # 주문 기록이 오기 전이라도 행을 채울 수 있게


def test_an_order_confirmed_without_a_request_is_recorded_as_auto_accepted() -> None:
    sink = Recorded()
    _hub(sink).submit(_order())
    (record,) = sink.decisions
    assert _fields(record) == ("call-1", "auto_accepted", "", NOW.isoformat(), "")


def test_waiting_and_withdrawn_requests_are_not_decisions() -> None:
    sink = Recorded()
    hub = _hub(sink)
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.withdraw_approval("call-1", 1, "changed")
    hub.open_approval("call-1", ApprovalRequest(2, _order()), lambda d: None)
    hub.call_ended("call-1", "hangup")  # 통화 뒤 대기로 넘어감
    assert sink.decisions == []


def test_a_failing_sink_does_not_stop_the_decision() -> None:
    hub = _hub(Broken())
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    assert hub.accept("call-1")["status"] == "accepted"


def test_later_decision_kinds_map_from_plain_card_fields() -> None:
    """거절·늦춘 수락·자동 취소(티켓 03~06)는 카드 칸만 채우면 같은 길로 간다."""
    rejected = {"id": "call-2", "status": "rejected", "reject_reason": "out_of_stock",
                "decided_at": LATER.isoformat(), "auto_accepted": False}
    delayed = {"id": "call-3", "status": "accepted", "decided_at": LATER.isoformat(),
               "owner_pickup_time": "2026-11-08T18:45:00+09:00", "auto_accepted": False}
    expired = {"id": "call-4", "status": "expired", "decided_at": "", "auto_accepted": False}
    assert _fields(decision_of(rejected)) == (
        "call-2", "rejected", "out_of_stock", LATER.isoformat(), "")
    assert _fields(decision_of(delayed)) == (
        "call-3", "accepted", "", LATER.isoformat(), "2026-11-08T18:45:00+09:00")
    assert _fields(decision_of(expired)) == ("call-4", "expired", "", "", "")
    assert decision_of({"id": "call-5", "status": "new"}) is None
