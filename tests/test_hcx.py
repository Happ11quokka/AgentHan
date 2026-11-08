"""공용 HCX 설정 (API 키 불필요)."""

import pytest

from agenthan.core import llm as hcx


def test_options_by_model():
    assert hcx.request_options("HCX-005", 512) == {"max_tokens": 512}
    assert hcx.request_options("HCX-DASH-002", json_schema={"name": "x"}) == {"max_tokens": 1024}
    o = hcx.request_options("HCX-007", 300, {"name": "x"})
    assert o["max_completion_tokens"] == 300 and "max_tokens" not in o
    assert o["response_format"] == {"type": "json_schema", "json_schema": {"name": "x"}}
    assert "response_format" not in hcx.request_options("HCX-007")


def test_tool_call_options_keep_minimum_tokens():
    assert hcx.tool_call_options("HCX-005", 256) == {"max_tokens": 1024}
    assert "response_format" not in hcx.tool_call_options("HCX-007")


def test_make_client(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    with pytest.raises(KeyError):
        hcx.make_client()
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-key")
    monkeypatch.setenv("HCX_MAX_RETRIES", "3")
    client = hcx.make_client()
    assert str(client.base_url).rstrip("/") == hcx.DEFAULT_BASE_URL and client.max_retries == 3
    assert hcx.make_client(max_retries=0).max_retries == 0
