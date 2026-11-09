"""사장님 화면 HTTP: 개발 서버 하나에서 손님 통화(WS)와 사장님 화면(SSE·POST)을 같이 돌린다.

`with TestClient(app)` 안에서만 쓴다. `with` 없이 쓰면 요청마다 이벤트 루프가 달라
허브(루프 하나에 묶임)가 깨진다 (V1 판정 6).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient
from test_phone_ws import MAX_MESSAGES, ORDER_SCRIPT, Phone

from agenthan.voice.server import create_app
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.ws import WS_PATH

NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
KEY = "test-owner-key"
STREAM = "/api/owner/stream"


def _client(tmp_path) -> TestClient:
    settings = VoiceSettings(
        log_dir=tmp_path / "logs", now=NOW, web_dist=tmp_path / "dist",
        fake_replies=ORDER_SCRIPT, turn_model="off", owner_key=KEY,
    )
    return TestClient(create_app(settings))


def _sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """SSE 줄 → {"event", "id", "data"} (빈 줄이 이벤트 끝). 핑 같은 주석 줄은 버린다."""
    event: dict[str, Any] = {}
    for line in lines:
        if not line:
            if "data" in event:
                yield event
            event = {}
        elif line.startswith(":"):
            continue
        else:
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            event[field] = json.loads(value) if field == "data" else value


def _until(events: Iterator[dict[str, Any]], name: str) -> dict[str, Any]:
    for _, event in zip(range(MAX_MESSAGES), events, strict=False):
        if event["event"] == name:
            return event
    raise AssertionError(f"{name} 이벤트가 오지 않았다")


def _call_and_confirm(client: TestClient) -> None:
    """손님 한 통화: 메도빅 하나 → 6시 반 → 네 (주문 확정)."""
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0)
        phone.until("call.ready", 0)
        phone.played(0)
        for turn in (1, 2, 3, 4):
            phone.say(turn)
            if turn < 4:
                phone.played(turn)
        phone.until("order", 4)
        phone.played(4)
        assert phone.closed_by_server()


def test_a_confirmed_call_shows_up_on_the_owner_screen_and_the_owner_accepts_it(
    tmp_path,
) -> None:
    with _client(tmp_path) as client:
        with client.stream("GET", f"{STREAM}?key={KEY}") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            events = _sse(stream.iter_lines())
            snapshot = next(events)
            assert snapshot["event"] == "snapshot"
            assert snapshot["data"] == {"orders": [], "calls": []}

            _call_and_confirm(client)

            live = _until(events, "call.update")["data"]
            assert (live["status"], live["lines"]) == ("live", [])
            card = _until(events, "order.update")["data"]
            assert card["id"] == live["id"]  # 같은 통화 id로 통화 카드가 주문 카드가 된다
            ordered = _until(events, "call.update")["data"]
            assert (ordered["id"], ordered["status"]) == (live["id"], "ordered")
            assert ordered["lines"] == card["lines"]
            assert card["status"] == "new"
            assert [(line["name_ko"], line["quantity"]) for line in card["lines"]] == [
                ("메도빅", 1)
            ]
            assert card["pickup_label"] == "18:30"
            assert card["total_label"] == "7,500₩"

            accepted = client.post(f"/api/owner/orders/{card['id']}/accept?key={KEY}")
            assert accepted.status_code == 200
            assert accepted.json()["status"] == "accepted"
            update = _until(events, "order.update")["data"]
            assert (update["id"], update["status"]) == (card["id"], "accepted")


def test_owner_paths_need_the_secret_key(tmp_path) -> None:
    with _client(tmp_path) as client:
        assert client.get(STREAM).status_code == 401
        assert client.get(f"{STREAM}?key=wrong").status_code == 401
        assert client.post("/api/owner/orders/call-1/accept").status_code == 401
        assert client.post("/api/owner/orders/call-1/accept?key=wrong").status_code == 401
        assert client.post("/api/owner/calls/call-1/dismiss").status_code == 401


def test_accepting_an_unknown_order_is_not_found(tmp_path) -> None:
    with _client(tmp_path) as client:
        assert client.post(f"/api/owner/orders/nope/accept?key={KEY}").status_code == 404


def test_without_a_configured_key_the_owner_screen_stays_locked(tmp_path) -> None:
    settings = VoiceSettings(log_dir=tmp_path / "logs", now=NOW, web_dist=tmp_path / "dist")
    with TestClient(create_app(settings)) as client:
        assert client.get(f"{STREAM}?key=").status_code == 401
        assert client.get(f"{STREAM}?key=anything").status_code == 401


def test_a_call_the_customer_hangs_up_stays_as_a_missed_card_until_the_owner_closes_it(
    tmp_path,
) -> None:
    with _client(tmp_path) as client:
        with client.stream("GET", f"{STREAM}?key={KEY}") as stream:
            events = _sse(stream.iter_lines())
            next(events)  # snapshot
            with client.websocket_connect(WS_PATH) as ws:
                phone = Phone(ws)
                phone.send(type="call.start", turn=0)
                phone.until("call.ready", 0)
                phone.played(0)
                phone.say(1)
                phone.played(1)
                live = _until(events, "call.update")["data"]
                assert live["status"] == "live"
                busy = client.post(f"/api/owner/calls/{live['id']}/dismiss?key={KEY}")
                assert busy.status_code == 409  # 통화 중 카드는 닫지 않는다
                phone.send(type="call.end", turn=1)

            card = _until(events, "call.update")["data"]
            while card["status"] == "live":  # 장바구니가 바뀐 통화 중 카드를 지나 끝난 카드까지
                card = _until(events, "call.update")["data"]
            assert (card["id"], card["status"], card["reason"]) == (live["id"], "missed", "hangup")
            assert [(line["name_ko"], line["quantity"]) for line in card["lines"]] == [
                ("메도빅", 1)
            ]

            closed = client.post(f"/api/owner/calls/{card['id']}/dismiss?key={KEY}")
            assert closed.status_code == 200
            assert closed.json()["status"] == "closed"
            update = _until(events, "call.update")["data"]
            assert (update["id"], update["status"]) == (card["id"], "closed")


def test_closing_an_unknown_call_card_is_not_found(tmp_path) -> None:
    with _client(tmp_path) as client:
        assert client.post(f"/api/owner/calls/nope/dismiss?key={KEY}").status_code == 404
