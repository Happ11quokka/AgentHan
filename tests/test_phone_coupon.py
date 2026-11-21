"""비서 쿠폰으로 전화 주문 (전화 메뉴·주문 현황·쿠폰 티켓 09). 발표 서버 하나에서 비서 API로
쿠폰을 저장하고 전화(WS)로 주문한다.

바깥에서 보이는 것만 본다: 복창, 손님 화면 이벤트(`state`·`order`), 사장님 카드(SSE), 손님 주문
현황(`GET /api/phone/orders/{id}`), DB 합계(테스트 DB가 있을 때). 금액을 말하거나 보여 주는 곳이
모두 같은 낼 금액을 쓰는지 끝에서 끝까지 본다. 파일럿 가게에는 "전화 주문 1,000원 할인"(최소 주문
10,000원, 11/02~12/31) 쿠폰이 이미 있다.
"""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi.testclient import TestClient

from test_db import TEST_URL
from test_phone_menu import KEY, SESSIONS, _call, _client, _first_sse, _order_script, _session

PILOT_COUPON = "전화 주문 1,000원 할인"
FIRST = {"title": "첫 주문 15% 할인", "discount_type": "percent", "discount_value": 15,
         "start_date": "2026-11-01", "end_date": "2026-11-30"}


def _save_coupon(client: TestClient, session: str, coupon: dict[str, Any]) -> None:
    """쿠폰 폼(`coupon.create`) → 사장님 승인(`task.approve`). 화면이 하는 그대로."""

    def action(action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        sent = client.post(f"{SESSIONS}/{session}/actions",
                           json={"action_id": action_id, "args": args, "label": action_id})
        assert sent.status_code == 200, sent.text
        return sent.json()

    [pending] = action("coupon.create", coupon)["pending"]
    done = action("task.approve", {"task_id": pending["task_id"], "version": pending["version"]})
    assert any(c["title"] == coupon["title"] for c in done["place"]["coupon"])


def _readback(says: list[str]) -> str:
    """최종 복창 한 번(문장마다 나뉘어 온 `bot.say`를 이은 것)."""
    found = re.search(r"주문 확인할게요\..*?맞으세요\?", " ".join(says))
    assert found, says
    return found.group()


def test_a_coupon_saved_in_the_assistant_is_read_back_on_the_next_call(tmp_path) -> None:
    """저장 전 통화는 쿠폰 없이(파일럿 쿠폰은 금액이 모자라 말하지 않는다), 저장 뒤 새 통화는
    복창에 쿠폰 이름·할인액·낼 금액이 들어가고 주문서에 쿠폰이 남는다."""
    script = _order_script("메도빅 하나요")
    with _client(tmp_path, script + script) as client:
        session = _session(client)
        before, before_phone = _call(client, 4)
        _save_coupon(client, session, FIRST)
        after, after_phone = _call(client, 4)

    assert "쿠폰" not in _readback(before_phone.says())
    assert before["total"] == 7500
    assert (before["order"]["coupon_id"], before["order"]["discount"]) == ("", 0)
    assert _readback(after_phone.says()) == (
        "주문 확인할게요. 메도빅 하나, 6시 반에 찾으러 오시고, 첫 주문 15% 할인 쿠폰이 있어서 "
        "1,120원 빼고 모두 6,380원이에요. 맞으세요?")
    assert after["order"]["coupon_name"] == "첫 주문 15% 할인"
    assert after["order"]["discount"] == 1120
    assert after["order"]["coupon_id"]
    assert (after["subtotal"], after["discount"], after["total"]) == (7500, 1120, 6380)


def test_a_coupon_waiting_for_approval_does_not_reach_the_phone(tmp_path) -> None:
    """승인 전 미리보기(`_dry_run`)로 쓴 쿠폰은 게시되지 않는다."""
    script = _order_script("메도빅 하나요")
    with _client(tmp_path, script) as client:
        session = _session(client)
        sent = client.post(f"{SESSIONS}/{session}/actions", json={
            "action_id": "coupon.create", "args": FIRST, "label": "coupon.create"})
        assert sent.json()["pending"]
        order, phone = _call(client, 4)

    assert "쿠폰" not in _readback(phone.says())
    assert order["total"] == 7500


def test_every_place_shows_the_same_payable_total(tmp_path) -> None:
    """복창, 손님 화면 `state`·`order` 이벤트, 사장님 카드, 주문 현황, DB 합계가 같은 낼 금액."""
    env = {"AGENTHAN_DB_URL": _migrated(TEST_URL)} if TEST_URL else {}
    with _client(tmp_path, _order_script("메도빅 하나랑 아메리카노 하나요"), **env) as client:
        order, phone = _call(client, 4)
        order_id = order["order"]["id"]
        with client.stream("GET", f"/api/owner/stream?key={KEY}") as stream:
            board = _first_sse(stream.iter_lines())
        status = client.get(f"/api/phone/orders/{order_id}").json()

    readback = _readback(phone.says())
    assert f"{PILOT_COUPON} 쿠폰이 있어서 1,000원 빼고 모두 11,000원이에요." in readback
    shown = [e for e in phone.events if e["type"] == "state" and e["dialog"] == "confirm"]
    assert shown and {(e["subtotal"], e["discount"], e["coupon_name"], e["total"])
                      for e in shown} == {(12000, 1000, PILOT_COUPON, 11000)}
    assert (order["subtotal"], order["discount"], order["total"]) == (12000, 1000, 11000)
    (card,) = board["orders"]
    assert (card["subtotal"], card["coupon_name"], card["discount"], card["total"]) == (
        12000, PILOT_COUPON, 1000, 11000)
    assert status["amount"] == {"subtotal": 12000, "discount": 1000,
                                "coupon_name": PILOT_COUPON, "total": 11000}
    if TEST_URL:
        assert _db_row(TEST_URL, order_id) == (11000, "coupon-1", PILOT_COUPON, 1000)


def test_a_call_without_a_usable_coupon_looks_as_before(tmp_path) -> None:
    """쿠폰을 못 쓰면 이벤트·카드·주문 현황에 할인 칸이 없다."""
    with _client(tmp_path, _order_script("메도빅 하나요")) as client:
        order, phone = _call(client, 4)
        with client.stream("GET", f"/api/owner/stream?key={KEY}") as stream:
            board = _first_sse(stream.iter_lines())
        status = client.get(f"/api/phone/orders/{order['order']['id']}").json()

    assert "discount" not in order and order["total"] == 7500
    assert all("discount" not in e for e in phone.events if e["type"] == "state")
    (card,) = board["orders"]
    assert (card["discount"], card["coupon_name"], card["total"]) == (0, "", 7500)
    assert status["amount"] == {"subtotal": 7500, "total": 7500}


# ---------------------------------------------------------------- 테스트 DB (있을 때만)


def _migrated(url: str) -> str:
    from agenthan.db import conn as dbconn
    from agenthan.db.migrate import migrate

    with dbconn.connect(url) as c:
        migrate(c)
    return url


def _db_row(url: str, order_id: str) -> tuple[Any, ...]:
    """주문 기록 쓰기는 따로 도는 스레드다. 행이 생길 때까지 잠깐 기다린다."""
    from agenthan.db import conn as dbconn

    deadline = time.monotonic() + 10
    while True:
        with dbconn.connect(url) as c:
            row = c.execute("SELECT total, coupon_id, coupon_name, discount FROM phone_orders"
                            " WHERE id = %s AND status = 'confirmed'", (order_id,)).fetchone()
        if row is not None or time.monotonic() > deadline:
            assert row is not None, "phone_orders에 주문이 남지 않았다"
            return tuple(row)
        time.sleep(0.05)
