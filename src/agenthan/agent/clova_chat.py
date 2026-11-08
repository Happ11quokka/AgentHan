"""Owner-assistant chat turn over the shared HCX client (see the hcx package).

Messages use the OpenAI format (tool_calls, tool_call_id). tool_choice is never sent
and errors are sanitized so a server body or key is never shown.
"""

import json
import os

from agenthan.core import llm as hcx

CHAT_MODEL = "HCX-005"
REQUEST_TIMEOUT = 45
TEMPERATURE = 0.2


class ClovaChatError(Exception):
    """An error safe to display without a server body or credentials."""


def make_client():
    """hcx.make_client() with a sanitized error when the key is missing."""
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        raise ClovaChatError("CLOVASTUDIO_API_KEY 환경변수를 설정해 주세요.")
    return hcx.make_client(timeout=REQUEST_TIMEOUT)


def _get(value: object, key: str):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def parse_message(value: object) -> dict:
    """Normalize an SDK message or dict to {"role", "content"[, "tool_calls"]}."""
    if value is None or isinstance(value, list | str):
        raise ClovaChatError("CLOVA 대화 응답 형식이 올바르지 않습니다.")
    role = _get(value, "role") or "assistant"
    content = _get(value, "content") or ""
    calls = _get(value, "tool_calls") or []
    if role != "assistant" or not isinstance(content, str) or not isinstance(calls, list):
        raise ClovaChatError("CLOVA 대화 응답 형식이 올바르지 않습니다.")
    parsed = {"role": "assistant", "content": content}
    out, ids = [], set()
    for call in calls:
        call_id = _get(call, "id")
        fn = _get(call, "function")
        name = _get(fn, "name") if fn is not None else None
        if (
            not isinstance(call_id, str)
            or not call_id.strip()
            or call_id in ids
            or (_get(call, "type") or "function") != "function"
            or not isinstance(name, str)
        ):
            raise ClovaChatError("CLOVA 도구 호출 형식이 올바르지 않습니다.")
        ids.add(call_id)
        arguments = _get(fn, "arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, ensure_ascii=False)
        out.append(
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        )
    if out:
        parsed["tool_calls"] = out
    elif not content.strip():
        raise ClovaChatError("CLOVA 대화 응답이 비어 있습니다.")
    return parsed


def chat(messages: list[dict], *, tools: list[dict], client=None, model: str = CHAT_MODEL) -> dict:
    """One chat completion. Tools are sent only when non-empty; tool_choice is never sent."""
    client = client or make_client()
    kwargs = {"model": model, "messages": messages, "temperature": TEMPERATURE}
    kwargs.update(hcx.tool_call_options(model))
    if tools:
        kwargs["tools"] = tools
    failure = None
    try:
        choice = client.chat.completions.create(**kwargs).choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            failure = "CLOVA 대화 응답이 길이 제한으로 중단됐습니다."
        else:
            return parse_message(choice.message)
    except ClovaChatError as error:
        failure = str(error)
    except Exception:  # noqa: BLE001 - SDK/transport errors may carry a body or key
        failure = "CLOVA 대화 요청에 실패했습니다."
    # Outside except: even the exception context must not expose a body/key.
    raise ClovaChatError(failure)
