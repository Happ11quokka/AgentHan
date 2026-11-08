"""CLOVA Studio HCX 공용 클라이언트(PRD §2.1): OpenAI 호환 클라이언트와 모델별 요청 옵션.

세 파트(사장님 비서·가게 지식·전화 주문)가 같이 쓴다. 지금은 openai SDK로 OpenAI 호환
엔드포인트를 부른다. PRD 방향(sdk.md 네이티브 REST v3)으로 바꿀 때는 이 파일만 고친다.

CLOVA Studio 실측 제약 (2026-11-07, scripts/hcx_check.py):
- 요청당 system 메시지 1개만 받는다
- tool_choice 객체 지정은 400 → 보내지 않는다
- 도구 호출은 max_tokens 1024 이상
- 연속 호출은 429 → SDK가 재시도 (HCX_MAX_RETRIES, 기본 8)
- response_format(JSON 스키마)은 HCX-007만 받는다 (HCX-005·DASH는 400)

환경 변수: CLOVASTUDIO_API_KEY (필수), CLOVASTUDIO_BASE_URL, HCX_MAX_RETRIES (선택)
"""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://clovastudio.stream.ntruss.com/v1/openai"
DEFAULT_MAX_RETRIES = 8
TOOL_MAX_TOKENS = 1024


def make_client(*, max_retries: int | None = None, timeout: float | None = None):
    """OpenAI 호환 CLOVA Studio 클라이언트. 키가 없으면 KeyError("CLOVASTUDIO_API_KEY")."""
    from openai import OpenAI  # 테스트에서 가짜 client를 넣을 수 있도록 지연 import

    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if not key:
        raise KeyError("CLOVASTUDIO_API_KEY")
    kwargs = {}
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
