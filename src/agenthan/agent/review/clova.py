"""CLOVA Studio 네이티브 REST v3 클라이언트 (팀 sdk.md 3장 '외부 API 계약'·부록 B-3을 따른다).

PRD §2.1은 세 파트가 같이 쓰는 공통 클라이언트(`core/llm.py`)를 두기로 했다. 그 파일이 생기기
전까지 리뷰 도구는 이 모듈을 쓰고, 생기면 그쪽으로 옮긴다. 그래서 agent.review 안의 다른 모듈을
import하지 않는다.

계약
- `POST {host}/v3/chat-completions/{model}`, `Authorization: Bearer <키>`, 요청 ID 헤더
- HTTP 상태를 먼저 보고, 본문의 `status.code == "20000"`일 때만 `result`를 읽는다
- 429·5xx는 최대 2번 재시도. `Retry-After`(초)가 10초를 넘으면 바로 오류
- 네트워크 예외는 그 호출을 끝낸다. 오류 메시지에 키나 서버 본문을 넣지 않는다
- 메시지는 네이티브 형식: assistant `toolCalls[]`, tool `toolCallId`
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from uuid import uuid4

DEFAULT_HOST = "https://clovastudio.stream.ntruss.com"
CHAT_PATH = "/v3/chat-completions/{model}"

# (url, headers, body, timeout) -> (HTTP 상태, 응답 헤더, 응답 본문)
Transport = Callable[[str, dict, bytes, float], tuple[int, dict, bytes]]


class ClovaError(RuntimeError):
    """화면에 그대로 보여도 되는 오류 (키·서버 본문 없음). code: CLOVA 상태 코드(숫자 문자열)만."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def _status_code(raw: bytes) -> str | None:
    """오류 본문에서 status.code만 꺼낸다 (숫자일 때만. 서버 메시지는 남기지 않는다)."""
    try:
        code = (json.loads(raw.decode("utf-8")).get("status") or {}).get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and code.isdecimal() else None


def _urllib_transport(url: str, headers: dict, body: bytes, timeout: float):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (고정 호스트)
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


def model_options(model: str, max_tokens: int = 1024, schema: dict | None = None) -> dict:
    """모델별 생성 옵션 (sdk.md B-2·B-3).
    - HCX-007: 출력 상한은 maxCompletionTokens. 도구 호출·Structured Outputs와 추론을 같이 못 쓰므로
      thinking.effort=none. JSON 스키마를 주면 responseFormat으로 보낸다
    - 그 밖(HCX-005, HCX-DASH-002): maxTokens. Structured Outputs는 007만 지원하므로 JSON은
      프롬프트로 요청하고 앱에서 검증한다
    """
    if model.startswith("HCX-007"):
        opts: dict = {"maxCompletionTokens": max_tokens, "thinking": {"effort": "none"}}
        if schema:
            opts["responseFormat"] = {"type": "json", "schema": schema}
        return opts
    return {"maxTokens": max_tokens}


