"""통화 세션의 포트 (spec 테스트 이음매). 세션은 바깥과 이것들로만 만난다.

- `SttStream`: CLOVA Speech 스트리밍(티켓 02)이 구현한다.
  오디오 프레임과 `epFlag`를 보내고 결과를 받는다
- `Tts`: CLOVA Voice(티켓 03)가 구현한다. 문장 하나 → PCM16LE 16kHz mono (WAV 헤더 없음)
- `Clock`: 가게 시각(KST, 시간대 있음)과 지연 측정용 단조 시각
- `Outbound`: WS 라우터(티켓 04)가 구현한다. JSON 이벤트와 오디오 프레임을 내보낸다
- `OrderSink`: `agenthan.core.contracts.OrderSink` 그대로 (계약)

어댑터는 실패를 `PortError`(또는 하위 클래스)로 올린다.
세션이 `code`·`retry`를 `error` 이벤트로 옮긴다.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Protocol

from agenthan.core.contracts import OrderSink
from agenthan.voice.store_config import KST

__all__ = [
    "CUSTOMER_FATAL",
    "CUSTOMER_RETRY",
    "Clock",
    "END_EPD_TYPES",
    "OrderSink",
    "Outbound",
    "PortError",
    "SttResult",
    "SttStream",
    "SystemClock",
    "Tts",
]

# 이 끝난 이유로 온 결과만 "말 끝" 후보다 (spec 조각 합치기).
# 나머지(`durationThreshold`·`period`)는 쌓기만 하고 다음 끝 후보 결과와 합친다. CLOVA `epdType` 값.
END_EPD_TYPES = frozenset({"gap", "endPoint", "unvoice"})


# 손님 화면에 나가는 `error.message` 기본값 (spec 이야기 20). 어댑터 메시지(gRPC 상태·서버 설명·
# 변수 이름)는 개발자용이라 로그·JSONL에만 남긴다 (리뷰 15번)
CUSTOMER_RETRY = "잠시 후 다시 걸어 주세요."
CUSTOMER_FATAL = "지금은 전화 주문을 받을 수 없어요. 가게로 직접 연락해 주세요."


class PortError(Exception):
    """어댑터 실패. `code`는 `error` 이벤트의 `code`, `retry`는 다시 걸면 될지.

    메시지(`str(err)`)는 개발자용이다. 손님에게는 `customer_message`가 나간다.
    """

    def __init__(self, message: str, *, code: str = "port", retry: bool = True,
                 customer: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry = retry
        self.customer = customer

    @property
    def customer_message(self) -> str:
        return self.customer or (CUSTOMER_RETRY if self.retry else CUSTOMER_FATAL)


@dataclass(frozen=True)
class SttResult:
    """STT 결과 하나 (CLOVA `transcription` 응답 한 개).

    `start_ms`·`end_ms`는 통화 오디오 시각이다. 세션이 이 스트림에 처음 쓴 오디오 바이트가 0.
    어댑터가 다시 연결해서 CLOVA 시각이 0부터 다시 시작해도,
    이전 연결까지 쓴 오디오 길이를 더해 이어 준다.
    """

    text: str
    start_ms: int
    end_ms: int
    # CLOVA `epdType`: gap · endPoint · durationThreshold · period · unvoice
    epd_type: str = "gap"
    ep_flag: bool = False  # `epFlag=true` 조각에 대한 응답 (빈 글일 수 있음)
    seq_id: int = 0
    confidence: float | None = None

    @property
    def ends_speech(self) -> bool:
        return self.ep_flag or self.epd_type in END_EPD_TYPES


class SttStream(Protocol):
    """통화 하나 = 스트림 하나.

    세션은 한 작업에서만 `write`하고, 한 작업에서만 `results`를 읽는다.
    """

    async def start(self) -> None:
        """연결하고 CONFIG를 보낸 뒤 성공 응답까지 기다린다. 실패면 `PortError`."""

    async def write(self, pcm: bytes, *, ep_flag: bool = False, seq_id: int = 0) -> None:
        """PCM16LE 16kHz mono 프레임(보통 100ms = 3,200바이트) 하나.

        `ep_flag`면 이 DATA에 `{"epFlag": true, "seqId": seq_id}`를 붙인다.
        """

    def results(self) -> AsyncIterator[SttResult]:
        """결과를 도착 순서대로. 스트림이 닫히면 끝난다. 연결이 깨지면 `PortError`를 올린다."""
        ...

    async def close(self) -> None:
        """남은 것을 정리한다. 여러 번 불러도 된다."""


class Tts(Protocol):
    async def synth(self, text: str) -> bytes:
        """문장 하나(150자 이하) → PCM16LE 16kHz mono.

        WAV 헤더는 어댑터가 뗀다. 실패면 `PortError`.
        """
        ...


class Clock(Protocol):
    def now(self) -> datetime:
        """가게 시각. 시간대가 있는 값(KST). 엔진의 `now`로 넘어간다."""
        ...

    def monotonic(self) -> float:
        """초. 지연 기록용 (벽시계가 바뀌어도 거꾸로 가지 않는다)."""
        ...


class SystemClock:
    """서버 기본 TZ(보통 UTC)와 상관없이 가게 시간대(기본 KST)로 `now`를 준다."""

    def __init__(self, tz: tzinfo = KST) -> None:
        self._tz = tz

    def now(self) -> datetime:
        return datetime.now(self._tz)

    def monotonic(self) -> float:
        return time.monotonic()


class Outbound(Protocol):
    """세션의 송신 작업 하나만 부른다(Starlette `send`에 잠금이 없음).

    예외를 올리면 세션은 남은 송신을 버리고 통화를 끝낸다.
    """

    async def send_event(self, event: dict[str, Any]) -> None: ...

    async def send_audio(self, frame: bytes) -> None:
        """`frames.pack_audio_frame` 결과(8바이트 헤더 + PCM)를 binary 메시지 하나로."""
        ...
