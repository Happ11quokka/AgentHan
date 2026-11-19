"""통화 문지기 (6단계): Origin·손님 IP·IP별 상한·하루 예산·점검(drain).

바깥 행동은 WS로 본다(거절 이벤트가 `busy`와 같은 모양으로 온다). 시계·날짜가 필요한 것만
`CallGate`를 직접 부른다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice.gate import CallGate, GateSettings, client_ip
from agenthan.voice.settings import SettingsError, VoiceSettings
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.ws import WS_PATH, phone_router

NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
GUEST = "211.36.140.7"  # 공인 주소 (문서용 대역 203.0.113.0/24는 파이썬이 사설로 본다)
CADDY = "172.18.0.3"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.day = "2026-11-12"

    def monotonic(self) -> float:
        return self.t

    def today(self) -> str:
        return self.day


def _gate(tmp_path=None, **kw: Any) -> tuple[CallGate, Clock]:
    clock = Clock()
    path = tmp_path / "phone-budget.json" if tmp_path is not None else None
    gate = CallGate(GateSettings(**kw), monotonic=clock.monotonic, today=clock.today,
                    budget_file=path)
    return gate, clock


def _app(tmp_path, **kw: Any) -> FastAPI:
    settings = VoiceSettings(nlu="fake", stt="fake", tts="fake", log_dir=tmp_path / "logs",
                             now=NOW, **kw)
    app = FastAPI()
    app.include_router(phone_router(
        store=FakeStoreKnowledge.from_fixture(), config=StoreConfig.from_fixture(),
        order_sink=MemoryOrderSink(), settings=settings,
    ))
    return app


def _start(ws: Any) -> dict[str, Any]:
    ws.send_text(json.dumps({"type": "call.start", "turn": 0}))
    while True:
        message = ws.receive()
        if message.get("text") is not None:
            event = json.loads(message["text"])
            if event["type"] in ("call.ready", "error"):
                return event


def _refused(ws: Any) -> dict[str, Any]:
    error = json.loads(ws.receive_text())
    assert ws.receive()["type"] == "websocket.close"
    return error


# ---------------------------------------------------------------- Origin

def test_without_an_allowed_origin_list_any_origin_may_call(tmp_path) -> None:
    client = TestClient(_app(tmp_path))
    with client.websocket_connect(WS_PATH, headers={"Origin": "https://elsewhere.example"}) as ws:
        assert _start(ws)["type"] == "call.ready"


def test_a_page_on_another_site_cannot_open_a_call(tmp_path) -> None:
    client = TestClient(_app(tmp_path, allowed_origins=("https://shop.example",)))
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            WS_PATH, headers={"Origin": "https://elsewhere.example"}):
        pass
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(WS_PATH):
        pass  # Origin이 없는 스크립트도 막는다
    with client.websocket_connect(WS_PATH, headers={"Origin": "https://shop.example"}) as ws:
        assert _start(ws)["type"] == "call.ready"


def test_origin_is_compared_without_case_or_trailing_slash() -> None:
    gate, _ = _gate(allowed_origins=("https://Shop.example/",))
    assert gate.origin_allowed("https://shop.EXAMPLE")
    assert not gate.origin_allowed("https://shop.example.evil.test")
    assert not gate.origin_allowed(None)


# ---------------------------------------------------------------- 손님 IP

def test_the_guest_address_is_read_from_the_proxy_header_only_behind_a_trusted_proxy() -> None:
    trusted = ("172.16.0.0/12",)
    assert client_ip(CADDY, GUEST, trusted) == GUEST
    assert client_ip(CADDY, f"10.9.9.9, {GUEST}", trusted) == GUEST  # 손님이 꾸민 앞부분은 무시
    assert client_ip(CADDY, None, trusted) == CADDY
    assert client_ip(GUEST, "39.7.28.1", trusted) == GUEST  # 프록시가 아니면 헤더를 믿지 않는다
    assert client_ip(CADDY, GUEST, ()) == CADDY
    assert client_ip(CADDY, GUEST, ("private",)) == GUEST
    assert client_ip(None, None, trusted) == ""


# ---------------------------------------------------------------- IP별 상한

def test_one_address_may_hold_only_so_many_calls_at_once() -> None:
    gate, _ = _gate(ip_max_calls=2, ip_calls_per_hour=0)
    first = gate.admit(GUEST)
    second = gate.admit(GUEST)
    refused = gate.admit(GUEST)
    assert (first.refusal, second.refusal) == (None, None)
    assert refused.refusal is not None and refused.refusal.code == "busy_ip"
    assert gate.admit("39.7.28.9").refusal is None  # 다른 손님은 받는다
    gate.release(first)
    assert gate.admit(GUEST).refusal is None


def test_one_address_may_start_only_so_many_calls_an_hour() -> None:
    gate, clock = _gate(ip_max_calls=0, ip_calls_per_hour=3)
    for _ in range(3):
        gate.release(gate.admit(GUEST))
    refused = gate.admit(GUEST)
    assert refused.refusal is not None and refused.refusal.code == "rate_limited"
    assert refused.refusal.retry
    clock.t += 3_601
    assert gate.admit(GUEST).refusal is None


def test_local_and_private_addresses_are_not_limited() -> None:
    # 로컬 개발·테스트 클라이언트·프록시 설정을 빠뜨린 서버(모두가 Caddy 주소로 보임)를 막지 않는다
    gate, _ = _gate(ip_max_calls=1, ip_calls_per_hour=1)
    for ip in ("127.0.0.1", "::1", CADDY, "testclient", ""):
        assert gate.admit(ip).refusal is None
        assert gate.admit(ip).refusal is None


def test_the_ip_limit_reaches_the_screen_like_busy(tmp_path) -> None:
    client = TestClient(_app(tmp_path, ip_max_calls=1, trusted_proxies=("private",)),
                        client=(CADDY, 50_000))
    headers = {"X-Forwarded-For": GUEST}
    with client.websocket_connect(WS_PATH, headers=headers) as first:
        assert _start(first)["type"] == "call.ready"
        with client.websocket_connect(WS_PATH, headers=headers) as second:
            error = _refused(second)
        assert (error["type"], error["code"], error["retry"]) == ("error", "busy_ip", True)
        assert "통화" in error["message"]
        with client.websocket_connect(WS_PATH, headers={"X-Forwarded-For": "39.7.28.9"}) as o:
            assert _start(o)["type"] == "call.ready"


# ---------------------------------------------------------------- 하루 예산

def test_the_day_closes_after_the_daily_number_of_calls(tmp_path) -> None:
    gate, clock = _gate(tmp_path, day_max_calls=2)
    gate.release(gate.admit(GUEST))
    gate.release(gate.admit("39.7.28.9"))
    refused = gate.admit("175.223.10.4")
    assert refused.refusal is not None
    assert (refused.refusal.code, refused.refusal.retry) == ("closed_today", False)
    assert "마감" in refused.refusal.message
    clock.day = "2026-11-13"
    assert gate.admit("175.223.10.4").refusal is None  # 다음 날 다시 연다


def test_the_day_closes_after_the_daily_call_minutes(tmp_path) -> None:
    gate, clock = _gate(tmp_path, day_max_minutes=5)
    ticket = gate.admit(GUEST)
    clock.t += 4 * 60
    gate.release(ticket)
    ticket = gate.admit(GUEST)  # 4분 썼다: 아직 받는다
    clock.t += 90
    gate.release(ticket)
    assert gate.admit(GUEST).refusal.code == "closed_today"  # type: ignore[union-attr]


def test_the_daily_count_survives_a_restart(tmp_path) -> None:
    gate, _ = _gate(tmp_path, day_max_calls=2)
    gate.release(gate.admit(GUEST))
    gate.release(gate.admit(GUEST))
    again, _ = _gate(tmp_path, day_max_calls=2)
    assert again.admit(GUEST).refusal is not None
    assert json.loads((tmp_path / "phone-budget.json").read_text())["calls"] == 2


def test_a_broken_budget_file_starts_the_day_from_zero(tmp_path) -> None:
    (tmp_path / "phone-budget.json").write_text("{not json")
    gate, _ = _gate(tmp_path, day_max_calls=1)
    assert gate.admit(GUEST).refusal is None


def test_the_daily_limit_reaches_the_screen_and_the_call_does_not_start(tmp_path) -> None:
    client = TestClient(_app(tmp_path, day_max_calls=1))
    with client.websocket_connect(WS_PATH) as ws:
        assert _start(ws)["type"] == "call.ready"
    with client.websocket_connect(WS_PATH) as ws:
        error = _refused(ws)
    assert (error["code"], error["retry"]) == ("closed_today", False)
    assert error["message"].startswith("오늘은 전화 주문이 마감됐어요")
    assert (tmp_path / "logs" / "phone-budget.json").is_file()


def test_without_a_daily_limit_nothing_is_written(tmp_path) -> None:
    client = TestClient(_app(tmp_path))
    with client.websocket_connect(WS_PATH) as ws:
        assert _start(ws)["type"] == "call.ready"
    assert not (tmp_path / "logs" / "phone-budget.json").exists()


# ---------------------------------------------------------------- 점검(drain)

def test_while_draining_new_calls_are_turned_away_and_running_calls_are_counted() -> None:
    gate, _ = _gate()
    ticket = gate.admit(GUEST)
    gate.drain()
    refused = gate.admit(GUEST)
    assert refused.refusal is not None and refused.refusal.code == "maintenance"
    assert (gate.active, gate.draining) == (1, True)
    gate.release(ticket)
    assert gate.active == 0


def test_a_drain_file_in_the_log_folder_turns_new_calls_away(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    app = _app(tmp_path)  # 시작할 때는 지난 배포가 남긴 표시를 지운다
    client = TestClient(app)
    (logs / "DRAIN").write_text("")
    with client.websocket_connect(WS_PATH) as ws:
        error = _refused(ws)
    assert (error["code"], error["retry"]) == ("maintenance", True)
    (logs / "DRAIN").unlink()
    with client.websocket_connect(WS_PATH) as ws:
        assert _start(ws)["type"] == "call.ready"


def test_a_stale_drain_file_is_removed_at_start(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "DRAIN").write_text("")
    client = TestClient(_app(tmp_path))
    with client.websocket_connect(WS_PATH) as ws:
        assert _start(ws)["type"] == "call.ready"


# ---------------------------------------------------------------- 메시지 크기·설정

def test_an_oversized_frame_is_dropped_and_the_call_goes_on(tmp_path, caplog) -> None:
    client = TestClient(_app(tmp_path, ws_max_bytes=4_000))
    with client.websocket_connect(WS_PATH) as ws:
        assert _start(ws)["type"] == "call.ready"
        ws.send_bytes(bytes(8_000))
        ws.send_text(json.dumps({"type": "call.end", "turn": 0, "pad": "x" * 8_000}))
        ws.send_text(json.dumps({"type": "call.end", "turn": 0}))
        while ws.receive()["type"] != "websocket.close":
            pass
    assert "큰 메시지를 버림" in caplog.text


def test_gate_settings_come_from_the_environment() -> None:
    settings = VoiceSettings.from_env({
        "PHONE_ALLOWED_ORIGINS": "https://a.example, https://b.example",
        "PHONE_TRUSTED_PROXIES": "private",
        "PHONE_IP_MAX_CALLS": "0", "PHONE_IP_CALLS_PER_HOUR": "12",
        "PHONE_DAY_MAX_CALLS": "300", "PHONE_DAY_MAX_MINUTES": "600",
        "PHONE_WS_MAX_BYTES": "32768",
    }, env_file=None)
    assert settings.allowed_origins == ("https://a.example", "https://b.example")
    assert settings.trusted_proxies == ("private",)
    assert (settings.ip_max_calls, settings.ip_calls_per_hour) == (0, 12)
    assert (settings.day_max_calls, settings.day_max_minutes) == (300, 600.0)
    assert settings.ws_max_bytes == 32_768


def test_a_star_switches_the_origin_check_off() -> None:
    # compose가 배포 도메인을 기본값으로 넣는다. 터널 같은 비상 주소로 열 때 끄는 값
    assert VoiceSettings.from_env({"PHONE_ALLOWED_ORIGINS": "*"},
                                  env_file=None).allowed_origins == ()


def test_gate_defaults_are_loose_and_the_daily_budget_is_off() -> None:
    settings = VoiceSettings.from_env({}, env_file=None)
    assert settings.allowed_origins == () and settings.trusted_proxies == ()
    assert (settings.ip_max_calls, settings.ip_calls_per_hour) == (4, 30)
    assert (settings.day_max_calls, settings.day_max_minutes) == (0, 0.0)
    assert settings.ws_max_bytes == 65_536


@pytest.mark.parametrize("name, value", [
    ("PHONE_IP_MAX_CALLS", "-1"), ("PHONE_DAY_MAX_CALLS", "many"),
    ("PHONE_TRUSTED_PROXIES", "not-a-network"), ("PHONE_ALLOWED_ORIGINS", "shop.example"),
])
def test_wrong_gate_values_stop_the_server_with_the_variable_name(name, value) -> None:
    with pytest.raises(SettingsError, match=name):
        VoiceSettings.from_env({name: value}, env_file=None)


def test_the_daily_budget_counts_real_days_not_the_fixed_store_clock(tmp_path) -> None:
    """`PHONE_NOW`로 가게 시각을 고정해도 하루 예산의 날짜는 실제 오늘이다 (6단계 배포 뒤 확인)."""
    import json as _json
    from datetime import datetime

    from agenthan.voice.ws import make_gate

    fixed = datetime.fromisoformat("2026-01-01T16:00:00+09:00")
    settings = VoiceSettings(nlu="fake", stt="fake", tts="fake", log_dir=tmp_path / "logs",
                             web_dist=tmp_path / "none", turn_model="off", now=fixed,
                             day_max_calls=5)
    gate = make_gate(settings)
    gate.release(gate.admit("203.0.113.9"))
    saved = _json.loads((tmp_path / "logs" / "phone-budget.json").read_text())
    assert saved["date"] == datetime.now(settings.tz).date().isoformat() != "2026-01-01"
