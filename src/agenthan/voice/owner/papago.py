"""NCP Papago Translation 어댑터 (4단계 티켓 03). `translate.Translator` 구현.

    # 키가 없으면 None
    translator = papago_from_env(settings.env, timeout=settings.translate_timeout_s)

- `POST https://papago.apigw.ntruss.com/nmt/v1/translation`, 헤더 `X-NCP-APIGW-API-KEY-ID`·
  `X-NCP-APIGW-API-KEY`(Application 정보, 키 이름은 Part A와 같은 `PAPAGO_CLIENT_ID`·
  `PAPAGO_CLIENT_SECRET`), 본문 `{"source", "target", "text"}`. 답은 `message.result.translatedText`
- 문장마다 요청 하나, 한 번에 같이 보낸다(2026-11-10 동시 11~16개 모두 200). 빈 문장은 안 보낸다
- 다시 걸지 않는다. 실패하면 카드는 원문만이고, 다음 카드 갱신 때 허브가 다시 맡긴다
- 요청 시간 제한은 설정값(`PHONE_TRANSLATE_TIMEOUT_S`, 기본 3초). 티켓 04 실측: 이어 쓰는 연결
  P50 0.60초·최대 0.95초, 새 연결 첫 호출 P50 1.06초
- 유휴 연결은 설정값(`PHONE_TRANSLATE_KEEPALIVE_S`, 기본 60초)만큼 남겨 둔다. httpx 기본(5초)이면
  턴 사이마다 다시 연결한다(실측 쉬었다 부르기 중앙값 0.82초 → 60초 유지 0.60초, 티켓 12)
- `warm()`: 서버 시작 때 짧은 글 하나를 보내 연결을 미리 연다(통화 첫 카드가 TLS 비용을 안 내게).
  실패해도 오류를 내지 않는다. 서버 종료 때 `aclose()`
- Part A(`agent/review/translate.py`)를 가져오지 않는다. `core/` 공용화는 회의에서 제안만 (spec)
- 키는 오류 메시지·repr에 넣지 않는다. 서버 오류 본문도 넣지 않는다
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence

import httpx

from agenthan.voice.owner.translate import TranslateError

log = logging.getLogger(__name__)

PAPAGO_URL = "https://papago.apigw.ntruss.com/nmt/v1/translation"
KEY_ENVS = ("PAPAGO_CLIENT_ID", "PAPAGO_CLIENT_SECRET")
TIMEOUT_S = 3.0
KEEPALIVE_S = 60.0
WARM_TEXT = "네"  # 데우기 글(1자)


class PapagoTranslator:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = TIMEOUT_S,
        keepalive: float = KEEPALIVE_S,
        url: str = PAPAGO_URL,
    ) -> None:
        self._url = url
        self._headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        }
        # 연결을 이어 쓴다(첫 호출만 TLS 비용). 쉬는 동안에도 `keepalive`초는 안 닫는다
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=timeout),
            limits=httpx.Limits(keepalive_expiry=keepalive),
        )

    def __repr__(self) -> str:
        return "PapagoTranslator(keys=***)"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def warm(self) -> bool:
        """연결을 미리 연다. 됐으면 True. 실패해도 오류를 내지 않는다(카드는 그때 다시 연결)."""
        try:
            await self._one(WARM_TEXT, "ko", "ru")
        except TranslateError as err:
            log.warning("Papago 데우기 실패(%s): 첫 카드 때 다시 연결", err.code)
            return False
        return True

    async def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        return list(await asyncio.gather(*(self._one(t, source, target) for t in texts)))

    async def _one(self, text: str, source: str, target: str) -> str:
        if not text.strip():
            return ""
        body = {"source": source, "target": target, "text": text}
        try:
            res = await self._http.post(self._url, headers=self._headers, json=body)
        except httpx.TimeoutException:
            raise TranslateError("Papago 시간 초과", code="translate_timeout") from None
        except httpx.HTTPError as err:
            raise TranslateError(f"Papago 연결 오류 ({type(err).__name__})",
                                 code="translate_network") from None
        if res.status_code in (401, 403):
            raise TranslateError(f"Papago 인증 실패 HTTP {res.status_code} (키·API 선택 확인)",
                                 code="translate_auth", retry=False)
        if res.status_code == 429:
            raise TranslateError("Papago 한도 초과 HTTP 429", code="translate_429")
        if not res.is_success:
            raise TranslateError(f"Papago HTTP {res.status_code}", code="translate_http")
        try:
            return str(res.json()["message"]["result"]["translatedText"])
        except (ValueError, KeyError, TypeError):
            raise TranslateError("Papago 응답 형식 오류", code="translate_bad_response") from None


def papago_from_env(
    env: Mapping[str, str], *, timeout: float = TIMEOUT_S, keepalive: float = KEEPALIVE_S
) -> PapagoTranslator | None:
    """`PAPAGO_CLIENT_ID`·`PAPAGO_CLIENT_SECRET`이 둘 다 있으면 어댑터, 아니면 None(원문만)."""
    client_id, secret = (env.get(k, "").strip() for k in KEY_ENVS)
    if not (client_id and secret):
        return None
    return PapagoTranslator(client_id, secret, timeout=timeout, keepalive=keepalive)
