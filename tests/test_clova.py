"""네이티브 v3 클라이언트: 요청 모양, 상태 코드, 재시도 규칙 (sdk.md 3장). 키·네트워크 불필요."""

import json

import pytest

from agenthan.agent.review.clova import ClovaClient, ClovaError, model_options

OK = {"status": {"code": "20000"}}


class Transport:
    """(HTTP 상태, 헤더, 본문 dict)를 차례로 돌려주는 가짜 전송."""

    def __init__(self, *responses):
        self.responses, self.sent = list(responses), []

    def __call__(self, url, headers, body, timeout):
        self.sent.append({"url": url, "headers": headers, "body": json.loads(body)})
        status, hdrs, data = self.responses.pop(0)
        return status, hdrs, json.dumps(data).encode()


def client(*responses, **kw):
    t = Transport(*responses)
    return ClovaClient(api_key="test-key", transport=t, min_interval=0, **kw), t


def msg(content="", **extra):
    return {**OK, "result": {"message": {"role": "assistant", "content": content, **extra}}}


def test_request_shape_and_tool_calls():
    tc = {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}
    c, t = client((200, {}, msg("", toolCalls=[tc])))
    out = c.chat("HCX-005", [{"role": "user", "content": "q"}], tools=[{"x": 1}], maxTokens=1024)
    sent = t.sent[0]
    assert sent["url"].endswith("/v3/chat-completions/HCX-005")
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["headers"]["X-NCP-CLOVASTUDIO-REQUEST-ID"]
    assert sent["body"]["maxTokens"] == 1024 and sent["body"]["tools"] == [{"x": 1}]
    assert "toolChoice" not in sent["body"]
    assert out["toolCalls"][0]["function"]["arguments"] == {"a": 1}  # 문자열이어도 dict로


def test_retry_on_429_then_success(monkeypatch):
    monkeypatch.setattr("agenthan.agent.review.clova.time.sleep", lambda s: None)
    c, t = client((429, {"Retry-After": "1"}, {}), (200, {}, msg("안녕")))
    assert c.text("HCX-005", [{"role": "user", "content": "q"}]) == "안녕"
    assert len(t.sent) == 2


def test_long_retry_after_fails_fast(monkeypatch):
    monkeypatch.setattr("agenthan.agent.review.clova.time.sleep", lambda s: None)
    c, t = client((429, {"Retry-After": "30"}, {}))
    with pytest.raises(ClovaError, match="HTTP 429"):
        c.text("HCX-005", [{"role": "user", "content": "q"}])
    assert len(t.sent) == 1


def test_retries_are_limited(monkeypatch):
    monkeypatch.setattr("agenthan.agent.review.clova.time.sleep", lambda s: None)
    c, t = client(*[(503, {}, {})] * 3)
    with pytest.raises(ClovaError, match="HTTP 503"):
        c.text("HCX-005", [{"role": "user", "content": "q"}])
    assert len(t.sent) == 3  # 첫 호출 + 재시도 2번


def test_body_status_and_truncation_checked():
    c, _ = client((200, {}, {"status": {"code": "40001"}, "result": {}}))
    with pytest.raises(ClovaError, match="40001"):
        c.text("HCX-005", [])
    c, _ = client((200, {}, {**OK, "result": {"finishReason": "length", "message": {}}}))
    with pytest.raises(ClovaError, match="잘렸"):
        c.text("HCX-005", [])


def test_key_never_leaks():
    c, _ = client((401, {}, {"error": "secret body"}))
    with pytest.raises(ClovaError) as e:
        c.text("HCX-005", [])
    assert "test-key" not in str(e.value) and "secret body" not in str(e.value)
    assert "test-key" not in repr(c)
    with pytest.raises(ClovaError, match="키 원문"):
        ClovaClient(api_key="").text("HCX-005", [])


def test_model_options():
    assert model_options("HCX-005", 512) == {"maxTokens": 512}
    o = model_options("HCX-007", 512, {"type": "object"})
    assert o == {
        "maxCompletionTokens": 512,
        "thinking": {"effort": "none"},
        "responseFormat": {"type": "json", "schema": {"type": "object"}},
    }
