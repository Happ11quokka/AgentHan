"""주문 현황 다시 열기 (전화 메뉴·주문 현황·쿠폰 티켓 06): 허브에 카드가 없을 때 전화 주문 기록
한 줄로 손님 주문 현황을 만든다. 허브 조회와 같은 모양(줄·금액·픽업 시각·단계 + 결과·사유·품절
메뉴·취소 가능)이다. 결정 전 기록은 확인 요청을 연 시각 + 5분이 지나면 자동 취소로 읽는다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest
from agenthan.voice.owner import sms
from agenthan.voice.owner.decisions import DecisionRecord
from agenthan.voice.owner.hub import OwnerHub, UnknownCard
from agenthan.voice.owner.order_archive import ArchivedOrder, archived_order_status

PLACED = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
EXPIRE = timedelta(minutes=5)
CALL = "call-1"
ORDER = Order(id=CALL, lines=[OrderLine("m01", 2, name="메도빅", unit_price=7_500),
                              OrderLine("m02", 1, ("초코",), "포크 주세요", "나폴레옹", 8_000)],
              pickup_time="2026-11-08T18:30:00+09:00", status="confirmed",
              created_at=PLACED.isoformat())


def _status(now: datetime = PLACED + timedelta(minutes=1), **kw) -> dict | None:
    return archived_order_status(ArchivedOrder(order=kw.pop("order", ORDER), **kw), now=now,
                                 expire=EXPIRE)


def _outcome(status: dict | None) -> tuple:
    assert status is not None
    return status["stage"], status["outcome"], status["reason"], status["sold_out"]


def test_a_record_reads_back_with_lines_amount_and_pickup() -> None:
    status = _status(owner_status="accepted")

    assert status == {
        "order_id": CALL,
        "stage": "confirmed",
        "placed_at": PLACED.isoformat(),
        "lines": [
            {"menu_id": "m01", "name": "메도빅", "quantity": 2, "options": [], "request": "",
             "subtotal": 15_000},
            {"menu_id": "m02", "name": "나폴레옹", "quantity": 1, "options": ["초코"],
             "request": "포크 주세요", "subtotal": 8_000},
        ],
        "amount": {"subtotal": 23_000, "total": 23_000},
        "pickup": {"time": "2026-11-08T18:30:00+09:00", "label": "18:30", "original_time": "",
                   "original_label": ""},
        "outcome": "accepted", "reason": None, "sold_out": [],
        "can_cancel": False,  # 허브에 카드가 없는 주문은 손님 취소를 받을 곳이 없다
    }


def test_an_undecided_record_is_checking_until_five_minutes_after_the_request() -> None:
    assert _outcome(_status(PLACED + timedelta(minutes=4, seconds=59))) == (
        "checking", None, None, [])
    # 서버가 다시 떠 자동 취소 타이머가 없다: 읽을 때 마감으로 자동 취소를 정한다
    assert _outcome(_status(PLACED + EXPIRE)) == ("cancelled", "expired", None, [])


def test_owner_decisions_map_to_outcomes() -> None:
    later = _status(owner_status="accepted", owner_pickup_time="2026-11-08T18:45:00+09:00")
    assert later is not None and later["outcome"] == "delayed"
    assert (later["pickup"]["label"], later["pickup"]["original_label"]) == ("18:45", "18:30")
    assert _outcome(_status(owner_status="auto_accepted"))[:2] == ("confirmed", "auto")
    assert _outcome(_status(owner_status="expired"))[:2] == ("cancelled", "expired")
    # 늦춘 시각을 손님이 거절한 주문(cancelled)·손님 취소(티켓 07)
    assert _outcome(_status(owner_status="cancelled"))[:2] == ("cancelled", "customer_cancelled")
    assert _outcome(_status(owner_status="customer_cancelled"))[:2] == (
        "cancelled", "customer_cancelled")


def test_a_reject_reads_back_with_its_reason_and_sold_out_menus() -> None:
    assert _outcome(_status(owner_status="rejected", reject_reason="busy")) == (
        "cancelled", "rejected", {"code": "busy", "text": sms.rejected_text("busy")}, [])
    assert _outcome(_status(owner_status="rejected", reject_reason="out_of_stock",
                            reject_menu_ids=("m02",))) == (
        "cancelled", "out_of_stock",
        {"code": "out_of_stock", "text": sms.rejected_text("out_of_stock", ["나폴레옹"])},
        ["나폴레옹"])
    written = _status(owner_status="rejected", reject_reason="free", reject_text="Закрываемся",
                      reject_text_ko="문을 닫아요")
    assert _outcome(written)[2] == {"code": "free", "text": "문을 닫아요"}
    untranslated = _status(owner_status="rejected", reject_reason="free",
                           reject_text="Закрываемся")
    assert _outcome(untranslated)[2] == {"code": "free", "text": "Закрываемся"}


def test_a_handoff_or_an_undecided_draft_has_no_order_status() -> None:
    assert _status(order=Order(id=CALL, lines=[], status="handoff")) is None
    assert _status(order=Order(id=CALL, lines=ORDER.lines, status="draft")) is None


@pytest.mark.parametrize("decide", [
    pytest.param(lambda hub: hub.accept(CALL, delay_min=15), id="delayed"),
    pytest.param(lambda hub: hub.reject(CALL, "busy"), id="rejected"),
    pytest.param(lambda hub: hub.reject(CALL, "out_of_stock", menu_ids=["m02"]), id="out_of_stock"),
])
def test_the_record_gives_the_same_answer_as_the_hub_card(decide) -> None:
    """같은 주문: 허브 카드로 만든 응답과 기록으로 만든 응답이 같다(결과·사유·품절 메뉴까지)."""
    clock = FakeClock(PLACED)
    recorded: list[DecisionRecord] = []

    class Sink:
        def record(self, decision: DecisionRecord) -> None:
            recorded.append(decision)

    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock, decision_sink=Sink())
    hub.open_approval(CALL, ApprovalRequest(1, ORDER, in_call=False), lambda d: None)
    hub.submit(ORDER)
    hub.call_ended(CALL, "confirmed")
    clock.set(PLACED + timedelta(minutes=1))
    decide(hub)
    live = hub.order_status(CALL)

    last = recorded[-1]
    archived = archived_order_status(ArchivedOrder(
        order=ORDER, owner_status=last.owner_status, decided_at=last.decided_at,
        owner_pickup_time=last.owner_pickup_time, reject_reason=last.reject_reason,
        reject_menu_ids=last.reject_menu_ids, reject_text=last.reject_text,
        reject_text_ko=last.reject_text_ko), now=clock.now(), expire=EXPIRE)
    assert archived is not None
    assert {k: archived[k] for k in live} == live


class _Archive:
    def __init__(self, *records: ArchivedOrder, broken: bool = False) -> None:
        self.records = {r.order.id: r for r in records}
        self.broken = broken
        self.asked: list[str] = []

    def load(self, order_id: str) -> ArchivedOrder | None:
        self.asked.append(order_id)
        if self.broken:
            raise RuntimeError("DB 끊김")
        return self.records.get(order_id)


def _hub(archive: _Archive | None) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(),
                    clock=FakeClock(PLACED + timedelta(minutes=1)), archive=archive)


def test_the_hub_reads_the_record_only_when_it_has_no_card() -> None:
    archive = _Archive(ArchivedOrder(order=ORDER, owner_status="rejected", reject_reason="busy"))
    hub = _hub(archive)
    status = asyncio.run(hub.lookup_order_status(CALL))
    assert (status["stage"], status["outcome"]) == ("cancelled", "rejected")

    hub.open_approval(CALL, ApprovalRequest(1, ORDER, in_call=False), lambda d: None)
    assert asyncio.run(hub.lookup_order_status(CALL))["stage"] == "checking"  # 허브가 먼저
    assert archive.asked == [CALL]


def test_an_order_in_neither_place_is_not_found() -> None:
    for hub in (_hub(None), _hub(_Archive()), _hub(_Archive(broken=True))):
        with pytest.raises(UnknownCard):
            asyncio.run(hub.lookup_order_status("call-nope"))
