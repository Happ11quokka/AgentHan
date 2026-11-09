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
- 질문 카드(`owner.question` 새 카드 · `owner.question.update` 바뀐 카드, 티켓 05): 근거 없는 메뉴
  질문. 열림(open) → 사장님 버튼 답(돼요/안 돼요/모르겠어요)이면 답함(answered). 답은 한 번만 받고
  통화 세션의 받는 곳(`open_question`의 `reply`)으로 넘긴다. 마감 = 보낸 시각 + `question_wait_s`
- 구독자(`events`)는 먼저 `snapshot`(`{"orders": [...], "calls": [...], "questions": [...]}`, 통화
  카드는 ordered·closed 빼고)을 받고, 그 뒤 `order.update`·`call.update`·`owner.question*`을 받는다.
  이벤트마다 늘어나는 번호(`id`)를 단다. 재연결 때 이어 받기 버퍼는 없다(늘 snapshot부터)
- `submit`·통화 알림은 이벤트 루프에서 불리면 바로 처리한다(구독자 큐에 `put_nowait`만, 막히지
  않음). 다른 스레드에서 불리면 `call_soon_threadsafe`로 루프에 넘긴다
- 구독자 큐는 크기 제한이 없다. SSE를 안 읽는 동안에도 통화 쪽이 막히지 않게 (V1 판정 6)

카드의 한국어 메뉴명·단가·받은 시각은 주문에 담긴 값(`OrderLine.name`·`unit_price`,
`Order.created_at`, 계약 v1 제안 6·7)을 쓴다. 비어 있으면 메뉴명·단가는 `menu_id`로 가게 메뉴에서
찾고, 받은 시각은 허브 시계로 찍는다. 시각·금액은 번역 없이 코드가 고정 표기(`18:30`, `25,000₩`)로
넣는다.

사장님 언어 (티켓 03, `OwnerHub(..., translator=..., owner_lang="ru")`).
카드 줄(주문·통화 카드 같음):
- `name_owner`: `menu_id`로 찾은 원어 이름(`MenuItem.name_native`, 번역기 안 씀). 비면 `name_ko`
- `options_owner`: 옵션 이름을 정적 사전(`lexicon.py`)으로. 사전에 없으면 한국어 그대로
- `request`(한국어 원문) + `request_owner`(번역, 아직·실패·버림이면 "") + `badges`(`remove`·
  `allergy`, 한국어 원문 규칙 `badges.py`)
- 통화 카드는 `heard`(넘김 카드 손님 말 원문) 옆에 `heard_owner`(줄마다 번역 또는 "")
- 질문 카드는 `menu_name_owner`(원어 이름, 비면 한국어)와 `text_owner`(손님 질문 번역 또는 "")
- 번역은 비동기다. 카드는 원문으로 먼저 보내고, 번역이 오면 같은 카드를 다시 보낸다(주문 카드와,
  ordered·closed가 아닌 통화 카드, 질문 카드는 `owner.question.update`). 같은 문장은 통화 동안
  한 번만 번역한다(`translation.py`). 번역기가 없으면(키 없음) 원문만
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from agenthan.core.contracts import Order, OrderLine, StoreKnowledge
from agenthan.voice.call.defaults import OWNER_WAIT_S
from agenthan.voice.call.ports import Clock
from agenthan.voice.dialog.owner_question import OWNER_BUTTONS, OwnerQuestion, OwnerReply
from agenthan.voice.owner.badges import request_badges
from agenthan.voice.owner.lexicon import lexicon_for
from agenthan.voice.owner.translate import Translator
from agenthan.voice.owner.translation import CardTexts

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerEvent:
    """사장님 화면으로 가는 이벤트 하나. HTTP 쪽이 SSE `event`·`data`·`id`로 옮긴다."""

    name: str  # snapshot · order.update · call.update · owner.question · owner.question.update
    data: dict[str, Any]
    id: int


class UnknownCard(KeyError):
    """그런 카드가 없다 (HTTP 404)."""


class CardBusy(Exception):
    """그 카드는 지금 닫을 수 없다: 통화 중이거나 주문이 됐다 (HTTP 409)."""


class AlreadyAnswered(Exception):
    """이미 답한 질문이다. 답은 한 번만 받는다 (HTTP 409)."""


# 넘김 카드에 붙이는 마지막 손님 말 줄 수 (spec 이야기 49)
HEARD_LINES = 3
# 사장님이 닫을 수 있는 통화 카드 상태
_CLOSABLE = frozenset({"handoff", "missed"})


