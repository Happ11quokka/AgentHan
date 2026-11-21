"""손님 취소 API (전화 메뉴·주문 현황·쿠폰 티켓 07): `POST /api/phone/orders/{주문 번호}/cancel`.

토큰 없음(주문 번호가 열쇠다). 받으면 바뀐 주문 현황(200), 받지 않으면 409와 거부 사유 코드·손님
화면 한 줄·지금 현황(진 쪽 화면이 이긴 결과를 보인다). 모르는 주문은 404. 손님 취소와 사장님 결정은
한 주문에 결정 한 번이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest
from agenthan.voice.owner.http import customer_orders_router, owner_router
from agenthan.voice.owner.hub import OwnerHub

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
KEY = "test-owner-key"
CALL = "call-1"
CANCEL = f"/api/phone/orders/{CALL}/cancel"


def _app() -> tuple[TestClient, OwnerHub, FakeClock]:
    """통화 뒤 확인 요청 1번이 사장님 결정을 기다리는 서버 (통화 중 대기 0)."""
    clock = FakeClock(NOW)
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock)
    order = Order(id=CALL, lines=[OrderLine("m01", 1, name="메도빅", unit_price=7_500)],
                  pickup_time="2026-11-08T18:30:00+09:00", status="confirmed",
                  created_at=NOW.isoformat())
    hub.call_started(CALL)
    hub.open_approval(CALL, ApprovalRequest(1, order, in_call=False), lambda _: None)
    hub.submit(order)
    hub.call_ended(CALL, "confirmed")
    app = FastAPI()
    app.include_router(customer_orders_router(hub))
    app.include_router(owner_router(hub, key=KEY))
    return TestClient(app), hub, clock


def test_a_checking_order_is_cancelled_and_the_owner_can_no_longer_decide() -> None:
    client, _, _ = _app()
    with client:
        before = client.get(f"/api/phone/orders/{CALL}").json()
        cancelled = client.post(CANCEL)
        accept = client.post(f"/api/owner/orders/{CALL}/accept?key={KEY}")
        reject = client.post(f"/api/owner/orders/{CALL}/reject?key={KEY}", json={"reason": "busy"})
        after = client.get(f"/api/phone/orders/{CALL}").json()

    assert before["can_cancel"] is True
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert (body["stage"], body["outcome"], body["can_cancel"]) == (
        "cancelled", "customer_cancelled", False)
    assert (accept.status_code, reject.status_code) == (409, 409)  # 늦게 온 사장님 결정은 거부
    assert after == body


def test_a_later_pickup_can_be_cancelled() -> None:
    client, _, _ = _app()
    with client:
        client.post(f"/api/owner/orders/{CALL}/accept?key={KEY}", json={"delay_min": 15})
        cancelled = client.post(CANCEL)

    assert cancelled.status_code == 200
    assert cancelled.json()["outcome"] == "customer_cancelled"


@pytest.mark.parametrize(("decide", "code", "stage", "message"), [
    ({"accept": {}}, "accepted", "confirmed",
     "가게가 이미 주문을 확정했어요. 취소하려면 가게로 전화 주세요."),
    ({"reject": {"reason": "busy"}}, "rejected", "cancelled", "가게에서 이미 주문을 취소했어요."),
    ({"reject": {"reason": "out_of_stock", "menu_ids": ["m01"]}}, "out_of_stock", "cancelled",
     "가게에서 이미 주문을 취소했어요."),
])
def test_a_cancel_after_the_owner_decided_is_refused_with_the_reason(
    decide, code, stage, message,
) -> None:
    client, _, _ = _app()
    [(action, body)] = decide.items()
    with client:
        client.post(f"/api/owner/orders/{CALL}/{action}?key={KEY}", json=body)
        refused = client.post(CANCEL)

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert (detail["code"], detail["message"]) == (code, message)
    assert detail["order"]["stage"] == stage  # 진 쪽 화면은 이긴 결과를 보인다


def test_an_auto_cancelled_or_already_cancelled_order_is_refused() -> None:
    client, _, clock = _app()
    with client:
        clock.set(NOW + timedelta(minutes=5))
        expired = client.post(CANCEL)
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "expired"

    client, _, _ = _app()
    with client:
        client.post(CANCEL)
        again = client.post(CANCEL)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "customer_cancelled"
    assert again.json()["detail"]["message"] == "이미 취소한 주문이에요."


def test_an_unknown_order_cannot_be_cancelled() -> None:
    client, _, _ = _app()
    with client:
        missing = client.post("/api/phone/orders/call-nope/cancel")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "주문을 찾을 수 없어요"
