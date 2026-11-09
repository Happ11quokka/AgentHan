"""사장님 허브 (4단계 spec "사장님 허브"). `OrderSink`와 `OwnerChannel`을 구현해 카드를 메모리에
두고 구독자에게 보낸다.

    hub = OwnerHub(store=store, clock=clock)
    phone_router(..., order_sink=hub, owner=hub, ...)  # 통화 세션이 주문·통화 상황을 넘긴다
    app.include_router(owner_router(hub, key=...))  # 사장님 화면 SSE·POST (`http.py`)

- 가게 하나, 메모리 저장, uvicorn 워커 1개 전제. 서버를 다시 띄우면 카드가 사라진다
- 카드는 통화 id(= `Order.id`)마다 하나다. 이벤트는 카드 전체를 덮어쓴다(바뀐 칸만 보내지 않는다)
- 주문 카드(`order.update`): 확정 주문. 처리 상태 new → accepted(접수)
- 통화 카드(`call.update`, 티켓 02): 통화 중(live) → 확정되면 ordered(같은 id의 주문 카드가 생기고
  통화 카드는 빠진다) · 넘김이면 handoff("손님 연락 필요", 장바구니 + 마지막 손님 말 3줄) · 확정
  없이 끝나면 missed(회색 미완료, 담긴 메뉴 + 끝난 이유). handoff·missed는 사장님이 닫는다(closed)
- 구독자(`events`)는 먼저 `snapshot`(`{"orders": [...], "calls": [...]}`, 통화 카드는 ordered·closed
  빼고)을 받고, 그 뒤 `order.update`·`call.update`를 받는다.
  이벤트마다 늘어나는 번호(`id`)를 단다. 재연결 때 이어 받기 버퍼는 없다(늘 snapshot부터)
- `submit`·통화 알림은 이벤트 루프에서 불리면 바로 처리한다(구독자 큐에 `put_nowait`만, 막히지
  않음). 다른 스레드에서 불리면 `call_soon_threadsafe`로 루프에 넘긴다
- 구독자 큐는 크기 제한이 없다. SSE를 안 읽는 동안에도 통화 쪽이 막히지 않게 (V1 판정 6)

카드의 한국어 메뉴명·단가·받은 시각은 주문에 담긴 값(`OrderLine.name`·`unit_price`,
`Order.created_at`, 계약 v1 제안 6·7)을 쓴다. 비어 있으면 메뉴명·단가는 `menu_id`로 가게 메뉴에서
찾고, 받은 시각은 허브 시계로 찍는다. 시각·금액은 번역 없이 코드가 고정 표기(`18:30`, `25,000₩`)로
넣는다.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agenthan.core.contracts import Order, OrderLine, StoreKnowledge
from agenthan.voice.call.ports import Clock

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerEvent:
    """사장님 화면으로 가는 이벤트 하나. HTTP 쪽이 SSE `event`·`data`·`id`로 옮긴다."""

    name: str  # snapshot · order.update · call.update
    data: dict[str, Any]
    id: int


class UnknownCard(KeyError):
    """그런 카드가 없다 (HTTP 404)."""


class CardBusy(Exception):
    """그 카드는 지금 닫을 수 없다: 통화 중이거나 주문이 됐다 (HTTP 409)."""


# 넘김 카드에 붙이는 마지막 손님 말 줄 수 (spec 이야기 49)
HEARD_LINES = 3
# 사장님이 닫을 수 있는 통화 카드 상태
_CLOSABLE = frozenset({"handoff", "missed"})


class OwnerHub:
    """`OrderSink`·`OwnerChannel` 구현 + 사장님 화면 구독. 이벤트 루프 하나에 묶여 쓴다."""

    def __init__(self, *, store: StoreKnowledge, clock: Clock) -> None:
        self._store = store
        self._clock = clock
        self._cards: dict[str, dict[str, Any]] = {}  # 통화 id → 주문 카드 (넣은 순서 = 온 순서)
        self._calls: dict[str, dict[str, Any]] = {}  # 통화 id → 통화 카드 (넣은 순서 = 걸려 온 순)
        self._heard: dict[str, deque[str]] = {}  # 통화 중인 id → 마지막 손님 말들
        self._subscribers: set[asyncio.Queue[OwnerEvent]] = set()
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- OrderSink ---------------------------------------------------------------------

    def submit(self, order: Order) -> None:
        """통화 세션이 주문 확정 때 부른다. 바로 돌아온다(계약 제안 8)."""
        log.info("주문 %s: %s, 항목 %d개, 픽업 %s",
                 order.id, order.status, len(order.lines), order.pickup_time)
        self._on_loop(self._take_order, order)

    # --- OwnerChannel (통화 세션이 부른다, 티켓 02) ------------------------------------

    def call_started(self, call_id: str) -> None:
        self._on_loop(self._start_call, call_id)

    def cart_changed(self, call_id: str, lines: Sequence[OrderLine]) -> None:
        self._on_loop(self._change_cart, call_id, tuple(lines))

    def customer_said(self, call_id: str, text: str) -> None:
        self._on_loop(self._hear, call_id, text)

    def bot_said(self, call_id: str, text: str) -> None:
        """AI 말은 지금 카드에 쓰지 않는다(통화 자막은 티켓 10)."""

    def call_ended(self, call_id: str, reason: str) -> None:
        self._on_loop(self._end_call, call_id, reason)

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

    def dismiss(self, call_id: str) -> dict[str, Any]:
        """닫기 버튼(넘김·미완료 카드): → closed. 바뀐 카드를 돌려준다.

        없으면 `UnknownCard`, 통화 중이거나 주문이 된 카드면 `CardBusy`. 이미 닫은 카드면 그대로
        돌려준다(두 화면에서 같이 눌러도 된다).
        """
        card = self._calls.get(call_id)
        if card is None:
            raise UnknownCard(call_id)
        if card["status"] == "closed":
            return card
        if card["status"] not in _CLOSABLE:
            raise CardBusy(call_id)
        return self._put_call({**card, "status": "closed"})

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
        if order.status == "handoff":
            self._hand_off(order)
            return
        if order.status != "confirmed":
            return
        card = self._order_card(order)
        self._cards[order.id] = card
        self._publish("order.update", card)
        call = self._calls.get(order.id)
        if call is not None and call["status"] == "live":
            self._put_call({**call, "status": "ordered"})  # 화면은 통화 카드를 빼고 주문 카드만

    def _start_call(self, call_id: str) -> None:
        if call_id in self._calls:
            return
        self._heard[call_id] = deque(maxlen=HEARD_LINES)
        now = self._clock.now()
        self._put_call({
            "id": call_id,
            "status": "live",
            "started_at": now.isoformat(),
            "started_label": now.strftime("%H:%M"),
            "reason": "",  # 끝난 이유 (`CallSession._end`): handoff·hangup·cancelled·no_input·error
            **self._cart_fields(()),
            "heard": [],  # 넘김 카드만: 마지막 손님 말 (한국어 원문, 오래된 것부터)
        })

    def _change_cart(self, call_id: str, lines: tuple[OrderLine, ...]) -> None:
        call = self._calls.get(call_id)
        if call is None or call["status"] != "live":
            return
        fields = self._cart_fields(lines)
        if fields["lines"] != call["lines"]:
            self._put_call({**call, **fields})

    def _hear(self, call_id: str, text: str) -> None:
        heard = self._heard.get(call_id)
        if heard is not None:
            heard.append(text)

    def _hand_off(self, order: Order) -> None:
        """넘김 주문 → "손님 연락 필요" 카드. 통화 카드가 없었으면(알림 없이 온 주문) 새로 만든다"""
        if order.id not in self._calls:
            self._start_call(order.id)
        call = self._calls[order.id]
        heard = list(self._heard.get(order.id, ()))
        self._put_call({**call, **self._cart_fields(order.lines), "status": "handoff",
                        "reason": "handoff", "heard": heard})

    def _end_call(self, call_id: str, reason: str) -> None:
        self._heard.pop(call_id, None)
        call = self._calls.get(call_id)
        if call is not None and call["status"] == "live":
            self._put_call({**call, "status": "missed", "reason": reason})

    def _put_call(self, card: dict[str, Any]) -> dict[str, Any]:
        self._calls[card["id"]] = card
        self._publish("call.update", card)
        return card

    def _cart_fields(self, lines: Sequence[OrderLine]) -> dict[str, Any]:
        card_lines = self._card_lines(lines)
        total = sum(line["subtotal"] for line in card_lines)
        return {"lines": card_lines, "total": total, "total_label": won_label(total)}

    def _order_card(self, order: Order) -> dict[str, Any]:
        lines = self._card_lines(order.lines)
        total = sum(line["subtotal"] for line in lines)
        return {
            "id": order.id,
            "status": "new",
            "received_at": order.created_at or self._clock.now().isoformat(),
            "pickup_time": order.pickup_time,
            "pickup_label": clock_label(order.pickup_time),
            "lines": lines,
            "total": total,
            "total_label": won_label(total),
        }

    def _card_lines(self, order_lines: Sequence[OrderLine]) -> list[dict[str, Any]]:
        """주문 카드·통화 카드가 같이 쓰는 줄 모양."""
        lines = []
        for line in order_lines:
            name, unit = self._name_and_unit_price(line)
            lines.append({
                "menu_id": line.menu_id,
                "name_ko": name,
                "quantity": line.quantity,
                "options": list(line.options),
                "request": line.request,
                "subtotal": unit * line.quantity,
            })
        return lines

    def _name_and_unit_price(self, line: OrderLine) -> tuple[str, int]:
        """줄의 한국어 이름·한 개 가격. 주문에 담긴 값(손님이 들은 값)을 먼저 쓴다.

        이름이 비어 있으면(계약 v1 전 주문·다른 구현) 가게 메뉴에서 찾는다: 가격 + 고른 옵션
        추가금. 메뉴도 없으면 이름 = menu_id, 단가 0.
        """
        if line.name:
            return line.name, line.unit_price
        item = self._store.get_menu(line.menu_id)
        if item is None:
            return line.menu_id, 0
        extra = {o.name: o.extra_price for o in item.options}
        return item.name_ko, item.price + sum(extra.get(o, 0) for o in line.options)

    def _snapshot(self) -> dict[str, Any]:
        calls = [c for c in self._calls.values() if c["status"] not in ("ordered", "closed")]
        return {"orders": list(self._cards.values()), "calls": calls}

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