class OwnerHub:
    """`OrderSink`·`OwnerChannel` 구현 + 사장님 화면 구독. 이벤트 루프 하나에 묶여 쓴다."""

    def __init__(
        self,
        *,
        store: StoreKnowledge,
        clock: Clock,
        question_wait_s: float = OWNER_WAIT_S,
        translator: Translator | None = None,
        owner_lang: str = "ru",
    ) -> None:
        self._store = store
        self._clock = clock
        self._question_wait = timedelta(seconds=question_wait_s)
        self._questions: dict[str, dict[str, Any]] = {}  # 질문 id → 질문 카드 (온 순서)
        self._replies: dict[str, Callable[[OwnerReply], None]] = {}  # 답을 아직 안 넘긴 질문
        self._lexicon = lexicon_for(owner_lang)
        self._texts = CardTexts(translator, store=store, lexicon=self._lexicon)
        self._tasks: set[asyncio.Task[None]] = set()  # 진행 중인 번역 (GC 막기)
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

    def open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerReply], None]
    ) -> None:
        self._on_loop(self._open_question, call_id, question, reply)

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

    def answer_question(self, question_id: str, answer: str) -> dict[str, Any]:
        """질문 카드 버튼 답(yes·no·unsure). 바뀐 카드를 돌려주고 답을 통화 세션에 넘긴다.

        없는 질문이면 `UnknownCard`, 이미 답했으면 `AlreadyAnswered`, 모르는 답이면 `ValueError`.
        세션의 받는 곳이 실패해도(통화가 이미 끝남 등) 카드는 답함으로 남는다.
        """
        card = self._questions.get(question_id)
        if card is None:
            raise UnknownCard(question_id)
        if answer not in OWNER_BUTTONS:
            raise ValueError(f"모르는 답: {answer!r}")
        if card["status"] == "answered":
            raise AlreadyAnswered(question_id)
        card = {**card, "status": "answered", "answer": answer}
        self._questions[question_id] = card
        self._publish("owner.question.update", card)
        reply = self._replies.pop(question_id, None)
        if reply is not None:
            try:
                reply(OwnerReply(card["number"], answer))  # type: ignore[arg-type]
            except Exception:
                log.exception("사장님 답을 통화에 넘기지 못함 (%s)", question_id)
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
        if order.status == "handoff":
            self._hand_off(order)
            return
        if order.status != "confirmed":
            return
        card = self._dressed(self._order_card(order))
        self._cards[order.id] = card
        self._publish("order.update", card)
        self._translate_later(card)
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
        changed = self._dressed({**call, **self._cart_fields(lines)})
        if changed["lines"] != call["lines"]:
            self._put_call(changed)

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

    def _open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerReply], None]
    ) -> None:
        question_id = f"{call_id}-q{question.number}"
        now = self._clock.now()
        card = {
            "id": question_id,
            "call_id": call_id,
            "number": question.number,
            "menu_id": question.menu_id,
            "menu_name": question.menu_name,  # 한국어
            "menu_name_owner": self._owner_name(question.menu_id, question.menu_name),  # 티켓 03
            "text": question.text,  # 손님 질문 원문 (한국어)
            "text_owner": self._texts.known(call_id, question.text),  # 번역, 아직·실패면 ""
            "sent_at": now.isoformat(),
            "deadline_at": (now + self._question_wait).isoformat(),  # 화면 카운트다운과 같은 시계
            "status": "open",  # open → answered (시간 초과·통화 끝은 티켓 06·07)
            "answer": "",  # yes · no · unsure
        }
        self._questions[question_id] = card
        self._replies[question_id] = reply
        self._publish("owner.question", card)
        self._translate_soon(call_id, [question.text])

    def _put_call(self, card: dict[str, Any]) -> dict[str, Any]:
        card = self._dressed(card)
        self._calls[card["id"]] = card
        self._publish("call.update", card)
        self._translate_later(card)
        return card

    # --- 번역 (티켓 03): 카드는 원문으로 먼저, 번역이 오면 같은 카드를 다시 ---------------

    def _dressed(self, card: dict[str, Any]) -> dict[str, Any]:
        """카드의 자유 문장(요청사항, 넘김 카드 손님 말)에 캐시에 있는 번역을 붙인다(없으면 "")."""
        call_id = card["id"]
        lines = [{**line, "request_owner": self._texts.known(call_id, line["request"])}
                 for line in card["lines"]]
        dressed = {**card, "lines": lines}
        if "heard" in card:
            dressed["heard_owner"] = [self._texts.known(call_id, t) for t in card["heard"]]
        return dressed

    def _translate_later(self, card: dict[str, Any]) -> None:
        texts = [line["request"] for line in card["lines"]] + list(card.get("heard", ()))
        self._translate_soon(card["id"], texts)

    def _translate_soon(self, call_id: str, texts: list[str]) -> None:
        todo = self._texts.claim(call_id, texts)
        if not todo:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 루프 밖(구독자 없이 부른 동기 코드): 번역 없이 원문만
            self._texts.release(call_id, todo)
            return
        task = loop.create_task(self._translate(call_id, todo))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _translate(self, call_id: str, texts: list[str]) -> None:
        if not await self._texts.fetch(call_id, texts):
            return  # 실패·버림: 원문 카드 그대로, 다시 보내지 않는다
        order = self._cards.get(call_id)
        if order is not None and (dressed := self._dressed(order)) != order:
            self._cards[call_id] = dressed
            self._publish("order.update", dressed)
        call = self._calls.get(call_id)
        if (call is not None and call["status"] not in ("ordered", "closed")
                and (dressed := self._dressed(call)) != call):
            self._calls[call_id] = dressed
            self._publish("call.update", dressed)
        for question_id, question in list(self._questions.items()):
            text_owner = self._texts.known(call_id, question["text"])
            if question["call_id"] == call_id and question["text_owner"] != text_owner:
                self._questions[question_id] = {**question, "text_owner": text_owner}
                self._publish("owner.question.update", self._questions[question_id])

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
                "name_owner": self._owner_name(line.menu_id, name),
                "quantity": line.quantity,
                "options": list(line.options),
                "options_owner": [self._lexicon.option(o) for o in line.options],
                "request": line.request,
                "badges": request_badges(line.request),
                "subtotal": unit * line.quantity,
            })
        return lines

    def _owner_name(self, menu_id: str, name_ko: str) -> str:
        """사장님이 쓰는 원어 이름(`MenuItem.name_native`, 번역기 안 씀). 비면 한국어 이름."""
        item = self._store.get_menu(menu_id)
        return (item.name_native if item else "") or name_ko

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
        return {"orders": list(self._cards.values()), "calls": calls,
                "questions": list(self._questions.values())}

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
