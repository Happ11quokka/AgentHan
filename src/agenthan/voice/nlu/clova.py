"""CLOVA Studio Chat Completions v3 최소 클라이언트 (HcxNlu 전용).

계약은 `docs/sdk.md` §3을 따른다.
- `POST {CLOVA}/v3/chat-completions/{model}`, Bearer 인증, 요청마다 `X-NCP-CLOVASTUDIO-REQUEST-ID`
- HTTP 상태와 본문의 `status.code == "20000"`을 각각 검사한다
- 429·5xx는 최대 두 번 재시도한다. 초 단위 `Retry-After`가 10초를 넘으면 바로 오류다
- 네트워크 예외는 그 호출을 끝낸다

키는 오류 메시지·repr·로그에 남기지 않는다. 서버가 준 쿠키는 요청마다 비운다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

CLOVA = "https://clovastudio.stream.ntruss.com"
KEY_ENV = "CLOVASTUDIO_API_KEY"
_MAX_RETRIES = 2
_MAX_WAIT = 10.0


class ClovaError(Exception):
    """CLOVA 호출 실패. `code`는 본문의 `status.code`(예: "40009") 또는 "http-429" 같은 꼬리표."""

    def __init__(self, message: str, *, code: str = "", request_id: str = "") -> None:
        super().__init__(f"{message} ({request_id})" if request_id else message)
        self.code = code
        self.request_id = request_id


def load_api_key(env_file: Path | None = Path(".env")) -> str:
    """환경 변수 → (없으면) `.env` 파일의 `CLOVASTUDIO_API_KEY`. 못 찾으면 ""."""
    if key := os.environ.get(KEY_ENV, "").strip():
        return key
    if env_file is not None and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == KEY_ENV:
                return value.strip().strip("'\"")
    return ""


class ClovaClient:
    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.Client | None = None,
        timeout: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or api_key.startswith("Bearer "):
            raise ValueError(f"{KEY_ENV}에 'Bearer ' 없이 키 원문을 넣어 주세요.")
        self._key = api_key
        self._http = http or httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0))
        self._sleep = sleep

    def __repr__(self) -> str:
        return "ClovaClient(api_key=***)"

    def chat(self, model: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """chat-completions 한 번. 성공하면 본문의 `result`를 돌려준다."""
        body = self._post(f"{CLOVA}/v3/chat-completions/{model}", payload)
        status = body.get("status")
        code = status.get("code") if isinstance(status, dict) else None
        result = body.get("result")
        if code != "20000" or not isinstance(result, dict):
            raise ClovaError(f"CLOVA 응답 오류 {code}", code=str(code or ""))
        return result

    def _post(self, url: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(uuid4())
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id,
        }
        for attempt in range(_MAX_RETRIES + 1):
            # CLOVA가 주는 세션 쿠키(JSESSIONID·XSRF)는 쓸 데가 없다. 다음 요청에 돌려보내면
            # 로그·녹화본에 남으니 매번 비운다.
            self._http.cookies.clear()
            try:
                response = self._http.post(url, headers=headers, json=dict(payload))
            except httpx.HTTPError:
                raise ClovaError("CLOVA 연결 오류", code="network", request_id=request_id) from None
            status = response.status_code
            if status == 429 or 500 <= status < 600:
                after = response.headers.get("Retry-After", "")
                wait = float(after) if after.isdecimal() else float(2**attempt)
                if attempt < _MAX_RETRIES and wait <= _MAX_WAIT:
                    self._sleep(wait)
                    continue
            body = _json_or_none(response)
            if not 200 <= status < 300:
                code = _status_code(body) or f"http-{status}"
                raise ClovaError(f"CLOVA HTTP {status} {code}", code=code, request_id=request_id)
            if not isinstance(body, dict):
                raise ClovaError("CLOVA 응답 구조 오류", code="bad-json", request_id=request_id)
            return body
        raise AssertionError("unreachable")  # pragma: no cover


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _status_code(body: Any) -> str:
    if isinstance(body, dict) and isinstance(body.get("status"), dict):
        code = body["status"].get("code")
        return code if isinstance(code, str) else ""
    return ""
