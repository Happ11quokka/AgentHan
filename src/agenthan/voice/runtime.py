"""프로세스가 같이 쓰는 외부 API 연결 (6단계 운영 보강).

전에는 통화마다 HCX용 `httpx.Client`와 CLOVA Voice용 `httpx.AsyncClient`를 새로 만들었다
(HCX 쪽은 닫지도 않았다). 여기서 프로세스에 하나씩만 만들고 서버가 끝날 때 닫는다.

- `HcxRuntime`: NLU(`HcxNlu`)와 답변기(`HcxAnswerer`) 하나씩. 처음 쓰는 통화가 만든다.
  분당 버킷·사용량 기록·규칙 NLU 폴백·짧은 재시도(1회)를 여기서 끼운다. 라우터(`ws.py`)가
  통화 세션에 이 객체를 넘긴다(세션은 문자열 `"hcx"` 대신 만들어진 NLU를 받는다)
- `shared_tts_http()`: CLOVA Voice용 `httpx.AsyncClient` 하나. 유휴 연결을 60초 둔다
  (httpx 기본 5초면 턴 사이마다 TLS를 다시 맺는다)

키는 `VoiceSettings.env`(환경 변수 + `.env`)에서 꺼낸다. 없으면 전처럼 `PortError(nlu_config)`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import httpx

from agenthan.voice.call.ports import PortError
from agenthan.voice.nlu.budget import RateBudget, UsageLog
from agenthan.voice.nlu.clova import KEY_ENV, ClovaClient, load_api_key
from agenthan.voice.settings import VoiceSettings

if TYPE_CHECKING:
    from agenthan.voice.dialog.answerer import Answerer
    from agenthan.voice.nlu.base import Nlu

log = logging.getLogger(__name__)

TASK_ENV = "PHONE_NLU_TASK_ID"
ANSWER_MODEL_ENV = "PHONE_ANSWER_MODEL"
RETRY_BACKOFF_S = 0.4  # 429·5xx 뒤 한 번 다시 부르기 전 쉬는 시간
ANSWER_TIMEOUT_S = 4.0
KEEPALIVE_S = 60.0
TTS_TIMEOUT_S = 10.0


class HcxRuntime:
    def __init__(self, settings: VoiceSettings, *,
                 transport: httpx.BaseTransport | None = None) -> None:
        """`transport`는 테스트용(HTTP 흉내)."""
        self._settings = settings
        self._transport = transport
        self._lock = threading.Lock()
        self._clients: list[ClovaClient] = []
        self._nlu: Nlu | None = None
        self._answerer: Answerer | None = None
        self.budget = RateBudget(rpm=settings.hcx_rpm, tpm=settings.hcx_tpm)
        tz = settings.tz
        self.usage = UsageLog(settings.log_dir, today=lambda: _today(tz))

    def _key(self) -> str:
        key = self._settings.env.get(KEY_ENV, "").strip() or load_api_key()
        if not key:
            raise PortError(f"{KEY_ENV}가 없어요. 환경 변수나 .env에 넣어 주세요.",
                            code="nlu_config", retry=False)
        return key

    def _client(self, timeout: float) -> ClovaClient:
        http = httpx.Client(timeout=httpx.Timeout(timeout, connect=2.0),
                            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S),
                            transport=self._transport)
        try:
            client = ClovaClient(self._key(), http=http, max_retries=1,
                                 backoff_s=RETRY_BACKOFF_S, observe=self.usage.observe)
        except ValueError as err:
            http.close()
            raise PortError(str(err), code="nlu_config", retry=False) from err
        except PortError:
            http.close()
            raise
        self._clients.append(client)
        return client

    def nlu(self) -> Nlu:
        """모든 통화가 같이 쓰는 HCX NLU. 키가 없으면 `PortError`."""
        with self._lock:
            if self._nlu is None:
                from agenthan.voice.nlu.fake import FakeNlu
                from agenthan.voice.nlu.hcx import HcxNlu

                s = self._settings
                task_id = s.env.get(TASK_ENV, "").strip() or load_api_key(name=TASK_ENV)
                self._nlu = HcxNlu(
                    self._client(s.nlu_timeout_s),
                    task_id=task_id,
                    max_tokens=s.nlu_max_tokens or None,
                    fallback=FakeNlu() if s.nlu_fallback else None,
                    budget=self.budget,
                    usage=self.usage,
                )
            return self._nlu

    def answerer(self) -> Answerer | None:
        """손님 질문 답변기. `PHONE_ANSWER=off`면 None."""
        if not self._settings.answer:
            return None
        with self._lock:
            if self._answerer is None:
                from agenthan.voice.nlu.hcx_answer import MODEL, HcxAnswerer

                env = self._settings.env
                model = (env.get(ANSWER_MODEL_ENV, "").strip()
                         or load_api_key(name=ANSWER_MODEL_ENV) or MODEL)
                self._answerer = HcxAnswerer(self._client(ANSWER_TIMEOUT_S), model=model,
                                             usage=self.usage)
            return self._answerer

    def ready(self) -> dict[str, bool]:
        return {"nlu": self._nlu is not None, "answerer": self._answerer is not None}

    def close(self) -> None:
        with self._lock:
            clients, self._clients = self._clients, []
            self._nlu = self._answerer = None
        for client in clients:
            try:
                client.close()
            except Exception:
                log.info("HCX 연결 닫기 실패", exc_info=True)


def _today(tz: object) -> str:
    from datetime import datetime

    return datetime.now(tz).strftime("%Y%m%d")  # type: ignore[arg-type]


def shared_tts_http(timeout: float = TTS_TIMEOUT_S) -> httpx.AsyncClient:
    """CLOVA Voice용 연결 풀 하나. 만든 쪽(서버·라우터)이 끝날 때 `aclose()`한다."""
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0),
                             limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S))
