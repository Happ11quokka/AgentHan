import json
import traceback
from types import SimpleNamespace as NS

import pytest

from agenthan.agent import clova_chat as clova
from agenthan.tools.owner_tools import tool_schema


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)


class FakeClient:
    """OpenAI SDK shape: client.chat.completions.create(**kw) -> resp.choices[0].message."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw, ensure_ascii=False)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if hasattr(reply, "choices"):
            return reply
        return NS(choices=[NS(message=reply, finish_reason="stop")])


def sdk_call(id_, name, arguments):
    return NS(id=id_, type="function", function=NS(name=name, arguments=arguments))


def test_request_and_tool_result_roundtrip():
    args = json.dumps({"name": "연습 차", "price": 5000}, ensure_ascii=False)
    client = FakeClient(
        NS(role="assistant", content=None, tool_calls=[sdk_call("call-1", "upsert_menu", args)]),
        NS(role="assistant", content="등록했어요.", tool_calls=None),
    )
    tools = [tool_schema("menu")]
    messages = [{"role": "user", "content": "연습 차 5000원 메뉴 등록"}]
    message = clova.chat(messages, tools=tools, client=client)
    assert message == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "upsert_menu", "arguments": args},
            }
        ],
    }
    messages += [message, {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'}]
    assert clova.chat(messages, tools=[], client=client)["content"] == "등록했어요."
    first, second = client.requests
    assert first["model"] == "HCX-005" and first["tools"] == tools
    assert first["max_tokens"] >= 1024 and "response_format" not in first
    # CLOVA rejects tool_choice objects; never send it, and omit empty tools.
    assert "tool_choice" not in first and "tool_choice" not in second
    assert "tools" not in second
    assert second["messages"][-1]["tool_call_id"] == "call-1"


def test_dict_arguments_are_serialized():
    msg = clova.parse_message(
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c", "type": "function", "function": {"name": "f", "arguments": {"a": 1}}}
            ],
        }
    )
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'


@pytest.mark.parametrize("failure", ["sdk", "length", "message"])
def test_sanitized_errors(failure):
    secret = "fake-sensitive-test-chat-token"
    if failure == "sdk":
        reply = RuntimeError(secret)
    elif failure == "length":
        reply = NS(choices=[NS(message=NS(content=secret), finish_reason="length")])
    else:
        reply = NS(role=secret, content=secret)
    with pytest.raises(clova.ClovaChatError) as error:
        clova.chat([], tools=[], client=FakeClient(reply))
    assert secret not in "".join(traceback.format_exception(error.value))


def test_missing_key():
    with pytest.raises(clova.ClovaChatError, match="CLOVASTUDIO_API_KEY"):
        clova.chat([], tools=[])


def test_client_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-chat-key")
    monkeypatch.delenv("CLOVASTUDIO_BASE_URL", raising=False)
    client = clova.make_client()
    assert str(client.base_url).rstrip("/") == "https://clovastudio.stream.ntruss.com/v1/openai"
    assert client.max_retries == 8


@pytest.mark.parametrize(
    "message",
    [
        [],
        {"role": "user", "content": "bad"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "tool_calls": "bad"},
        {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
    ],
)
def test_invalid_message(message):
    with pytest.raises(clova.ClovaChatError):
        clova.parse_message(message)
