"""CLOVA Studio HCX 공용 클라이언트(PRD §2.1): OpenAI 호환 클라이언트와 모델별 요청 옵션.

세 파트(사장님 비서·가게 지식·전화 주문)가 같이 쓴다. 대화·도구 호출은 openai SDK로 OpenAI 호환
엔드포인트를 부른다. 이미지 입력(HCX-005 dataUri)처럼 호환 API로 확인하지 않은 기능은
`chat_v3()`로 네이티브 REST v3를 부른다(sdk.md 3장 계약). PRD 방향대로 옮길 때는 이 파일만 고친다.

CLOVA Studio 실측 제약 (2026-11-07, scripts/hcx_check.py):
- 요청당 system 메시지 1개만 받는다
- tool_choice 객체 지정은 400 → 보내지 않는다
- 도구 호출은 max_tokens 1024 이상
- 연속 호출은 429 → SDK가 재시도 (HCX_MAX_RETRIES, 기본 8)
- response_format(JSON 스키마)은 HCX-007만 받는다 (HCX-005·DASH는 400)

환경 변수: CLOVASTUDIO_API_KEY (필수), CLOVASTUDIO_BASE_URL, HCX_MAX_RETRIES (선택)
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_BASE_URL = "https://clovastudio.stream.ntruss.com/v1/openai"
DEFAULT_MAX_RETRIES = 8
TOOL_MAX_TOKENS = 1024


def make_client(*, max_retries: int | None = None, timeout: float | None = None):
    """OpenAI 호환 CLOVA Studio 클라이언트. 키가 없으면 KeyError("CLOVASTUDIO_API_KEY")."""
    import ssl

    from openai import DefaultHttpxClient, OpenAI  # 테스트에서 가짜 client를 넣도록 지연 import

    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if not key:
        raise KeyError("CLOVASTUDIO_API_KEY")
    # SDK 기본 HTTP 클라이언트는 truststore(macOS 키체인)로 인증서를 검증한다.
    # 샌드박스(Claude Code·Codex·geass)는 키체인 접근을 막아 SSL 오류(OSStatus -26276)가 난다.
    # 그래서 urllib(임베딩)과 같은 OpenSSL 기본 검증을 쓴다.
    kwargs = {"http_client": DefaultHttpxClient(verify=ssl.create_default_context())}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAI(
        api_key=key,
        base_url=os.getenv("CLOVASTUDIO_BASE_URL", DEFAULT_BASE_URL),
        # 429(호출 한도)·일시 오류는 SDK가 retry-after를 보고 기다렸다 재시도한다
        max_retries=(
            int(os.getenv("HCX_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))
            if max_retries is None
            else max_retries
        ),
        **kwargs,
    )


def request_options(model: str, max_tokens: int = 1024, json_schema: dict | None = None) -> dict:
    """모델별 요청 옵션.
    - HCX-005, HCX-DASH-002: max_tokens만. response_format을 보내면 400 → 프롬프트 + 검증으로 JSON
    - HCX-007: 추론 모델이라 max_completion_tokens. JSON 스키마와 추론은 함께 쓸 수 없어
      reasoning_effort=none. json_schema를 주면 Structured Outputs로 보낸다
    """
    if model.startswith("HCX-007"):
        opts = {"max_completion_tokens": max_tokens, "extra_body": {"reasoning_effort": "none"}}
        if json_schema:
            opts["response_format"] = {"type": "json_schema", "json_schema": json_schema}
        return opts
    return {"max_tokens": max_tokens}


def tool_call_options(model: str, max_tokens: int = TOOL_MAX_TOKENS) -> dict:
    """도구 호출 턴의 옵션: max_tokens 1024 이상, JSON 스키마 없음."""
    return request_options(model, max(max_tokens, TOOL_MAX_TOKENS))


# ---------------------------------------------------------------- 네이티브 REST v3

V3_HOST = "https://clovastudio.stream.ntruss.com"
V3_MAX_RETRIES = 2  # sdk.md 3장: 429·5xx는 최대 두 번
V3_MAX_RETRY_AFTER = 10  # 초. 이보다 길면 기다리지 않고 바로 실패
V3_TIMEOUT = 60


class ClovaV3Error(Exception):
    """서버 본문·키가 들어가지 않은, 화면에 보여도 되는 오류."""


def _retry_wait(attempt: int, retry_after: str | None) -> float | None:
    """다시 시도할 때 기다릴 초. 기다리면 안 되면 None."""
    if attempt >= V3_MAX_RETRIES:
        return None
    if retry_after and retry_after.strip().isdigit():
        wait = int(retry_after)
        return None if wait > V3_MAX_RETRY_AFTER else float(wait)
    return 0.5 * (attempt + 1)


def chat_v3(model: str, body: dict, *, timeout: float = V3_TIMEOUT) -> dict:
    """POST /v3/chat-completions/{model}. 성공(status.code 20000)이면 `result`를 돌려준다.

    body는 네이티브 필드 그대로다(messages, maxTokens, maxCompletionTokens, thinking,
    responseFormat 등). 429·5xx는 최대 두 번 재시도하고, Retry-After가 10초를 넘으면 바로 실패한다.
    서버 본문과 원래 예외는 밖으로 내보내지 않는다(키가 섞일 수 있다).
    """
    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if not key:
        raise ClovaV3Error("CLOVASTUDIO_API_KEY 환경변수를 설정해 주세요.")
    url = f"{V3_HOST}/v3/chat-completions/{model}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(V3_MAX_RETRIES + 1):
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        wait = None
        failure = "CLOVA 요청에 실패했습니다."
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            code = str(payload["status"]["code"])
            if code == "20000":
                return payload["result"]
            failure = f"CLOVA API 오류 (code {code[:5]})"
            if code[:3] == "429" or code[:1] == "5":
                wait = _retry_wait(attempt, None)
        except urllib.error.HTTPError as error:
            failure = f"CLOVA HTTP 오류 ({error.code})"
            if error.code == 429 or 500 <= error.code <= 599:
                wait = _retry_wait(attempt, error.headers.get("Retry-After"))
            error.close()
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
            failure = "CLOVA 응답 또는 연결이 올바르지 않습니다."
        if wait is not None:
            time.sleep(wait)
            continue
        raise ClovaV3Error(failure)
    raise ClovaV3Error("CLOVA 요청에 실패했습니다.")  # pragma: no cover


def image_content(data: bytes, mime: str) -> dict:
    """HCX-005 이미지 입력 항목(네이티브 dataUri). 턴당 1장."""
    import base64

    return {
        "type": "image_url",
        "dataUri": {"data": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")},
    }
