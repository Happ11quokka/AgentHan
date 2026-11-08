"""주문 대화 엔진. 코드 상태기계가 대화를 이끌고 NLU는 슬롯만 뽑는다 (spec D1, §5).

한 턴의 흐름 (`handle`):
1. `_parse`: 발화 → act 목록 (티켓 05가 NLU 앞에 규칙 단계를 끼운다)
2. `_dispatch`: act마다 (상태, act 종류)에 등록된 처리기를 부른다. 없으면 `_on_unhandled`
3. `_finish`: 처리기들이 남긴 표시(`_Turn`)를 보고 이번 턴의 질문 하나를 정한다

처리기 추가는 `@on("act 종류", State.X, ...)`를 붙인 메서드 하나를 쓰면 된다. 공용 코드는 안 고친다.
질문은 한 턴에 하나만 한다: 질문은 `_ask`로만 하고, `_Turn.asked`가 이미 참이면 더 묻지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from agenthan.core.contracts import MenuItem, Order, StoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.cart import Cart, CartLine
from agenthan.voice.dialog.pickup import judge_pickup, today_at
from agenthan.voice.dialog.states import TERMINAL, State
from agenthan.voice.matching import MenuMatcher
from agenthan.voice.nlu.base import Act, ActType, Nlu, NluContext
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.timeparse import parse_pickup

OrderStatus = Literal["draft", "confirmed", "handoff"]


@dataclass(frozen=True)
class TurnResult:
    utterances: list[str]  # 손님에게 할 말 (2단계에서 TTS로 보냄)
    state: State
    cart: Cart  # 이번 턴 뒤 장바구니 (시나리오 테스트가 비교)
    order: Order | None  # CLOSING·FALLBACK에서만 채워짐


@dataclass
class _Turn:
    """한 턴 동안 처리기들이 남기는 표시. `_finish`가 읽는다."""

    now: datetime
    utterances: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)  # 이번 턴에 담은 항목 문구
    done_ordering: bool = False
    asked: bool = False
    unhandled: bool = False
    order: Order | None = None


Handler = Callable[["DialogEngine", _Turn, Act], None]
_HANDLERS: dict[tuple[State, ActType], Handler] = {}


def on(act_type: ActType, *states: State) -> Callable[[Handler], Handler]:
    """(상태, act 종류) → 처리기 등록."""

    def register(fn: Handler) -> Handler:
        for state in states:
            _HANDLERS[(state, act_type)] = fn
        return fn

    return register


class DialogEngine:
    def __init__(
        self, store: StoreKnowledge, nlu: Nlu, config: StoreConfig, *, order_id: str
    ) -> None:
        self._nlu = nlu
        self._config = config
        self._order_id = order_id
        self._menu: list[MenuItem] = store.list_menu()  # 통화 시작 때 한 번 (spec §4)
        self._matcher = MenuMatcher(self._menu)
        self._state = State.GREETING
        self._cart = Cart()
        self._pickup: datetime | None = None
        self._pickup_offer: datetime | None = None  # ASK_PICKUP에서 "네"를 기다리는 시각
        self._pickup_text = ""  # 주문 중에 말했지만 되물어야 하는 시각 원문
        self._last_question = ""
        self._transcript: list[str] = []
        # CLARIFY가 무엇을 기다리는지. 티켓 02(메뉴 확인)·03(옵션)이 자기 타입을 넣는다.
        self._pending: Any = None

    # ------------------------------------------------------------ 진입점

    def start(self, now: datetime) -> TurnResult:
        turn = _Turn(now)
        self._say(turn, templates.greeting(self._config.store_name))
        self._state = State.ORDERING
        return self._result(turn)

    def handle(self, text: str, now: datetime) -> TurnResult:
        if self._state is State.GREETING:
            raise RuntimeError("start()를 먼저 불러야 해요")
        turn = _Turn(now)
        if self._state is State.DONE:
            return self._result(turn)
        self._transcript.append(f"손님: {text}")
        for act in self._parse(text):
            self._dispatch(turn, act)
            if self._state in TERMINAL:
                break
        self._finish(turn)
        return self._result(turn)

    # ------------------------------------------------------------ 턴 골격

    def _parse(self, text: str) -> list[Act]:
        ctx = NluContext(
            menu_names=tuple(m.name_ko for m in self._menu),
            cart_summary=self._cart.summary(),
            last_question=self._last_question,
            state=self._state.value,
        )
        return self._nlu.parse(text, ctx)

    def _dispatch(self, turn: _Turn, act: Act) -> None:
        handler = _HANDLERS.get((self._state, act.type), DialogEngine._on_unhandled)
        handler(self, turn, act)

    def _finish(self, turn: _Turn) -> None:
        """처리기가 질문을 안 했으면 상태에 맞는 질문 하나를 붙인다."""
        if self._state is State.ORDERING and not turn.asked:
            if turn.done_ordering:
                if turn.accepted:
                    self._say(turn, templates.items_noted(turn.accepted))
                self._advance(turn)
            elif turn.accepted:
                self._ask(turn, templates.item_accepted(turn.accepted))

    def _advance(self, turn: _Turn) -> None:
        """주문이 끝났을 때 빈 슬롯을 차례로 보고 다음 상태와 질문을 정한다.

        티켓 03은 장바구니 다음에 필수 옵션 검사를 끼운다.
        """
        if self._cart.is_empty():
            self._state = State.ORDERING
            self._ask(turn, templates.ask_order())
        elif self._pickup is None:
            self._state = State.ASK_PICKUP
            self._ask_pickup(turn)
        else:
            self._state = State.CONFIRM
            readback = templates.final_readback(
                self._cart.summary(), self._pickup, self._cart.total()
            )
            self._ask(turn, readback)

    def _result(self, turn: _Turn) -> TurnResult:
        result = TurnResult(list(turn.utterances), self._state, self._cart, turn.order)
        if self._state in TERMINAL:
            self._state = State.DONE
        return result

    # ------------------------------------------------------------ 말하기

    def _say(self, turn: _Turn, text: str) -> None:
        turn.utterances.append(text)
        self._transcript.append(f"AI: {text}")

    def _ask(self, turn: _Turn, question: str) -> None:
        if turn.asked:
            return
        turn.asked = True
        self._last_question = question
        self._say(turn, question)

    def _on_unhandled(self, turn: _Turn, act: Act) -> None:
        """이 상태에서 받지 않는 act. 직전 질문을 다시 한다. 티켓 05가 실패 횟수를 센다."""
        if turn.unhandled or turn.accepted or turn.asked:
            return
        turn.unhandled = True
        self._say(turn, templates.not_understood())
        self._ask(turn, self._last_question or templates.ask_order())

    # ------------------------------------------------------------ ORDERING: 메뉴 담기

    @on("add_item", State.ORDERING)
    def _on_add_item(self, turn: _Turn, act: Act) -> None:
        item = self._resolve_menu(turn, act)
        if item is not None:
            self._add_to_cart(turn, item, act)

    def _resolve_menu(self, turn: _Turn, act: Act) -> MenuItem | None:
        """매처 판정 → 담을 메뉴. 못 담으면 안내하고 None.

        티켓 02가 confirm·choose·sold_out 판정을 붙인다.
        """
        result = self._matcher.match(act.menu_text, act.menu_guess)
        if result.decision == "accept" and result.item is not None:
            return result.item
        self._say(turn, templates.not_on_menu([m.name_ko for m in self._menu]))
        return None

    def _add_to_cart(self, turn: _Turn, item: MenuItem, act: Act) -> None:
        """장바구니에 담고 되받을 문구를 남긴다. 티켓 03이 옵션·필수 옵션을 붙인다."""
        line = CartLine(item, act.qty or 1)
        self._cart = self._cart.add(line.item, line.qty, options=line.options)
        turn.accepted.append(line.phrase())

    @on("done_ordering", State.ORDERING)
    def _on_done_ordering(self, turn: _Turn, act: Act) -> None:
        turn.done_ordering = True

    # ------------------------------------------------------------ 픽업 시간

    @on("set_pickup_time", State.ORDERING)
    def _on_pickup_while_ordering(self, turn: _Turn, act: Act) -> None:
        """주문 중에 시간을 먼저 말하면 기억만 해 둔다.

        바로 잡을 수 있는 시각(수락·지금)만 잡는다. 되물어야 하는 시각은 원문을 두었다가
        `ASK_PICKUP`에 들어갈 때 다시 판단한다. 못 읽은 말은 버린다.
        """
        verdict = judge_pickup(parse_pickup(act.time_text, turn.now), turn.now, self._config)
        if verdict.kind in ("accept", "asap"):
            self._pickup = verdict.at
            self._pickup_text = ""
        elif verdict.kind != "unclear":  # 나중에 말한 시각이 앞의 것을 대신한다
            self._pickup = None
            self._pickup_text = act.time_text

    @on("set_pickup_time", State.ASK_PICKUP)
    def _on_pickup(self, turn: _Turn, act: Act) -> None:
        self._apply_pickup(turn, act.time_text)

    @on("yes", State.ASK_PICKUP)
    def _on_pickup_yes(self, turn: _Turn, act: Act) -> None:
        """제안·확인한 시각에 "네"."""
        if self._pickup_offer is None:
            self._on_unhandled(turn, act)
            return
        self._pickup, self._pickup_offer = self._pickup_offer, None
        self._advance(turn)

    @on("no", State.ASK_PICKUP)
    def _on_pickup_no(self, turn: _Turn, act: Act) -> None:
        if self._pickup_offer is None:
            self._on_unhandled(turn, act)
            return
        self._pickup_offer = None
        self._ask(turn, templates.ask_pickup_again())

    def _ask_pickup(self, turn: _Turn) -> None:
        """`ASK_PICKUP`에 들어갈 때. 주문 중에 말해 둔 시각이 있으면 그걸 먼저 판단한다."""
        if self._pickup_text:
            text, self._pickup_text = self._pickup_text, ""
            self._apply_pickup(turn, text)
        else:
            self._ask(turn, templates.ask_pickup())

    def _apply_pickup(self, turn: _Turn, time_text: str) -> None:
        """가게 규칙(`dialog/pickup.py`)의 판정대로 잡거나, 제안하거나, 다시 묻는다."""
        self._pickup_offer = None
        verdict = judge_pickup(parse_pickup(time_text, turn.now), turn.now, self._config)
        prep = self._config.prep_minutes
        if verdict.kind == "unclear":
            self._ask(turn, templates.pickup_not_understood())
        elif verdict.kind == "before_open":
            open_at = today_at(turn.now, self._config.open_time)
            self._ask(turn, templates.pickup_before_open(open_at))
        elif verdict.kind == "after_close":
            close = today_at(turn.now, self._config.close_time)
            self._ask(turn, templates.pickup_after_close(close))
        elif verdict.kind == "too_early":
            assert verdict.at is not None
            self._pickup_offer = verdict.at
            self._ask(turn, templates.pickup_too_early(prep, verdict.at))
        elif verdict.kind == "confirm":
            assert verdict.at is not None
            self._pickup_offer = verdict.at
            self._ask(turn, templates.confirm_pickup(verdict.at))
        else:  # accept, asap
            if verdict.kind == "asap":
                assert verdict.at is not None
                self._say(turn, templates.pickup_asap(prep, verdict.at))
            self._pickup = verdict.at
            self._advance(turn)

    # ------------------------------------------------------------ CONFIRM

    @on("yes", State.CONFIRM)
    def _on_confirm_yes(self, turn: _Turn, act: Act) -> None:
        assert self._pickup is not None
        self._state = State.CLOSING
        self._say(turn, templates.closing(self._pickup))
        turn.order = self._build_order("confirmed")

    @on("no", State.CONFIRM)
    def _on_confirm_no(self, turn: _Turn, act: Act) -> None:
        """티켓 05가 "아니요, 두 개요" 같은 한 번에 고치기를 붙인다."""
        self._state = State.ORDERING
        self._ask(turn, templates.ask_what_to_fix())

    # ------------------------------------------------------------ Order

    def _build_order(self, status: OrderStatus) -> Order:
        return Order(
            id=self._order_id,
            lines=self._cart.to_order_lines(),
            pickup_time=self._pickup.isoformat() if self._pickup else "",
            customer_phone=self._config.demo_phone,
            status=status,
            transcript=list(self._transcript),
        )
