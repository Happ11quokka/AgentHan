"""HCX chat over CLOVA Studio's OpenAI-compatible API, shared with review_tools.

The owner assistant and review_tools use the same client: the openai SDK pointed at
CLOVA Studio's /v1/openai endpoint, with model options from
naver_review_clf.backends.hcx.request_options(). Messages use the OpenAI format
(tool_calls, tool_call_id).

CLOVA constraints measured by review_tools (2026-11-07): one system message per
request, a tool_choice object is rejected with 400, tool calling needs max_tokens >=
1024, and consecutive calls hit 429 (the SDK retries; HCX_MAX_RETRIES, default 8).
"""

import json
import os

from naver_review_clf.backends.hcx import DEFAULT_BASE_URL, request_options

CHAT_MODEL = "HCX-005"
MAX_TOKENS = 1024
REQUEST_TIMEOUT = 45
TEMPERATURE = 0.2


class ClovaChatError(Exception):
    """An error safe to display without a server body or credentials."""


def make_client():
    """OpenAI-compatible CLOVA Studio client. Reads only CLOVASTUDIO_* / HCX_* env vars."""
    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if not key:
        raise ClovaChatError("CLOVASTUDIO_API_KEY 환경변수를 설정해 주세요.")
    from openai import OpenAI

    return OpenAI(
        api_key=key,
        base_url=os.getenv("CLOVASTUDIO_BASE_URL", DEFAULT_BASE_URL),
        max_retries=int(os.getenv("HCX_MAX_RETRIES", "8")),
        timeout=REQUEST_TIMEOUT,
    )


def chat_options(model: str = CHAT_MODEL) -> dict:
    """Request options for a tool-calling turn (same rule as review_tools.agent.run_turn)."""
    opts = request_options(model, MAX_TOKENS)
    opts.pop("response_format", None)  # tool calls and JSON schema are not used together
    return opts


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
    kwargs.update(chat_options(model))
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
