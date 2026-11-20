"""주문 대화 엔진. 코드 상태기계가 대화를 이끌고 NLU는 슬롯만 뽑는다 (spec D1, §5).

한 턴의 흐름 (`handle`):
1. `_parse`: 발화 → act 목록. 규칙 단계(`dialog/rules.py`)가 먼저 보고, 안 걸리면 NLU
2. `_dispatch`: act마다 (상태, act 종류)에 등록된 처리기를 부른다. 없으면 `_on_unhandled`
3. `_finish`: 처리기들이 남긴 표시(`_Turn`)를 보고 이번 턴의 질문 하나를 정한다
4. `_count_failure`: 같은 슬롯에서 3번 연속 못 알아들으면 FALLBACK (티켓 05). 두 번째에는 보기를
   붙이고, 통화 전체 되묻기가 가게 기준(`StoreConfig.max_reasks`)이면 넘긴다 (6단계 품질)
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

import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from agenthan.core.contracts import MenuItem, MenuOption, Order, Review, StoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.answerer import (
    FULL_FACTS_MENUS,
    UNSURE,
    Answerer,
    AnswerReply,
    AnswerRequest,
    Notes,
    checked,
    store_facts,
)
from agenthan.voice.dialog.cart import Cart, CartLine
from agenthan.voice.dialog.knowledge import KnowledgeKey, KnowledgeReader, KnowledgeRequest
from agenthan.voice.dialog.menu_info import MenuAnswer, menu_answer
from agenthan.voice.dialog.options import find_options
from agenthan.voice.dialog.owner_question import OwnerAnswer, OwnerQuestion
from agenthan.voice.dialog.pickup import PickupVerdict, earliest_pickup, judge_pickup, today_at
from agenthan.voice.dialog.rules import (
    apply_rules,
    frustrated,
    no_and_done,
    retracted,
    wait_request_rest,
)
from agenthan.voice.dialog.states import TERMINAL, State
from agenthan.voice.matching import MenuMatcher
from agenthan.voice.nlu.base import Act, ActType, Nlu, NluContext
from agenthan.voice.nlu.catalog import menu_catalog
from agenthan.voice.store_config import RequiredOption, StoreConfig
from agenthan.voice.timeparse import PickupParse, parse_pickup

log = logging.getLogger(__name__)

# 답변기를 NLU와 같이 돌리는 스레드 (5단계 티켓 11). 프로세스에 하나, 처음 쓸 때 만든다
_ANSWER_THREADS = 4
_answer_pool: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _answer_pool
    if _answer_pool is None:
        _answer_pool = ThreadPoolExecutor(_ANSWER_THREADS, thread_name_prefix="phone-answer")
    return _answer_pool


OrderStatus = Literal["draft", "confirmed", "handoff"]
# 사장님께 넘긴 이유 (6단계 품질). requested: 손님이 사람을 찾음 · slot_failures: 같은 슬롯 3번 ·
# call_failures: 통화 전체 되묻기 상한 · frustrated: 손님 짜증
HandoffReason = Literal["requested", "slot_failures", "call_failures", "frustrated"]
# 이번 턴이 엔진 안에서 탄 길 (턴 기록의 경로별 지연용). rule: NLU를 안 부름 · nlu: NLU(HCX)를
# 부름 · answer: 답변기까지 부름
Route = Literal["rule", "nlu", "answer"]

MAX_FAILURES = 3  # 같은 슬롯에서 이만큼 연속으로 못 알아들으면 사장님께 넘긴다 (spec §5)
HINT_AT_FAILURE = 2  # 같은 슬롯 이만큼째 되묻기에는 보기를 붙인다 (6단계 품질)
MENU_EXAMPLES = 3  # 보기로 드는 메뉴 수. 길면 못 외운다
TIME_EXAMPLE_GAP = timedelta(minutes=30)  # 시각 보기 둘 사이
# 무응답(3단계 티켓 04): 이만큼째 무응답이면 마지막 인사를 하고 끝낸다. 그 전에는 되묻는다
# (두 번 되묻고 세 번째에 끝냄, CLOVA AiCall 시나리오 가이드와 같다)
MAX_NO_INPUT = 3
# 사장님 질문 (4단계 티켓 06): 통화당 이만큼까지 연다
MAX_OWNER_QUESTIONS = 2
MAX_QTY = 99  # 전화로 한 줄에 받는 수량 상한. HCX 정규화(`nlu/hcx.py` `_qty`)와 같다

# 손님과 대화 중인 상태. 전역 규칙(취소·사장님 연결·메뉴 질문)을 받는다.
_ACTIVE = (State.ORDERING, State.CLARIFY, State.ASK_PICKUP, State.CONFIRM)
# "더 필요하신 거?"에 "아니요, ~"의 뒷말이 이것뿐이면 "아니요"는 주문 끝이다 (`_no_means_done`)
_AFTER_DONE: frozenset[ActType] = frozenset({"set_pickup_time", "set_name", "done_ordering"})
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
# NLU가 놓친 메뉴 한 마디를 매처로 다시 볼 최대 길이(공백·문장부호 뺀 글자). "맥도빅 주세요"(6)까지
RESCUE_MAX_CHARS = 7
# 5단계 티켓 11. NLU가 못 알아들은 말을 답변기에 보일 최소 길이와 상태(고르기·확인 중은 뺀다)
QUESTION_RESCUE_MIN_CHARS = 5
_RESCUE_STATES = (State.ORDERING, State.ASK_PICKUP, State.CONFIRM)
_RETRACT_STATES = (State.ORDERING, State.ASK_PICKUP)  # 말하다 거둔 말을 기다림으로 받는 상태
REVIEWS_FOR_FACTS = 30  # 답변기 자료에 넣으려고 읽는 최근 후기 수
SEARCH_SUGGEST = 3  # 이름으로 못 찾은 메뉴 말을 가게 지식 검색으로 찾았을 때 되묻는 후보 수
RELATED_REVIEWS = 3  # 질문과 가까운 후기(의미 검색) 수. 가게 지식이 search_reviews를 할 때만
RELATED_REVIEW_CHARS = 80  # 그 후기 한 개를 자료에 넣는 최대 글자 수
RELATED_REVIEW_LABEL = "손님 후기"  # 답 검사(`answerer`)가 아는 자료 이름과 같게
RECENT_LINES = 4  # 답변기에 주는 직전 대화 줄 수
# 답변기가 못 답했을 때도 규칙으로 답하는 종류: 메뉴 필드에 그대로 있는 값
_FIELD_KINDS = frozenset({"allergen", "allergy", "price"})
# 질문처럼 들리는 말. 답을 고르는 데는 쓰지 않는다. 이 말이 있으면 NLU를 기다리는 동안 답변기를
# 미리 불러 둘 뿐이다(틀리면 호출 한 번을 버리거나, 미리 못 불러 조금 늦는다)
_QUESTION_CUE = re.compile(
    r"\?|(?<!하)나요|까요|던가|는지|인가|얼마|몇\s|뭐|무슨|어떤|어때|어떻|언제|어디|왜"
    r"|돼|되나|되죠|될까|있어|있나|있죠|없어|없나|들어가|들어\s*있|달아|매워|맛|많아|커요|작아")
_SPACES_PUNCT = re.compile(r"[\s.,!?~]+")


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
    # 이번 턴에 연 사장님 질문(4단계 티켓 05). 세션이 사장님 화면에 보낸다. 세션은 열린 질문을
    # 엔진 속성으로 읽지 않고 이 칸으로만 안다(엔진이 다른 스레드에서 돌 수 있다, V1 놓친 것 3)
    owner_question: OwnerQuestion | None = None
    # 이번 턴의 보충 요청(티켓 13): 답하지 못하고 "사장님께 전해 둘게요"라고 한 메뉴 질문. 세션이
    # 사장님 쪽(`OwnerChannel.knowledge_wanted`)에 넘긴다
    knowledge_requests: tuple[KnowledgeRequest, ...] = ()
    # 5단계 티켓 11. 이번 턴에 답변기가 낸 결과(검사 뒤). 답변기를 안 불렀으면 None
    answer: AnswerReply | None = None
    # 6단계 품질 (턴 기록용). `route`: 이번 턴이 탄 길. `reask`: 이번 턴에 못 알아듣고 되물은 슬롯
    # (order·option·pickup·confirm·cancel, 말했는데 글 없음은 not_heard). 없으면 "".
    # `reasks`: 통화 전체 누적. `handoff`: 이번 턴에 사장님께 넘겼으면 그 이유
    route: Route = "rule"
    reask: str = ""
    reasks: int = 0
    handoff: HandoffReason | Literal[""] = ""
    # 이 턴의 첫 말이 받아들인 대답이다(담기·고치기·빼기 되받기, 마무리). 세션이 `PHONE_ACK_FIRST`
    # 일 때 그 앞에 고정 문장 "네."를 따로 둔다(TTS 첫 바이트, `templates.ack_first`)
    ack: bool = False


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
    # 4단계 티켓 05. 이번 손님 말 원문(사장님 질문 카드에 그대로 간다)과 이번 턴에 연 사장님 질문
    heard: str = ""
    owner_question: OwnerQuestion | None = None
    knowledge_requests: list[KnowledgeRequest] = field(default_factory=list)  # 티켓 13
    answer: AnswerReply | None = None  # 5단계 티켓 11. 이번 턴에 답변기가 낸 결과 (로그용)
    # 6단계 품질
    nlu_called: bool = False  # 이번 턴에 NLU를 불렀다 (`TurnResult.route`)
    lost: bool = False  # 못 알아들은 턴(모르는 말·못 읽은 시각). 두 번째 되묻기에 보기를 붙인다
    reask: str = ""  # 세어진 되묻기의 슬롯
    handoff: HandoffReason | Literal[""] = ""
    # 이번 턴에 손님이 확인해 준 큰 수량 (메뉴 id, 수량). 같은 턴에 다시 묻지 않는다
    qty_confirmed: tuple[str, int] | None = None
    acks: list[str] = field(default_factory=list)  # 받아들인 대답 문장들 (`TurnResult.ack`)

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


# 스냅숏 분류표 (6단계 티켓 02 "앞 턴 되감기"). 엔진의 모든 필드가 여기 있어야 한다
# (`tests/voice/test_engine_snapshot.py`가 `vars(engine)`과 맞춰 본다). 필드를 더하면 종류를 적는다.
# - fixed: 통화 동안 안 바뀐다(포트·설정·메뉴). 스냅숏에 넣지 않는다
# - value: 불변 값(문자열·수·frozen dataclass·`Cart`)이나 통째로 바꿔 끼우는 참조. 참조만 든다
# - copy: 제자리에서 고치는 그릇(`_transcript` list, `_MenuClarify.deferred`). 사본을 든다
# - cache: 처음 쓸 때 한 번 읽는 값. 되돌려도 다시 읽으면 같아서 그대로 둔다
SnapshotKind = Literal["fixed", "value", "copy", "cache"]
SNAPSHOT_FIELDS: dict[str, SnapshotKind] = {
    "_nlu": "fixed", "_answerer": "fixed", "_store": "fixed", "_config": "fixed",
    "_order_id": "fixed", "_menu": "fixed", "_matcher": "fixed", "_catalog": "fixed",
    "_ask_owner": "fixed", "_knowledge": "fixed",
    "_facts_reviews": "cache",
    # `_answer_early`는 (손님 말, 이미 낸 답변기 Future)다. 되감은 뒤에는 합친 말이라 글이 달라
    # 안 쓰인다(`_consult`가 글로 맞춰 보고, `_parse`가 턴마다 지운다). 턴 전 값으로 돌려 둔다
    "_answer_ready": "value", "_answer_early": "value", "_topic": "value",
    "_state": "value", "_cart": "value", "_pickup": "value", "_pickup_offer": "value",
    "_pickup_offer_question": "value", "_pickup_text": "value", "_last_question": "value",
    "_again": "value", "_focus": "value", "_fixing": "value", "_cancel_resume": "value",
    "_fail_key": "value", "_fail_count": "value", "_no_input": "value", "_reasks": "value",
    "_owner_open": "value", "_owner_asked": "value", "_read_back": "value",
    "_owner_timed_out": "value", "_owner_seen_said": "value",
    "_transcript": "copy", "_pending": "copy",
}


@dataclass(frozen=True)
class EngineSnapshot:
    """`DialogEngine.snapshot()`이 뜬 턴 전 상태. 속은 엔진만 본다."""

    values: dict[str, Any] = field(repr=False)


def _copied(name: str, value: Any) -> Any:
    """`copy` 필드의 사본. 스냅숏을 뜰 때와 되돌릴 때 둘 다 쓴다(같은 그릇을 나눠 들지 않게)."""
    if name == "_transcript":
        return list(value)
    if isinstance(value, _MenuClarify):  # `_OptionPending`은 frozen이라 그대로
        return replace(value, deferred=list(value.deferred))
    return value


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
        self, store: StoreKnowledge, nlu: Nlu, config: StoreConfig, *, order_id: str,
        ask_owner: bool = False, knowledge: KnowledgeReader | None = None,
        answerer: Answerer | None = None,
    ) -> None:
        """`ask_owner`면 근거 없는 메뉴 질문을 사장님께 묻는다(4단계 티켓 05). 아니면 1단계처럼
        "그건 제가 정확히 모르겠어요". 사장님 쪽이 붙은 통화 세션만 켠다.

        `knowledge`(티켓 13)를 주면 필드 근거가 없는 메뉴 질문에 사장님 보충 지식으로 먼저 답하고,
        그것도 없고 사장님께 바로 묻지도 않으면 "사장님께 전해 둘게요" + 보충 요청을 턴 결과에
        싣는다. 없으면 1단계 그대로.

        `answerer`(5단계 티켓 11)를 주면 손님 질문에 가게 자료(메뉴·영업시간·후기·사장님 메모)로
        답한다. 없으면 예전처럼 메뉴 필드 규칙(`menu_info`)으로만 답한다.
        """
        self._nlu = nlu
        self._answerer = answerer
        self._store = store
        # NLU가 못 알아들은 말을 답변기가 질문으로 본 결과. 그 턴의 질문 처리기가 꺼내 쓴다
        self._answer_ready: AnswerReply | None = None
        self._facts_reviews: tuple[Review, ...] | None = None  # 첫 질문 때 한 번 읽는다
        # NLU와 같이 미리 불러 둔 답변기 (손님 말, 결과). `_consult`가 같은 말이면 꺼내 쓴다
        self._answer_early: tuple[str, Future[AnswerReply]] | None = None
        # 마지막으로 질문한 메뉴. 이름 없는 이어 묻기("그럼 많이 달아요?")의 대상이다
        self._topic: MenuItem | None = None
        self._config = config
        self._order_id = order_id
        self._menu: list[MenuItem] = store.list_menu()  # 통화 시작 때 한 번 (spec §4)
        self._matcher = MenuMatcher(self._menu)
        # NLU에 줄 가게 메뉴 정보. 통화 동안 메뉴가 안 바뀌므로 한 번 (5단계 티켓 05)
        self._catalog = menu_catalog(self._menu, config.required_options)
        self._state = State.GREETING
        self._cart = Cart()
        self._pickup: datetime | None = None
        self._pickup_offer: datetime | None = None  # ASK_PICKUP에서 "네"를 기다리는 시각
        self._pickup_offer_question = ""  # 그 시각을 물은 말. 항목을 고친 뒤 다시 묻는다
        self._pickup_text = ""  # 주문 중에 말했지만 되물어야 하는 시각 원문
        self._last_question = ""
        # 되물을 때(무응답·못 알아들음) 직전 질문 대신 할 짧은 질문. 없으면 직전 질문의 질문 문장만
        # (`templates.question_only`, 감사 문제 A)
        self._again: str | None = None
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
        # 통화 전체에서 못 알아듣거나 되물은 횟수 (6단계 품질). 진전이 있어도 지우지 않는다
        self._reasks = 0
        self._no_input = 0  # 연속 무응답 횟수 (3단계 티켓 04). 받아들인 말이 오면 0
        # 4단계 티켓 05. 상태는 그대로 두고 열린 사장님 질문 하나만 든다(`_cancel_resume`과 같은
        # 모양). 하던 질문은 `_last_question`에 그대로 있고 답이 오면 `_question_again`으로 돌아간다
        self._ask_owner = ask_owner
        self._knowledge = knowledge
        self._owner_open: OwnerQuestion | None = None
        self._owner_asked = 0  # 이 통화에서 연 질문 수 (질문 번호)
        # 티켓 06 막는 조건: 복창이 한 번이라도 나갔다(고치러 ORDERING으로 돌아가도 그대로) ·
        # 이 통화에서 시간 초과가 났다. 열린 질문을 사장님이 봤다고 이미 말했다
        self._read_back = False
        self._owner_timed_out = False
        self._owner_seen_said = False

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
        turn.heard = text
        rest = wait_request_rest(text) if wait_request else None
        if rest == "" or (rest is None and self._state in _RETRACT_STATES and retracted(text)):
            # "잠깐만요", 또는 말하다 거둔 말("한 여섯 시… 아 아니다"). 못 알아들었다고 하지 않고
            # 기다린다. 복창 중의 "아니다"는 고쳐 달라는 말이라 평소처럼 본다
            self._no_input = 0
            self._say(turn, templates.take_your_time())
            return self._result(turn)
        if self._reasks and self._state in _ACTIVE and frustrated(text):
            # 이미 되물은 통화에서 손님이 짜증을 냈다. 더 묻지 않고 넘긴다 (6단계 품질)
            self._say(turn, templates.sorry_for_trouble())
            self._hand_off(turn, "frustrated")
            return self._result(turn)
        acts = self._parse(text if rest is None else rest, turn)
        acts = self._question_rescue(turn, text if rest is None else rest, acts)
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
        if self._state is State.ORDERING:
            self._drop_question(turn)
        self._say(turn, templates.take_your_time(after_reply=bool(turn.utterances)))

    def _drop_question(self, turn: _Turn) -> None:
        """이번 턴 질문에서 질문 문장("…있으세요?")만 뺀다. 질문은 `_last_question`에 남는다.

        `_take_time`·`_hold_for_owner`가 같이 쓴다. 복창(CONFIRM)은 손대지 않는다(복창 불변식:
        복창이 그 턴의 마지막 말이어야 이른 "네" 거르기·버튼 막기가 걸린다, 리뷰 C3).
        """
        at = turn.question_at
        if self._state is State.CONFIRM or at is None:
            return
        kept = [s for s in _SENTENCE_END.split(turn.utterances[at]) if not s.endswith("?")]
        if kept:
            turn.utterances[at] = " ".join(kept)
        else:
            del turn.utterances[at]
        turn.question_at = None

    def not_heard(self, now: datetime) -> TurnResult:
        """말소리는 있었는데 STT 글이 없었다(세션이 불렀다, 3단계 티켓 04). 다시 말해 달라고 한다.

        같은 슬롯의 못 알아들음 실패로 세지 않고, 무응답 횟수도 그대로 둔다. 직전 질문은 바꾸지
        않는다. 통화 전체 되묻기에는 넣는다(6단계 품질): 상한이면 다시 말해 달라고 하지 않고 넘긴다.
        """
        if self._state is State.GREETING:
            raise RuntimeError("start()를 먼저 불러야 해요")
        turn = _Turn(self._local(now), cart_before=self._cart)
        if self._state is not State.DONE:
            self._reasks += 1
            turn.reask = "not_heard"
            if self._over_call_limit():
                self._give_up(turn, "call_failures")
            else:
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
        self._ask(turn, self._question_again(short=True))
        return self._result(turn)

    def owner_answered(self, number: int, answer: OwnerAnswer, now: datetime) -> TurnResult:
        """사장님 답(4단계 티켓 05). 세션이 봇이 먼저 말하는 턴으로 넣는다. 장바구니는 안 바꾼다.

        답 종류별 고정 존댓말 문장 + 하던 질문(`_question_again`, 복창 중이면 지금 장바구니 복창).
        사장님 글을 손님에게 그대로 읽지 않는다. 열린 질문이 없거나 번호가 다르면(낡은 답) 말 없는
        턴이다. 세션은 그런 답을 넣지 않는다(빈 봇 턴은 무음 대기를 다시 센다, V1 놓친 것 2).
        """
        if self._state is State.GREETING:
            raise RuntimeError("start()를 먼저 불러야 해요")
        turn = _Turn(self._local(now), cart_before=self._cart)
        question = self._owner_open
        if self._state is State.DONE or question is None or question.number != number:
            return self._result(turn)
        self._owner_open = None
        if answer == "timeout":
            self._owner_timed_out = True
        # 들어가요?류 질문은 버튼 뜻이 들어가요/안 들어가요다 (리뷰 P2)
        subject = question.subject if question.yes_no else ""
        label = templates.owner_answer_label(answer, contains=question.yes_no)
        self._transcript.append(f"사장님: {label}")
        self._say(turn, templates.owner_answer(answer, name=question.menu_name, subject=subject))
        self._ask(turn, self._question_again())
        return self._result(turn)

    def owner_seen(self, number: int, now: datetime) -> TurnResult:
        """사장님이 질문 카드를 봤다(티켓 06). 열린 질문마다 한 번 "사장님이 보고 계세요".

        질문을 다시 하지 않는다(아직 기다리는 중). 열린 질문이 아니거나 이미 말했으면 말 없는 턴.
        """
        if self._state is State.GREETING:
            raise RuntimeError("start()를 먼저 불러야 해요")
        turn = _Turn(self._local(now), cart_before=self._cart)
        question = self._owner_open
        if (self._state is State.DONE or question is None or question.number != number
                or self._owner_seen_said):
            return self._result(turn)
        self._owner_seen_said = True
        self._say(turn, templates.owner_seen())
        return self._result(turn)

    def snapshot(self) -> EngineSnapshot:
        """지금 대화 상태를 떠 둔다 (6단계 티켓 02 "앞 턴 되감기").

        세션이 손님 턴을 `handle`에 넘기기 직전에 부른다. 봇 답의 첫 소리 전에 손님이 말을
        이으면("아니요 … 그게 다예요") `restore`로 이 상태로 돌아가 합친 말을 다시 처리한다.
        엔진이 쉬는 동안(다른 스레드에서 `handle`이 돌지 않을 때)에만 부른다.
        """
        return EngineSnapshot({
            name: _copied(name, getattr(self, name)) if kind == "copy" else getattr(self, name)
            for name, kind in SNAPSHOT_FIELDS.items() if kind in ("value", "copy")
        })

    def restore(self, snap: EngineSnapshot) -> None:
        """`snapshot()` 때로 되돌린다. 그 뒤의 `handle`·봇 턴이 없던 일이 된다.

        엔진 밖으로 나간 것(세션이 보낸 주문·사장님 질문·보충 요청)은 되돌리지 못한다. 그래서
        세션은 그런 턴을 되감지 않는다. 엔진이 쉬는 동안에만 부른다.
        """
        for name, value in snap.values.items():
            kind = SNAPSHOT_FIELDS[name]
            setattr(self, name, _copied(name, value) if kind == "copy" else value)

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
            menu_catalog=self._catalog,
        )

    def _local(self, now: datetime) -> datetime:
        """`now` → 가게 시각. naive면 가게 시각으로 보고 tz만 붙인다 (모듈 설명 참고)."""
        tz = self._config.tz
        return now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)

    # ------------------------------------------------------------ 턴 골격

    def _parse(self, text: str, turn: _Turn | None = None) -> list[Act]:
        """규칙 단계 → (안 걸리면) NLU. 내용 앞의 "네/아니요"는 뗀다.

        "아니요, 두 개요" → [no, change_qty]에서 no를 떼고 change_qty만 남긴다. 복창(CONFIRM)에서는
        정정 act가 오면 그 자체로 정정으로 보므로 no가 따로 필요 없다.
        """
        acts = apply_rules(text)
        if acts is not None and no_and_done(text) and not self._takes_no_and_done():
            acts = None  # 예/아니요를 묻는 중이다. "아니요"가 답일 수 있어 전처럼 NLU가 읽는다
        self._answer_early = None
        if acts is None:
            self._start_answer_early(text)
            if turn is not None:
                turn.nlu_called = True
            acts = self._nlu.parse(text, self.nlu_context())
            acts = self._menu_word_rescue(text, acts)
            acts = self._pickup_with_done(text, acts, turn)
        if not any(a.type == "add_item" for a in acts):  # 같은 말에서 담으면 붙일 데가 있다
            acts = [self._baseless_option(a) for a in acts]
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

    def _pickup_with_done(self, text: str, acts: list[Act],
                          turn: _Turn | None) -> list[Act]:
        """주문 끝과 같이 말한 시각을 NLU가 빠뜨렸으면 채운다 (6단계, 실기기 대본 C4).

        "아니요 지금 바로 갈게요"를 HCX가 done_ordering 하나로만 내서 "몇 시쯤 오세요?"를 다시
        물었다. 주문 받는 중에 주문 끝만 있고 시각 act가 없을 때, 그 말이 픽업 시각으로 읽히면
        (`parse_pickup`, 시각 답을 읽는 그 파서) 시각 act를 덧붙인다. 최종 복창이 한 번 더 확인한다.
        """
        if (turn is None or self._state is not State.ORDERING
                or [a.type for a in acts] != ["done_ordering"]):
            return acts
        if parse_pickup(text, turn.now).time is None:
            return acts
        return [*acts, Act("set_pickup_time", time_text=text)]

    def _takes_no_and_done(self) -> bool:
        """"아니요 + 끝 표현" 규칙([no, done_ordering], 6단계 티켓 02)을 지금 써도 되나.

        주문 받는 중과 복창 중에만 쓴다. 주문 중 "더 필요하신 거?" 뒤면 주문 끝이고
        (`_no_means_done`), "어떤 걸 고쳐 드릴까요?" 뒤면 고칠 것이 없으니 다시 복창한다.
        복창 중이면 앞의 no를 떼고 done_ordering만 남아 확정도 확정 거부도 아니다(못 알아들었다고
        하고 복창을 다시 한다).
        메뉴 확인("맞으세요?")·시각 제안("괜찮으세요?")·취소 확인("취소할까요?")을 기다리는 중에는
        "아니요"가 그 질문의 답이고 "괜찮아요"·"됐어요"는 군말일 수 있다. 규칙으로 못 박지 않는다.
        """
        return self._cancel_resume is None and self._state in (State.ORDERING, State.CONFIRM)

    def _menu_word_rescue(self, text: str, acts: list[Act]) -> list[Act]:
        """NLU가 다 unknown으로 낸 짧은 말이 메뉴와 비슷하면 그 메뉴 주문으로 본다 (5단계 티켓 04).

        실기기 통화에서 STT "맥도빅" 한 마디를 HCX가 unknown으로 냈다. 매처가 수락·되묻기로 보는
        짧은 말(`RESCUE_MAX_CHARS` 이하)만 바꾼다. 긴 문장은 다른 말이 섞여 메뉴로 짐작하지 않는다.
        되묻기 판정이면 엔진이 "혹시 ○○ 말씀하신 거 맞으세요?"로 묻는다.
        """
        if self._state is not State.ORDERING or any(a.type != "unknown" for a in acts):
            return acts
        compact = _SPACES_PUNCT.sub("", text)
        if not compact or len(compact) > RESCUE_MAX_CHARS:
            return acts
        if self._matcher.match(text, "").decision not in ("accept", "confirm"):
            return acts
        return [Act("add_item", menu_text=text.strip(" .?!"))]

    def _baseless_option(self, act: Act) -> Act:
        """어느 메뉴에도 없는 옵션 말만 있는 `set_option`, 담은 것이 없을 때의 요청(`set_request`)은
        못 알아들은 말로 돌린다 (티켓 11).

        실기기에서 NLU가 "추천 좀 해줘"를 옵션으로 내 "어떤 메뉴에 해 드릴까요?"라고 되물었다.
        메뉴 이름도, 담은 것도, 그 옵션이 있는 메뉴도 없으면 옵션이 아니다. 못 알아들은 말은
        답변기가 질문인지 한 번 더 본다(`_question_rescue`).
        """
        if act.type not in ("set_option", "set_request") or _names_menu(act):
            return act
        if self._state is not State.ORDERING or self._pending is not None or self._cart.lines:
            return act
        if act.type == "set_request":  # 담은 것이 없으면 요청을 붙일 데가 없다
            return Act("unknown", free_text=act.free_text)
        every = [o for item in self._menu
                 for o in [c for g in self._required_for(item) for c in g.choices]
                 + list(item.options)]
        if act.option_text and find_options(every, act.option_text):
            return act
        return Act("unknown", free_text=act.free_text or act.option_text)

    def _start_answer_early(self, text: str) -> None:
        """질문처럼 들리면 NLU와 같이 답변기를 불러 둔다. 질문 턴이 NLU + 답변기(약 2.5초)에서
        둘 중 긴 쪽(약 1.5초)으로 줄어든다. 메뉴가 많아 자료를 줄여 주는 가게는 메뉴를 알아야
        해서 미리 부르지 않는다."""
        if self._answerer is None or self._state not in _ACTIVE:
            return
        if len(self._menu) > FULL_FACTS_MENUS or not _QUESTION_CUE.search(text):
            return
        # 이름 없는 질문의 대상: 방금 담은 메뉴, 없으면 마지막으로 질문한 메뉴
        item = self._asked_item(Act("ask_menu_info"))[0] or self._topic
        request = self._answer_request(text, item, self._transcript[-RECENT_LINES - 1:-1])
        self._answer_early = (text, _pool().submit(self._answered, request))

    def _question_rescue(self, turn: _Turn, text: str, acts: list[Act]) -> list[Act]:
        """NLU가 다 unknown으로 낸 말을 답변기에 한 번 보인다 (5단계 티켓 11).

        실기기 통화에서 STT "불린의결과류 들어가"(블린에 견과류 들어가?)를 NLU가 unknown으로 냈다.
        답변기가 질문으로 보면(답했거나, 질문인데 자료에 없음) 질문 act로 바꾼다. 질문이 아니라고
        하면 그대로 둔다. 고르기·확인을 기다리는 중(CLARIFY)과 아주 짧은 말은 보내지 않는다.
        """
        self._answer_ready = None
        if self._answerer is None or self._state not in _RESCUE_STATES:
            return acts
        if not acts or any(a.type != "unknown" for a in acts):
            return acts
        if len(_SPACES_PUNCT.sub("", text)) < QUESTION_RESCUE_MIN_CHARS:
            return acts
        reply = self._consult(turn, text, None)
        if reply.kind == "not_question":
            return acts
        self._answer_ready = reply
        return [Act("ask_other", free_text=text)]

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
        return (isinstance(pending, _MenuClarify) and len(pending.candidates) == 1
                and not pending.declined)

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
                    self._say(turn, self._noted(turn, turn.accepted))
                if turn.pickup_note and self._missing_required() is None:
                    # "아니요, 지금 갈게요" → 복창 앞에 준비 시간 안내 (2단계 리뷰 5번).
                    # 필수 옵션을 먼저 물을 때는 그 질문만 한다
                    self._say(turn, turn.pickup_note)
                self._advance(turn)
            elif (missing := self._missing_required()) is not None:
                if turn.accepted:
                    self._say(turn, self._noted(turn, turn.accepted))
                if turn.pickup_note:
                    self._say(turn, turn.pickup_note)
                self._ask_required(turn, *missing)
            elif turn.accepted and turn.pickup_note:
                self._say(turn, self._noted(turn, turn.accepted))
                self._say(turn, turn.pickup_note)
                self._ask(turn, templates.anything_else())
            elif turn.accepted:
                self._noted(turn, turn.accepted)
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
            self._ask(turn, self._question_again(short=True))
        if (turn.answered and not turn.asked and turn.owner_question is None
                and self._state not in TERMINAL):
            self._ask(turn, self._question_again())
        if self._state is State.CONFIRM and self._cancel_resume is None:
            if turn.question not in self._readbacks():  # 복창 불변식 (모듈 설명)
                self._ask_readback(turn)
        if turn.owner_question is not None:
            self._hold_for_owner(turn)

    def _hold_for_owner(self, turn: _Turn) -> None:
        """사장님 질문을 연 턴의 끝(4단계 티켓 05). 대사는 "잠시만요, 사장님께 여쭤볼게요."로 끝.

        같은 턴에 다른 질문이 붙었으면("메도빅 하나요. 더 필요하신 거 있으세요?") 질문 문장만 뺀다.
        그 질문은 `_last_question`에 남아 사장님 답 뒤에 다시 한다. 같은 턴에 대화가 끝났으면
        (사장님 연결 등) 질문을 열지 않는다.

        같은 턴 뒤쪽 말로 복창(CONFIRM)에 들어갔으면("견과 들어가요? 6시 반에 갈게요") 질문을
        거둔다(막는 조건과 같다, 리뷰 C3). 그 질문에는 막혔을 때 문장을 복창 앞에 넣는다.
        """
        question = turn.owner_question
        if question is None:
            return
        if self._state in TERMINAL or self._state is State.CONFIRM:
            self._owner_open = turn.owner_question = None
            self._owner_asked -= 1  # 연 질문이 아니니 번호를 돌려놓는다
            if self._state is State.CONFIRM:
                self._cannot_answer(turn, question.key, question.menu_name, before_question=True)
            return
        self._drop_question(turn)
        self._say(turn, templates.ask_owner_hold())

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
            (changed if was_there else added).append(line.phrase(self._unsaid(line.item)))
        notes = []
        if changed:
            notes.append(self._ok(turn, templates.changed_to(changed)))
        if added:
            notes.append(self._noted(turn, added))
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
            self._cart.summary(self._unsaid),
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
            turn.order = self._build_order(turn.order_status, turn.now)
        readback = self._state is State.CONFIRM and turn.question in self._readbacks()
        result = TurnResult(list(turn.utterances), self._state, self._cart, turn.order,
                            readback=readback, owner_question=turn.owner_question,
                            knowledge_requests=tuple(turn.knowledge_requests),
                            answer=turn.answer, route=_route(turn), reask=turn.reask,
                            reasks=self._reasks, handoff=turn.handoff,
                            ack=bool(turn.utterances) and any(
                                turn.utterances[0].startswith(a) for a in turn.acks))
        if self._state is State.CONFIRM:
            self._read_back = True
        if self._state in TERMINAL:
            self._owner_open = None  # 취소·넘김·마무리: 열린 질문을 닫는다 (티켓 06)
            self._state = State.DONE
        return result

    # ------------------------------------------------------------ 말하기

    def _say(self, turn: _Turn, text: str) -> None:
        """대사는 턴이 끝날 때 `_result`가 transcript에 옮긴다 (FALLBACK이 바꿀 수 있게)."""
        turn.utterances.append(text)

    def _noted(self, turn: _Turn, phrases: Sequence[str]) -> str:
        """담은 항목 되받기("메도빅 하나요."). 받아들인 대답으로 적어 둔다 (`TurnResult.ack`)."""
        return self._ok(turn, templates.items_noted(phrases))

    @staticmethod
    def _ok(turn: _Turn, text: str) -> str:
        turn.acks.append(text)
        return text

    def _ask(
        self, turn: _Turn, question: str, *, force: bool = False, again: str | None = None
    ) -> None:
        """이번 턴의 질문 하나. 이미 물었으면 무시한다. `force`면 앞 질문을 지우고 이것을 묻는다.

        `again`은 되물을 때 할 짧은 질문이다(없으면 `question`의 질문 문장만). 같은 질문을 다시
        물을 때는 앞에서 받은 `again`을 그대로 둔다.
        """
        if turn.asked:
            if not force:
                return
            if turn.question_at is not None:
                del turn.utterances[turn.question_at]
        turn.asked = True
        if question != self._last_question or again is not None:
            self._again = again
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
        turn.unhandled = turn.lost = True
        self._fail(turn)

    def _question_again(self, *, short: bool = False) -> str:
        """다시 할 질문. ORDERING에서는 되받기("메도빅 하나요.")를 빼고 질문만 한다.

        CONFIRM에서는 늘 지금 장바구니로 만든 복창이다 (리뷰 C2, P1).
        `short`(무응답 되묻기·못 알아들음)면 직전 질문 앞의 안내·사과 문장("죄송해요, 저희가 9시에
        문을 닫아요.")은 빼고 질문만 한다(spec "여보세요? + 직전 질문", 감사 문제 A). 손님이 다시
        말해 달라고 했을 때("뭐라고요?")는 앞 문장까지 그대로 한다.
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
        if not self._last_question:
            return templates.ask_order()
        if short:
            return self._again or templates.question_only(self._last_question)
        return self._last_question

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
        """실패만 있고 진전이 없는 턴을 센다. 같은 슬롯에서 `MAX_FAILURES`번이면 FALLBACK.

        진전(담기·고치기·주문 끝·질문 답하기)이 있거나 실패가 없는 턴이면 슬롯 횟수를 지운다.
        FALLBACK이면 이번 턴에 하려던 재질문은 버리고 넘김 안내로 바꾼다.

        6단계 품질(되묻기 루프 방지). 같은 슬롯 횟수와 통화 전체 횟수(`_reasks`)를 따로 센다:
        - 같은 슬롯 1번째: 전처럼 되묻는다. 2번째(`HINT_AT_FAILURE`): 질문 앞에 보기를 붙인다
          (`_choices_hint`). 3번째: 넘긴다(이유 slot_failures)
        - 통화 전체가 `StoreConfig.max_reasks`번이면 슬롯이 달라도 넘긴다(이유 call_failures).
          진전이 있어도 지우지 않는다. 말했는데 글 없음(`not_heard`)도 여기에 든다
        """
        progress = (
            turn.accepted or turn.touched or turn.changed or turn.done_ordering or turn.answered
        )
        if not turn.failed or progress or self._state in TERMINAL:
            self._fail_key, self._fail_count = "", 0
            return
        self._reasks += 1
        turn.reask = turn.failed
        if turn.failed == self._fail_key:
            self._fail_count += 1
        else:
            self._fail_key, self._fail_count = turn.failed, 1
        if self._fail_count >= MAX_FAILURES:
            self._give_up(turn, "slot_failures")
        elif self._over_call_limit():
            self._give_up(turn, "call_failures")
        elif self._fail_count == HINT_AT_FAILURE and turn.lost:
            self._say_hint(turn)

    def _over_call_limit(self) -> bool:
        limit = self._config.max_reasks
        return limit > 0 and self._reasks >= limit

    def _give_up(self, turn: _Turn, reason: HandoffReason) -> None:
        """더 되묻지 않고 넘긴다. 이번 턴에 하려던 말은 버린다."""
        turn.utterances.clear()
        turn.question_at = None
        self._say(turn, templates.too_many_failures())
        self._hand_off(turn, reason)

    def _say_hint(self, turn: _Turn) -> None:
        """두 번째 되묻기: 질문 앞에 보기를 넣는다. 질문이 "?"로 안 끝나면("…한 번 더 말씀해
        주세요.") 그 뒤에 붙인다."""
        hint = self._choices_hint(turn)
        if not hint:
            return
        at = turn.question_at
        if at is None:
            self._say(turn, hint)
        elif turn.utterances[at].endswith("?"):
            turn.utterances.insert(at, hint)
            turn.question_at = at + 1
        else:
            turn.utterances.insert(at + 1, hint)

    def _choices_hint(self, turn: _Turn) -> str:
        """지금 질문에 붙일 보기·답하는 법. 가게 데이터(메뉴·준비 시간)로 만든다. 없으면 "".

        이미 보기가 든 질문(필수 옵션 "따뜻하게 드릴까요, 차갑게 드릴까요?", 둘 중 고르기)과
        메뉴 목록을 방금 말한 턴에는 붙이지 않는다.
        """
        if turn.menu_listed:
            return ""
        if self._cancel_resume is not None:
            return templates.yes_no_hint()
        if self._state is State.CONFIRM:
            return templates.confirm_hint()
        if self._state is State.ASK_PICKUP:
            if self._pickup_offer is not None:
                return templates.yes_no_hint()
            first = earliest_pickup(turn.now, self._config)
            close = today_at(turn.now, self._config.close_time)
            times = [t for t in (first, first + TIME_EXAMPLE_GAP) if t <= close]
            return templates.time_examples(times) if times else ""
        if self._state is State.CLARIFY:
            pending = self._pending
            if (isinstance(pending, _MenuClarify) and len(pending.candidates) == 1
                    and not pending.declined):
                return templates.yes_no_hint()
            return ""
        if self._state is State.ORDERING and not self._fixing:
            names = self._available_names()[:MENU_EXAMPLES]
            if not names:
                return ""
            if self._cart.is_empty():
                return templates.menu_examples(names)
            return templates.more_or_done_hint(names[:2])
        return ""

    def _hand_off(self, turn: _Turn, reason: HandoffReason) -> None:
        """FALLBACK: 사장님께 메모를 남긴다. 지금까지의 장바구니·시각·대화를 handoff Order로.

        넘긴 이유는 `TurnResult.handoff`로 나가 세션이 턴 기록에 남긴다.
        """
        self._pending = None
        self._cancel_resume = None
        self._state = State.FALLBACK
        self._say(turn, templates.handoff())
        turn.asked = True
        turn.order_status = "handoff"
        turn.handoff = reason

    @on("want_owner", *_ACTIVE)
    def _on_want_owner(self, turn: _Turn, act: Act) -> None:
        self._hand_off(turn, "requested")

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
        self._again = None
        return False

    # ------------------------------------------------------------ 다시 말하기·메뉴 질문 (티켓 05)

    @on("repeat_please", *_ACTIVE)
    def _on_repeat_please(self, turn: _Turn, act: Act) -> None:
        self._ask(turn, self._question_again())

    @on("ask_menu_info", *_ACTIVE)
    def _on_ask_menu_info(self, turn: _Turn, act: Act) -> None:
        """메뉴 질문. 답변기가 있으면 가게 자료로, 없거나 못 답하면 메뉴 필드 규칙으로 답한다.
        상태는 그대로이고 직전 질문은 `_finish`가 다시 한다."""
        turn.answered = True
        item, unlisted = self._asked_item(act)
        question = act.free_text or act.menu_text or turn.heard
        if self._answerer is not None:
            item = item or self._topic
            reply = self._answer_ready or self._consult(turn, question, item)
            self._answer_ready = None
            if reply.kind == "not_question" and item is not None:
                # NLU는 질문으로 봤다. 미리 부른 답변기는 메뉴를 몰랐을 수 있어 메뉴를 주고 다시
                reply = self._consult(turn, question, item, fresh=True)
            item = self._menu_named(reply.menu) or item
            self._topic = item or self._topic
            if reply.kind == "answer":
                self._say(turn, reply.say)
                return
            if item is None:
                self._cannot_answer(turn, KnowledgeKey("", "other", _store_gist(question)),
                                    templates.STORE_QUESTION)
                return
            unlisted = False
        if unlisted:
            self._say(turn, templates.not_on_menu(self._available_names()))
            return
        if item is None:
            self._say(turn, templates.dont_know())
            return
        answer = menu_answer(item, question)
        key = KnowledgeKey(item.id, answer.kind, answer.subject, answer.yes_no)
        # 답변기가 못 답한 질문은 틀림없는 필드 답(가격·알레르기 표시)만 규칙으로 한다. 맛·옵션
        # 규칙은 낱말만 보고 엉뚱한 답을 한 적이 있다("홀케이크면 달아요?" → 옵션 가격)
        sure = self._answerer is None or answer.kind in _FIELD_KINDS
        if answer.text is not None and sure:
            self._say(turn, answer.text)
        elif (known := self._known(key, item, answer)) is not None:
            self._say(turn, known)
        elif self._ask_owner and answer.for_owner and self._may_ask_owner():
            self._open_owner_question(turn, item, answer)
        else:
            self._cannot_answer(turn, key, item.name_ko)

    def _asked_item(self, act: Act) -> tuple[MenuItem | None, bool]:
        """질문이 가리키는 메뉴와, 이름은 말했는데 메뉴판에 없는지."""
        if _names_menu(act):
            result = self._matcher.match(act.menu_text, act.menu_guess)
            item = result.item or next(iter(result.candidates), None)
            if result.decision == "reject" or item is None:
                return None, True
            return item, False
        # "글루텐 프리예요?": 방금 다룬 메뉴, 장바구니에 하나뿐이면 그 메뉴
        focus = self._focus_index()
        only = self._cart.lines[0] if len(self._cart.lines) == 1 else None
        line = self._cart.lines[focus] if focus is not None else only
        return (None if line is None else line.item), False

    def _menu_named(self, name: str) -> MenuItem | None:
        return next((m for m in self._menu if m.name_ko == name.strip()), None)

    def _consult(self, turn: _Turn, question: str, item: MenuItem | None, *,
                 fresh: bool = False) -> AnswerReply:
        """답변기에 묻고 답을 가게 자료에 대어 본다(`answerer.checked`). 실패는 "unsure".
        같은 말로 미리 불러 둔 것(`_start_answer_early`)이 있으면 그 결과를 쓴다(`fresh`면 새로)."""
        early, self._answer_early = self._answer_early, None
        if early is not None and early[0] == question and not fresh:
            reply = early[1].result()
        else:
            reply = self._answered(
                self._answer_request(question, item, self._transcript[-RECENT_LINES - 1:-1]))
        turn.answer = reply
        return reply

    def _answer_request(self, question: str, item: MenuItem | None,
                        recent: Sequence[str]) -> AnswerRequest:
        if self._facts_reviews is None:
            self._facts_reviews = tuple(self._store.recent_reviews(REVIEWS_FOR_FACTS))
        notes = self._knowledge if isinstance(self._knowledge, Notes) else None
        facts = store_facts(self._config, self._menu, self._facts_reviews, notes,
                            focus_id=item.id if item else "")
        return AnswerRequest(question, facts, focus=item.name_ko if item else "",
                             recent=tuple(recent))

    def _answered(self, request: AnswerRequest) -> AnswerReply:
        """답변기 한 번 + 검사. 엔진 상태를 건드리지 않는다(다른 스레드에서 돈다)."""
        assert self._answerer is not None
        try:
            request = self._with_related_reviews(request)
            return checked(self._answerer.answer(request), request)
        except Exception:  # noqa: BLE001  답변기 고장으로 통화가 끊기면 안 된다
            log.exception("답변기 실패")
            return UNSURE

    def _with_related_reviews(self, request: AnswerRequest) -> AnswerRequest:
        """가게 지식이 리뷰 의미 검색(`search_reviews`, 계약 밖 선택 기능)을 하면 질문과 가까운 후기
        몇 개를 자료 한 줄로 더한다 ("웨이팅 길어요?" → 웨이팅 후기). 없거나 실패하면 그대로.
        답변기 스레드에서 돈다(질문 임베딩 호출이 통화 턴을 막지 않게)."""
        search = getattr(self._store, "search_reviews", None)
        if not callable(search) or not request.text.strip():
            return request
        try:
            hits = search(request.text, k=RELATED_REVIEWS)
        except Exception:  # noqa: BLE001
            log.exception("후기 검색 실패")
            return request
        known = " ".join(request.facts)
        texts = []
        for review, _score in hits:
            text = " ".join(review.text.split())[:RELATED_REVIEW_CHARS]
            if text and text not in known:
                texts.append(text)
        if not texts:
            return request
        line = " | ".join(f"{RELATED_REVIEW_LABEL}: {t}" for t in texts)
        return replace(request, facts=(*request.facts, line))

    def _cannot_answer(self, turn: _Turn, key: KnowledgeKey, menu_name: str, *,
                       before_question: bool = False) -> None:
        """근거 없는 메뉴 질문에 사장님께 묻지 않고 답한다. 보충 지식 저장소가 붙었으면 "사장님께
        전해 둘게요" + 보충 요청(티켓 13, Q17), 아니면 1단계 "그건 제가 정확히 모르겠어요".

        `before_question`이면 이번 턴 질문(복창) 앞에 넣는다(복창이 마지막 말로 남게).
        """
        if self._knowledge is not None:
            line = templates.pass_on_to_owner()
            turn.knowledge_requests.append(
                KnowledgeRequest(key, menu_name=menu_name, text=turn.heard))
        else:
            line = templates.dont_know()
        if before_question and turn.question_at is not None:
            turn.utterances.insert(turn.question_at, line)
            turn.question_at += 1
        else:
            self._say(turn, line)

    def _known(self, key: KnowledgeKey, item: MenuItem, answer: MenuAnswer) -> str | None:
        """사장님 보충 지식으로 한 답(티켓 13). 없으면 None.

        예/아니오로 답할 질문("견과류 들어가요?")이면 사장님 확인을 먼저 고정 문장으로, 아니면
        사장님 설명을 인용 틀로 말한다(사장님 글을 다듬지 않는다).
        """
        known = self._knowledge.lookup(key) if self._knowledge is not None else None
        if known is None:
            return None
        if answer.yes_no and known.contains is not None:
            return templates.owner_checked(item.name_ko, answer.subject, known.contains)
        if known.note:
            return templates.owner_said(known.note)
        return None

    def _may_ask_owner(self) -> bool:
        """막는 조건(티켓 06): 복창이 나간 뒤 · 이미 열린 질문 · 통화당 `MAX_OWNER_QUESTIONS`번 ·
        이 통화에서 시간 초과 한 번. 막히면 1단계처럼 "그건 제가 정확히 모르겠어요".
        """
        return not (self._read_back or self._state is State.CONFIRM
                    or self._owner_open is not None
                    or self._owner_asked >= MAX_OWNER_QUESTIONS or self._owner_timed_out)

    def _open_owner_question(self, turn: _Turn, item: MenuItem, answer: MenuAnswer) -> None:
        """근거 없는 메뉴 질문을 사장님 질문으로 연다(4단계 티켓 05). 대사는 `_hold_for_owner`가.

        상태와 직전 질문은 그대로 둔다. 막는 조건은 `_may_ask_owner`.
        """
        self._owner_asked += 1
        self._owner_seen_said = False
        question = OwnerQuestion(number=self._owner_asked, menu_id=item.id,
                                 menu_name=item.name_ko, text=turn.heard, kind=answer.kind,
                                 subject=answer.subject, yes_no=answer.yes_no)
        self._owner_open = turn.owner_question = question

    @on("ask_other", *_ACTIVE)
    def _on_ask_other(self, turn: _Turn, act: Act) -> None:
        """가게 질문(영업시간·주차·할인 등). 답변기가 없으면 근거가 없으므로 모른다고 한다."""
        turn.answered = True
        if self._answerer is None:
            self._say(turn, templates.dont_know())
            return
        question = act.free_text or turn.heard
        reply = self._answer_ready or self._consult(turn, question, None)
        self._answer_ready = None
        item = self._menu_named(reply.menu)
        self._topic = item or self._topic
        if reply.kind == "answer":
            self._say(turn, reply.say)
            return
        key = KnowledgeKey(item.id if item else "", "other", _store_gist(question))
        self._cannot_answer(turn, key, item.name_ko if item else templates.STORE_QUESTION)

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
            searched = self._searched_menus(act)
            if searched and item not in searched:
                # 글자는 비슷하지만 검색은 다른 메뉴를 가리킴
                # ("얇은 팬케이크": 매처 메도빅, 검색 블린)
                # → 하나만 확인하지 않고 둘 다 묻는다
                options = (item, *searched)[:SEARCH_SUGGEST]
                names = [m.name_ko for m in options]
                self._open_menu_clarify(turn, act, options, templates.choose_menu(names))
            else:
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
        elif suggested := self._searched_menus(act):
            # 이름으로는 못 찾았지만 가게 지식 검색이 찾음 ("꿀 들어간 케이크" → 메도빅).
            # 담지 않고 묻는다
            names = [m.name_ko for m in suggested]
            question = (templates.confirm_menu(names[0]) if len(names) == 1
                        else templates.choose_menu(names))
            self._open_menu_clarify(turn, act, suggested, question)
        else:
            self._fail(turn)
            self._say(turn, templates.not_on_menu(self._available_names()))
            turn.menu_listed = True
        return None

    def _searched_menus(self, act: Act) -> tuple[MenuItem, ...]:
        """매처가 거절하거나 확신하지 못한(confirm) 메뉴 말을 가게 지식 검색에 보인다.

        Part B 검색은 설명·별칭·오타·의미로 찾고, 근거가 없으면 빈 목록이다(없는 메뉴 = 없음).
        통화 메뉴에 있고 품절이 아닌 것만, 많아야 SEARCH_SUGGEST개. 검색이 실패하면 빈 튜플.
        """
        text = (act.menu_text or act.free_text or "").strip()
        if not text:
            return ()
        try:
            hits = self._store.search_menu(text, k=SEARCH_SUGGEST * 2)
        except Exception:  # noqa: BLE001  검색 고장으로 통화가 멈추면 안 된다
            log.exception("가게 지식 검색 실패")
            return ()
        by_id = {m.id: m for m in self._menu if not m.sold_out}
        found: list[MenuItem] = []
        for hit in hits:
            item = by_id.get(hit.item.id)
            if item is not None and item not in found:
                found.append(item)
        return tuple(found[:SEARCH_SUGGEST])

    def _add_to_cart(self, turn: _Turn, item: MenuItem, act: Act) -> None:
        """장바구니에 담는다. 수량 기본값 1, 기본값이 있는 필수 옵션은 미리 고르고,
        손님이 같이 말한 옵션(`act.option_text`)을 붙인다. 되받기는 `_finish`가 한다."""
        qty = 1 if act.qty is None else act.qty
        if qty <= 0:  # "메도빅 0개": 1개로 바꾸지 않는다 (리뷰 C17)
            self._on_unhandled(turn, act)
            return
        if not self._qty_ok(turn, qty):
            return
        if self._confirm_big_qty(turn, item, replace(act, qty=qty)):
            return
        options = self._default_choices(item)
        option_text = act.option_text or self._option_in_name(item, act.menu_text)
        if option_text:
            options = self._resolve_options(turn, item, option_text, options)
        line = CartLine(item, qty, options)
        added = self._cart.add(item, line.qty, options=options)
        if not self._order_ok(turn, added):  # 주문 전체 상한 (6단계 주문 가드)
            return
        self._cart = added
        self._touch(turn, line)

    def _option_in_name(self, item: MenuItem, menu_text: str) -> str:
        """메뉴명 쪽에 붙은 옵션 말("아이스 아메리카노", "아이스아메리카노"). 없으면 "".

        HCX가 "아이스"를 option_text가 아니라 menu_text에 넣는다(5단계 티켓 04, 실기기 통화).
        그 메뉴가 받는 옵션이 그 말에서 찾아질 때만 옵션 말로 쓴다.
        """
        if not menu_text:
            return ""
        candidates = [c for g in self._required_for(item) for c in g.choices] + list(item.options)
        return menu_text if find_options(candidates, menu_text) else ""

    def _qty_ok(self, turn: _Turn, qty: int) -> bool:
        """수량 상한(`MAX_QTY`). 넘으면 안내하고 담지 않는다."""
        if qty <= MAX_QTY:
            return True
        self._fail(turn)
        self._say(turn, templates.qty_too_large(MAX_QTY))
        turn.changed = True
        return False

    def _order_ok(self, turn: _Turn, cart: Cart) -> bool:
        """주문 전체 상한(`StoreConfig.max_order_qty`·`max_order_total`, 6단계 주문 가드).
        `cart`가 넘으면 안내하고 담지 않는다(한 줄 상한 `_qty_ok`와 같은 방식, 통화는 이어진다)."""
        over = cart.over_limit(self._config.max_order_qty, self._config.max_order_total)
        if over is None:
            return True
        self._fail(turn)
        self._say(turn, templates.order_too_many(self._config.max_order_qty) if over == "qty"
                  else templates.order_too_expensive(self._config.max_order_total))
        turn.changed = True
        return False

    # ------------------------------------------------------------ 큰 수량 확인 (6단계 품질)
    #
    # 한 줄 수량이 가게 기준(`StoreConfig.confirm_qty_from`) 이상이면 담지 않고 "○○ 12개
    # 맞으세요?"를 묻는다. 메뉴 되묻기(`_MenuClarify`, 후보 하나)와 같은 틀을 쓴다: 같은 발화의
    # 나머지 act는 미뤄 두고, "네"면 담고, 정정 중에 열었으면 답 뒤에 다시 복창한다.
    # - "네"·같은 수를 되풀이 → 그 수량으로 담는다(줄을 고치던 것이면 그 줄을 고친다)
    # - "아니요" → "그럼 ○○은 몇 개 드릴까요?"(`declined`). 실패로 세지 않는다
    # - 다른 수("두 개요") → 그 수량으로. 그 수도 크면 다시 확인한다
    # - "빼 주세요" → 담지 않고 닫는다. 다른 메뉴를 말하면 이 줄은 버리고 그 메뉴를 받는다

    def _confirm_big_qty(self, turn: _Turn, item: MenuItem, act: Act,
                         line: CartLine | None = None) -> bool:
        """수량이 크면 확인 질문을 열고 True(담지 않는다). `line`은 수량을 바꾸려던 줄."""
        limit = self._config.confirm_qty_from
        qty = act.qty or 0
        if limit <= 0 or qty < limit or turn.qty_confirmed == (item.id, qty):
            return False
        phrase = templates.line_phrase(item.name_ko, qty, templates.counter_for(item.category))
        before = self._pending
        self._open_menu_clarify(turn, act, (item,), templates.confirm_qty(phrase))
        pending = self._pending
        if isinstance(pending, _MenuClarify) and pending is not before:
            pending.big_qty = True
            pending.line = line
        return True  # 이번 턴에 다른 질문이 먼저 나갔으면 열지 못한다. 그래도 담지는 않는다

    def _big_qty_pending(self, turn: _Turn, act: Act) -> _MenuClarify | None:
        """앞 턴에 연 큰 수량 확인이고, `act`가 그 메뉴(또는 메뉴 이름 없는 말)를 가리키면 그것."""
        pending = self._pending
        if (self._state is not State.CLARIFY or not isinstance(pending, _MenuClarify)
                or not pending.big_qty or pending.opened_in is turn):
            return None
        if _names_menu(act) and not self._names_one_of(pending, act):
            return None
        return pending

    @staticmethod
    def _names_one_of(pending: _MenuClarify, act: Act) -> bool:
        picked = MenuMatcher(list(pending.candidates), fuzzy_accept_needs_guess=False).match(
            act.menu_text, act.menu_guess)
        return picked.decision == "accept"

    def _answer_qty(self, turn: _Turn, pending: _MenuClarify, qty: int) -> None:
        """큰 수량 확인에 수량으로 답했다. 물어본 수를 되풀이했으면 확인이다."""
        item = pending.candidates[0]
        if qty == pending.act.qty and not pending.declined:
            turn.qty_confirmed = (item.id, qty)
        self._close_menu_clarify(turn, pending, item, replace(pending.act, qty=qty))

    def _settle_big_qty(self, turn: _Turn, pending: _MenuClarify, act: Act) -> None:
        """확인이 끝난(또는 새로 받은) 수량을 장바구니에 넣는다. 고치던 줄이 있으면 그 줄을."""
        item = pending.candidates[0]
        index = None if pending.line is None else self._cart.index_of(pending.line)
        if index is None:
            self._add_to_cart(turn, item, act)
            return
        assert act.qty is not None
        line = self._cart.lines[index]
        if not self._qty_ok(turn, act.qty) or self._confirm_big_qty(turn, item, act, line):
            return
        new = replace(line, qty=act.qty)
        if self._order_ok(turn, self._cart.replace_at(index, new)):  # 6단계 주문 가드
            self._replace_line(turn, index, new)

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
        if (option := self._option_removal(act)) is not None:
            self._on_option_act(turn, option)  # "샤워 크림 빼 주세요"는 메뉴 빼기가 아니다
            return
        if (big := self._big_qty_pending(turn, act)) is not None:
            # 확인하던 큰 수량을 거뒀다. 담지 않고 닫는다(고치던 줄은 그대로 둔다)
            self._close_menu_clarify(turn, big, None)
            self._say(turn, self._ok(turn, templates.removed(big.candidates[0].name_ko)))
            turn.changed = True
            return
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
            if _names_menu(act) and item is not None and (act.qty or not self._cart.lines):
                # 11/16 실기기: "블린 하나 주세요"를 STT가 "블린 하나 죽었어"로 들었고 HCX가
                # 빼기로 읽었다. 없는 메뉴를 수량까지 붙여(또는 빈 장바구니에서) 빼 달라면 주문일
                # 가능성이 크다.
                # 담을지 묻는다(네 → 담기, 아니요 → 다시 묻기)
                add = replace(act, type="add_item", qty=act.qty or 1, free_text="")
                phrase = templates.line_phrase(item.name_ko, add.qty or 1,
                                               templates.counter_for(item.category))
                self._open_menu_clarify(turn, add, (item,), templates.offer_add(phrase),
                                        notice=templates.not_in_cart(name))
                turn.changed = True
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
            self._say(turn, self._ok(turn, templates.removed(name)))
        turn.changed = True

    def _option_removal(self, act: Act) -> Act | None:
        """빼 달라는 것이 메뉴가 아니라 어떤 메뉴의 빼기 옵션 대상어면 그 옵션 act. 아니면 None.

        HCX가 "블린 하나요 샤워 크림 빼주세요"를 remove_item "샤워 크림"으로 냈다(5단계 티켓 04).
        메뉴로는 못 찾고(`reject`), 가게 메뉴 중 하나의 옵션으로 찾아질 때만 바꾼다.
        """
        if not act.menu_text or self._matcher.match(act.menu_text, act.menu_guess).decision \
                != "reject":
            return None
        said = f"{act.menu_text} 빼"
        if not any(find_options(list(item.options), said) for item in self._menu):
            return None
        return Act("set_option", option_text=said)

    def _on_option_act(self, turn: _Turn, act: Act) -> None:
        if self._state is State.CLARIFY:
            self._on_option_answer(turn, act)
        else:
            self._on_set_option(turn, act)

    @on("change_qty", State.ORDERING, State.CLARIFY)
    def _on_change_qty(self, turn: _Turn, act: Act) -> None:
        if act.qty is None:
            self._on_unhandled(turn, act)
            return
        if act.qty <= 0:
            self._on_remove_item(turn, act)
            return
        if (big := self._big_qty_pending(turn, act)) is not None:
            self._answer_qty(turn, big, act.qty)  # "○○ 12개 맞으세요?" → "두 개요"
            return
        if not self._leave_option_clarify(turn, act):
            return
        if not self._qty_ok(turn, act.qty):
            return
        index, item = self._pick_line(act)
        if index is not None:
            line = self._cart.lines[index]
            if self._confirm_big_qty(turn, line.item, act, line):
                return
            new = replace(line, qty=act.qty)
            if self._order_ok(turn, self._cart.replace_at(index, new)):  # 6단계 주문 가드
                self._replace_line(turn, index, new)
        elif item is not None:  # 아직 안 담은 메뉴 → 그 수량으로 담는다
            self._add_to_cart(turn, item, act)
        elif _names_menu(act):
            self._on_add_item(turn, act)
        else:
            self._on_unhandled(turn, act)

    @on("set_option", State.ORDERING)
    def _on_set_option(self, turn: _Turn, act: Act) -> None:
        act = self._option_only(act)
        index, item = self._pick_line(act)
        if index is not None:
            self._apply_option(turn, index, act.option_text)
        elif item is not None:
            self._add_to_cart(turn, item, act)
        elif _names_menu(act):
            self._on_add_item(turn, act)
        elif (only := self._only_menu_with(act.option_text)) is not None:
            self._add_to_cart(turn, only, act)  # 그 옵션이 있는 메뉴가 하나뿐 (티켓 10)
        else:
            self._ask(turn, templates.which_item())

    def _only_menu_with(self, option_text: str) -> MenuItem | None:
        """이 옵션 말을 받는 메뉴가 가게에 하나뿐이면 그 메뉴. 아니면 None.

        담은 메뉴도 마지막에 다룬 메뉴도 없을 때 "홀케이크로 줘"를 메도빅으로 담는다(실기기 통화).
        """
        if not option_text:
            return None
        hits = [item for item in self._menu if not item.sold_out and find_options(
            [c for g in self._required_for(item) for c in g.choices] + list(item.options),
            option_text)]
        return hits[0] if len(hits) == 1 else None

    @on("set_option", State.CLARIFY)
    def _on_option_answer(self, turn: _Turn, act: Act) -> None:
        """필수 옵션 질문의 답. 고를 값이 아닌 옵션이면 그것만 붙이고 `_finish`가 다시 묻는다.

        그 메뉴에 없는 값이거나("미지근하게") 한 묶음 값을 둘 이상 말했으면 실패로 세고 같은
        질문을 다시 한다 (리뷰 C8, C15). 계속 안 맞으면 사장님께 넘긴다.
        """
        act = self._option_only(act)
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

    def _option_only(self, act: Act) -> Act:
        """옵션 act의 메뉴 말이 메뉴가 아니면("아이스로") 메뉴 말을 버린다 (5단계 티켓 04).

        HCX가 온도 질문에 대한 "아이스로 주세요"를 menu_text "아이스로"로 냈고, 엔진이 그것을
        메뉴로 찾다가 "그 메뉴는 없어요"라고 했다. 메뉴 말을 버리면 묻던 줄·마지막 줄에 붙는다.
        """
        if not act.option_text or not act.menu_text:
            return act
        if self._matcher.match(act.menu_text, act.menu_guess).decision != "reject":
            return act
        return replace(act, menu_text="", menu_guess="")

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

    def _unsaid(self, item: MenuItem) -> frozenset[str]:
        """손님에게 말하지 않을 옵션: 꼭 고를 것의 기본값("보통"). 주문서에는 남는다 (티켓 06)."""
        return frozenset(g.default for g in self._required_for(item) if g.default)

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
            self._touch(turn, line)  # 이미 그 옵션이다. 말없이 넘어가지 않게 되받는다 (티켓 04)
            turn.changed = True
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
                line = self._cart.lines[index]
                turn.accepted.append(line.phrase(self._unsaid(line.item)))

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
            turn.lost = True
            self._ask(turn, templates.pickup_not_understood())
        elif verdict.kind == "before_open":
            open_at = today_at(turn.now, self._config.open_time)
            self._ask(turn, templates.pickup_before_open(open_at))
        elif verdict.kind == "after_close":
            close = today_at(turn.now, self._config.close_time)
            self._ask(turn, templates.pickup_after_close(close),
                      again=templates.pickup_before_close(close))
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
        # 답을 못 받은 사장님 질문이 있으면 문자 약속을 붙이고 닫는다 (티켓 06)
        self._say(turn, self._ok(turn, templates.closing(
            self._pickup, owner_pending=self._owner_open is not None)))
        self._owner_open = None
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
            self._say(turn, self._noted(turn, turn.accepted))
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
        if item is not None and pending.big_qty:
            self._settle_big_qty(turn, pending, act or pending.act)
        elif item is not None:
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
        # "둘 중 어떤 거요?"·"몇 개 드릴까요?"에 "네": 실패로 세고 다시 묻는다
        if len(pending.candidates) != 1 or pending.declined:
            self._fail(turn)
            self._ask(turn, pending.question)
            return
        if pending.big_qty and pending.act.qty is not None:
            turn.qty_confirmed = (pending.candidates[0].id, pending.act.qty)
        self._close_menu_clarify(turn, pending, pending.candidates[0])

    @on("no", State.CLARIFY)
    def _on_clarify_no(self, turn: _Turn, act: Act) -> None:
        pending = self._menu_clarify(turn, act)
        if pending is None:
            return
        if pending.big_qty:
            if pending.declined:  # "몇 개 드릴까요?"에 또 "아니요"
                self._fail(turn)
            else:  # 수량이 틀렸다. 못 알아들은 것이 아니라 답이다
                item = pending.candidates[0]
                pending.declined = True
                pending.question = templates.ask_qty(
                    item.name_ko, templates.counter_for(item.category))
            self._ask(turn, pending.question)
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
        if pending.big_qty and (not _names_menu(act) or self._names_one_of(pending, act)):
            # 큰 수량 확인 중에 그 메뉴를 다시 말했다(NLU가 수량만 담기로 낼 때도 있다).
            # 수량이 있으면 답, 없으면 다시 묻는다
            if act.qty is None or act.qty <= 0:
                self._on_unhandled(turn, act)
            else:
                self._answer_qty(turn, pending, act.qty)
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

    def _build_order(self, status: OrderStatus, now: datetime) -> Order:
        """`now`는 이 턴의 가게 시각. 주문서를 만든 시각(`created_at`)으로 초까지 남긴다."""
        return Order(
            id=self._order_id,
            lines=self._cart.to_order_lines(),
            pickup_time=self._pickup.isoformat() if self._pickup else "",
            customer_phone=self._config.demo_phone,
            status=status,
            transcript=list(self._transcript),
            created_at=now.isoformat(timespec="seconds"),
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
    # 6단계 품질: 큰 수량 확인("○○ 12개 맞으세요?"). 후보 하나가 그 메뉴, `act.qty`가 물어본 수
    big_qty: bool = False
    line: CartLine | None = None  # 수량을 바꾸려던 줄. 없으면 새로 담는다
    declined: bool = False  # "아니요" 뒤 "몇 개 드릴까요?"를 묻는 중


def _store_gist(question: str) -> str:
    """가게 질문의 보충 지식 대상: 띄어쓰기·부호를 뺀 원문 (`menu_info._gist`와 같은 뜻)."""
    return re.sub(r"[\s?!.,~]", "", question)


def _route(turn: _Turn) -> Route:
    if turn.answer is not None:
        return "answer"
    return "nlu" if turn.nlu_called else "rule"


def _names_menu(act: Act) -> bool:
    return bool(act.menu_text or act.menu_guess)
