"""늦춘 수락 끝에서 끝 (주문 승인 티켓 04): 서버 하나에서 손님 통화(WS)와 사장님 화면(SSE·POST).


통화 → 확인 요청 카드 → [+15분] POST → 손님이 바뀐 시각을 듣고 "네" → 그 시각으로 확정 + 늦춘 수락
문자. `with TestClient(app)` 안에서만 쓴다(`test_owner_http.py`와 같은 까닭).
"""

from __future__ import annotations

from test_owner_http import KEY, STREAM, _client, _sse, _until
from test_phone_ws import ORDER_SCRIPT, Phone

from agenthan.voice.ws import WS_PATH


def test_the_owner_accepts_fifteen_minutes_later_and_the_customer_agrees(tmp_path) -> None:
    with _client(tmp_path, replies=(*ORDER_SCRIPT, "네")) as client:
        with client.stream("GET", f"{STREAM}?key={KEY}") as stream:
            events = _sse(stream.iter_lines())
            assert next(events)["event"] == "snapshot"

            with client.websocket_connect(WS_PATH) as ws:
                phone = Phone(ws)
                phone.send(type="call.start", turn=0)
                phone.until("call.ready", 0)
                phone.played(0)
                for turn in (1, 2, 3, 4):
                    phone.say(turn)
                    phone.played(turn)  # 4: "사장님께 주문 확인 중이에요. 잠시만요."

                card = _until(events, "order.update")["data"]
                url = f"/api/owner/orders/{card['id']}/accept?key={KEY}"
                assert client.post(url, json={"delay_min": 20}).status_code == 422
                accepted = client.post(url, json={"delay_min": 15})
                assert accepted.status_code == 200
                body = accepted.json()
                assert (body["status"], body["delay_min"], body["owner_pickup_label"],
                        body["delay_reply"]) == ("accepted", 15, "18:45", "")  # 묻기 전
                assert client.post(url).status_code == 409  # 결정은 한 번
                reject = f"/api/owner/orders/{card['id']}/reject?key={KEY}"
                # 손님이 "괜찮으세요?"에 답하는 중에도 거절은 받지 않는다 (티켓 05와 같은 409)
                assert client.post(reject, json={"reason": "busy"}).status_code == 409

                phone.played(5)  # 봇이 먼저 말하는 턴: 바뀐 시각 묻기
                phone.say(6)  # "네"
                order = phone.until("order", 6)
                phone.played(6)
                assert phone.closed_by_server()

            update = _until(events, "order.update")["data"]
            while update.get("delay_reply") != "yes":
                update = _until(events, "order.update")["data"]
            assert [t["kind"] for t in update["sms"]] == ["delayed"]

    offer = " ".join(e["text"] for e in phone.of("bot.say", 5))
    closing = " ".join(e["text"] for e in phone.of("bot.say", 6))
    assert offer == "사장님이 6시 45분에 준비 가능하시대요. 괜찮으세요?"
    assert closing == "네, 6시 45분까지 준비해 둘게요. 감사합니다."
    assert order["order"]["pickup_time"] == "2026-11-08T18:45:00+09:00"
    (ended,) = phone.of("call.ended")
    assert ended["reason"] == "confirmed"
    assert [t["text"] for t in ended["sms"]] == [
        "주문이 확정됐어요. 메도빅 1개, 합계 7,500원. 18:45에 준비돼요. 어려우시면 전화 주세요."]
    approval = [e["approval"] for e in phone.of("state") if e.get("approval")]
    assert [a["status"] for a in approval][-2:] == ["delayed", "accepted"]  # 손님 화면 배지

