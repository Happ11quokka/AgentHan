"""주문 대화 엔진. 코드 상태기계가 대화를 이끌고 NLU는 슬롯만 뽑는다 (spec D1, §5).

한 턴의 흐름 (`handle`):
1. `_parse`: 발화 → act 목록. 규칙 단계(`dialog/rules.py`)가 먼저 보고, 안 걸리면 NLU
2. `_dispatch`: act마다 (상태, act 종류)에 등록된 처리기를 부른다. 없으면 `_on_unhandled`
3. `_finish`: 처리기들이 남긴 표시(`_Turn`)를 보고 이번 턴의 질문 하나를 정한다
4. `_count_failure`: 같은 슬롯에서 3번 연속 못 알아들으면 FALLBACK (티켓 05)
5. `_result`: 이번 턴 대사를 transcript에 쌓고, 확정·handoff면 `Order`를 만든다

처리기 추가는 `@on("act 종류", State.X, ...)`를 붙인 메서드 하나를 쓰면 된다. 공용 코드는 안 고친다.
질문은 한 턴에 하나만 한다: 질문은 `_ask`로만 하고, `_Turn.asked`가 이미 참이면 더 묻지 않는다.

시각 (`now`): 엔진은 받은 `now`를 가게 시간대(`StoreConfig.tz`, 기본 KST)로 바꿔서 쓴다.
UTC 등 다른 시간대여도 되고, 시간대가 없는(naive) 값은 이미 가게 시각이라고 보고 tz만 붙인다.
영업시간·당일 판단·`Order.pickup_time`(+09:00)이 모두 이 시각 기준이다.

복창 불변식: CONFIRM으로 끝나는 턴의 질문은 늘 지금 장바구니로 만든 최종 복창이다 (spec D6, §5).
다른 질문이 먼저 나갔으면 복창으로 바꾼다(`_ask(force=True)`). 그래서 손님이 복창을 듣지 않고
"네"로 확정하는 길이 없다.

전역 규칙 (spec §5, 티켓 05): `cancel_order` → "주문 취소할까요?" → 네면 DONE(Order 없음),
`want_owner` → FALLBACK, 같은 슬롯에 실패 3번 → FALLBACK. 메뉴 질문은 어느 상태에서든 답하고
상태를 바꾸지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

from agenthan.core.contracts import MenuItem, MenuOption, Order, StoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.cart import Cart, CartLine
from agenthan.voice.dialog.menu_info import answer_menu_question
from agenthan.voice.dialog.options import find_options
from agenthan.voice.dialog.pickup import PickupVerdict, judge_pickup, today_at
from agenthan.voice.dialog.rules import apply_rules, wait_request_rest
from agenthan.voice.dialog.states import TERMINAL, State
from agenthan.voice.matching import MenuMatcher
from agenthan.voice.nlu.base import Act, ActType, Nlu, NluContext
from agenthan.voice.store_config import RequiredOption, StoreConfig
from agenthan.voice.timeparse import PickupParse, parse_pickup

OrderStatus = Literal["draft", "confirmed", "handoff"]

MAX_FAILURES = 3  # 같은 슬롯에서 이만큼 연속으로 못 알아들으면 사장님께 넘긴다 (spec §5)
# 무응답(3단계 티켓 04): 이만큼째 무응답이면 마지막 인사를 하고 끝낸다. 그 전에는 되묻는다
# (두 번 되묻고 세 번째에 끝냄, CLOVA AiCall 시나리오 가이드와 같다)
MAX_NO_INPUT = 3
MAX_QTY = 99  # 전화로 한 줄에 받는 수량 상한. HCX 정규화(`nlu/hcx.py` `_qty`)와 같다

# 손님과 대화 중인 상태. 전역 규칙(취소·사장님 연결·메뉴 질문)을 받는다.
_ACTIVE = (State.ORDERING, State.CLARIFY, State.ASK_PICKUP, State.CONFIRM)
# "더 필요하신 거?"에 "아니요, ~"의 뒷말이 이것뿐이면 "아니요"는 주문 끝이다 (`_no_means_done`)
_AFTER_DONE: frozenset[ActType] = frozenset({"set_pickup_time", "set_name", "done_ordering"})
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")


@dataclass(frozen=True)
class TurnResult:
    utterances: list[str]  # 손님에게 할 말 (2단계에서 TTS로 보냄)
    state: State
    cart: Cart  # 이번 턴 뒤 장바구니 (시나리오 테스트가 비교)
    order: Order | None  # CLOSING·FALLBACK에서만 채워짐
    # 이 턴의 마지막 질문이 최종 복창이다(3단계 리뷰 C6). CONFIRM 상태여도 "네, 천천히
    # 말씀하세요"·"잘 못 들었어요"처럼 복창이 없는 턴은 거짓이다. 세션이 말 끊기 버튼을 막고
    # 금액 문장 전의 "네"를 무시하는 기준이다
    readback: bool = False


@dataclass
class _Turn:
    """한 턴 동안 처리기들이 남기는 표시. `_finish`가 읽는다."""

    now: datetime
    utterances: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)  # 이번 턴에 담은 항목 문구
    done_ordering: bool = False
    asked: bool = False
    question_at: int | None = None  # 이번 턴 질문이 `utterances`의 몇 번째인지 (`_ask`)
    unhandled: bool = False
    order: Order | None = None
    order_status: OrderStatus | None = None  # 채워지면 `_result`가 Order를 만든다
    # 티켓 03. 이번 턴에 담거나 고친 줄(종류만 본다). `_finish`가 장바구니에서 다시 읽어 되받는다
    touched: list[CartLine] = field(default_factory=list)
    changed: bool = False  # 되받을 줄 없이 장바구니를 건드렸거나 안내했다 (빼기·요청·없는 옵션)
    # 티켓 05
    cart_before: Cart = field(default_factory=Cart)  # 턴 시작 때 장바구니 (정정 되받기용)
    correcting: bool = False  # 복창 뒤 정정 중. 고친 항목만 되받고 다시 복창한다
    answered: bool = False  # 메뉴 질문에 답했다. 다른 질문이 없으면 직전 질문을 다시 한다
    failed: str = ""  # 이번 턴에 못 알아들은 슬롯 (`_slot_key`)
    pickup_note: str = ""  # 주문 중에 시각만 말했을 때 되받을 말
    time_said: bool = False  # 주문 중에 시각을 말했다 (못 읽었어도 대답은 한다)
    # 티켓 06. 없는 메뉴·대안 없는 품절을 안내했다. 다른 질문이 없으면 주문 질문을 붙인다
    menu_listed: bool = False
    # 리뷰 C10. 픽업 시각을 묻는 중에 항목을 고쳤다. `_finish`가 되받고 시각을 다시 묻는다
    pickup_items: bool = False

    @property
    def question(self) -> str:
        return "" if self.question_at is None else self.utterances[self.question_at]

    @property
    def responded(self) -> bool:
        """이번 턴에 이미 손님에게 답할 거리가 생겼다 (질문·담기·고치기·안내)."""
        return self.asked or bool(self.accepted or self.touched) or self.changed


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
            if (state, act_type) in _HANDLERS:
                raise ValueError(f"처리기가 이미 있어요: ({state.value}, {act_type})")
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
        self._pickup_offer_question = ""  # 그 시각을 물은 말. 항목을 고친 뒤 다시 묻는다
        self._pickup_text = ""  # 주문 중에 말했지만 되물어야 하는 시각 원문
        self._last_question = ""
        self._transcript: list[str] = []
        # CLARIFY가 무엇을 기다리는지. 티켓 02(메뉴 확인)·03(옵션)이 자기 타입을 넣는다.
        self._pending: _MenuClarify | _OptionPending | None = None
        # 메뉴 이름 없이 "두 개로 바꿔 주세요"라고 하면 고칠 줄 (마지막에 담거나 고친 줄)
        self._focus: CartLine | None = None
        # 티켓 05
        self._fixing = False  # 복창에 "아니요"만 했다. 다음에 고치면 바로 다시 복창한다
        self._cancel_resume: str | None = None  # "주문 취소할까요?" 답을 기다리는 중. 돌아갈 질문
        self._fail_key = ""  # 연속으로 실패한 슬롯과 횟수
        self._fail_count = 0
        self._no_input = 0  # 연속 무응답 횟수 (3단계 티켓 04). 받아들인 말이 오면 0

    # ------------------------------------------------------------ 진입점

    def start(self, now: datetime) -> TurnResult:
        turn = _Turn(self._local(now))
        self._say(turn, templates.greeting(self._config.store_name))
        self._state = State.ORDERING
        return self._result(turn)

    def handle(self, text: str, now: datetime, *, wait_request: bool = False) -> TurnResult:
        """손님 말 하나. `wait_request`는 끝말 규칙이 기다림 요청("잠깐만요")으로 본 말이다.

        기다림 요청이면(3단계 티켓 04) 앞부분만 해석한다. 앞부분이 없으면 NLU를 부르지 않고
        "네, 천천히 말씀하세요"만 한다(실패로 세지 않음). 앞부분이 있으면 평소처럼 처리한 뒤
        `_take_time`이 대답 끝을 고친다. 무엇을 기다리는지(직전 질문)는 그대로 둔다.
        """
        if self._state is State.GREETING:
            raise RuntimeError("start()를 먼저 불러야 해요")
        turn = _Turn(self._local(now), cart_before=self._cart)
        if self._state is State.DONE:
            return self._result(turn)
        self._transcript.append(f"손님: {text}")
        rest = wait_request_rest(text) if wait_request else None
        if rest == "":
            self._no_input = 0
            self._say(turn, templates.take_your_time())
            return self._result(turn)
        acts = self._parse(text if rest is None else rest)
        if any(a.type != "unknown" for a in acts):
            self._no_input = 0  # 받아들인 말. 못 알아들은 말(unknown)만이면 그대로 둔다
        for act in acts:
            self._dispatch(turn, act)
            if self._state in TERMINAL:
                break
        self._finish(turn)
        self._count_failure(turn)
        if rest is not None:
            self._take_time(turn)
        return self._result(turn)

    def _take_time(self, turn: _Turn) -> None:
        """항목 + "잠시만요"의 대답 끝. 주문 중이면 다음 질문 문장을 빼고 "천천히 말씀하세요".

        주문 중의 질문("더 필요하신 거 있으세요?")은 손님이 기다려 달라고 했으니 하지 않는다.
        직전 질문(`_last_question`)으로는 남아서 다음 무응답 되묻기가 다시 한다. 다른 상태의
        질문(되묻기·시각)은 그대로 하고 뒤에 붙인다. 복창(CONFIRM)과 끝난 대화는 손대지 않는다
        (복창 불변식: 복창이 그 턴의 마지막 말이어야 한다).
        """
        if self._state in TERMINAL or self._state is State.CONFIRM:
            return
        at = turn.question_at
        if self._state is State.ORDERING and at is not None:
            kept = [s for s in _SENTENCE_END.split(turn.utterances[at]) if not s.endswith("?")]
            if kept:
                turn.utterances[at] = " ".join(kept)
            else:
                del turn.utterances[at]
            turn.question_at = None
        self._say(turn, templates.take_your_time(after_reply=bool(turn.utterances)))

    def not_heard(self, now: datetime) -> TurnResult:
        """말소리는 있었는데 STT 글이 없었다(세션이 불렀다, 3단계 티켓 04). 다시 말해 달라고 한다.

        못 알아들음 실패로 세지 않고, 무응답 횟수도 그대로 둔다. 직전 질문은 바꾸지 않는다.
        """
        if self._state is State.GREETING:
            raise RuntimeError("start()를 먼저 불러야 해요")
        turn = _Turn(self._local(now), cart_before=self._cart)
        if self._state is not State.DONE:
            self._say(turn, templates.not_heard())
        return self._result(turn)

    def no_input(self, now: datetime) -> TurnResult:
        """손님이 말이 없다(세션의 무음 타이머가 불렀다, 3단계 티켓 04). 장바구니는 안 바꾼다.

        `MAX_NO_INPUT`번째 전까지는 "여보세요?" + 직전 질문(복창 중이면 지금 장바구니 복창)을
        다시 하고, 그다음은 마지막 인사를 하고 DONE으로 끝낸다(Order 없음). 횟수는 받아들인 손님 말
        (`handle`)에 0으로 돌아간다. 못 알아들음 실패 횟수(`_count_failure`)와는 따로 센다.
        """
        if self._state is State.GREETING:
            raise RuntimeError("start()를 먼저 불러야 해요")
        turn = _Turn(self._local(now), cart_before=self._cart)
        if self._state is State.DONE:
            return self._result(turn)
        self._no_input += 1
        if self._no_input >= MAX_NO_INPUT:
            self._pending = None
            self._cancel_resume = None
            self._state = State.DONE
            self._say(turn, templates.no_input_goodbye())
            return self._result(turn)
        self._say(turn, templates.hello_again())
        self._ask(turn, self._question_again())
        return self._result(turn)

    def nlu_context(self) -> NluContext:
        """다음 `handle`이 NLU에 넘길 문맥 (3단계 티켓 02 미리 해석의 키, 끝말 규칙의 대화 상태).

        시각 필드가 없어서 엔진이 돌지 않는 동안에는 값이 흔들리지 않는다.
        `handle`이 도는 중(다른 스레드)에는 부르지 않는다.
        세션은 엔진이 쉬는 듣기 국면에서만 부른다.
        """
        return NluContext(
            menu_names=tuple(m.name_ko for m in self._menu),
            cart_summary=self._cart.summary(),
            last_question=self._last_question,
            state=self._state.value,
        )

    def _local(self, now: datetime) -> datetime:
        """`now` → 가게 시각. naive면 가게 시각으로 보고 tz만 붙인다 (모듈 설명 참고)."""
        tz = self._config.tz
        return now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)

    # ------------------------------------------------------------ 턴 골격

    def _parse(self, text: str) -> list[Act]:
        """규칙 단계 → (안 걸리면) NLU. 내용 앞의 "네/아니요"는 뗀다.

        "아니요, 두 개요" → [no, change_qty]에서 no를 떼고 change_qty만 남긴다. 복창(CONFIRM)에서는
        정정 act가 오면 그 자체로 정정으로 보므로 no가 따로 필요 없다.
        """
        acts = apply_rules(text)
        if acts is None:
            acts = self._nlu.parse(text, self.nlu_context())
        if self._no_means_done(acts):
            # "더 필요하신 거?" → "아니요, 지금 바로 갈게요": 아니요가 답이다 (2단계 리뷰 5번)
            acts = [Act("done_ordering"), *acts[1:]]
        if any(a.type not in ("yes", "no") for a in acts):
            keep_yes = self._awaiting_menu_yes() and not any(a.type == "add_item" for a in acts)
            while acts and acts[0].type in ("yes", "no"):
                if keep_yes and acts[0].type == "yes":
                    break  # "혹시 메도빅 맞으세요?" → "네, 두 개요": 네가 답이다 (리뷰 C9)
                acts = acts[1:]
        return acts

    def _no_means_done(self, acts: list[Act]) -> bool:
        """주문 중 "더 필요하신 거?" 바로 뒤, 맨 앞 "아니요" + 장바구니를 안 건드리는 말.

        뒤에 픽업 시간·이름만 오면 "아니요"는 주문 끝이다. 뒤가 장바구니를 고치는 말이면
        ("아니요, 두 개요") 지금처럼 정정으로 보고 "아니요"를 뗀다.
        """
        if self._state is not State.ORDERING or not self._asked_anything_else():
            return False
        if len(acts) < 2 or acts[0].type != "no":
            return False
        return all(a.type in _AFTER_DONE for a in acts[1:])

    def _awaiting_menu_yes(self) -> bool:
        """CLARIFY가 후보 하나짜리 메뉴 확인("맞으세요?")의 답을 기다린다."""
        pending = self._pending
        return isinstance(pending, _MenuClarify) and len(pending.candidates) == 1

    def _dispatch(self, turn: _Turn, act: Act) -> None:
        if self._cancel_resume is not None and self._on_cancel_answer(turn, act):
            return
        handler = _HANDLERS.get((self._state, act.type), DialogEngine._on_unhandled)
        handler(self, turn, act)

    def _finish(self, turn: _Turn) -> None:
        """처리기가 질문을 안 했으면 상태에 맞는 질문 하나를 붙인다."""
        self._collect_readback(turn)
        if self._fixing and self._state is State.ORDERING and (turn.accepted or turn.changed):
            turn.done_ordering = turn.correcting = True  # "어떤 걸 고쳐 드릴까요?"의 답
        if self._state is State.ORDERING and not turn.asked:
            if turn.done_ordering:
                if turn.correcting:
                    self._say_corrections(turn)
                elif turn.accepted:
                    self._say(turn, templates.items_noted(turn.accepted))
                if turn.pickup_note and self._missing_required() is None:
                    # "아니요, 지금 갈게요" → 복창 앞에 준비 시간 안내 (2단계 리뷰 5번).
                    # 필수 옵션을 먼저 물을 때는 그 질문만 한다
                    self._say(turn, turn.pickup_note)
                self._advance(turn)
            elif (missing := self._missing_required()) is not None:
                if turn.accepted:
                    self._say(turn, templates.items_noted(turn.accepted))
                if turn.pickup_note:
                    self._say(turn, turn.pickup_note)
                self._ask_required(turn, *missing)
            elif turn.accepted and turn.pickup_note:
                self._say(turn, templates.items_noted(turn.accepted))
                self._say(turn, turn.pickup_note)
                self._ask(turn, templates.anything_else())
            elif turn.accepted:
                self._ask(turn, templates.item_accepted(turn.accepted))
            elif turn.changed or turn.time_said or turn.menu_listed:
                if turn.pickup_note:
                    self._say(turn, turn.pickup_note)
                if self._fixing:  # "어떤 걸 고쳐 드릴까요?"에 없는 메뉴를 말했다
                    self._ask(turn, templates.ask_what_to_fix())
                elif self._cart.is_empty():
                    self._ask(turn, templates.ask_order())
                else:
                    self._ask(turn, templates.anything_else())
        if turn.pickup_items and self._state is not State.ORDERING:
            self._finish_pickup_items(turn)
        if turn.unhandled and not turn.asked and self._state not in TERMINAL:
            self._say(turn, templates.not_understood())
            self._ask(turn, self._question_again())
        if turn.answered and not turn.asked and self._state not in TERMINAL:
            self._ask(turn, self._question_again())
        if self._state is State.CONFIRM and self._cancel_resume is None:
            if turn.question not in self._readbacks():  # 복창 불변식 (모듈 설명)
                self._ask_readback(turn)

    def _finish_pickup_items(self, turn: _Turn) -> None:
        """픽업 시각을 묻는 중에 고친 항목을 되받는다 (C10). 질문이 이미 있으면 그 앞에 넣는다.

        같은 턴에 시각도 받았으면 처리기가 이미 복창했다. 아니면 다음 빈 슬롯을 묻는다
        (필수 옵션 → 시각). 기다리던 제안 시각("5시 25분 괜찮으세요?")은 그대로 다시 묻는다.
        """
        if not turn.asked and self._state is State.ASK_PICKUP:
            if self._cart.is_empty() or self._missing_required() is not None:
                self._advance(turn)
            elif self._pickup_offer is not None:
                self._ask(turn, self._pickup_offer_question)
            else:
                self._advance(turn)
        self._say_corrections(turn, before_question=True)

    def _say_corrections(self, turn: _Turn, *, before_question: bool = False) -> None:
        """정정한 줄만 되받는다. 원래 있던 메뉴는 "~로 바꿨어요", 새로 담은 메뉴는 "~요"."""
        changed: list[str] = []
        added: list[str] = []
        seen: set[int] = set()
        for kind in turn.touched:
            index = self._cart.index_of(kind)
            if index is None or index in seen:
                continue
            seen.add(index)
            line = self._cart.lines[index]
            was_there = bool(turn.cart_before.indices_for(line.item.id))
            (changed if was_there else added).append(line.phrase())
        notes = []
        if changed:
            notes.append(templates.changed_to(changed))
        if added:
            notes.append(templates.items_noted(added))
        for note in notes:
            if before_question and turn.question_at is not None:
                turn.utterances.insert(turn.question_at, note)
                turn.question_at += 1
            else:
                self._say(turn, note)

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
        elif self._pickup < turn.now + timedelta(minutes=self._config.prep_minutes):
            # 주문 중에 받은 시각이 그새 준비 시간 안으로 들어왔다 (C16). 가게 규칙대로 다시 본다
            stale, self._pickup = self._pickup, None
            self._state = State.ASK_PICKUP
            self._apply_verdict(turn, judge_pickup(PickupParse(stale), turn.now, self._config))
        else:
            self._state = State.CONFIRM
            self._fixing = False
            self._ask_readback(turn)

    def _readback(self, *, after_fix: bool) -> str:
        assert self._pickup is not None
        return templates.final_readback(
            self._cart.summary(),
            self._pickup,
            self._cart.total(),
            requests=self._cart.requests(),
            after_fix=after_fix,
        )

    def _readbacks(self) -> tuple[str, ...]:
        """지금 장바구니로 만든 복창 (앞에 "그럼"이 붙은 것과 안 붙은 것). 시각이 없으면 없음."""
        if self._pickup is None:
            return ()
        return self._readback(after_fix=False), self._readback(after_fix=True)

    def _ask_readback(self, turn: _Turn) -> None:
        """최종 복창. 이번 턴에 다른 질문을 이미 했으면 그 질문을 지우고 복창으로 바꾼다."""
        self._ask(turn, self._readback(after_fix=turn.correcting), force=True)

    def _result(self, turn: _Turn) -> TurnResult:
        self._transcript.extend(f"AI: {text}" for text in turn.utterances)
        if turn.order_status is not None:
            turn.order = self._build_order(turn.order_status)
        readback = self._state is State.CONFIRM and turn.question in self._readbacks()
        result = TurnResult(list(turn.utterances), self._state, self._cart, turn.order,
                            readback=readback)
        if self._state in TERMINAL:
            self._state = State.DONE
        return result

    # ------------------------------------------------------------ 말하기

    def _say(self, turn: _Turn, text: str) -> None:
        """대사는 턴이 끝날 때 `_result`가 transcript에 옮긴다 (FALLBACK이 바꿀 수 있게)."""
        turn.utterances.append(text)

    def _ask(self, turn: _Turn, question: str, *, force: bool = False) -> None:
        """이번 턴의 질문 하나. 이미 물었으면 무시한다. `force`면 앞 질문을 지우고 이것을 묻는다."""
        if turn.asked:
            if not force:
                return
            if turn.question_at is not None:
                del turn.utterances[turn.question_at]
        turn.asked = True
        self._last_question = question
        turn.question_at = len(turn.utterances)
        self._say(turn, question)

    def _on_unhandled(self, turn: _Turn, act: Act) -> None:
        """이 상태에서 받지 않는 act. 실패로 세고, 턴 끝에(`_finish`) 직전 질문을 다시 한다.

        바로 묻지 않는 이유: 같은 발화 뒤쪽 act가 진전을 만들 수 있다("어, 6시 반이요").
        그때는 그 act의 질문(복창 등)이 이긴다 (리뷰 C1).
        """
        if turn.unhandled or turn.responded:
            return
        turn.unhandled = True
        self._fail(turn)

    def _question_again(self) -> str:
        """다시 할 질문. ORDERING에서는 되받기("메도빅 하나요.")를 빼고 질문만 한다.

        CONFIRM에서는 늘 지금 장바구니로 만든 복창이다 (리뷰 C2, P1).
        """
        if self._cancel_resume is None:
            if self._state is State.ORDERING:
                if self._fixing:  # 복창에 "아니요" 뒤. 고칠 것을 다시 묻는다 (티켓 06)
                    return templates.ask_what_to_fix()
                if self._cart.is_empty():
                    return templates.ask_order()
                return templates.anything_else()
            if self._state is State.CONFIRM and self._pickup is not None:
                return self._readback(after_fix=False)
        return self._last_question or templates.ask_order()

    # ------------------------------------------------------------ 실패 카운터·FALLBACK (티켓 05)

    def _slot_key(self) -> str:
        """지금 손님 답을 기다리는 슬롯. 같은 슬롯의 실패만 이어서 센다."""
        if self._cancel_resume is not None:
            return "cancel"
        if self._state is State.CLARIFY:
            return "option" if isinstance(self._pending, _OptionPending) else "order"
        return {State.ASK_PICKUP: "pickup", State.CONFIRM: "confirm"}.get(self._state, "order")

    def _fail(self, turn: _Turn) -> None:
        """이번 턴에 지금 슬롯을 못 채웠다 (unknown, 없는 메뉴, 못 읽은 시각, 고르라는데 "네")."""
        if not turn.failed:
            turn.failed = self._slot_key()

    def _count_failure(self, turn: _Turn) -> None:
        """실패만 있고 진전이 없는 턴이 같은 슬롯에서 `MAX_FAILURES`번이면 FALLBACK.

        진전(담기·고치기·주문 끝·질문 답하기)이 있거나 실패가 없는 턴이면 횟수를 지운다.
        FALLBACK이면 이번 턴에 하려던 재질문은 버리고 넘김 안내로 바꾼다.
        """
        progress = (
            turn.accepted or turn.touched or turn.changed or turn.done_ordering or turn.answered
        )
        if not turn.failed or progress or self._state in TERMINAL:
            self._fail_key, self._fail_count = "", 0
            return
        if turn.failed == self._fail_key:
            self._fail_count += 1
        else:
            self._fail_key, self._fail_count = turn.failed, 1
        if self._fail_count >= MAX_FAILURES:
            turn.utterances.clear()
            self._say(turn, templates.too_many_failures())
            self._hand_off(turn)

    def _hand_off(self, turn: _Turn) -> None:
        """FALLBACK: 사장님께 메모를 남긴다. 지금까지의 장바구니·시각·대화를 handoff Order로."""
        self._pending = None
        self._cancel_resume = None
        self._state = State.FALLBACK
        self._say(turn, templates.handoff())
        turn.asked = True
        turn.order_status = "handoff"

    @on("want_owner", *_ACTIVE)
    def _on_want_owner(self, turn: _Turn, act: Act) -> None:
        self._hand_off(turn)

    # ------------------------------------------------------------ 주문 취소 (티켓 05)

    @on("cancel_order", *_ACTIVE)
    def _on_cancel_order(self, turn: _Turn, act: Act) -> None:
        """바로 끊지 않고 확인한다. 답은 `_on_cancel_answer`가 받는다. 상태는 그대로 둔다."""
        if self._cancel_resume is None:
            self._cancel_resume = self._question_again()
        self._ask(turn, templates.confirm_cancel())

    def _on_cancel_answer(self, turn: _Turn, act: Act) -> bool:
        """"주문 취소할까요?"의 답. 처리했으면 True, 원래 상태 처리기로 넘길 act면 False.

        네(또는 취소를 다시 말함) → DONE, Order 없음. 아니요 → 직전 질문으로 돌아간다.
        주문을 이어 가는 말(메뉴 등) → 취소 질문을 접고 그 act를 원래 상태에서 처리한다.
        """
        resume = self._cancel_resume
        assert resume is not None
        if act.type in ("yes", "cancel_order"):
            self._cancel_resume = None
            self._pending = None
            self._state = State.DONE
            self._say(turn, templates.cancelled())
            turn.asked = True
            return True
        if act.type == "no":
            self._cancel_resume = None
            self._say(turn, templates.cancel_declined())
            self._ask(turn, resume)
            return True
        if act.type in ("unknown", "repeat_please", "ask_menu_info", "ask_other", "want_owner"):
            return False  # 취소 질문은 그대로 두고 평소처럼 (다시 묻기·답하기·사장님 연결)
        self._cancel_resume = None
        self._last_question = resume
        return False

    # ------------------------------------------------------------ 다시 말하기·메뉴 질문 (티켓 05)

    @on("repeat_please", *_ACTIVE)
    def _on_repeat_please(self, turn: _Turn, act: Act) -> None:
        self._ask(turn, self._question_again())

    @on("ask_menu_info", *_ACTIVE)
    def _on_ask_menu_info(self, turn: _Turn, act: Act) -> None:
        """메뉴 필드로만 답한다. 상태는 그대로이고 직전 질문은 `_finish`가 다시 한다."""
        turn.answered = True
        if _names_menu(act):
            result = self._matcher.match(act.menu_text, act.menu_guess)
            item = result.item or next(iter(result.candidates), None)
            if result.decision == "reject" or item is None:
                self._say(turn, templates.not_on_menu(self._available_names()))
                return
        else:  # "글루텐 프리예요?": 방금 다룬 메뉴, 장바구니에 하나뿐이면 그 메뉴
            focus = self._focus_index()
            only = self._cart.lines[0] if len(self._cart.lines) == 1 else None
            line = self._cart.lines[focus] if focus is not None else only
            if line is None:
                self._say(turn, templates.dont_know())
                return
            item = line.item
        self._say(turn, answer_menu_question(item, act.free_text or act.menu_text))

    @on("ask_other", *_ACTIVE)
    def _on_ask_other(self, turn: _Turn, act: Act) -> None:
        """가게 정보 질문 등. 1단계에는 근거 필드가 없으므로 모른다고 한다."""
        turn.answered = True
        self._say(turn, templates.dont_know())

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
                self._say(turn, notice)  # 대안이 없다 = 남은 메뉴가 없다. 더 묻지 않는다
        else:
            self._fail(turn)
            self._say(turn, templates.not_on_menu(self._available_names()))
            turn.menu_listed = True
        return None

    def _add_to_cart(self, turn: _Turn, item: MenuItem, act: Act) -> None:
        """장바구니에 담는다. 수량 기본값 1, 기본값이 있는 필수 옵션은 미리 고르고,
        손님이 같이 말한 옵션(`act.option_text`)을 붙인다. 되받기는 `_finish`가 한다."""
        qty = 1 if act.qty is None else act.qty
        if qty <= 0:  # "메도빅 0개": 1개로 바꾸지 않는다 (리뷰 C17)
            self._on_unhandled(turn, act)
            return
        if not self._qty_ok(turn, qty):
            return
        options = self._default_choices(item)
        if act.option_text:
            options = self._resolve_options(turn, item, act.option_text, options)
        line = CartLine(item, qty, options)
        self._cart = self._cart.add(item, line.qty, options=options)
        self._touch(turn, line)

    def _qty_ok(self, turn: _Turn, qty: int) -> bool:
        """수량 상한(`MAX_QTY`). 넘으면 안내하고 담지 않는다."""
        if qty <= MAX_QTY:
            return True
        self._fail(turn)
        self._say(turn, templates.qty_too_large(MAX_QTY))
        turn.changed = True
        return False

    @on("done_ordering", State.ORDERING)
    def _on_done_ordering(self, turn: _Turn, act: Act) -> None:
        turn.done_ordering = True

    def _asked_anything_else(self) -> bool:
        """직전 질문이 "더 필요하신 거 있으세요?"였다 (정정 중이 아님)."""
        asked = self._last_question.endswith(templates.anything_else())
        return asked and not self._fixing and not self._cart.is_empty()

    @on("no", State.ORDERING)
    def _on_ordering_no(self, turn: _Turn, act: Act) -> None:
        """"더 필요하신 거 있으세요?"에 "아니요" = 주문 끝 (티켓 06)."""
        if self._asked_anything_else():
            turn.done_ordering = True
        else:
            self._on_unhandled(turn, act)

    @on("yes", State.ORDERING)
    def _on_ordering_yes(self, turn: _Turn, act: Act) -> None:
        """"더 필요하신 거 있으세요?"에 "네"만 했다. 무엇을 더 드릴지 묻는다 (티켓 06)."""
        if self._asked_anything_else():
            self._ask(turn, templates.ask_order())
        else:
            self._on_unhandled(turn, act)

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
        if not self._qty_ok(turn, act.qty):
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
        """필수 옵션 질문의 답. 고를 값이 아닌 옵션이면 그것만 붙이고 `_finish`가 다시 묻는다.

        그 메뉴에 없는 값이거나("미지근하게") 한 묶음 값을 둘 이상 말했으면 실패로 세고 같은
        질문을 다시 한다 (리뷰 C8, C15). 계속 안 맞으면 사장님께 넘긴다.
        """
        pending = self._pending
        if isinstance(pending, _OptionPending) and not _names_menu(act):
            problem = self._option_answer_problem(pending, act.option_text)
            if problem:
                self._fail(turn)
                self._say(turn, problem)
                self._ask(turn, self._last_question)
                return
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

    def _option_answer_problem(self, pending: _OptionPending, text: str) -> str:
        """필수 옵션 답이 고를 수 없는 말이면 안내 문구, 아니면 ""."""
        item = pending.line.item
        candidates = [c for g in self._required_for(item) for c in g.choices] + list(item.options)
        found = find_options(candidates, text)
        if not found:
            return templates.not_a_choice()
        if sum(option in pending.group.choices for option in found) > 1:
            return templates.one_choice_only(pending.group.name)
        return ""

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
        for group in groups:  # 한 묶음 값을 둘 이상 말했다: 고르지 않고 다시 묻는다 (리뷰 C15)
            if sum(option in group.choices for option in found) > 1:
                self._say(turn, templates.one_choice_only(group.name))
                found = [o for o in found if o not in group.choices]
                result = [o for o in result if o not in group.choices]
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
        """주문 중에 시간을 먼저 말하면 기억해 두고 짧게 되받는다.

        바로 잡을 수 있는 시각(수락·지금)만 잡는다("6시 픽업으로 알아 둘게요."). 되물어야 하는
        시각은 원문을 두었다가 `ASK_PICKUP`에 들어갈 때 다시 판단한다. 못 읽은 말은 버린다.
        복창에 "아니요" 뒤라면 시각 정정이다.
        """
        if self._fixing:
            self._correct_pickup(turn, act)
            return
        turn.time_said = True
        turn.pickup_note = self._remember_pickup(turn, act.time_text)

    def _remember_pickup(self, turn: _Turn, time_text: str) -> str:
        """`ASK_PICKUP` 전에 들은 시각. 잡았으면 되받을 말, 아니면 "".

        못 읽은 말이면 이미 받은 시각을 그대로 둔다(리뷰 C14). 되물어야 하는 시각은 원문을 둔다.
        """
        verdict = judge_pickup(parse_pickup(time_text, turn.now), turn.now, self._config)
        if verdict.kind in ("accept", "asap"):
            assert verdict.at is not None
            self._pickup = verdict.at
            self._pickup_text = ""
            if verdict.kind == "accept":
                return templates.pickup_noted(verdict.at)
            return templates.pickup_asap(self._config.prep_minutes, verdict.at)
        if verdict.kind != "unclear":  # 나중에 말한 시각이 앞의 것을 대신한다
            self._pickup = None
            self._pickup_text = time_text
        return ""

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
        self._fail(turn)  # 제안 시각을 거절. 계속 안 맞으면 사장님께 넘긴다
        self._ask(turn, templates.ask_pickup_again())

    @on("add_item", State.ASK_PICKUP)
    @on("change_qty", State.ASK_PICKUP)
    @on("remove_item", State.ASK_PICKUP)
    @on("set_option", State.ASK_PICKUP)
    @on("set_request", State.ASK_PICKUP)
    def _on_item_while_asking_pickup(self, turn: _Turn, act: Act) -> None:
        """시각을 묻는 중에 항목을 더하거나 고친다 (리뷰 C10). ORDERING 처리기로 고친 뒤
        ASK_PICKUP으로 돌아온다. 같은 발화의 시각·"네"는 그대로 픽업 처리기가 받는다.

        메뉴 되묻기가 열리면 CLARIFY에 남고, 답을 받으면 정정처럼 다음 단계로 간다.
        """
        self._state = State.ORDERING
        self._dispatch(turn, act)
        if self._state is State.ORDERING:
            self._state = State.ASK_PICKUP
            turn.pickup_items = True
        elif isinstance(self._pending, _MenuClarify):
            self._pending.correcting = True

    def _ask_pickup(self, turn: _Turn) -> None:
        """`ASK_PICKUP`에 들어갈 때. 주문 중에 말해 둔 시각이 있으면 그걸 먼저 판단한다."""
        if self._pickup_text:
            text, self._pickup_text = self._pickup_text, ""
            self._apply_pickup(turn, text)
        else:
            self._pickup_offer = None
            self._ask(turn, templates.ask_pickup())

    def _apply_pickup(self, turn: _Turn, time_text: str) -> None:
        verdict = judge_pickup(parse_pickup(time_text, turn.now), turn.now, self._config)
        self._apply_verdict(turn, verdict)

    def _apply_verdict(self, turn: _Turn, verdict: PickupVerdict) -> None:
        """가게 규칙(`dialog/pickup.py`)의 판정대로 잡거나, 제안하거나, 다시 묻는다."""
        self._pickup_offer = None
        prep = self._config.prep_minutes
        if verdict.kind in ("unclear", "before_open", "after_close"):
            self._fail(turn)
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
            self._offer_pickup(turn, verdict.at, templates.pickup_too_early(prep, verdict.at))
        elif verdict.kind == "past":
            assert verdict.at is not None
            self._offer_pickup(turn, verdict.at, templates.pickup_passed(verdict.at))
        elif verdict.kind == "confirm":
            assert verdict.at is not None
            self._offer_pickup(turn, verdict.at, templates.confirm_pickup(verdict.at))
        else:  # accept, asap
            assert verdict.at is not None
            if verdict.kind == "asap":
                self._say(turn, templates.pickup_asap(prep, verdict.at))
            elif turn.correcting:
                self._say(turn, templates.pickup_changed(verdict.at))
            self._pickup = verdict.at
            self._advance(turn)

    def _offer_pickup(self, turn: _Turn, at: datetime, question: str) -> None:
        """시각을 제안하거나 확인한다. "네"를 받으면 그 시각으로 잡는다."""
        self._pickup_offer = at
        self._pickup_offer_question = question
        self._ask(turn, question)

    # ------------------------------------------------------------ CONFIRM

    @on("yes", State.CONFIRM)
    def _on_confirm_yes(self, turn: _Turn, act: Act) -> None:
        assert self._pickup is not None
        self._state = State.CLOSING
        self._say(turn, templates.closing(self._pickup))
        turn.order_status = "confirmed"

    @on("no", State.CONFIRM)
    def _on_confirm_no(self, turn: _Turn, act: Act) -> None:
        """"아니요"만 했다. 무엇을 고칠지 묻고, 다음 턴에 고치면 바로 다시 복창한다(`_fixing`)."""
        self._state = State.ORDERING
        self._fixing = True
        self._ask(turn, templates.ask_what_to_fix())

    # 정정 (티켓 05): "아니요, 두 개요", "아메리카노 두 잔이요", "블린 빼 주세요" 등.
    # ORDERING 처리기를 그대로 쓰고, 주문 끝(done_ordering)으로 표시해 `_finish`가 고친 항목만
    # 되받은 뒤 `_advance`로 다시 복창한다. 필수 옵션이 비면 그것부터 묻는다.

    @on("add_item", State.CONFIRM)
    @on("change_qty", State.CONFIRM)
    @on("remove_item", State.CONFIRM)
    @on("set_option", State.CONFIRM)
    @on("set_request", State.CONFIRM)
    def _on_confirm_correction(self, turn: _Turn, act: Act) -> None:
        if not _names_menu(act) and len(self._cart.lines) == 1:
            self._focus = self._cart.lines[0]  # 메뉴가 하나뿐이면 "두 개요"의 대상은 그것
        if act.type == "add_item" and (item := self._menu_of(act)) is not None:
            if self._cart.indices_for(item.id):  # 이미 있는 메뉴를 다시 말함 = 그 줄을 고친다
                if act.qty is not None:
                    act = replace(act, type="change_qty")
                elif act.option_text:
                    act = replace(act, type="set_option")
                else:
                    # "메도빅이요": 복창을 듣고 이름만 되풀이했다. 더 담지 않고 복창을 다시 한다
                    # (리뷰 C5). 더 담으려면 수량을 말한다("메도빅 두 개로요").
                    return
        self._start_correction(turn)
        self._dispatch(turn, act)
        if turn.unhandled:
            # 못 고쳤다. CONFIRM에 남고, `_finish`가 "못 알아들었어요" + 복창을 다시 한다.
            # 상태를 먼저 되돌려야 재질문이 ORDERING의 "더 필요하세요?"가 되지 않는다 (C2, P1).
            self._state = State.CONFIRM
            turn.done_ordering = turn.correcting = False

    @on("set_pickup_time", State.CONFIRM)
    def _on_confirm_pickup(self, turn: _Turn, act: Act) -> None:
        self._correct_pickup(turn, act)

    def _start_correction(self, turn: _Turn) -> None:
        self._state = State.ORDERING
        turn.done_ordering = turn.correcting = True

    def _correct_pickup(self, turn: _Turn, act: Act) -> None:
        """시각 정정. 원문을 두고 `_advance` → `ASK_PICKUP`에서 가게 규칙대로 다시 판단한다."""
        self._start_correction(turn)
        self._pickup = None
        self._pickup_offer = None
        self._pickup_text = act.time_text

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
        self._pending = _MenuClarify(
            act, candidates, question, opened_in=turn, correcting=turn.correcting
        )
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
        if pending.correcting:  # 복창 뒤 정정에서 연 되묻기. 답을 받으면 다시 복창한다
            turn.done_ordering = turn.correcting = True
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
        if len(pending.candidates) != 1:  # "둘 중 어떤 거요?"에 "네": 실패로 세고 다시 묻는다
            self._fail(turn)
            self._ask(turn, pending.question)
            return
        self._close_menu_clarify(turn, pending, pending.candidates[0])

    @on("no", State.CLARIFY)
    def _on_clarify_no(self, turn: _Turn, act: Act) -> None:
        pending = self._menu_clarify(turn, act)
        if pending is None:
            return
        self._fail(turn)  # 메뉴 슬롯을 못 채웠다. 다음 턴에 메뉴를 말하면 횟수가 지워진다
        self._say(turn, templates.menu_declined(self._available_names()))
        self._close_menu_clarify(turn, pending, None)
        if not (turn.responded or turn.done_ordering):
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
        """질문의 답은 아니지만 버리지 않을 act. 미뤄 두고, 질문 뒤 턴이면 질문을 다시 한다.

        필수 옵션 질문 중이면(티켓 06) "그게 다예요"는 답을 받은 뒤 바로 다음 단계로 가라는
        표시로, 시각은 `ASK_PICKUP`에서 판단할 원문으로 두고 옵션을 다시 묻는다.
        """
        if isinstance(self._pending, _OptionPending):
            if act.type == "done_ordering":
                self._pending = replace(self._pending, then_advance=True)
            else:
                self._remember_pickup(turn, act.time_text)
            self._ask(turn, self._last_question)
            return
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
    correcting: bool = False  # 복창 뒤 정정 중에 열었다 (티켓 06)


def _names_menu(act: Act) -> bool:
    return bool(act.menu_text or act.menu_guess)
