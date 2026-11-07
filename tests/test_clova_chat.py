import http.client
import io
import json
import traceback
import urllib.error
import uuid

import pytest

from agenthan.agent import clova_chat as clova
from agenthan.tools.owner_tools import tool_schema


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        pytest.fail("Chat unit tests must not use the network")

    monkeypatch.setattr(clova.urllib.request, "urlopen", no_network)


def response(code="20000", message=None, finish_reason="stop"):
    return io.BytesIO(
        json.dumps(
            {
                "status": {"code": code},
                "result": {
                    "message": message or {"role": "assistant", "content": "안녕하세요."},
                    "finishReason": finish_reason,
                },
            }
        ).encode()
    )


def test_request_and_tool_result_roundtrip(monkeypatch):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-chat-key")
    requests = []
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "upsert_menu", "arguments": {"name": "연습 차", "price": 5000}},
    }

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 45
        if len(requests) == 1:
            return response(message={"role": "assistant", "toolCalls": [tool_call]})
        return response()

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    tools = [tool_schema("menu")]
    messages = [{"role": "user", "content": "연습 차 5000원 메뉴 등록"}]
    message = clova.chat(messages, tools=tools)
    assert message == {"role": "assistant", "content": "", "toolCalls": [tool_call]}
    messages += [message, {"role": "tool", "toolCallId": "call-1", "content": '{"ok":true}'}]
    clova.chat(messages, tools=[], tool_choice="none")
    assert requests[0].full_url == clova.ENDPOINT
    assert requests[0].get_method() == "POST"
    payload = json.loads(requests[0].data)
    assert payload["tools"] == tools
    assert payload["toolChoice"] == "auto"
    assert payload["maxTokens"] >= 1024
    second = json.loads(requests[1].data)
    assert second["messages"][-1]["toolCallId"] == "call-1"
    assert second["tools"] == [] and second["toolChoice"] == "none"
    ids = []
    for request in requests:
        headers = {k.lower(): v for k, v in request.header_items()}
        assert headers["authorization"] == "Bearer fake-chat-key"
        assert headers["content-type"] == "application/json"
        ids.append(headers["x-ncp-clovastudio-request-id"])
        assert uuid.UUID(ids[-1]).version == 4
    assert len(set(ids)) == 2


@pytest.mark.parametrize("code", [429, 500, 503, 599, 400, 401, 403])
@pytest.mark.parametrize("api_status", [True, False])
def test_retry_policy(monkeypatch, code, api_status):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-chat-key")
    requests, sleeps = [], []

    def urlopen(request, timeout):
        requests.append(request)
        if api_status:
            return response(str(code) + "01")
        raise urllib.error.HTTPError(clova.ENDPOINT, code, "hidden body", {}, io.BytesIO())

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(clova.time, "sleep", sleeps.append)
    with pytest.raises(clova.ClovaChatError):
        clova.chat([], tools=[])
    retryable = code == 429 or code >= 500
    assert len(requests) == (3 if retryable else 1)
    assert sleeps == ([0.25, 0.5] if retryable else [])


def test_retry_then_success(monkeypatch):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-chat-key")
    replies = iter([response("42900"), response("50300"), response()])
    monkeypatch.setattr(clova.urllib.request, "urlopen", lambda *a, **kw: next(replies))
    monkeypatch.setattr(clova.time, "sleep", lambda _: None)
    assert clova.chat([], tools=[])["content"] == "안녕하세요."


@pytest.mark.parametrize(
    "failure", ["http", "protocol", "transport", "json", "schema", "status", "message", "length"]
)
def test_sanitized_errors(monkeypatch, failure):
    secret = "fake-sensitive-test-chat-token"
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", secret)

    def urlopen(*args, **kwargs):
        if failure == "http":
            raise urllib.error.HTTPError(secret, 401, secret, {}, io.BytesIO(secret.encode()))
        if failure == "protocol":
            raise http.client.IncompleteRead(secret.encode())
        if failure == "transport":
            raise urllib.error.URLError(secret)
        if failure == "json":
            return io.BytesIO(secret.encode())
        if failure == "schema":
            return io.BytesIO(json.dumps({secret: secret}).encode())
        if failure == "status":
            return response(code=secret)
        if failure == "message":
            return response(message={"role": secret, "content": secret})
        return response(finish_reason="length")

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    with pytest.raises(clova.ClovaChatError) as error:
        clova.chat([], tools=[])
    assert secret not in "".join(traceback.format_exception(error.value))


def test_missing_key():
    with pytest.raises(clova.ClovaChatError, match="CLOVASTUDIO_API_KEY"):
        clova.chat([], tools=[])


@pytest.mark.parametrize(
    "message",
    [
        [],
        {"role": "user", "content": "bad"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "toolCalls": "bad"},
        {"role": "assistant", "toolCalls": [{"id": "call-1"}]},
    ],
)
def test_invalid_message(message):
    with pytest.raises(clova.ClovaChatError):
        clova.parse_message(message)
