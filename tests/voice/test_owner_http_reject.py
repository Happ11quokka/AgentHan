"""사장님 화면 HTTP 거절 (주문 승인 티켓 05): 손님 통화(WS)와 사장님 화면(SSE·POST)을 같이 돌린다.

`with TestClient(app)` 안에서만 쓴다(허브는 이벤트 루프 하나에 묶인다).
"""

from __future__ import annotations

from test_owner_http import KEY, STREAM, _client, _sse, _until
from test_phone_ws import Phone

from agenthan.voice.ws import WS_PATH

BUSY = "죄송해요, 지금 가게가 바빠서 주문을 받기 어렵대요. 다음에 다시 주문해 주세요."
BUSY_TEXT = ("죄송해요, 가게가 바빠서 주문을 받기 어려워 주문이 취소됐어요. "
             "다음에 다시 주문해 주세요.")


def test_the_owner_rejects_as_busy_and_the_customer_hears_the_apology(tmp_path) -> None:
    """끝에서 끝: 통화 → 확인 요청 카드 → 거절(바쁨) POST → 손님 사과 → 취소 문자."""
    with _client(tmp_path) as client:
        with client.stream("GET", f"{STREAM}?key={KEY}") as stream:
            events = _sse(stream.iter_lines())
            next(events)  # snapshot
            with client.websocket_connect(WS_PATH) as ws:
                phone = Phone(ws)
                phone.send(type="call.start", turn=0)
                phone.until("call.ready", 0)
                phone.played(0)
                for turn in (1, 2, 3, 4):
                    phone.say(turn)
                    phone.played(turn)  # 4: "사장님께 주문 확인 중이에요. 잠시만요."

                card = _until(events, "order.update")["data"]
                assert (card["status"], card["reject_reason"]) == ("new", "")
                base = f"/api/owner/orders/{card['id']}"
                assert client.post(f"{base}/reject?key={KEY}",
                                   json={"reason": "free", "text": " "}).status_code == 422
                assert client.post(f"{base}/reject?key={KEY}",
                                   json={"reason": "out_of_stock",
                                         "menu_ids": ["m01"]}).status_code == 422  # 티켓 06
                assert client.post(f"{base}/reject?key={KEY}",
                                   json={"reason": "rain"}).status_code == 422
                rejected = client.post(f"{base}/reject?key={KEY}", json={"reason": "busy"})
                assert rejected.status_code == 200
                assert (rejected.json()["status"], rejected.json()["reject_reason"]) == (
                    "rejected", "busy")
                assert client.post(f"{base}/reject?key={KEY}",
                                   json={"reason": "closed"}).status_code == 409  # 한 번만
                assert client.post(f"{base}/accept?key={KEY}").status_code == 409
                update = _until(events, "order.update")["data"]
                assert (update["status"], [t["text"] for t in update["sms"]]) == (
                    "rejected", [BUSY_TEXT])

                phone.played(5)  # 봇이 먼저 말하는 턴: 사과
                assert phone.closed_by_server()

    assert " ".join(e["text"] for e in phone.of("bot.say", 5)) == BUSY
    assert phone.of("order") == []
    (ended,) = phone.of("call.ended")
    assert ended["reason"] == "cancelled"
    assert [(t["kind"], t["text"]) for t in ended["sms"]] == [("rejected", BUSY_TEXT)]
    states = [e["approval"] for e in phone.of("state") if e.get("approval")]
    assert states[-1] == {"status": "rejected", "number": 1, "delay_min": 0, "reason": "busy"}


def test_rejecting_needs_the_key_and_a_known_order(tmp_path) -> None:
    with _client(tmp_path) as client:
        body = {"reason": "busy"}
        assert client.post("/api/owner/orders/call-1/reject", json=body).status_code == 401
        assert client.post("/api/owner/orders/call-1/reject?key=wrong",
                           json=body).status_code == 401
        assert client.post(f"/api/owner/orders/nope/reject?key={KEY}",
                           json=body).status_code == 404
