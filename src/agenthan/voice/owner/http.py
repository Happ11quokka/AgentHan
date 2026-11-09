"""사장님 화면 HTTP (4단계 spec "사장님 화면 HTTP"). 허브 하나를 SSE 하나와 POST로 연다.

    app.include_router(owner_router(hub, key=settings.owner_key))

- `GET /api/owner/stream?key=…`: SSE. 먼저 `snapshot`(`{"orders": [카드…]}`),
  그 뒤 `order.update`(카드 전체)
- `POST /api/owner/orders/{id}/accept?key=…`: 접수(new → accepted). 바뀐 카드를 돌려준다.
  없는 주문은 404

- 모든 경로가 쿼리 `key`(비밀 토큰, `PHONE_OWNER_KEY`)를 요구한다. `EventSource`가 헤더를 못 붙여서
  쿼리로 받는다. 없거나 틀리거나 서버에 토큰이 없으면 401
- SSE는 FastAPI 내장 응답을 쓴다: 15초 핑, `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
  이벤트 `id`는 허브 번호다. `Last-Event-ID`는 읽지 않는다(재연결하면 늘 snapshot부터)
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent

from agenthan.voice.owner.hub import OwnerHub, UnknownCard

OWNER_PREFIX = "/api/owner"


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

    return router
