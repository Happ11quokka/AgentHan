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
- 질문 카드(`owner.question` 새 카드 · `owner.question.update` 바뀐 카드, 티켓 05·06): 근거 없는
  메뉴 질문. 상태: 열림(open) · 답함(answered) · 시간 초과(timeout, 세션이 알림) · 통화 끝
  (call_ended, 열린 채 통화가 끝남). 시간 초과·통화 끝 카드도 답을 받는다(늦은 답 → 손님 문자,
  티켓 07). 자유 입력 답(free)은 사장님 글을 `answer_text`, 번역을 `answer_text_ko`에 둔다. 답은
  한 번만 받고 통화 세션의 받는 곳(`open_question`의 `reply`)으로 넘긴다. 카드 열람
  (`seen_at`)도 답 전이면 세션에 한 번 알린다. 마감 = 보낸 시각 + `question_wait_s`
- 통화 자막(`call.caption`, 티켓 10): 통화 중(live) 카드의 손님 말(`customer_said`)·AI 말
  (`bot_said`) 최근 4줄. data는 `{"call_id", "lines": [{"n", "who": customer|bot, "text",
  "text_owner"}]}`로 그 통화 자막 전체다. 손님 말은 원문으로 먼저 보내고 번역이 오면 같은 번호의
  줄을 덮어써 다시 보낸다(실패하면 원문만). AI 말은 번역하지 않는다(`text_owner` = "").
  CLOVA Speech `translation.targets`는 쓰지 않는다(응답 형식이 문서에 없다, spec "Papago 실측").
  통화가 끝나면(`call_ended`) 자막을 버린다. ordered·handoff가 된 통화도 snapshot에 자막을 안 넣는다
- 손님 문자 미리보기 (티켓 07, `sms.py` 문장): 실제 발송은 없다. 통화 id마다 보낸 순서로 쌓고
  주문 카드·통화 카드의 `sms`에 싣는다(문자가 생기면 그 카드를 다시 보낸다). 종류: 확정 주문
  (order), 늦은 버튼 답(late_answer, 시간 초과·통화 끝 카드에 온 답), 자유 입력 답(free_answer,
  제시간이든 늦든. 사장님 글을 한국어로 번역해 넣고, 안 되면 원문 + `translate_failed`).
  손님 통화 화면은 `call.ended`(세션이 `customer_texts`로 담음)와 `watch_texts` 스트림으로 받는다
- "손님이 궁금해한 것" 카드(`knowledge.update`, 티켓 13): AI가 답하지 못한 메뉴 질문. 보충 요청
  (`knowledge_wanted`, 손님은 "사장님께 전해 둘게요"를 들음)·질문 카드 모르겠어요·시간 초과가
  만든다. 같은 메뉴·종류·대상(`KnowledgeKey`)의 열린 카드 하나로 묶고 `count`를 늘린다(문구는
  마지막 질문). 사장님 글(ru) → `preview_knowledge`(ko 번역 미리보기, 저장 안 함) →
  `save_knowledge`(한국어 그대로 보충 지식 저장소에, 카드 open → filled). 질문 카드의
  돼요/안 돼요는 "들어가요?"류 질문(`OwnerQuestion.yes_no`)이면 저장소에 사장님 확인으로 넣는다.
  자유 입력·통화 끝 카드는 넣지 않는다. 저장소는 `self.knowledge`(통화 엔진이 읽는다)
- 확정 주문 카드의 `owner_check`: 그 통화에 답을 못 받은 질문(열림·시간 초과)이 있다. 손님은 문자
  약속을 들었다 ("사장님 확인 필요", 티켓 06)
