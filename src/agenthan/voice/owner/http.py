"""사장님 화면 HTTP (4단계 spec "사장님 화면 HTTP"). 허브 하나를 SSE 하나와 POST로 연다.

    app.include_router(owner_router(hub, key=settings.owner_key))

- `GET /api/owner/stream?key=…`: SSE. 먼저 `snapshot`(`{"orders": [...], "calls": [...],
  "questions": [...]}`), 그 뒤 `order.update`·`call.update`·`owner.question`·`owner.question.update`
  (카드 전체)
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
from collections.abc import AsyncIterator
from contextlib import aclosing
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
        try:
            return hub.accept(order_id)
        except UnknownCard as err:
            raise HTTPException(status_code=404, detail="그런 주문이 없어요") from err

    @router.post("/calls/{call_id}/dismiss")
    async def dismiss(call_id: str) -> dict[str, Any]:
        try:
            return hub.dismiss(call_id)
        except UnknownCard as err:
            raise HTTPException(status_code=404, detail="그런 통화 카드가 없어요") from err
        except CardBusy as err:
            raise HTTPException(status_code=409, detail="통화 중이거나 주문이 된 카드예요") from err

    @router.post("/questions/{question_id}/answer")
    async def answer(question_id: str, body: AnswerBody) -> dict[str, Any]:
        try:
            return hub.answer_question(question_id, body.answer, body.text)
        except UnknownCard as err:
            raise HTTPException(status_code=404, detail="그런 질문이 없어요") from err
        except AlreadyAnswered as err:
            raise HTTPException(status_code=409, detail="이미 답한 질문이에요") from err
        except ValueError as err:  # 자유 입력인데 글이 비었다
            raise HTTPException(status_code=422, detail="답할 글을 적어 주세요") from err

    @router.post("/questions/{question_id}/seen")
    async def seen(question_id: str) -> dict[str, Any]:
        try:
            return hub.see_question(question_id)
        except UnknownCard as err:
            raise HTTPException(status_code=404, detail="그런 질문이 없어요") from err

    return router


def customer_texts_router(hub: OwnerHub, *, window_s: float = TEXTS_WINDOW_S) -> APIRouter:
    """손님 통화 화면의 "받은 문자" 스트림 (티켓 07). 사장님 토큰이 없다(통화 id는 추측할 수 없다).

    `GET /api/phone/calls/{id}/sms`: SSE. 먼저 `sms.snapshot`(`{"call_id", "sms": [...]}`), 그 뒤
    새 문자마다 `sms`(문자 하나). `window_s`가 지나면 `sms.end`를 보내고 닫는다(브라우저
    `EventSource`가 다시 붙지 않게 화면이 `sms.end`에서 닫는다). 허브가 모르는 통화는 404
    """

    def watch(call_id: str) -> AsyncIterator[OwnerEvent]:
        # 스트림을 열기 전에(의존성에서) 모르는 통화를 404로 거절한다
        try:
            return hub.watch_texts(call_id)
        except UnknownCard as err:
            raise HTTPException(status_code=404, detail="그런 통화가 없어요") from err

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
