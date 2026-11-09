"""개발용 서버: `voice`만 띄우는 FastAPI 앱 (티켓 04). 빌드된 `web/dist`가 있으면 같이 낸다."""

from __future__ import annotations

import json
from datetime import datetime

import httpx
from fastapi.testclient import TestClient

from agenthan.voice import server
from agenthan.voice.owner.papago import PapagoTranslator
from agenthan.voice.server import create_app
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.ws import WS_PATH

NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")


def _settings(tmp_path, **kw) -> VoiceSettings:
    return VoiceSettings(log_dir=tmp_path / "logs", now=NOW, web_dist=tmp_path / "dist", **kw)


def test_dev_server_greets_with_the_sample_store_without_any_key(tmp_path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_text(json.dumps({"type": "call.start", "turn": 0}))
        while True:
            message = ws.receive()
            if message.get("text") is None:
                continue
            event = json.loads(message["text"])
            if event["type"] == "bot.say":
                break
    assert event["text"] == "네, 샘플 러시안 베이커리입니다."


def test_dev_server_serves_the_web_build_and_falls_back_to_index_for_screens(tmp_path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>통화</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client = TestClient(create_app(_settings(tmp_path)))

    assert "통화" in client.get("/").text
    assert client.get("/assets/app.js").text == "console.log(1)"
    phone = client.get("/phone", headers={"accept": "text/html"})
    assert phone.status_code == 200 and "통화" in phone.text


def test_dev_server_without_a_web_build_still_starts(tmp_path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))
    assert client.get("/").status_code == 404


def test_the_server_warms_papago_up_at_start_and_closes_it_at_stop(tmp_path, monkeypatch) -> None:
    """티켓 12: 시작 때 Papago에 한 번 보내 연결을 열고(유휴 연결 유지는 설정값), 끝날 때 닫는다."""
    sent: list[str] = []

    def papago_api(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"message": {"result": {"translatedText": "да"}}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(papago_api))
    built: dict[str, float] = {}

    def fake_papago_from_env(env, *, timeout, keepalive):
        built.update(timeout=timeout, keepalive=keepalive)
        return PapagoTranslator("id", "secret", http=http)

    monkeypatch.setattr(server, "papago_from_env", fake_papago_from_env)
    app = create_app(_settings(tmp_path, translate_keepalive_s=45.0))

    with TestClient(app):
        assert len(sent) == 1
        assert not http.is_closed

    assert http.is_closed
    assert built == {"timeout": 3.0, "keepalive": 45.0}


def test_a_fixed_store_time_starts_a_store_clock_that_keeps_running(tmp_path) -> None:
    # 리뷰 C2: `PHONE_NOW` 시연에서 시계가 멈춰 있으면 질문 카드 마감이 화면 카운트다운과 어긋난다
    # (카드가 뜨자마자 "시간 지남"). 그 시각에서 시작해 실제 시간만큼 간다
    from agenthan.voice.ws import store_clock

    ticks = iter([100.0, 100.0, 107.5])
    clock = store_clock(_settings(tmp_path), monotonic=lambda: next(ticks))
    assert clock.now() == NOW
    assert clock.now().isoformat() == "2026-11-08T16:00:07.500000+09:00"