- 구독자(`events`)는 먼저 `snapshot`(`{"orders": [...], "calls": [...], "questions": [...],
  "captions": [...], "knowledge": [...]}`, 통화 카드는 ordered·closed 빼고, 자막은 통화 중 카드
  것만)을 받고, 그 뒤 `order.update`·`call.update`·`call.caption`·`owner.question*`·
  `knowledge.update`를 받는다.
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
from agenthan.voice.dialog.knowledge import KnowledgeKey, KnowledgeRequest, KnowledgeStore
from agenthan.voice.dialog.owner_question import (
    OWNER_POSTS,
    OwnerNote,
    OwnerQuestion,
    OwnerReply,
    OwnerSeen,
)
from agenthan.voice.owner import sms
from agenthan.voice.owner.badges import request_badges
from agenthan.voice.owner.lexicon import lexicon_for
from agenthan.voice.owner.translate import Translator
from agenthan.voice.owner.translation import SOURCE_LANG, CardTexts

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerEvent:
    """사장님 화면으로 가는 이벤트 하나. HTTP 쪽이 SSE `event`·`data`·`id`로 옮긴다."""

    name: str  # snapshot · order.update · call.update · call.caption · owner.question(.update)
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
# 통화 자막 줄 수 (spec "Papago 실측": 최근 4줄, 티켓 10)
CAPTION_LINES = 4
# 사장님이 닫을 수 있는 통화 카드 상태
_CLOSABLE = frozenset({"handoff", "missed"})
# 사장님 답을 아직 못 받은 질문 카드 상태 (확정 주문에 "사장님 확인 필요")
_UNANSWERED = frozenset({"open", "timeout"})
# 이 상태에서 받은 답은 늦은 답이다. 통화가 말하지 않으니 손님 문자로 간다 (티켓 07)
_LATE = frozenset({"timeout", "call_ended"})


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
        knowledge: KnowledgeStore | None = None,
    ) -> None:
        self._store = store
        # 보충 지식 저장소(티켓 13). 허브가 쓰고, 통화 엔진이 읽는다(`phone_router(knowledge=...)`)
        self.knowledge = knowledge if knowledge is not None else KnowledgeStore()
        self._wanted: dict[str, dict[str, Any]] = {}  # 카드 id → "손님이 궁금해한 것" 카드
        self._asked: dict[str, OwnerQuestion] = {}  # 질문 id → 연 질문 (종류·대상을 본다)
        self._clock = clock
        self._question_wait = timedelta(seconds=question_wait_s)
        self._questions: dict[str, dict[str, Any]] = {}  # 질문 id → 질문 카드 (온 순서)
        self._replies: dict[str, Callable[[OwnerNote], None]] = {}  # 답을 아직 안 넘긴 질문
        self._lexicon = lexicon_for(owner_lang)
        self._translator = translator  # 사장님 자유 입력 글 → 한국어 (티켓 07)
        self._texts = CardTexts(translator, store=store, lexicon=self._lexicon)
        self._tasks: set[asyncio.Task[None]] = set()  # 진행 중인 번역 (GC 막기)
        self._cards: dict[str, dict[str, Any]] = {}  # 통화 id → 주문 카드 (넣은 순서 = 온 순서)
        self._calls: dict[str, dict[str, Any]] = {}  # 통화 id → 통화 카드 (넣은 순서 = 걸려 온 순)
        self._heard: dict[str, deque[str]] = {}  # 통화 중인 id → 마지막 손님 말들
        self._captions: dict[str, _Captions] = {}  # 통화 중인 id → 자막 (티켓 10)
        # 통화 id → 손님에게 보낸 문자 미리보기, 보낸 순서 (티켓 07)
        self._texts_out: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: set[asyncio.Queue[OwnerEvent]] = set()
        # 통화 id → 손님 통화 화면의 문자 스트림 구독자 (티켓 07)
        self._text_watchers: dict[str, set[asyncio.Queue[OwnerEvent]]] = {}
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
        """AI 말은 카드에 쓰지 않고 자막에만 둔다(번역 안 함, 티켓 10)."""
        self._on_loop(self._caption, call_id, "bot", text)

    def call_ended(self, call_id: str, reason: str) -> None:
        self._on_loop(self._end_call, call_id, reason)

    def open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerNote], None]
    ) -> None:
        self._on_loop(self._open_question, call_id, question, reply)

    def question_timed_out(self, call_id: str, number: int) -> None:
        self._on_loop(self._time_out, f"{call_id}-q{number}")

    def knowledge_wanted(self, call_id: str, request: KnowledgeRequest) -> None:
        self._on_loop(self._want, call_id, request.key, request.menu_name, request.text)

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

    def answer_question(self, question_id: str, answer: str, text: str = "") -> dict[str, Any]:
        """질문 카드 답: 버튼(yes·no·unsure) 또는 자유 입력(free + 사장님 글 `text`, 티켓 07).
        바뀐 카드를 돌려주고 답 종류를 통화 세션에 넘긴다(사장님 글은 통화에 넘기지 않는다).

        늦은 버튼 답(시간 초과·통화 끝 카드)은 고정 문장 손님 문자가 된다. 자유 입력은 언제든
        사장님 글을 한국어로 번역해 손님 문자가 된다(번역은 비동기, 안 되면 원문).
        없는 질문이면 `UnknownCard`, 이미 답했으면 `AlreadyAnswered`, 모르는 답이거나 자유 입력
        글이 비면 `ValueError`. 세션의 받는 곳이 실패해도(통화가 이미 끝남 등) 카드는 답함으로
        남는다.
        """
        card = self._questions.get(question_id)
        if card is None:
            raise UnknownCard(question_id)
        if answer not in OWNER_POSTS:
            raise ValueError(f"모르는 답: {answer!r}")
        written = text.strip() if answer == "free" else ""
        if answer == "free" and not written:
            raise ValueError("자유 입력 답에 글이 없어요")
        if card["status"] == "answered":
            raise AlreadyAnswered(question_id)
        # 시간 초과·통화 끝 카드의 늦은 답도 받는다. 세션은 번호로 버리고 손님에게는 문자로 간다
        late = card["status"] in _LATE
        card = self._put_question({**card, "status": "answered", "answer": answer,
                                   "answer_text": written})
        reply = self._replies.pop(question_id, None)
        if reply is not None:
            self._tell_call(reply, OwnerReply(card["number"], answer), question_id)
        if answer == "free":
            self._text_written_answer_soon(question_id)
        elif late:
            self._text_customer(card["call_id"], "late_answer",
                                sms.late_answer_text(card["menu_name"], answer),
                                question_id=question_id)
        self._learn(question_id, answer)  # 보충 지식 (티켓 13): 카드 이벤트는 문자 뒤에
        return card

    def see_question(self, question_id: str) -> dict[str, Any]:
        """질문 카드 열람(티켓 06). 처음 본 시각을 찍고, 답 전이면 통화 세션에 알린다.

        없는 질문이면 `UnknownCard`. 두 번째부터는 카드를 그대로 돌려준다(이벤트·알림 없음).
        """
        card = self._questions.get(question_id)
        if card is None:
            raise UnknownCard(question_id)
        if card["seen_at"]:
            return card
        card = self._put_question({**card, "seen_at": self._clock.now().isoformat()})
        reply = self._replies.get(question_id)
        if reply is not None and card["status"] == "open":
            self._tell_call(reply, OwnerSeen(card["number"]), question_id)
        return card

    def knowledge_cards(self) -> list[dict[str, Any]]:
        """"손님이 궁금해한 것" 카드 전체(만든 순서, 채운 카드 포함, 티켓 13)."""
        return list(self._wanted.values())

    async def preview_knowledge(self, card_id: str, text: str) -> dict[str, Any]:
        """사장님 답(사장님 언어) → 한국어 번역 미리보기(티켓 13). 저장하지 않는다.

        `{"text": 사장님 글, "text_ko": 번역, "translate_failed": bool}`. 번역기가 없거나 실패하면
        `text_ko`가 비고 `translate_failed`가 참이다(사장님이 한국어를 직접 적을 수 있다).
        없는 카드면 `UnknownCard`, 빈 글이면 `ValueError`.
        """
        if card_id not in self._wanted:
            raise UnknownCard(card_id)
        text = text.strip()
        if not text:
            raise ValueError("미리 볼 글이 없어요")
        translated = ""
        if self._translator is not None:
            try:
                [translated] = await self._translator.translate(
                    [text], self._lexicon.lang, SOURCE_LANG)
            except Exception as err:  # noqa: BLE001 — 번역이 안 되면 사장님이 한국어를 고친다
                log.warning("보충 답 번역 실패(%s): %s", type(err).__name__, card_id)
                translated = ""
        translated = translated.strip()
        return {"text": text, "text_ko": translated, "translate_failed": not translated}

    def save_knowledge(self, card_id: str, text_ko: str, text: str = "") -> dict[str, Any]:
        """사장님이 "저장"을 눌렀다(티켓 13, HITL). 한국어 글을 그대로 보충 지식에 넣고 카드를
        채움으로 바꾼다. 다시 저장하면 덮어쓴다. 없는 카드면 `UnknownCard`, 빈 글이면 `ValueError`.
        """
        card = self._wanted.get(card_id)
        if card is None:
            raise UnknownCard(card_id)
        text_ko = text_ko.strip()
        self.knowledge.save_note(_key(card), text_ko)  # 빈 글이면 ValueError
        return self._put_wanted({**card, "status": "filled", "answer_ko": text_ko,
                                 "answer_text": text.strip(),
                                 "saved_at": self._clock.now().isoformat()})

    def customer_texts(self, call_id: str) -> list[dict[str, Any]]:
        """그 통화의 손님에게 보낸 문자(보낸 순서). 없거나 모르는 통화면 빈 목록 (티켓 07)."""
        return list(self._texts_out.get(call_id, ()))

    def watch_texts(self, call_id: str) -> AsyncIterator[OwnerEvent]:
        """손님 통화 화면의 "받은 문자" 구독 (티켓 07). 처음에 `sms.snapshot`(`{"call_id", "sms":
        [...]}`, 지금까지의 문자), 그 뒤 새 문자마다 `sms`(문자 하나). 닫으면 구독을 푼다.

        허브가 모르는 통화(카드도 문자도 없음)면 바로 `UnknownCard`. 시간 제한은 HTTP 쪽이 둔다.
        """
        if call_id not in self._calls and call_id not in self._cards \
                and call_id not in self._texts_out:
            raise UnknownCard(call_id)
        return self._watch_texts(call_id)

    async def _watch_texts(self, call_id: str) -> AsyncIterator[OwnerEvent]:
        self._bind()
        queue: asyncio.Queue[OwnerEvent] = asyncio.Queue()
        watchers = self._text_watchers.setdefault(call_id, set())
        watchers.add(queue)
        try:
            yield OwnerEvent("sms.snapshot",
                             {"call_id": call_id, "sms": self.customer_texts(call_id)}, self._seq)
            while True:
                yield await queue.get()
        finally:
            watchers.discard(queue)
            if not watchers:
                self._text_watchers.pop(call_id, None)

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
        base = self._order_card(order)
        self._add_text(order.id, "order", sms.order_text(
            [(line["name_ko"], line["quantity"]) for line in base["lines"]],
            base["total"], base["pickup_label"]))
        card = {**self._dressed(base), "owner_check": any(
            q["call_id"] == order.id and q["status"] in _UNANSWERED
            for q in self._questions.values()), "sms": self.customer_texts(order.id)}
        self._cards[order.id] = card
        self._publish("order.update", card)
        self._translate_later(card)
        call = self._calls.get(order.id)
        if call is not None and call["status"] == "live":
            self._put_call({**call, "status": "ordered"})  # 화면은 통화 카드를 빼고 주문 카드만

    def _add_text(self, call_id: str, kind: str, text: str, *, question_id: str = "",
                  translate_failed: bool = False) -> dict[str, Any]:
        """손님 문자 미리보기 하나를 남긴다(실제 발송 없음)."""
        texts = self._texts_out.setdefault(call_id, [])
        now = self._clock.now()
        entry = {
            "id": f"{call_id}-s{len(texts) + 1}",
            "call_id": call_id,
            "kind": kind,  # order · late_answer · free_answer
            "text": text,  # 손님이 받는 한국어 문장
            "sent_at": now.isoformat(),
            "sent_label": now.strftime("%H:%M"),
            "question_id": question_id,  # 사장님 답 문자면 그 질문 id
            "translate_failed": translate_failed,  # 자유 입력 글을 번역하지 못해 원문을 넣었다
        }
        texts.append(entry)
        self._seq += 1
        for queue in self._text_watchers.get(call_id, ()):
            queue.put_nowait(OwnerEvent("sms", entry, self._seq))
        return entry

    def _text_customer(self, call_id: str, kind: str, text: str, **kw: Any) -> None:
        """문자를 남기고 그 통화의 사장님 카드(주문 카드, 화면에 있는 통화 카드)를 다시 보낸다."""
        self._add_text(call_id, kind, text, **kw)
        texts = self.customer_texts(call_id)
        order = self._cards.get(call_id)
        if order is not None:
            self._cards[call_id] = {**order, "sms": texts}
            self._publish("order.update", self._cards[call_id])
        call = self._calls.get(call_id)
        if call is not None:
            if call["status"] in ("ordered", "closed"):
                self._calls[call_id] = {**call, "sms": texts}  # 화면에 없는 카드: 보내지 않는다
            else:
                self._put_call({**call, "sms": texts})

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
            "sms": self.customer_texts(call_id),  # 손님에게 보낸 문자 (티켓 07)
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
        self._caption(call_id, "customer", text)

    # --- 통화 자막 (티켓 10) --------------------------------------------------------------

    def _caption(self, call_id: str, who: str, text: str) -> None:
        """자막 한 줄을 더하고 그 통화 자막 전체(`call.caption`)를 보낸다. 통화 중 카드만."""
        call = self._calls.get(call_id)
        if not text or call is None or call["status"] != "live":
            return
        captions = self._captions.setdefault(call_id, _Captions())
        captions.add(who, text)
        self._publish_captions(call_id)
        if who == "customer":  # 손님 말만 번역한다. 캐시라 넘김 카드 손님 말에도 그대로 쓰인다
            self._translate_soon(call_id, [text])

    def _publish_captions(self, call_id: str) -> None:
        """그 통화 자막 전체를 보낸다. 지난번 보낸 것과 같으면(번역이 안 바뀜) 보내지 않는다."""
        captions = self._captions.get(call_id)
        if captions is None:
            return
        event = {"call_id": call_id, "lines": [
            {"n": n, "who": who, "text": text,
             "text_owner": self._texts.known(call_id, text) if who == "customer" else ""}
            for n, who, text in captions.lines]}
        if event != captions.sent:
            captions.sent = event
            self._publish("call.caption", event)

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
        self._captions.pop(call_id, None)  # 자막은 통화 중에만 (화면도 통화 중 카드 아래에만 둔다)
        call = self._calls.get(call_id)
        if call is not None and call["status"] == "live":
            self._put_call({**call, "status": "missed", "reason": reason})
        for question in list(self._questions.values()):  # 열린 질문은 "통화 끝" (답은 계속 받는다)
            if question["call_id"] == call_id and question["status"] == "open":
                self._put_question({**question, "status": "call_ended"})

    def _time_out(self, question_id: str) -> None:
        question = self._questions.get(question_id)
        if question is not None and question["status"] == "open":
            self._put_question({**question, "status": "timeout"})
            self._learn(question_id, "timeout")

    # --- 보충 지식 (티켓 13): "손님이 궁금해한 것" 카드, 질문 카드 버튼 답 ------------------

    def _learn(self, question_id: str, answer: str) -> None:
        """질문 카드 답에서 배운다. 돼요/안 돼요는 "들어가요?"류 질문이면 사장님 확인으로 저장하고,
        모르겠어요·시간 초과는 보충 요청 카드로 보낸다. 자유 입력(그 손님 문자)은 넣지 않는다.
        """
        asked = self._asked.get(question_id)
        if asked is None:
            return
        if answer in ("yes", "no"):
            if asked.yes_no:
                self.knowledge.save_check(asked.key, answer == "yes")
        elif answer in ("unsure", "timeout"):
            self._want(self._questions[question_id]["call_id"], asked.key, asked.menu_name,
                       asked.text)

    def _want(self, call_id: str, key: KnowledgeKey, menu_name: str, text: str) -> None:
        """보충 요청 하나. 같은 열쇠의 열린 카드가 있으면 횟수를 늘리고 문구를 마지막 질문으로."""
        now = self._clock.now().isoformat()
        found = next((c for c in self._wanted.values()
                      if c["status"] == "open" and _key(c) == key), None)
        asked = {"call_id": call_id, "text": text, "text_owner": self._texts.known(call_id, text),
                 "last_asked_at": now}
        if found is not None:
            self._put_wanted({**found, **asked, "count": found["count"] + 1})
        else:
            card_id = f"k{len(self._wanted) + 1}"
            self._put_wanted({
                "id": card_id,
                "menu_id": key.menu_id,
                "menu_name": menu_name,  # 한국어
                "menu_name_owner": self._owner_name(key.menu_id, menu_name),
                "kind": key.kind,  # `menu_info.QuestionKind`
                "subject": key.subject,  # 알레르기 성분·이름 붙은 재료·그 밖 질문 요약
                "yes_no": key.yes_no,  # 들어가는지만 묻는 질문 (열쇠의 한 칸, 리뷰 C4)
                **asked,  # 마지막 질문: 통화 id, 원문(한국어), 번역(아직·실패면 "")
                "count": 1,
                "asked_at": now,
                "last_asked_at": now,
                "status": "open",  # open · filled(사장님이 저장함)
                "answer_text": "",  # 사장님 글 원문 (사장님 언어)
                "answer_ko": "",  # 저장한 한국어 (엔진이 인용 틀로 그대로 말한다)
                "saved_at": "",
            })
        self._translate_soon(call_id, [text])

    def _put_wanted(self, card: dict[str, Any]) -> dict[str, Any]:
        self._wanted[card["id"]] = card
        self._publish("knowledge.update", card)
        return card

    def _open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerNote], None]
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
            "status": "open",  # open · answered · timeout · call_ended (티켓 06)
            "answer": "",  # yes · no · unsure · free (자유 입력, 티켓 07)
            "seen_at": "",  # 사장님이 카드를 처음 본 시각 (허브 시계 ISO, 티켓 06)
            "answer_text": "",  # 자유 입력 답의 사장님 글 원문 (사장님 언어, 티켓 07)
            "answer_text_ko": "",  # 그 글의 한국어 번역 (손님 문자에 들어감). 아직·실패면 ""
        }
        self._questions[question_id] = card
        self._asked[question_id] = question
        self._replies[question_id] = reply
        self._publish("owner.question", card)
        self._translate_soon(call_id, [question.text])

    def _put_question(self, card: dict[str, Any]) -> dict[str, Any]:
        self._questions[card["id"]] = card
        self._publish("owner.question.update", card)
        return card

    def _tell_call(self, reply: Callable[[OwnerNote], None], note: OwnerNote, question_id: str
                   ) -> None:
        try:
            reply(note)
        except Exception:
            log.exception("사장님 쪽 알림을 통화에 넘기지 못함 (%s)", question_id)

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
        self._publish_captions(call_id)  # 자막 손님 줄도 같은 자리에서 번역으로 덮어쓴다
        for question_id, question in list(self._questions.items()):
            text_owner = self._texts.known(call_id, question["text"])
            if question["call_id"] == call_id and question["text_owner"] != text_owner:
                self._questions[question_id] = {**question, "text_owner": text_owner}
                self._publish("owner.question.update", self._questions[question_id])
        for card in list(self._wanted.values()):  # "손님이 궁금해한 것" 카드 (티켓 13)
            text_owner = self._texts.known(call_id, card["text"])
            if card["call_id"] == call_id and card["text_owner"] != text_owner:
                self._put_wanted({**card, "text_owner": text_owner})

    # --- 자유 입력 답 → 손님 문자 (티켓 07): 사장님 글(ru) → ko. 메뉴명 보호 없음, 다듬지 않음 ---

    def _text_written_answer_soon(self, question_id: str) -> None:
        if self._translator is None:
            self._text_written_answer(question_id, "")  # 키 없음: 원문 문자
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 루프 밖(동기 코드): 번역 없이 원문 문자
            self._text_written_answer(question_id, "")
            return
        task = loop.create_task(self._translate_written_answer(question_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _translate_written_answer(self, question_id: str) -> None:
        assert self._translator is not None
        original = self._questions[question_id]["answer_text"]
        try:
            [translated] = await self._translator.translate(
                [original], self._lexicon.lang, SOURCE_LANG)
        except Exception as err:  # noqa: BLE001 — 번역이 안 되면 원문으로 보낸다
            log.warning("자유 입력 답 번역 실패(%s): 원문을 문자로 (%s)",
                        type(err).__name__, question_id)
            translated = ""
        translated = translated.strip()
        if translated:
            card = self._questions[question_id]
            self._put_question({**card, "answer_text_ko": translated})
        self._text_written_answer(question_id, translated)

    def _text_written_answer(self, question_id: str, translated: str) -> None:
        card = self._questions[question_id]
        if translated:
            text = sms.free_answer_text(card["menu_name"], translated)
        else:
            text = sms.untranslated_answer_text(card["menu_name"], card["answer_text"])
        self._text_customer(card["call_id"], "free_answer", text, question_id=question_id,
                            translate_failed=not translated)

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
        captions = [c.sent for call_id, c in self._captions.items()
                    if c.sent is not None and self._calls[call_id]["status"] == "live"]
        return {"orders": list(self._cards.values()), "calls": calls,
                "questions": list(self._questions.values()), "captions": captions,
                "knowledge": self.knowledge_cards()}

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


class _Captions:
    """통화 하나의 자막: 최근 `CAPTION_LINES`줄 (번호, 누가, 한국어 원문). 번호는 통화 안 1부터."""

    def __init__(self) -> None:
        self.lines: deque[tuple[int, str, str]] = deque(maxlen=CAPTION_LINES)
        self.sent: dict[str, Any] | None = None  # 마지막으로 보낸 `call.caption` data
        self._count = 0

    def add(self, who: str, text: str) -> None:
        self._count += 1
        self.lines.append((self._count, who, text))


def _key(card: dict[str, Any]) -> KnowledgeKey:
    return KnowledgeKey(card["menu_id"], card["kind"], card["subject"], card["yes_no"])


def clock_label(iso: str) -> str:
    """ISO 8601 시각 → `18:30` (가게 시각 그대로, 24시간제). 비면 빈 글."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).strftime("%H:%M")


def won_label(amount: int) -> str:
    """원 금액 → `25,000₩` (언어와 상관없는 고정 표기)."""
    return f"{amount:,}₩"
