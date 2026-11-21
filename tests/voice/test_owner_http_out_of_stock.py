"""사장님 화면 HTTP 재료 소진 (주문 승인 티켓 06): 손님 통화(WS)와 사장님 화면(SSE·POST)을 같이
돌린다.

끝에서 끝: 통화 → 확인 요청 카드 → 재료 소진(메도빅) POST → 손님이 품절 안내를 듣고 "빼 주세요"
→ 재승인 없이 확정 → 카드는 남은 메뉴로 수락 + 확정 문자 → 다음 통화는 메도빅을 품절로 안내 →
되돌리기 POST → 그다음 통화는 다시 받는다. `with TestClient(app)` 안에서만 쓴다.
"""

from __future__ import annotations

from test_owner_http import KEY, STREAM, _client, _sse, _until
from test_phone_ws import Phone

from agenthan.voice.ws import WS_PATH

SCRIPT = ("메도빅 하나랑 나폴레옹 하나요", "그게 다예요", "6시 반이요", "네", "빼 주세요")
NOTICE = "메도빅은 지금 재료가 떨어졌대요. 빼고 주문할까요, 다른 메뉴로 바꾸실까요?"
CLOSING = "네, 6시 반까지 준비해 둘게요. 감사합니다."
SOLD_OUT = "죄송해요, 메도빅은 오늘 다 떨어졌어요."


def _first_answer(client) -> str:
    """새 통화에서 손님 첫 말(대본 첫 줄)에 대한 AI 대답."""
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0)
        phone.until("call.ready", 0)
        phone.played(0)
        phone.say(1)
        phone.until("bot.done", 1)
        return " ".join(e["text"] for e in phone.of("bot.say", 1))


def test_out_of_stock_then_dropping_it_confirms_and_the_next_call_hears_sold_out(tmp_path) -> None:
    with _client(tmp_path, SCRIPT) as client:
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
                base = f"/api/owner/orders/{card['id']}"
                assert client.post(f"{base}/reject?key={KEY}", json={
                    "reason": "out_of_stock", "menu_ids": ["m09"]}).status_code == 422
                rejected = client.post(f"{base}/reject?key={KEY}", json={
                    "reason": "out_of_stock", "menu_ids": ["m01"]})
                assert rejected.status_code == 200
                assert rejected.json()["sold_out"][0]["menu_id"] == "m01"
                sold_out = _until(events, "owner.sold_out")["data"]
                assert [item["menu_id"] for item in sold_out["items"]] == ["m01"]

                phone.played(5)  # 봇이 먼저 말하는 턴: 품절 안내 + 빼기/바꾸기 질문
                phone.say(6)  # "빼 주세요"
                phone.until("order", 6)
                phone.played(6)
                assert phone.closed_by_server()

            accepted = _until(events, "order.update")["data"]
            while accepted["status"] != "accepted":
                accepted = _until(events, "order.update")["data"]

        assert [(line["menu_id"], line["quantity"]) for line in accepted["lines"]] == [("m02", 1)]
        assert [t["kind"] for t in accepted["sms"]] == ["order"]
        assert _first_answer(client).startswith(SOLD_OUT)  # 다음 통화: 엔진의 품절 안내

        undo = client.post(f"/api/owner/sold-out/m01/undo?key={KEY}")
        assert (undo.status_code, undo.json()["items"]) == (200, [])
        assert client.post(f"/api/owner/sold-out/m99/undo?key={KEY}").status_code == 404
        assert client.post("/api/owner/sold-out/m01/undo").status_code == 401
        assert not _first_answer(client).startswith(SOLD_OUT)  # 되돌린 뒤에는 다시 받는다

    assert " ".join(e["text"] for e in phone.of("bot.say", 5)) == NOTICE
    assert " ".join(e["text"] for e in phone.of("bot.say", 6)) == CLOSING
    (ended,) = phone.of("call.ended")
    assert ended["reason"] == "confirmed"
    assert ended["sms"][-1]["kind"] == "order"
