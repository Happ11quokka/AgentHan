"""HCX-005 function calling with a standard-library HTTP client."""

import http.client
import json
import os
import time
import urllib.error
import urllib.request
import uuid

ENDPOINT = "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-005"
MAX_RETRIES = 2
REQUEST_TIMEOUT = 45
RETRY_DELAY = 0.25


class ClovaChatError(Exception):
    """An error safe to display without a server body or credentials."""


def parse_message(value: object) -> dict:
    """Keep only the HCX assistant message fields needed for the next request."""
    if not isinstance(value, dict) or value.get("role") != "assistant":
        raise ClovaChatError("CLOVA 대화 응답 형식이 올바르지 않습니다.")
    content = value.get("content") or ""
    calls = value.get("toolCalls", [])
    if not isinstance(content, str) or not isinstance(calls, list):
        raise ClovaChatError("CLOVA 대화 응답 형식이 올바르지 않습니다.")
    parsed = {"role": "assistant", "content": content}
    ids = set()
    for call in calls:
        if (
            not isinstance(call, dict)
            or call.get("type") != "function"
            or not isinstance(call.get("id"), str)
            or not call["id"].strip()
            or call["id"] in ids
            or not isinstance(call.get("function"), dict)
            or not isinstance(call["function"].get("name"), str)
        ):
            raise ClovaChatError("CLOVA 도구 호출 형식이 올바르지 않습니다.")
        ids.add(call["id"])
    if calls:
        parsed["toolCalls"] = calls
    elif not content.strip():
        raise ClovaChatError("CLOVA 대화 응답이 비어 있습니다.")
    return parsed


def _retryable(code: int) -> bool:
    return code == 429 or 500 <= code <= 599


def chat(messages: list[dict], *, tools: list[dict], tool_choice: str = "auto") -> dict:
    """Return result.message; retry HTTP/API 429 and 5xx at most twice."""
    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if not key:
        raise ClovaChatError("CLOVASTUDIO_API_KEY 환경변수를 설정해 주세요.")
    body = json.dumps(
        {
            "messages": messages,
            "tools": tools,
            "toolChoice": tool_choice,
            "maxTokens": 1024,
            "temperature": 0.2,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    for attempt in range(MAX_RETRIES + 1):
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        retry = False
        failure = "CLOVA 대화 요청에 실패했습니다."
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.load(response)
            code = payload["status"]["code"]
            if code == "20000":
                result = payload["result"]
                if result.get("finishReason") == "length":
                    raise ClovaChatError("CLOVA 대화 응답이 길이 제한으로 중단됐습니다.")
                return parse_message(result["message"])
            retry = isinstance(code, str) and code[:3].isdigit() and _retryable(int(code[:3]))
            failure = "CLOVA 대화 API가 오류를 반환했습니다."
        except urllib.error.HTTPError as error:
            retry = _retryable(error.code)
            error.close()
            failure = "CLOVA 대화 HTTP 요청에 실패했습니다."
        except (
            OSError,
            http.client.HTTPException,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            ClovaChatError,
        ):
            failure = "CLOVA 대화 응답 또는 연결을 확인해 주세요."
        if retry and attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY * (attempt + 1))
            continue
        # Outside except: even the exception context must not expose a body/key.
        raise ClovaChatError(failure)
    raise ClovaChatError("CLOVA 대화 요청에 실패했습니다.")  # pragma: no cover
