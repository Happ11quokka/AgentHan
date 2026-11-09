"""통화 세션의 포트 (spec 테스트 이음매). 세션은 바깥과 이것들로만 만난다.

- `SttStream`: CLOVA Speech 스트리밍(티켓 02)이 구현한다.
  오디오 프레임과 `epFlag`를 보내고 결과를 받는다
- `Tts`: CLOVA Voice(티켓 03)가 구현한다. 문장 하나 → PCM16LE 16kHz mono (WAV 헤더 없음)
- `Clock`: 가게 시각(KST, 시간대 있음)과 지연 측정용 단조 시각
- `Outbound`: WS 라우터(티켓 04)가 구현한다. JSON 이벤트와 오디오 프레임을 내보낸다
- `TurnModel`: 오디오 턴 모델(3단계 티켓 03, `voice/smart_turn.py`). 최근 오디오 → 끝남 확률
- `OrderSink`: `agenthan.core.contracts.OrderSink` 그대로 (계약)
- `OwnerChannel`: 사장님 쪽(4단계 사장님 허브 `voice/owner/hub.py`)에 통화 상황을 알린다

어댑터는 실패를 `PortError`(또는 하위 클래스)로 올린다.
세션이 `code`·`retry`를 `error` 이벤트로 옮긴다.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Protocol

from agenthan.core.contracts import OrderLine, OrderSink
from agenthan.voice.dialog.knowledge import KnowledgeRequest
from agenthan.voice.dialog.owner_question import OwnerNote, OwnerQuestion
from agenthan.voice.store_config import KST

__all__ = [
    "CUSTOMER_FATAL",
    "CUSTOMER_RETRY",
    "ClientGone",
    "Clock",
    "END_EPD_TYPES",
    "NullOwnerChannel",
    "OrderSink",
    "Outbound",
    "OwnerChannel",
    "PortError",
    "SttResult",
    "SttStream",
    "SystemClock",
    "Tts",
    "TurnModel",
]

# 이 끝난 이유로 온 결과만 "말 끝" 후보다 (spec 조각 합치기).
# 나머지(`durationThreshold`·`period`)는 쌓기만 하고 다음 끝 후보 결과와 합친다. CLOVA `epdType` 값.
END_EPD_TYPES = frozenset({"gap", "endPoint", "unvoice"})


# 손님 화면에 나가는 `error.message` 기본값 (spec 이야기 20). 어댑터 메시지(gRPC 상태·서버 설명·
# 변수 이름)는 개발자용이라 로그·JSONL에만 남긴다 (리뷰 15번)
CUSTOMER_RETRY = "잠시 후 다시 걸어 주세요."
CUSTOMER_FATAL = "지금은 전화 주문을 받을 수 없어요. 가게로 직접 연락해 주세요."


class ClientGone(Exception):
    """손님 쪽 연결이 끊겨 보낼 수 없다(`Outbound`). 보통 일이라 세션은 한 줄만 남긴다.

    traceback은 남기지 않는다. 어댑터가 전송 계층의 끊김(Starlette `WebSocketDisconnect`,
    uvicorn `ClientDisconnected`)을 이것으로 바꾼다. 세션은 FastAPI를 모른다 (3단계 감사 문제 B)
    """


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
    # 문장 신뢰도. 판정에 쓰지 않는다: 키워드 부스팅이 고친 음절은 신뢰도가 0.02~0.05로 떨어진다
    # (티켓 10, `stt/boost.py`). 신뢰도로 되묻게 되면 부스팅 단어는 예외로 둔다
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


class TurnModel(Protocol):
    """오디오로 말이 끝났는지 보는 모델 (Smart Turn, 3단계 티켓 03)."""

    def p_complete(self, pcm16: bytes) -> float:
        """PCM16LE 16kHz mono(최근 오디오, 마지막 8초만 본다) → 끝남 확률 0~1.

        동기 함수다. 세션이 전용 스레드 풀에서 부른다. 실패하면 아무 예외나 올려도 된다
        (세션은 그 판정을 규칙만으로 한다).
        """
        ...


class OwnerChannel(Protocol):
    """통화 세션 → 사장님 쪽 (4단계 spec "ASK_OWNER: 세션", 티켓 02). 사장님 허브가 구현한다.

    모두 이벤트 루프에서 불리고 바로 돌아와야 한다(막히면 통화가 늦는다). `call_id`는 통화 id
    (= 계약 `Order.id`). 확정·넘김 주문 자체는 `OrderSink.submit`으로 따로 간다. 세션은 이 포트가
    올린 예외를 로그만 남기고 통화를 잇는다.
    """

    def call_started(self, call_id: str) -> None:
        """통화가 연결됐다(`call.ready`를 보낸 때)."""

    def cart_changed(self, call_id: str, lines: Sequence[OrderLine]) -> None:
        """턴마다 지금 장바구니(바뀌지 않았어도 보낸다). 빈 장바구니는 빈 목록."""

    def customer_said(self, call_id: str, text: str) -> None:
        """손님 말 하나가 턴이 됐다(`stt.final`과 같은 글, 한국어)."""

    def bot_said(self, call_id: str, text: str) -> None:
        """한 턴에 AI가 한 말 전체(한국어, 문장을 이은 것)."""

    def call_ended(self, call_id: str, reason: str) -> None:
        """통화가 끝났다. 끝내는 길이 어디든 통화당 한 번. 이유는 `CallSession._end` 설명:
        confirmed · handoff · cancelled · no_input · hangup · error.
        """

    def open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerNote], None]
    ) -> None:
        """근거 없는 메뉴 질문을 사장님께 연다(티켓 05). 기다리지 않고 바로 돌아온다.

        사장님 쪽 일은 나중에 `reply`로 넘긴다: 카드 열람 `OwnerSeen`(티켓 06), 답 `OwnerReply`
        (질문당 한 번). `reply`는 세션의 받는 곳이라 바로 돌아오고, 이 호출 안에서 바로 불러도 되고
        다른 스레드에서 불러도 된다. 시간 초과·통화 끝 뒤의 답은 세션이 버린다(늦은 답 문자는 허브
        몫, 티켓 07).
        """

    def answer_said(self, call_id: str, number: int) -> None:
        """그 질문의 사장님 답(`OwnerReply`)을 손님에게 말하는 봇 턴을 열었다(리뷰 C1).

        제시간에 받았어도 손님이 말하는 중이라 미뤘다가 통화가 끝나면 말하지 못한다. 허브는 이
        알림이 없는 답을 통화 끝에 손님 문자로 돌린다.
        """

    def question_timed_out(self, call_id: str, number: int) -> None:
        """질문 카드를 보낸 뒤 답 대기 시간(`owner_wait_s`)이 지났다(티켓 06). 세션 시계로 잰다.

        손님에게는 "확인해서 문자로 알려 드릴게요"가 나간다(손님이 말하는 중이면 조금 뒤). 카드는
        닫지 않는다(늦은 답을 받는다).
        """

    def knowledge_wanted(self, call_id: str, request: KnowledgeRequest) -> None:
        """AI가 답하지 못하고 손님에게 "사장님께 전해 둘게요"라고 한 메뉴 질문(티켓 13, 보충 요청).

        사장님 화면 "손님이 궁금해한 것" 카드가 된다. 사장님이 채운 답은 보충 지식 저장소로 가고
        다음 통화부터 엔진이 읽는다(`dialog/knowledge.py`).
        """

    def customer_texts(self, call_id: str) -> list[dict[str, Any]]:
        """그 통화에서 지금까지 손님에게 보낸 문자 미리보기(티켓 07, 보낸 순서, JSON으로 보낼 모양).

        세션이 `call.ended`에 담아 손님 통화 화면에 보낸다. 통화 뒤 문자는 손님 화면이 문자
        스트림으로 따로 받는다. 없으면 빈 목록.
        """
        ...


class NullOwnerChannel:
    """아무에게도 알리지 않는다. 사장님 허브 없이 통화만 돌릴 때(1~3단계 테스트·CLI)."""

    def call_started(self, call_id: str) -> None:
        pass

    def cart_changed(self, call_id: str, lines: Sequence[OrderLine]) -> None:
        pass

    def customer_said(self, call_id: str, text: str) -> None:
        pass

    def bot_said(self, call_id: str, text: str) -> None:
        pass

    def call_ended(self, call_id: str, reason: str) -> None:
        pass

    def open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerNote], None]
    ) -> None:
        pass

    def answer_said(self, call_id: str, number: int) -> None:
        pass

    def question_timed_out(self, call_id: str, number: int) -> None:
        pass

    def knowledge_wanted(self, call_id: str, request: KnowledgeRequest) -> None:
        pass

    def customer_texts(self, call_id: str) -> list[dict[str, Any]]:
        return []
