"""사장님 허브 (4단계 spec "사장님 허브"). `OrderSink`를 구현해 카드를 메모리에 두고 구독자에게
보낸다.

    hub = OwnerHub(store=store, clock=clock)
    phone_router(..., order_sink=hub, ...)       # 통화 세션이 확정 주문을 넘긴다
    app.include_router(owner_router(hub, key=...))  # 사장님 화면 SSE·POST (`http.py`)

- 가게 하나, 메모리 저장, uvicorn 워커 1개 전제. 서버를 다시 띄우면 카드가 사라진다
- 카드는 통화 id(= `Order.id`)마다 하나다. 이벤트는 카드 전체를 덮어쓴다(바뀐 칸만 보내지 않는다)
- 구독자(`events`)는 먼저 `snapshot`(지금 카드 전체)을 받고, 그 뒤 `order.update`를 받는다.
  이벤트마다 늘어나는 번호(`id`)를 단다. 재연결 때 이어 받기 버퍼는 없다(늘 snapshot부터)
- `submit`은 이벤트 루프에서 불리면 바로 처리한다(구독자 큐에 `put_nowait`만, 막히지 않음).
  다른 스레드에서 불리면 `call_soon_threadsafe`로 루프에 넘긴다
- 구독자 큐는 크기 제한이 없다. SSE를 안 읽는 동안에도 통화 쪽이 막히지 않게 (V1 판정 6)

카드의 한국어 메뉴명·단가는 `menu_id`로 가게 메뉴에서 찾는다. 계약 `Order`에 이름·단가가 아직
없어서다(계약 제안 7, 4단계 티켓 09). 시각·금액은 번역 없이 코드가 고정 표기(`18:30`, `25,000₩`)로
넣는다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agenthan.core.contracts import Order, StoreKnowledge
from agenthan.voice.call.ports import Clock

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerEvent:
    """사장님 화면으로 가는 이벤트 하나. HTTP 쪽이 SSE `event`·`data`·`id`로 옮긴다."""

    name: str  # snapshot · order.update
    data: dict[str, Any]
    id: int


class UnknownCard(KeyError):
    """그런 카드가 없다 (HTTP 404)."""


class OwnerHub:
    """`OrderSink` 구현 + 사장님 화면 구독. 이벤트 루프 하나에 묶여 쓴다."""

    def __init__(self, *, store: StoreKnowledge, clock: Clock) -> None:
        self._store = store
        self._clock = clock
        self._cards: dict[str, dict[str, Any]] = {}  # 통화 id → 카드 (넣은 순서 = 온 순서)
        self._subscribers: set[asyncio.Queue[OwnerEvent]] = set()
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- OrderSink ---------------------------------------------------------------------

    def submit(self, order: Order) -> None:
        """통화 세션이 주문 확정 때 부른다. 바로 돌아온다(계약 제안 8)."""
        log.info("주문 %s: %s, 항목 %d개, 픽업 %s",
                 order.id, order.status, len(order.lines), order.pickup_time)
        self._on_loop(self._take_order, order)

    # --- 사장님 화면 ---------------------------------------------------------------------

    def accept(self, order_id: str) -> dict[str, Any]:
        """접수 버튼: 처리 상태 new → accepted. 바뀐 카드를 돌려준다. 없으면 `UnknownCard`.

        이미 접수한 카드면 그대로 돌려준다(두 화면에서 같이 눌러도 된다).
        """
        card = self._cards.get(order_id)
        if card is None:
            raise UnknownCard(order_id)
        if card["status"] != "accepted":
            card = {**card, "status": "accepted"}
            self._cards[order_id] = card
            self._publish("order.update", card)
        return card

    async def events(self) -> AsyncIterator[OwnerEvent]:
        """구독. 처음에 `snapshot` 하나, 그 뒤 바뀐 카드마다 이벤트 하나. 닫으면 구독을 푼다."""
        self._bind()
        queue: asyncio.Queue[OwnerEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield OwnerEvent("snapshot", self._snapshot(), self._seq)
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    # --- 안쪽 ---------------------------------------------------------------------------

    def _take_order(self, order: Order) -> None:
        if order.status != "confirmed":
            return  # 넘김(handoff) 카드는 4단계 티켓 02
        card = self._order_card(order)
        self._cards[order.id] = card
        self._publish("order.update", card)

    def _order_card(self, order: Order) -> dict[str, Any]:
        lines = []
        for line in order.lines:
            item = self._store.get_menu(line.menu_id)
            name = item.name_ko if item is not None else line.menu_id
            extra = {o.name: o.extra_price for o in item.options} if item is not None else {}
            unit = (item.price if item is not None else 0) + sum(
                extra.get(o, 0) for o in line.options
            )
            lines.append({
                "menu_id": line.menu_id,
                "name_ko": name,
                "quantity": line.quantity,
                "options": list(line.options),
                "request": line.request,
                "subtotal": unit * line.quantity,
            })
        total = sum(line["subtotal"] for line in lines)
        return {
            "id": order.id,
            "status": "new",
            "received_at": self._clock.now().isoformat(),
            "pickup_time": order.pickup_time,
            "pickup_label": clock_label(order.pickup_time),
            "lines": lines,
            "total": total,
            "total_label": won_label(total),
        }

    def _snapshot(self) -> dict[str, Any]:
        return {"orders": list(self._cards.values())}

    def _publish(self, name: str, data: dict[str, Any]) -> None:
        self._seq += 1
        event = OwnerEvent(name, data, self._seq)
        for queue in self._subscribers:
            queue.put_nowait(event)

    def _bind(self) -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

    def _on_loop(self, fn: Any, *args: Any) -> None:
        """허브의 루프에서 `fn`을 부른다. 지금 그 루프 위면 바로, 다른 스레드면 넘긴다."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and self._loop is None:
            self._loop = running
        if self._loop is None or running is self._loop:
            fn(*args)  # 루프에 아직 안 묶였으면(구독자 없음) 그냥 부른다
        else:
            self._loop.call_soon_threadsafe(fn, *args)


def clock_label(iso: str) -> str:
    """ISO 8601 시각 → `18:30` (가게 시각 그대로, 24시간제). 비면 빈 글."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).strftime("%H:%M")


def won_label(amount: int) -> str:
    """원 금액 → `25,000₩` (언어와 상관없는 고정 표기)."""
    return f"{amount:,}₩"
