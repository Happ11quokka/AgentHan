"""발표 서버(agenthan.serve): 전화 앱에 사장님 비서 API를 붙인다."""

from fastapi.testclient import TestClient

from agenthan.serve import create_app
from agenthan.tools import image_studio
from agenthan.voice.settings import VoiceSettings


def _settings(tmp_path, **env):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    return VoiceSettings(nlu="fake", stt="fake", tts="fake", log_dir=tmp_path / "logs",
                         web_dist=dist, env={"AGENTHAN_SESSIONS_DIR": str(tmp_path / "s"), **env})


def _missing_chat(app):
    return TestClient(app).get("/assistant/sessions/nope/place")


def test_assistant_routes_ride_on_the_phone_app(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    assert client.get("/healthz").json()["ok"] is True
    # 비서 API가 화면(index.html) 대신 답한다: 없는 대화는 비서의 404
    missing = _missing_chat(app)
    assert missing.status_code == 404
    assert missing.json() == {"detail": "없는 대화입니다."}
    # 화면 경로는 그대로 index.html
    page = client.get("/phone", headers={"accept": "text/html"})
    assert page.status_code == 200
    assert "root" in page.text


def test_assistant_can_be_left_off(tmp_path):
    app = create_app(_settings(tmp_path, AGENTHAN_ASSISTANT="off"))
    assert _missing_chat(app).json() != {"detail": "없는 대화입니다."}


def test_the_cutout_model_is_warmed_when_the_assistant_starts(tmp_path, monkeypatch):
    """첫 메뉴판 사진이 rembg import(빈 numba 캐시면 서버에서 34.5초)를 떠안지 않게
    앱이 뜰 때 미리 읽는다."""
    calls = []
    monkeypatch.setattr(image_studio, "warm_in_background", lambda: calls.append("warm"))
    create_app(_settings(tmp_path))
    assert calls == ["warm"]
    (tmp_path / "off").mkdir()
    create_app(_settings(tmp_path / "off", AGENTHAN_ASSISTANT="off"))
    assert calls == ["warm"]  # 비서가 없으면 메뉴 사진도 없다
