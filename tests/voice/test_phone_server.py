"""개발용 서버: `voice`만 띄우는 FastAPI 앱 (티켓 04). 빌드된 `web/dist`가 있으면 같이 낸다."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi.testclient import TestClient

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