def _arguments(value) -> dict:
    """toolCalls[].function.arguments를 dict로 (v3는 객체, 문자열로 오는 경우도 받는다)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class ClovaClient:
    def __init__(
        self,
        api_key: str | None = None,
        host: str | None = None,
        timeout: float = 45.0,
        max_retries: int | None = None,
        min_interval: float | None = None,
        transport: Transport | None = None,
        on_usage: Callable[[dict], None] | None = None,
    ):
        """on_usage: 응답마다 토큰 수를 받는 함수 (평가에서 리뷰 위임 토큰을 모을 때).
        {"prompt_tokens", "completion_tokens", "total_tokens"} 형식으로 넘긴다."""
        self._key = api_key if api_key is not None else os.getenv("CLOVASTUDIO_API_KEY", "")
        self.host = (host or os.getenv("CLOVASTUDIO_HOST") or DEFAULT_HOST).rstrip("/")
        self.timeout = timeout
        self.max_retries = (
            int(os.getenv("CLOVA_MAX_RETRIES", "2")) if max_retries is None else max_retries
        )
        # 테스트 키는 분당 호출 한도가 낮다. 호출 사이 최소 간격(초)으로 429를 미리 피한다
        self.min_interval = (
            float(os.getenv("HCX_MIN_INTERVAL", "1.0")) if min_interval is None else min_interval
        )
        self._last = 0.0
        self._send = transport or _urllib_transport
        self.on_usage = on_usage

    def __repr__(self) -> str:  # 키를 로그에 남기지 않는다
        return f"ClovaClient(host={self.host!r})"

    # ------------------------------------------------------------------ 공통
    def _post(self, path: str, payload: dict) -> dict:
        if not self._key or self._key.startswith("Bearer "):
            raise ClovaError("CLOVASTUDIO_API_KEY에 키 원문을 넣어 주세요.")
        request_id = str(uuid4())
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                status, resp_headers, raw = self._send(
                    self.host + path, headers, body, self.timeout
                )
            except OSError:
                raise ClovaError(f"CLOVA 연결 오류 ({request_id})") from None
            if status == 429 or 500 <= status < 600:
                after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
                delay = float(after) if after and after.isdecimal() else 2**attempt
                if attempt < self.max_retries and delay <= 10:
                    time.sleep(delay)
                    continue
            if not 200 <= status < 300:
                code = _status_code(raw)  # 예: 40009 Unsupported function (2026-11-08 실측)
                label = f" {code}" if code else ""
                raise ClovaError(f"CLOVA HTTP {status}{label} ({request_id})", code=code)
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise ClovaError(f"CLOVA JSON 응답 오류 ({request_id})") from None
            code = (data.get("status") or {}).get("code") if isinstance(data, dict) else None
            result = data.get("result") if isinstance(data, dict) else None
            if code != "20000" or not isinstance(result, dict):
                label = code if isinstance(code, str) and code.isdecimal() else "unknown"
                raise ClovaError(
                    f"CLOVA 응답 오류 {label} ({request_id})",
                    code=label if label != "unknown" else None,
                )
            return result
        raise ClovaError(f"CLOVA 재시도 초과 ({request_id})")  # pragma: no cover

    # ------------------------------------------------------------------ 채팅
    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        temperature: float | None = None,
        **params,
    ) -> dict:
        """채팅 v3 한 번. 돌려주는 값은 assistant 메시지 dict:
        {"role": "assistant", "content": str, "toolCalls": [{"id", "type", "function": {"name",
        "arguments": dict}}]} (도구 호출이 없으면 toolCalls 없음).
        params에는 model_options()의 값(maxTokens 등)을 그대로 넣는다."""
        payload: dict = {"messages": messages, **params}
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["toolChoice"] = tool_choice
        result = self._post(CHAT_PATH.format(model=model), payload)
        self._report_usage(result.get("usage"))  # 잘린 응답도 토큰은 쓴다
        if result.get("finishReason") == "length":
            raise ClovaError("CLOVA 응답이 출력 한도에서 잘렸습니다.")
        msg = result.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            raise ClovaError("CLOVA 응답에 assistant 메시지가 없습니다.")
        out = {"role": "assistant", "content": msg.get("content") or ""}
        calls = []
        for c in msg.get("toolCalls") or []:
            fn = c.get("function") or {}
            if not isinstance(fn.get("name"), str) or not c.get("id"):
                raise ClovaError("CLOVA 도구 호출 형식이 올바르지 않습니다.")
            calls.append(
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": fn["name"], "arguments": _arguments(fn.get("arguments"))},
                }
            )
        if calls:
            out["toolCalls"] = calls
        return out

    def _report_usage(self, usage) -> None:
        if self.on_usage is None or not isinstance(usage, dict):
            return
        counts = {
            "prompt_tokens": usage.get("promptTokens"),
            "completion_tokens": usage.get("completionTokens"),
            "total_tokens": usage.get("totalTokens"),
        }
        if isinstance(counts["total_tokens"], int):
            self.on_usage(counts)

    def text(self, model: str, messages: list[dict], **kw) -> str:
        """도구 없이 답변 본문만."""
        return self.chat(model, messages, **kw)["content"]
