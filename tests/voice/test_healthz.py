"""배포 안전 (6단계): `/healthz`, 점검 중 상태, 서버가 내려갈 때 통화 중인 손님에게 알리기."""

from __future__ import annotations

import json
import signal
from typing import Any

from fastapi.testclient import TestClient

from agenthan.voice import server
from agenthan.voice.gate import MSG_RESTART
from agenthan.voice.server import create_app
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.ws import WS_PATH


def _settings(tmp_path, **kw: Any) -> VoiceSettings:
    return VoiceSettings(log_dir=tmp_path / "logs", web_dist=tmp_path / "none", turn_model="off",
                         **kw)


def _ready(ws: Any) -> None:
    ws.send_text(json.dumps({"type": "call.start", "turn": 0}))
    while json.loads(ws.receive_text())["type"] != "call.ready":
        pass


def test_healthz_reports_calls_engines_and_version(tmp_path) -> None:
    settings = _settings(tmp_path, env={"PHONE_VERSION": "abc1234"})
    with TestClient(create_app(settings)) as client:
        idle = client.get("/healthz").json()
        assert idle == {"ok": True, "active": 0, "draining": False, "version": "abc1234",
                        "engines": {"nlu": "fake", "stt": "fake", "tts": "fake"}}
        with client.websocket_connect(WS_PATH) as ws:
            _ready(ws)
            assert client.get("/healthz").json()["active"] == 1
        assert client.get("/healthz").json()["active"] == 0


def test_healthz_stays_healthy_while_draining(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        (tmp_path / "logs").mkdir(exist_ok=True)
        (tmp_path / "logs" / "DRAIN").write_text("")
        reply = client.get("/healthz")
        assert reply.status_code == 200  # 점검 중이어도 컨테이너는 건강하다
        assert reply.json()["draining"] is True


def test_healthz_shows_budget_and_disk_only_with_the_owner_key(tmp_path) -> None:
    settings = _settings(tmp_path, owner_key="s3cret", day_max_calls=50)
    with TestClient(create_app(settings)) as client:
        assert "day" not in client.get("/healthz").json()
        assert "day" not in client.get("/healthz?key=wrong").json()
        detail = client.get("/healthz?key=s3cret").json()
        assert detail["day"] == {"calls": 0, "minutes": 0.0}
        assert set(detail) >= {"disk_pct", "threads", "hcx"}
        assert "s3cret" not in json.dumps(detail)


def test_version_falls_back_to_the_package_version(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/healthz").json()["version"]


def test_callers_on_the_line_are_told_when_the_server_goes_down(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client, client.websocket_connect(WS_PATH) as ws:
        _ready(ws)
        told = client.portal.call(app.state.gate.farewell)
        assert told == 1
        while True:
            event = json.loads(ws.receive_text())
            if event["type"] == "error":
                break
        assert (event["code"], event["retry"], event["message"]) == ("restarting", True,
                                                                       MSG_RESTART)
        assert client.get("/healthz").json()["draining"] is True
        with client.websocket_connect(WS_PATH) as late:  # 내려가는 중에는 새 통화를 받지 않는다
            assert json.loads(late.receive_text())["code"] == "maintenance"


def test_sigterm_tells_callers_first_and_then_lets_the_server_stop(tmp_path, monkeypatch) -> None:
    installed: dict[int, Any] = {}
    stopped: list[int] = []

    def previous(sig: int, frame: Any) -> None:
        stopped.append(sig)

    monkeypatch.setattr(signal, "getsignal", lambda sig: previous)
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.__setitem__(sig, handler))
    monkeypatch.setattr(server, "_in_main_thread", lambda: True)
    monkeypatch.setattr(server, "SHUTDOWN_NOTICE_S", 0.01)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        assert signal.SIGTERM in installed
        with client.websocket_connect(WS_PATH) as ws:
            _ready(ws)
            installed[signal.SIGTERM](signal.SIGTERM, None)
            while json.loads(ws.receive_text())["type"] != "error":
                pass
            for _ in range(200):
                if stopped:
                    break
                client.get("/healthz")
            assert stopped == [signal.SIGTERM]  # 알린 뒤에 원래 처리기(uvicorn 종료)를 부른다
            installed[signal.SIGTERM](signal.SIGTERM, None)  # 두 번째 신호는 바로 넘긴다
            assert stopped == [signal.SIGTERM, signal.SIGTERM]


def test_the_server_gives_shutdown_a_time_limit(tmp_path, monkeypatch) -> None:
    import uvicorn

    seen: dict = {}
    monkeypatch.chdir(tmp_path)  # 저장소 루트의 .env를 읽지 않게
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    server.main([])
    assert 0 < seen["timeout_graceful_shutdown"] <= 10


def test_healthz_survives_a_non_ascii_key(tmp_path) -> None:
    """비ASCII 키로 물어도 500이 아니다 (6단계 리뷰 6번)."""
    settings = _settings(tmp_path, owner_key="비밀-토큰")
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz", params={"key": "한"}).json().keys() == {
            "ok", "active", "draining", "version", "engines"}
        assert "day" in client.get("/healthz", params={"key": "비밀-토큰"}).json()
