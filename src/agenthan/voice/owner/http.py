"""사장님 화면 HTTP (4단계 spec "사장님 화면 HTTP"). 허브 하나를 SSE 하나와 POST로 연다.

    app.include_router(owner_router(hub, key=settings.owner_key))

- `GET /api/owner/stream?key=…`: SSE. 먼저 `snapshot`(`{"orders": [...], "calls": [...],
  "questions": [...], "captions": [...], "knowledge": [...], "sold_out": {...}, "now": 허브
  시각}`), 그 뒤 `order.update`·`call.update`·`call.caption`·`owner.question`·
  `owner.question.update`·`knowledge.update`(카드 전체)·`owner.sold_out`(오늘 품절 전체, 티켓 06)
- `POST /api/owner/orders/{id}/accept?key=…` 본문(선택) `{"delay_min": 0|15|30}`: 확인 대기 주문
  수락 (new → accepted, 주문 승인 티켓 02). 확정 문자를 보내고 통화 중이면 손님이 마무리를 듣는다.
  15·30은 늦춘 수락(티켓 04): 통화 중이면 손님이 바뀐 시각을 듣고 답한다(문자는 "네" 뒤), 통화
  뒤면 늦춘 수락 문자가 바로 간다. 바뀐 카드를 돌려준다. 없는 주문은 404, 이미 결정했거나 손님이
  고치는 중·취소한 요청은 409, 그 밖의 늦춘 분은 422
- `POST /api/owner/orders/{id}/reject?key=…` 본문 `{"reason": "busy"|"closed"|"free"|"out_of_stock",
  "menu_ids": [...], "text": "사장님 글"}`: 확인 대기 주문 거절(new → rejected, 주문 승인 티켓 05).
  사유별 취소 문자를 보내고 통화 중이면 손님이 사유별 사과를 듣고 통화가 끝난다. 직접 쓰기(free)
  글은 번역해 문자로만 간다. 바뀐 카드를 돌려준다. 없는 주문은 404, 이미 결정했거나(수락·늦춘
  수락·거절·자동 취소) 손님이 고치는 중·취소한 요청은 409, 모르는 사유·글 없는 직접 쓰기·500자
  넘는 글은 422. 재료 소진(out_of_stock, 티켓 06)은 `menu_ids`에 주문에 든 떨어진 메뉴를 하나
  이상 넣는다(없거나 주문에 없는 메뉴면 422). 그 메뉴들은 오늘 품절이 된다. 통화 중이면 손님은
  품절 안내와 "빼고 주문할까요, 다른 메뉴로 바꾸실까요?"를 듣고, 취소 문자는 손님이 고르지 않고
  끝났을 때만 간다
- `POST /api/owner/sold-out/{menu_id}/undo?key=…`: 오늘 품절 되돌리기(티켓 06). 오늘 품절 전체
  `{"date", "items": [...]}`를 돌려준다. 가게 메뉴에 없는 id는 404, 이미 빠진 메뉴는 그대로 200
- `POST /api/owner/calls/{id}/dismiss?key=…`: 넘김·미완료 통화 카드 닫기(→ closed, 티켓 02).
  바뀐 카드를 돌려준다. 없는 카드는 404, 통화 중이거나 주문이 된 카드는 409
- `POST /api/owner/questions/{id}/answer?key=…` 본문 `{"answer": "yes"|"no"|"unsure"}` 또는
  `{"answer": "free", "text": "사장님 글"}`: 질문 카드 답(버튼 티켓 05, 자유 입력 티켓 07). 바뀐
  카드를 돌려준다. 없는 질문은 404, 이미 답한 질문은 409(버튼·자유 입력 합쳐 한 번), 모르는 답·
  글 없는 자유 입력·500자 넘는 글은 422. 시간 초과·통화 끝 카드도 답을 받는다(티켓 06, 늦은 답 →
  손님 문자). `timeout`은 세션만 넣는 답이라 422
- `POST /api/owner/questions/{id}/seen?key=…`: 질문 카드 열람(티켓 06). 처음이면 열람 시각을 찍고
  답 전이면 통화에 알린다(손님에게 "사장님이 보고 계세요" 한 번). 바뀐 카드를 돌려준다. 없으면 404
- `POST /api/owner/knowledge/{id}/preview?key=…` 본문 `{"text": "사장님 글"}`: "손님이 궁금해한 것"
  카드(티켓 13) 답의 한국어 번역 미리보기 `{"text", "text_ko", "translate_failed"}`. 저장하지
  않는다. 없는 카드 404, 빈 글·500자 넘는 글 422
- `POST /api/owner/knowledge/{id}/save?key=…` 본문 `{"text_ko": "한국어", "text": "사장님 글"}`:
  한국어를 그대로 보충 지식으로 저장하고 채운 카드를 돌려준다(다시 저장하면 덮어씀). 없는 카드 404,
  빈 한국어·500자 넘는 글 422. 다음 통화부터 엔진이 "사장님 말씀으로는 …"으로 답한다

손님 쪽 (`customer_texts_router`, 티켓 07):
- `GET /api/phone/calls/{id}/sms`: 손님 통화 화면이 통화가 끝나면 여는 "받은 문자" SSE
  (최대 6분, 주문 승인 티켓 03).
  토큰 없음(통화 id는 추측할 수 없는 값). 모르는 통화는 404

손님 주문 현황 (`customer_orders_router`, 전화 메뉴·주문 현황·쿠폰 티켓 04):
- `GET /api/phone/orders/{id}`: 주문 번호(= 통화 id)로 주문 한 건의 현황. 모양과 단계는
  `order_status.py`(줄 목록·금액·픽업 시각·단계 checking·confirmed·cancelled). 토큰 없음(주문
  번호가 열쇠다). 허브에 주문 카드가 없으면(서버가 다시 뜸) 전화 주문 기록(DB)의 마지막 상태로
  같은 모양을 준다(티켓 06, `order_archive.py`). 둘 다 없으면(모르는 번호·주문 없이 끝난 통화·DB
  없음) 404 "주문을 찾을 수 없어요". 결과·사유·품절 메뉴·취소 가능(`outcome`·`reason`·`sold_out`·
  `can_cancel`)은 티켓 05
- `GET /api/phone/orders/{id}/stream`: 주문 현황 SSE (티켓 05, 문자 스트림과 같은 방식). 먼저
  `order.status`(조회와 같은 모양), 그 뒤 현황이 달라질 때마다 `order.status`(현황 전체). 창
  (`window_s`, 서버는 자동 취소 마감 + 60초)이 지나면 `order.end`를 보내고 닫는다. 허브에 주문
  카드가 없으면 404(화면은 조회 API로 읽는다. 기록에서 읽은 주문은 더 바뀌지 않는다)

- 사장님 경로는 모두 쿼리 `key`(비밀 토큰, `PHONE_OWNER_KEY`)를 요구한다. `EventSource`가 헤더를
  못 붙여서 쿼리로 받는다. 없거나 틀리거나 서버에 토큰이 없으면 401
- SSE는 FastAPI 내장 응답을 쓴다: 15초 핑, `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
  이벤트 `id`는 허브 번호다. `Last-Event-ID`는 읽지 않는다(재연결하면 늘 snapshot부터)
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import aclosing, contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from agenthan.voice.dialog.approval import AcceptDelay, RejectReason
from agenthan.voice.dialog.owner_question import OwnerPost
from agenthan.voice.owner.hub import (
    AlreadyAnswered,
    AlreadyDecided,
    CardBusy,
    OwnerEvent,
    OwnerHub,
    UnknownCard,
)

OWNER_PREFIX = "/api/owner"
CUSTOMER_TEXTS_PATH = "/api/phone/calls/{call_id}/sms"
# 손님 주문 현황 (티켓 04). 화면 주소 `/phone/orders/<번호>`와 겹치지 않게 `/api` 아래에 둔다
CUSTOMER_ORDERS_PREFIX = "/api/phone/orders"
# 손님 통화 화면이 통화 뒤 문자 스트림을 열어 두는 최소 시간 (4단계 spec "최대 2분"). 서버는
# 통화 뒤 확인 대기의 수락·자동 취소 문자까지 받게 `max(이 값, 자동 취소 마감 + 60초)`로 연다
# (`server.py`, 주문 승인 티켓 03). 창 길이는 `sms.snapshot`의 `window_s`로 화면에 알린다
TEXTS_WINDOW_S = 120.0
# 사장님 자유 입력 글 최대 길이 (문자 한 통 분량)
FREE_TEXT_MAX = 500


# 허브 예외 → HTTP 상태 (리뷰 S8). 경로마다 그 상태의 화면 문구를 `_hub_errors`에 준다
_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (UnknownCard, 404),  # 그런 카드가 없다
    (CardBusy, 409),  # 지금 닫을 수 없다(통화 중·주문됨)
    (AlreadyAnswered, 409),  # 답은 한 번만
    (AlreadyDecided, 409),  # 주문 결정은 한 번만 (주문 승인 티켓 02)
    (ValueError, 422),  # 모르는 답·빈 글
)


@contextmanager
def _hub_errors(details: Mapping[int, str]) -> Iterator[None]:
    """허브 호출을 감싸 `_STATUS` 표대로 HTTP 오류로 바꾼다. `details`에 없는 상태의 예외는
    그대로 올린다."""
    try:
        yield
    except Exception as err:
        status = next((code for kind, code in _STATUS if isinstance(err, kind)), None)
        if status is None or status not in details:
            raise
        raise HTTPException(status_code=status, detail=details[status]) from err


class KnowledgeDraft(BaseModel):
    """"손님이 궁금해한 것" 카드 답 미리보기: 사장님 글(사장님 언어, 티켓 13)."""

    text: str = Field(max_length=FREE_TEXT_MAX)


class KnowledgeSave(BaseModel):
    """"저장": 보충 지식이 될 한국어(번역문이나 사장님이 고친 글)와 사장님 글 원문."""

    text_ko: str = Field(max_length=FREE_TEXT_MAX)
    text: str = Field("", max_length=FREE_TEXT_MAX)


class AcceptBody(BaseModel):
    """수락 버튼(주문 승인 티켓 02). 늦춘 분 15·30은 [+15분] [+30분] (티켓 04)."""

    delay_min: AcceptDelay = 0


class RejectBody(BaseModel):
    """거절 사유 시트(주문 승인 티켓 05). 재료 소진은 떨어진 메뉴 id들(티켓 06), 직접 쓰기는 글."""

    reason: RejectReason
    menu_ids: list[str] = Field(default_factory=list, max_length=20)
    text: str = Field("", max_length=FREE_TEXT_MAX)  # 직접 쓰기 글(사장님 언어). 다른 사유면 무시


class AnswerBody(BaseModel):
    """질문 카드 답: 버튼(yes·no·unsure) 또는 자유 입력(free + `text`, 티켓 07)."""

    answer: OwnerPost  # 시간 초과는 세션이 넣는다. 화면은 못 보낸다
    text: str = Field("", max_length=FREE_TEXT_MAX)  # 자유 입력 글(사장님 언어). 버튼이면 무시


def owner_router(hub: OwnerHub, *, key: str) -> APIRouter:
    """`key`가 비면 사장님 경로가 모두 401이다(잠금). 허브는 서버의 이벤트 루프 하나에서 쓴다."""
    expected = key.encode()

    def check_key(key: Annotated[str | None, Query()] = None) -> None:
        if not expected or key is None or not secrets.compare_digest(key.encode(), expected):
            raise HTTPException(status_code=401, detail="사장님 화면 토큰이 없거나 틀려요")

    router = APIRouter(prefix=OWNER_PREFIX, dependencies=[Depends(check_key)])

    @router.get("/stream", response_class=EventSourceResponse)
    async def stream() -> AsyncIterator[ServerSentEvent]:
        async with aclosing(hub.events()) as events:
            async for event in events:
                yield ServerSentEvent(event=event.name, data=event.data, id=str(event.id))

    @router.post("/orders/{order_id}/accept")
    async def accept(order_id: str, body: AcceptBody | None = None) -> dict[str, Any]:
        """확인 대기 주문 수락. 본문이 없으면 늦춤 없이 수락한다(예전 접수 경로와 같은 주소)."""
        delay_min = body.delay_min if body is not None else 0
        with _hub_errors({404: "그런 주문이 없어요",
                          409: "이미 결정했거나 손님이 주문을 고치는 중이에요"}):
            return hub.accept(order_id, delay_min)

    @router.post("/orders/{order_id}/reject")
    async def reject(order_id: str, body: RejectBody) -> dict[str, Any]:
        # 422: 재료 소진은 떨어진 메뉴를 안 골랐거나 주문에 없는 메뉴, 직접 쓰기는 빈 글
        unfit = ("떨어진 메뉴를 주문에서 골라 주세요" if body.reason == "out_of_stock"
                 else "거절 사정을 적어 주세요")
        with _hub_errors({404: "그런 주문이 없어요",
                          409: "이미 결정했거나 손님이 주문을 고치는 중이에요",
                          422: unfit}):
            return hub.reject(order_id, body.reason, menu_ids=body.menu_ids, text=body.text)

    @router.post("/sold-out/{menu_id}/undo")
    async def undo_sold_out(menu_id: str) -> dict[str, Any]:
        with _hub_errors({404: "그런 메뉴가 없어요"}):
            return hub.undo_sold_out(menu_id)

    @router.post("/calls/{call_id}/dismiss")
    async def dismiss(call_id: str) -> dict[str, Any]:
        with _hub_errors({404: "그런 통화 카드가 없어요", 409: "통화 중이거나 주문이 된 카드예요"}):
            return hub.dismiss(call_id)

    @router.post("/questions/{question_id}/answer")
    async def answer(question_id: str, body: AnswerBody) -> dict[str, Any]:
        # 422: 자유 입력인데 글이 비었다
        with _hub_errors({404: "그런 질문이 없어요", 409: "이미 답한 질문이에요",
                          422: "답할 글을 적어 주세요"}):
            return hub.answer_question(question_id, body.answer, body.text)

    @router.post("/knowledge/{card_id}/preview")
    async def preview_knowledge(card_id: str, body: KnowledgeDraft) -> dict[str, Any]:
        with _hub_errors({404: "그런 카드가 없어요", 422: "답할 글을 적어 주세요"}):
            return await hub.preview_knowledge(card_id, body.text)

    @router.post("/knowledge/{card_id}/save")
    async def save_knowledge(card_id: str, body: KnowledgeSave) -> dict[str, Any]:
        with _hub_errors({404: "그런 카드가 없어요", 422: "저장할 한국어를 적어 주세요"}):
            return hub.save_knowledge(card_id, body.text_ko, body.text)

    @router.post("/questions/{question_id}/seen")
    async def seen(question_id: str) -> dict[str, Any]:
        with _hub_errors({404: "그런 질문이 없어요"}):
            return hub.see_question(question_id)

    return router


def customer_texts_router(hub: OwnerHub, *, window_s: float = TEXTS_WINDOW_S) -> APIRouter:
    """손님 통화 화면의 "받은 문자" 스트림 (티켓 07). 사장님 토큰이 없다(통화 id는 추측할 수 없다).

    `GET /api/phone/calls/{id}/sms`: SSE. 먼저 `sms.snapshot`(`{"call_id", "sms": [...]}`), 그 뒤
    새 문자마다 `sms`(문자 하나). snapshot에는 창 길이 `window_s`(초)도 싣는다(화면이 같은
    시간에 닫는다). `window_s`가 지나면 `sms.end`를 보내고 닫는다(브라우저
    `EventSource`가 다시 붙지 않게 화면이 `sms.end`에서 닫는다). 허브가 모르는 통화는 404
    """

    def watch(call_id: str) -> AsyncIterator[OwnerEvent]:
        # 스트림을 열기 전에(의존성에서) 모르는 통화를 404로 거절한다
        with _hub_errors({404: "그런 통화가 없어요"}):
            return hub.watch_texts(call_id)

    router = APIRouter()

    @router.get(CUSTOMER_TEXTS_PATH, response_class=EventSourceResponse)
    async def texts(
        events: AsyncIterator[OwnerEvent] = Depends(watch),  # noqa: B008 — 닫힌 함수는 문자열 주석으로 못 찾음
    ) -> AsyncIterator[ServerSentEvent]:
        async for event in _until_window(events, window_s):
            yield event

    return router


def customer_orders_router(hub: OwnerHub, *, window_s: float = TEXTS_WINDOW_S) -> APIRouter:
    """손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04·05). 사장님 토큰이 없다(주문 번호가 열쇠다).

    `GET /api/phone/orders/{id}`: 허브 주문 카드의 손님 모양(`order_status.py`). 카드가 없으면 전화
    주문 기록(티켓 06, `OwnerHub.lookup_order_status`). 둘 다 없으면 404.
    `GET /api/phone/orders/{id}/stream`: 허브 카드 현황의 SSE(`order.status` …, `window_s` 뒤
    `order.end`). 허브에 카드가 없으면 404.
    """
    router = APIRouter(prefix=CUSTOMER_ORDERS_PREFIX)

    def watch(order_id: str) -> AsyncIterator[OwnerEvent]:
        # 스트림을 열기 전에(의존성에서) 모르는 주문을 404로 거절한다
        with _hub_errors({404: "주문을 찾을 수 없어요"}):
            return hub.watch_order(order_id)

    @router.get("/{order_id}")
    async def order_status(order_id: str) -> dict[str, Any]:
        with _hub_errors({404: "주문을 찾을 수 없어요"}):
            return await hub.lookup_order_status(order_id)

    @router.get("/{order_id}/stream", response_class=EventSourceResponse)
    async def order_stream(
        events: AsyncIterator[OwnerEvent] = Depends(watch),  # noqa: B008 — 닫힌 함수는 문자열 주석으로 못 찾음
    ) -> AsyncIterator[ServerSentEvent]:
        async for event in _until_window(events, window_s, end_event="order.end"):
            yield event

    return router


async def _until_window(
    events: AsyncIterator[OwnerEvent], window_s: float, *, end_event: str = "sms.end"
) -> AsyncIterator[ServerSentEvent]:
    loop = asyncio.get_running_loop()
    end = loop.time() + window_s
    async with aclosing(events) as stream:  # type: ignore[type-var]
        while (left := end - loop.time()) > 0:
            try:
                event = await asyncio.wait_for(anext(stream), left)
            except TimeoutError:
                break
            data = event.data
            if event.name == "sms.snapshot":
                # 손님 화면이 같은 시간에 닫도록 창 길이를 알린다(설정에 따라 바뀐다, 리뷰 2번)
                data = {**data, "window_s": window_s}
            yield ServerSentEvent(event=event.name, data=data, id=str(event.id))
    yield ServerSentEvent(event=end_event, data={})
