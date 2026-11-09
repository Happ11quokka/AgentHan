"""사장님 화면 HTTP (4단계 spec "사장님 화면 HTTP"). 허브 하나를 SSE 하나와 POST로 연다.

    app.include_router(owner_router(hub, key=settings.owner_key))

- `GET /api/owner/stream?key=…`: SSE. 먼저 `snapshot`(`{"orders": [...], "calls": [...],
  "questions": [...], "captions": [...], "knowledge": [...], "now": 허브 시각}`), 그 뒤
  `order.update`·`call.update`·`call.caption`·`owner.question`·`owner.question.update`·
  `knowledge.update`(카드 전체)
- `POST /api/owner/orders/{id}/accept?key=…`: 접수(new → accepted). 바뀐 카드를 돌려준다.
  없는 주문은 404
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
- `GET /api/phone/calls/{id}/sms`: 손님 통화 화면이 통화가 끝나면 여는 "받은 문자" SSE(최대 2분).
  토큰 없음(통화 id는 추측할 수 없는 값). 모르는 통화는 404

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

from agenthan.voice.dialog.owner_question import OwnerPost
from agenthan.voice.owner.hub import (
    AlreadyAnswered,
    CardBusy,
    OwnerEvent,
    OwnerHub,
    UnknownCard,
)

OWNER_PREFIX = "/api/owner"
CUSTOMER_TEXTS_PATH = "/api/phone/calls/{call_id}/sms"
# 손님 통화 화면이 통화 뒤 문자 스트림을 열어 두는 시간 (spec "최대 2분")
TEXTS_WINDOW_S = 120.0
# 사장님 자유 입력 글 최대 길이 (문자 한 통 분량)
FREE_TEXT_MAX = 500


# 허브 예외 → HTTP 상태 (리뷰 S8). 경로마다 그 상태의 화면 문구를 `_hub_errors`에 준다
_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (UnknownCard, 404),  # 그런 카드가 없다
    (CardBusy, 409),  # 지금 닫을 수 없다(통화 중·주문됨)
    (AlreadyAnswered, 409),  # 답은 한 번만
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
    async def accept(order_id: str) -> dict[str, Any]:
        with _hub_errors({404: "그런 주문이 없어요"}):
            return hub.accept(order_id)

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
    새 문자마다 `sms`(문자 하나). `window_s`가 지나면 `sms.end`를 보내고 닫는다(브라우저
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


async def _until_window(
    events: AsyncIterator[OwnerEvent], window_s: float
) -> AsyncIterator[ServerSentEvent]:
    loop = asyncio.get_running_loop()
    end = loop.time() + window_s
    async with aclosing(events) as stream:  # type: ignore[type-var]
        while (left := end - loop.time()) > 0:
            try:
                event = await asyncio.wait_for(anext(stream), left)
            except TimeoutError:
                break
            yield ServerSentEvent(event=event.name, data=event.data, id=str(event.id))
    yield ServerSentEvent(event="sms.end", data={})
