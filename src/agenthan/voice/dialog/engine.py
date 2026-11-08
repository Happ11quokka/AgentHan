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
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from agenthan.core.contracts import MenuItem, MenuOption, Order, StoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.cart import Cart, CartLine
from agenthan.voice.dialog.options import find_options
from agenthan.voice.dialog.pickup import judge_pickup, today_at
from agenthan.voice.dialog.states import TERMINAL, State
from agenthan.voice.matching import MenuMatcher
from agenthan.voice.nlu.base import Act, ActType, Nlu, NluContext
from agenthan.voice.store_config import RequiredOption, StoreConfig
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
    # 티켓 03. 이번 턴에 담거나 고친 줄(종류만 본다). `_finish`가 장바구니에서 다시 읽어 되받는다
    touched: list[CartLine] = field(default_factory=list)
    changed: bool = False  # 되받을 줄 없이 장바구니를 건드렸거나 안내했다 (빼기·요청·없는 옵션)


@dataclass(frozen=True)
class _OptionPending:
    """CLARIFY가 필수 옵션 답을 기다린다 (티켓 03). `DialogEngine._pending`에 들어간다."""

    line: CartLine  # 종류만 본다 (`Cart.index_of`)
    group: RequiredOption
    then_advance: bool = False  # 손님이 이미 주문을 끝냈으면 답을 받은 뒤 바로 다음 단계로


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
        # 메뉴 이름 없이 "두 개로 바꿔 주세요"라고 하면 고칠 줄 (마지막에 담거나 고친 줄)
        self._focus: CartLine | None = None

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
        self._collect_readback(turn)
        if self._state is State.ORDERING and not turn.asked:
            if turn.done_ordering:
                if turn.accepted:
                    self._say(turn, templates.items_noted(turn.accepted))
                self._advance(turn)
            elif (missing := self._missing_required()) is not None:
                if turn.accepted:
                    self._say(turn, templates.items_noted(turn.accepted))
                self._ask_required(turn, *missing)
            elif turn.accepted:
                self._ask(turn, templates.item_accepted(turn.accepted))
            elif turn.changed:
                empty = self._cart.is_empty()
                self._ask(turn, templates.ask_order() if empty else templates.anything_else())

    def _advance(self, turn: _Turn) -> None:
        """주문이 끝났을 때 빈 슬롯을 차례로 보고 다음 상태와 질문을 정한다."""
        if self._cart.is_empty():
            self._state = State.ORDERING
            self._ask(turn, templates.ask_order())
        elif (missing := self._missing_required()) is not None:
            self._ask_required(turn, *missing, then_advance=True)
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
        if turn.unhandled or turn.accepted or turn.asked or turn.touched or turn.changed:
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
        """매처 판정 → 담을 메뉴. 바로 못 담으면 안내하거나 되묻고(CLARIFY) None.

        - accept: 그 메뉴
        - confirm·choose: "혹시 ○○ 말씀하신 거 맞으세요?" / "○○이랑 △△ 중에 어떤 거요?"
        - sold_out: 품절 안내 + 대안 권하기(대안이 있으면 CLARIFY)
        - reject: 메뉴 목록 안내, ORDERING 유지
        """
        result = self._matcher.match(act.menu_text, act.menu_guess)
        if result.decision == "accept" and result.item is not None:
            return result.item
        if result.decision == "confirm" and result.item is not None:
            item = result.item
            self._open_menu_clarify(turn, act, (item,), templates.confirm_menu(item.name_ko))
        elif result.decision == "choose":
            names = [m.name_ko for m in result.candidates]
            self._open_menu_clarify(turn, act, result.candidates, templates.choose_menu(names))
        elif result.decision == "sold_out" and result.item is not None:
            notice = templates.sold_out(result.item.name_ko)
            alternatives = tuple(self._matcher.alternatives(result.item))
            if alternatives:
                question = templates.suggest_menu([m.name_ko for m in alternatives])
                self._open_menu_clarify(turn, act, alternatives, question, notice=notice)
            else:
                self._say(turn, notice)
        else:
            self._say(turn, templates.not_on_menu(self._available_names()))
        return None

    def _add_to_cart(self, turn: _Turn, item: MenuItem, act: Act) -> None:
        """장바구니에 담는다. 수량 기본값 1, 기본값이 있는 필수 옵션은 미리 고르고,
        손님이 같이 말한 옵션(`act.option_text`)을 붙인다. 되받기는 `_finish`가 한다."""
        options = self._default_choices(item)
        if act.option_text:
            options = self._resolve_options(turn, item, act.option_text, options)
        line = CartLine(item, act.qty or 1, options)
        self._cart = self._cart.add(item, line.qty, options=options)
        self._touch(turn, line)

    @on("done_ordering", State.ORDERING)
    def _on_done_ordering(self, turn: _Turn, act: Act) -> None:
        turn.done_ordering = True

    # ------------------------------------------------------------ 티켓 03: 빼기·수량·옵션·요청

    @on("remove_item", State.ORDERING, State.CLARIFY)
    def _on_remove_item(self, turn: _Turn, act: Act) -> None:
        """메뉴를 말하면 그 메뉴 줄을 모두, 안 말하면 마지막에 다룬 줄을 뺀다."""
        if not self._leave_option_clarify(turn, act):
            return
        if _names_menu(act):
            item = self._menu_of(act)
            indices = self._cart.indices_for(item.id) if item else []
            name = item.name_ko if item else act.menu_text
        else:
            focus = self._focus_index()
            indices = [] if focus is None else [focus]
            name = "" if focus is None else self._cart.lines[focus].item.name_ko
        if not indices:
            if not name:
                self._on_unhandled(turn, act)
                return
            self._say(turn, templates.not_in_cart(name))
            turn.changed = True
            return
        gone = [self._cart.lines[i] for i in indices]
        for i in sorted(indices, reverse=True):
            self._cart = self._cart.replace_at(i, replace(self._cart.lines[i], qty=0))
        added_now = all(any(t.same_kind(g) for t in turn.touched) for g in gone)
        turn.touched = [t for t in turn.touched if not any(t.same_kind(g) for g in gone)]
        if self._focus is not None and any(self._focus.same_kind(g) for g in gone):
            self._focus = None
        if not added_now:  # 이번 턴에 담았다 바로 뺀 것은 따로 말하지 않는다
            self._say(turn, templates.removed(name))
        turn.changed = True

    @on("change_qty", State.ORDERING, State.CLARIFY)
    def _on_change_qty(self, turn: _Turn, act: Act) -> None:
        if act.qty is None:
            self._on_unhandled(turn, act)
            return
        if act.qty <= 0:
            self._on_remove_item(turn, act)
            return
        if not self._leave_option_clarify(turn, act):
            return
        index, item = self._pick_line(act)
        if index is not None:
            self._replace_line(turn, index, replace(self._cart.lines[index], qty=act.qty))
        elif item is not None:  # 아직 안 담은 메뉴 → 그 수량으로 담는다
            self._add_to_cart(turn, item, act)
        elif _names_menu(act):
            self._on_add_item(turn, act)
        else:
            self._on_unhandled(turn, act)

    @on("set_option", State.ORDERING)
    def _on_set_option(self, turn: _Turn, act: Act) -> None:
        index, item = self._pick_line(act)
        if index is not None:
            self._apply_option(turn, index, act.option_text)
        elif item is not None:
            self._add_to_cart(turn, item, act)
        elif _names_menu(act):
            self._on_add_item(turn, act)
        else:
            self._ask(turn, templates.which_item())

    @on("set_option", State.CLARIFY)
    def _on_option_answer(self, turn: _Turn, act: Act) -> None:
        """필수 옵션 질문의 답. 고를 값이 아닌 옵션이면 그것만 붙이고 `_finish`가 다시 묻는다."""
        pending = self._pending
        if not self._leave_option_clarify(turn, act):
            return
        if _names_menu(act):
            self._on_set_option(turn, act)
            return
        index = self._cart.index_of(pending.line)
        if index is not None:
            self._apply_option(turn, index, act.option_text)

    @on("set_request", State.ORDERING, State.CLARIFY)
    def _on_set_request(self, turn: _Turn, act: Act) -> None:
        """자유 요청 원문을 그 줄의 `request`에 적는다. 이미 있으면 뒤에 붙인다."""
        if not act.free_text:
            self._on_unhandled(turn, act)
            return
        if not self._leave_option_clarify(turn, act):
            return
        index, item = self._pick_line(act)
        if index is None and item is not None:
            self._add_to_cart(turn, item, replace(act, option_text=""))
            index = self._focus_index()
        if index is None:
            self._ask(turn, templates.which_item())
            return
        line = self._cart.lines[index]
        request = f"{line.request}, {act.free_text}" if line.request else act.free_text
        self._replace_line(turn, index, replace(line, request=request), readback=False)
        self._say(turn, templates.request_noted(line.item.name_ko, act.free_text))
        turn.changed = True

    # ------------------------------------------------------------ 티켓 03: 도우미

    def _required_for(self, item: MenuItem) -> tuple[RequiredOption, ...]:
        """그 메뉴의 필수 옵션. 계약 v1 뒤에는 `item.required_options`를 읽는다."""
        return self._config.required_options.get(item.id, ())

    def _default_choices(self, item: MenuItem) -> tuple[MenuOption, ...]:
        chosen = (g.choice(g.default) for g in self._required_for(item) if g.default)
        return tuple(c for c in chosen if c is not None)

    def _missing_required(self) -> tuple[CartLine, RequiredOption] | None:
        """장바구니 순서대로 처음 비어 있는 필수 옵션 하나."""
        for line in self._cart.lines:
            chosen = {o.name for o in line.options}
            for group in self._required_for(line.item):
                if not any(c.name in chosen for c in group.choices):
                    return line, group
        return None

    def _ask_required(
        self, turn: _Turn, line: CartLine, group: RequiredOption, *, then_advance: bool = False
    ) -> None:
        self._state = State.CLARIFY
        self._pending = _OptionPending(line, group, then_advance)
        names = [c.name for c in group.choices]
        self._ask(turn, templates.ask_required_option(line.item.name_ko, names))

    def _leave_option_clarify(self, turn: _Turn, act: Act) -> bool:
        """CLARIFY의 옵션 질문을 내려놓고 ORDERING으로 간다. 남은 질문은 `_finish`가 다시 한다.

        메뉴 되묻기(티켓 02)를 기다리는 중이면 건드리지 않고 False. 되묻기를 연 그 턴의
        act면 `_menu_clarify`가 미뤄 두고, 뒤 턴이면 `_on_unhandled`가 질문을 다시 한다.
        """
        if self._state is not State.CLARIFY:
            return True
        pending = self._pending
        if not isinstance(pending, _OptionPending):
            if self._menu_clarify(turn, act) is not None:
                self._on_unhandled(turn, act)
            return False
        self._pending = None
        self._state = State.ORDERING
        if pending.then_advance:
            turn.done_ordering = True
        return True

    def _resolve_options(
        self, turn: _Turn, item: MenuItem, text: str, current: tuple[MenuOption, ...]
    ) -> tuple[MenuOption, ...]:
        """옵션 말 → 붙일 옵션들. 없는 옵션·오늘 안 되는 옵션은 안내하고 뺀다.

        필수 옵션 값을 고르면 같은 묶음의 다른 값은 지운다 (차갑게 → 따뜻하게).
        """
        groups = self._required_for(item)
        candidates = [c for g in groups for c in g.choices] + list(item.options)
        found = find_options(candidates, text)
        if not found:
            can = [o.name for o in item.options if o.available]
            self._say(turn, templates.option_not_available(item.name_ko, can))
            turn.changed = True
            return current
        result = list(current)
        for option in found:
            if not option.available:
                self._say(turn, templates.option_unavailable(option.name))
                turn.changed = True
                continue
            group = next((g for g in groups if option in g.choices), None)
            if group is not None:
                result = [o for o in result if o not in group.choices]
            if option not in result:
                result.append(option)
        return tuple(result)

    def _apply_option(self, turn: _Turn, index: int, text: str) -> None:
        line = self._cart.lines[index]
        options = self._resolve_options(turn, line.item, text, line.options)
        if options == line.options:
            self._focus = line
            return
        self._replace_line(turn, index, replace(line, options=options))

    def _menu_of(self, act: Act) -> MenuItem | None:
        """빼기·고치기 대상 메뉴. 매처 후보 중 장바구니에 있는 것 먼저, 없으면 수락된 메뉴."""
        result = self._matcher.match(act.menu_text, act.menu_guess)
        candidates = ([result.item] if result.item else []) + list(result.candidates)
        for item in candidates:
            if self._cart.indices_for(item.id):
                return item
        return result.item if result.decision == "accept" else None

    def _focus_index(self) -> int | None:
        return None if self._focus is None else self._cart.index_of(self._focus)

    def _pick_line(self, act: Act) -> tuple[int | None, MenuItem | None]:
        """고칠 줄 위치와 메뉴.

        메뉴를 말하면 그 메뉴의 줄(여러 줄이면 마지막에 다룬 줄, 아니면 맨 뒤 줄),
        안 말하면 마지막에 다룬 줄. 메뉴는 알지만 장바구니에 없으면 (None, 메뉴).
        """
        focus = self._focus_index()
        if not _names_menu(act):
            return focus, None if focus is None else self._cart.lines[focus].item
        item = self._menu_of(act)
        if item is None:
            return None, None
        indices = self._cart.indices_for(item.id)
        if not indices:
            return None, item
        return (focus if focus in indices else indices[-1]), item

    def _touch(self, turn: _Turn, line: CartLine) -> None:
        if not any(t.same_kind(line) for t in turn.touched):
            turn.touched.append(line)
        self._focus = line

    def _replace_line(
        self, turn: _Turn, index: int, new: CartLine, *, readback: bool = True
    ) -> None:
        old = self._cart.lines[index]
        self._cart = self._cart.replace_at(index, new)
        was_touched = any(t.same_kind(old) for t in turn.touched)
        turn.touched = [t for t in turn.touched if not t.same_kind(old)]
        if readback or was_touched:
            self._touch(turn, new)
        else:
            self._focus = new

    def _collect_readback(self, turn: _Turn) -> None:
        """이번 턴에 다룬 줄을 지금 장바구니 모습대로 `turn.accepted`에 더한다."""
        seen: set[int] = set()
        for kind in turn.touched:
            index = self._cart.index_of(kind)
            if index is not None and index not in seen:
                seen.add(index)
                turn.accepted.append(self._cart.lines[index].phrase())

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

    # ------------------------------------------------------------ CLARIFY: 메뉴 되묻기 (티켓 02)
    #
    # 되묻는 턴에 같은 발화의 나머지 act는 `_MenuClarify.deferred`에 미뤄 두었다가,
    # 손님이 답한 턴에 ORDERING으로 돌아가 차례로 다시 처리한다.
    # `self._pending`이 `_MenuClarify`가 아니면(티켓 03의 옵션 질문 등) `_on_unhandled`로 넘긴다.

    def _open_menu_clarify(
        self,
        turn: _Turn,
        act: Act,
        candidates: tuple[MenuItem, ...],
        question: str,
        *,
        notice: str = "",
    ) -> None:
        """되묻기를 연다. 말하는 순서: 앞서 담은 항목 되받기 → 안내(품절 등) → 질문."""
        if turn.asked:  # 이번 턴에 이미 다른 질문을 했다. 한 턴에 질문은 하나.
            if notice:
                self._say(turn, notice)
            return
        self._collect_readback(turn)  # 앞서 담은 줄(티켓 03 `touched`)을 지금 모습으로
        turn.touched = []
        if turn.accepted:
            self._say(turn, templates.items_noted(turn.accepted))
        if notice:
            self._say(turn, notice)
        self._pending = _MenuClarify(act, candidates, question, opened_in=turn)
        self._state = State.CLARIFY
        self._ask(turn, question)

    def _menu_clarify(self, turn: _Turn, act: Act) -> _MenuClarify | None:
        """지금 기다리는 메뉴 되묻기. 되묻기를 연 그 턴의 act면 미뤄 두고 None."""
        pending = self._pending
        if not isinstance(pending, _MenuClarify):
            self._on_unhandled(turn, act)
            return None
        if pending.opened_in is turn:
            pending.deferred.append(act)
            return None
        return pending

    def _close_menu_clarify(
        self, turn: _Turn, pending: _MenuClarify, item: MenuItem | None, act: Act | None = None
    ) -> None:
        """되묻기를 닫고 ORDERING으로 돌아간다. 고른 메뉴를 담고, 미뤄 둔 act를 처리한다."""
        self._pending = None
        self._state = State.ORDERING
        if item is not None:
            self._add_to_cart(turn, item, act or pending.act)
        for deferred in pending.deferred:
            self._dispatch(turn, deferred)
            if self._state in TERMINAL:
                break

    @on("yes", State.CLARIFY)
    def _on_clarify_yes(self, turn: _Turn, act: Act) -> None:
        pending = self._menu_clarify(turn, act)
        if pending is None:
            return
        if len(pending.candidates) != 1:  # "둘 중 어떤 거요?"에 "네": 다시 묻는다
            self._ask(turn, pending.question)
            return
        self._close_menu_clarify(turn, pending, pending.candidates[0])

    @on("no", State.CLARIFY)
    def _on_clarify_no(self, turn: _Turn, act: Act) -> None:
        pending = self._menu_clarify(turn, act)
        if pending is None:
            return
        self._say(turn, templates.menu_declined(self._available_names()))
        self._close_menu_clarify(turn, pending, None)
        if not (turn.asked or turn.accepted or turn.touched or turn.changed or turn.done_ordering):
            self._ask(turn, templates.ask_order())

    @on("add_item", State.CLARIFY)
    def _on_clarify_add_item(self, turn: _Turn, act: Act) -> None:
        """답으로 메뉴를 다시 말함: 후보 중 하나면 그걸 담고, 아니면 새로 판정한다.

        필수 옵션 질문(티켓 03) 중이면 질문을 내려놓고 새 메뉴로 담는다. 옵션은 `_finish`가
        다시 묻는다.
        """
        if isinstance(self._pending, _OptionPending):
            self._leave_option_clarify(turn, act)
            self._on_add_item(turn, act)
            return
        pending = self._menu_clarify(turn, act)
        if pending is None:
            return
        if act.qty is None:
            act = replace(act, qty=pending.act.qty)
        if not act.option_text:  # "아메리카누 차갑게 하나" → "아메리카노요": 옵션은 처음 말 그대로
            act = replace(act, option_text=pending.act.option_text)
        picked = MenuMatcher(list(pending.candidates), fuzzy_accept_needs_guess=False).match(
            act.menu_text, act.menu_guess
        )
        if picked.decision == "accept" and picked.item is not None:
            self._close_menu_clarify(turn, pending, picked.item, act)
            return
        deferred, pending.deferred = pending.deferred, [act]
        pending.deferred.extend(deferred)
        self._close_menu_clarify(turn, pending, None)

    @on("done_ordering", State.CLARIFY)
    @on("set_pickup_time", State.CLARIFY)
    def _on_clarify_other(self, turn: _Turn, act: Act) -> None:
        """질문의 답은 아니지만 버리지 않을 act. 미뤄 두고, 질문 뒤 턴이면 질문을 다시 한다."""
        pending = self._menu_clarify(turn, act)
        if pending is not None:
            pending.deferred.append(act)
            self._ask(turn, pending.question)

    def _available_names(self) -> list[str]:
        return [m.name_ko for m in self._matcher.available()]

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


@dataclass
class _MenuClarify:
    """CLARIFY가 기다리는 메뉴 확인 (`DialogEngine._pending`에 들어간다).

    후보가 하나면 "맞으세요?"(네 → 담기), 둘 이상이면 "어떤 거요?"(메뉴 이름으로 답해야 함).
    품절 대안 권하기도 같은 모양이다.
    """

    act: Act  # 처음 받은 add_item. 답에 수량이 없으면 이 수량을 쓴다
    candidates: tuple[MenuItem, ...]
    question: str
    opened_in: _Turn = field(compare=False, repr=False)  # 되묻기를 연 턴
    deferred: list[Act] = field(default_factory=list)  # 같은 발화에서 질문 뒤에 온 act


def _names_menu(act: Act) -> bool:
    return bool(act.menu_text or act.menu_guess)
