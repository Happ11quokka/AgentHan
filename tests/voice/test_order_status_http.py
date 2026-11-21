"""손님 주문 현황 API (전화 메뉴·주문 현황·쿠폰 티켓 04): `GET /api/phone/orders/{주문 번호}`.

개발 서버 하나(기본 설정, 통화 중 대기 0)에서 손님 통화(WS)·사장님 화면(SSE·POST)·주문 현황
조회를 같이 돌린다. 주문 번호는 통화 id이고 토큰은 없다. 응답은 줄 목록·금액·픽업 시각·단계다.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_owner_http import KEY, NOW, STREAM, _sse
from test_phone_ws import ORDER_SCRIPT, Phone

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.owner.http import customer_orders_router
from agenthan.voice.owner.hub import OwnerHub
from agenthan.voice.server import create_app
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.ws import WS_PATH

PLACED = "주문 넣어 드렸어요. 가게에서 확인하면 화면으로 알려 드릴게요. 감사합니다."


def _client(tmp_path) -> TestClient:
    settings = VoiceSettings(log_dir=tmp_path / "logs", now=NOW, web_dist=tmp_path / "dist",
                             fake_replies=ORDER_SCRIPT, turn_model="off", owner_key=KEY)
    return TestClient(create_app(settings))


def _order_call(client: TestClient) -> tuple[str, Phone]:
    """메도빅 하나 → 6시 반 → 네. 서버가 끊을 때까지. 주문 번호(통화 id)를 돌려준다."""
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0)
        call_id = phone.until("call.ready", 0)["call"]
        phone.played(0)
        for turn in (1, 2, 3, 4):
            phone.say(turn)
            phone.played(turn)
        assert phone.closed_by_server()
    return call_id, phone


def test_an_order_placed_without_waiting_is_checking_until_the_owner_accepts(tmp_path) -> None:
    with _client(tmp_path) as client:
        with client.stream("GET", f"{STREAM}?key={KEY}") as stream:  # 사장님 화면 켜짐
            screen = _sse(stream.iter_lines())  # 들고 있어야 구독이 남는다
            next(screen)  # snapshot
            call_id, phone = _order_call(client)
            checking = client.get(f"/api/phone/orders/{call_id}")
            accepted = client.post(f"/api/owner/orders/{call_id}/accept?key={KEY}")
            confirmed = client.get(f"/api/phone/orders/{call_id}")

    assert " ".join(e["text"] for e in phone.of("bot.say", 4)) == PLACED
    assert phone.of("bot.say", 5) == []  # 대기·재촉 없이 끝났다
    assert phone.of("call.ended")[0]["reason"] == "confirmed"
    assert checking.status_code == 200
    body = checking.json()
    assert body["order_id"] == call_id
    assert body["stage"] == "checking"
    assert body["lines"] == [{"menu_id": "m01", "name": "메도빅", "quantity": 1, "options": [],
                              "request": "", "subtotal": 7500}]
    assert body["amount"] == {"subtotal": 7500, "total": 7500}
    assert body["pickup"] == {"time": "2026-11-08T18:30:00+09:00", "label": "18:30",
                              "original_time": "", "original_label": ""}
    assert body["placed_at"].startswith("2026-11-08T16:00")
    assert "deadline_at" not in body  # 남은 시간은 손님에게 보이지 않는다
    assert accepted.status_code == 200
    assert confirmed.json()["stage"] == "confirmed"
    assert confirmed.json()["pickup"]["label"] == "18:30"


def test_with_the_owner_screen_off_the_order_is_confirmed_at_once(tmp_path) -> None:
    with _client(tmp_path) as client:
        call_id, phone = _order_call(client)
        body = client.get(f"/api/phone/orders/{call_id}").json()

    assert " ".join(e["text"] for e in phone.of("bot.say", 4)) == (
        "네, 6시 반까지 준비해 둘게요. 감사합니다.")
    assert body["stage"] == "confirmed"


def test_an_unknown_order_number_is_not_found(tmp_path) -> None:
    with _client(tmp_path) as client:
        missing = client.get("/api/phone/orders/call-nope")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "주문을 찾을 수 없어요"


def test_a_call_that_ended_without_an_order_has_no_order_status(tmp_path) -> None:
    with _client(tmp_path) as client:
        with client.websocket_connect(WS_PATH) as ws:
            phone = Phone(ws)
            phone.send(type="call.start", turn=0)
            call_id = phone.until("call.ready", 0)["call"]
            phone.played(0)
            phone.send(type="call.end", turn=0)  # 손님이 주문 없이 끊음
        missing = client.get(f"/api/phone/orders/{call_id}")

    assert missing.status_code == 404


# ---------------------------------------------------------------- 결과와 실시간 갱신 (티켓 05)


def _stream_url(order_id: str) -> str:
    return f"/api/phone/orders/{order_id}/stream"


def test_the_order_stream_changes_by_itself_when_the_owner_rejects(tmp_path) -> None:
    with _client(tmp_path) as client:
        with client.stream("GET", f"{STREAM}?key={KEY}") as stream:  # 사장님 화면 켜짐
            screen = _sse(stream.iter_lines())
            next(screen)  # snapshot
            call_id, _ = _order_call(client)
            with client.stream("GET", _stream_url(call_id)) as live:
                assert live.status_code == 200
                assert live.headers["content-type"].startswith("text/event-stream")
                events = _sse(live.iter_lines())
                first = next(events)
                rejected = client.post(f"/api/owner/orders/{call_id}/reject?key={KEY}",
                                       json={"reason": "busy"})
                second = next(events)
            read = client.get(f"/api/phone/orders/{call_id}").json()

    assert (first["event"], first["data"]["stage"], first["data"]["outcome"]) == (
        "order.status", "checking", None)
    assert rejected.status_code == 200
    assert second["event"] == "order.status"
    assert (second["data"]["stage"], second["data"]["outcome"]) == ("cancelled", "rejected")
    assert second["data"]["reason"]["code"] == "busy"
    assert second["data"]["can_cancel"] is False
    assert second["data"] == read  # 끊겨서 다시 읽어도 같은 현황


def test_an_out_of_stock_reject_shows_the_menu_and_the_next_call_hears_it_is_sold_out(
    tmp_path,
) -> None:
    with _client(tmp_path) as client:
        with client.stream("GET", f"{STREAM}?key={KEY}") as stream:
            screen = _sse(stream.iter_lines())
            next(screen)
            call_id, _ = _order_call(client)
            client.post(f"/api/owner/orders/{call_id}/reject?key={KEY}",
                        json={"reason": "out_of_stock", "menu_ids": ["m01"]})
            body = client.get(f"/api/phone/orders/{call_id}").json()
            with client.websocket_connect(WS_PATH) as ws:  # 다음 통화: 메도빅 하나요
                phone = Phone(ws)
                phone.send(type="call.start", turn=0)
                phone.until("call.ready", 0)
                phone.played(0)
                phone.say(1)
                phone.until("bot.done", 1)
                phone.send(type="call.end", turn=1)

    assert (body["stage"], body["outcome"], body["sold_out"]) == (
        "cancelled", "out_of_stock", ["메도빅"])
    assert "메도빅" in body["reason"]["text"]
    heard = " ".join(e["text"] for e in phone.of("bot.say", 1))
    assert "메도빅은 오늘 다 떨어졌어요" in heard


def test_an_order_stream_for_an_unknown_order_is_not_found(tmp_path) -> None:
    with _client(tmp_path) as client:
        missing = client.get(_stream_url("call-nope"))

    assert missing.status_code == 404
    assert missing.json()["detail"] == "주문을 찾을 수 없어요"


def test_the_order_stream_says_it_ended_after_its_window() -> None:
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))
    app = FastAPI()
    app.include_router(customer_orders_router(hub, window_s=0.2))
    with TestClient(app) as client:
        hub.submit(Order(id="call-1", lines=[OrderLine("m01", 1, name="메도빅", unit_price=7_500)],
                         pickup_time="2026-11-08T18:30:00+09:00", status="confirmed"))
        with client.stream("GET", _stream_url("call-1")) as stream:
            events = list(_sse(stream.iter_lines()))  # 시간이 지나면 서버가 닫는다

    assert [e["event"] for e in events] == ["order.status", "order.end"]
    assert events[0]["data"]["outcome"] == "auto"
