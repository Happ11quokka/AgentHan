"""서비스 앱 모델(HCX-007·005)만 서비스 키로 부르고, 나머지는 테스트 키를 쓴다 (2026-11-21)."""

import json

from agenthan.agent.review.clova import ClovaClient
from agenthan.core import llm


def keys(monkeypatch, service="svc", models=None):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "base")
    if service is None:
        monkeypatch.delenv("CLOVASTUDIO_SERVICE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("CLOVASTUDIO_SERVICE_API_KEY", service)
    if models is None:
        monkeypatch.delenv("CLOVASTUDIO_SERVICE_MODELS", raising=False)
    else:
        monkeypatch.setenv("CLOVASTUDIO_SERVICE_MODELS", models)


def test_service_models_use_service_key(monkeypatch):
    keys(monkeypatch)
    assert llm.api_key("HCX-007") == "svc"
    assert llm.api_key("HCX-005") == "svc"


def test_other_calls_keep_base_key(monkeypatch):
    keys(monkeypatch)
    # 서비스 키는 승인 안 된 모델·임베딩·튜닝 작업에 401 (11/21 실측)
    assert llm.api_key("HCX-DASH-002") == "base"
    assert llm.api_key(None) == "base"


def test_without_service_key_everything_uses_base(monkeypatch):
    keys(monkeypatch, service=None)
    assert llm.api_key("HCX-007") == "base"


def test_service_model_list_is_configurable(monkeypatch):
    keys(monkeypatch, models="HCX-007")
    assert llm.api_key("HCX-007") == "svc"
    assert llm.api_key("HCX-005") == "base"


def test_review_client_picks_key_per_model(monkeypatch):
    keys(monkeypatch)
    seen = []

    def transport(url, headers, body, timeout):
        seen.append(headers["Authorization"])
        result = {"message": {"role": "assistant", "content": "ok"}, "usage": {}}
        return 200, {}, json.dumps({"status": {"code": "20000"}, "result": result}).encode()

    client = ClovaClient(transport=transport, min_interval=0)
    client.chat("HCX-005", [{"role": "user", "content": "hi"}])
    client.chat("HCX-DASH-002", [{"role": "user", "content": "hi"}])
    assert seen == ["Bearer svc", "Bearer base"]


def test_review_client_explicit_key_wins(monkeypatch):
    keys(monkeypatch)
    seen = []

    def transport(url, headers, body, timeout):
        seen.append(headers["Authorization"])
        result = {"message": {"role": "assistant", "content": "ok"}, "usage": {}}
        return 200, {}, json.dumps({"status": {"code": "20000"}, "result": result}).encode()

    ClovaClient(api_key="mine", transport=transport, min_interval=0).chat(
        "HCX-005", [{"role": "user", "content": "hi"}]
    )
    assert seen == ["Bearer mine"]
