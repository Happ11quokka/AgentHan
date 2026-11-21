"""L2 대본 시나리오 러너 (티켓 06, spec §9).

시나리오 JSON(`tests/voice/scenarios/*.json`) 하나를 가짜 NLU로 처음부터 끝까지 돌린다.
턴마다 기대값(상태·장바구니·대사 조각)과 공통 금지 규칙을 보고, 끝나면 최종 `Order`를 비교한다.

시나리오 모양:

    {
      "id": "s01-single-item",              # 파일 이름과 같다
      "title": "단품 한 개",
      "now": "2026-11-08T17:10:00+09:00",
      "sold_out": ["m01"],                  # 선택. 이 메뉴를 품절로 바꿔 넣는다
      "xfail": "이유",                      # 선택. 알고 있는 한계 (strict xfail)
      "turns": [
        {"guest": "메도비크하나 주세요",               # 손님이 하는 말
         "expect_state": "ordering",                        # 선택
         "expect_cart": [{"menu_id": "m01", "qty": 1}],     # 선택. options·request 기본값은 빈 값
                                                            # 없으면 직전 턴과 같아야 한다
         "expect_say": ["메도빅 하나요"]},  # 선택. 이번 턴 대사에 들어 있어야 할 조각
        {"guest": "음", "expect_say_exact": ["죄송해요, 잘 못 알아들었어요.", "몇 시쯤 오세요?"]},
                                                # 선택. 이번 턴 대사 전체(문장 목록)가 이것과 같다
        {"event": "no_input"},                  # 손님 말 대신 엔진 진입점 (3단계 티켓 04):
                                                # no_input(무응답) · not_heard(말했는데 글 없음)
        {"guest": "잠깐만요", "wait_request": true}  # 끝말 규칙이 기다림 요청으로 본 말
      ],
      "expect_order": {"lines": [{"menu_id": "m01", "quantity": 1}],
                       "pickup_time": "2026-11-08T18:30:00+09:00", "status": "confirmed"}
                       # 주문서가 없어야 하면 null (취소)
    }

가게 승인 시나리오 (주문 승인 티켓 11). `"approval": true`면 사장님 화면이 켜진 가게다. 복창에
"네"면 엔진이 확인 요청을 열고, 러너가 세션 대신 그 요청·주문서·통화 끝을 진짜 사장님 허브
(`OwnerHub`, 가짜 시계)에 넘긴다. 사장님 결정은 허브의 공개 결정 함수(화면 버튼이 부르는 것)로
넣고, 허브가 돌려준 결정을 엔진에 넣는다(세션의 `owner_decided` 봇 턴). 시나리오에 더하는 칸:

      "approval": true,
      "turns": [..., {"guest": "네", "expect_request": 1},   # 이 턴에 연 확인 요청 번호
                {"owner": "accept"},                         # 사장님 [수락]
                {"owner": "accept", "delay_min": 15},        # [+15분]
                {"owner": "reject", "reason": "out_of_stock", "menu_ids": ["m01"]},
                {"owner": "timeout"}],                       # 통화 중 대기(20초)가 지남
      "after_call_s": 280,                     # 선택. 통화가 끝난 뒤 허브 시계를 이만큼 옮긴다
      "expect_owner": {"status": "expired",    # 마지막 결정 기록(없으면 "new")
                       "sms": ["expired"]}     # 선택: reject_reason · owner_pickup_time ·
                                               # sold_out_today(오늘 품절 메뉴 id들)

"owner"도 턴이라 기대값 칸(expect_state·expect_say …)을 그대로 쓴다. 이음매: 엔진 + 사장님 허브.
"timeout"은 세션의 통화 중 대기 타이머를 대신한다(허브 시계를 `APPROVAL_WAIT_S`만큼 옮기고 시간
초과 결정을 넣는다). 20초·10초 재안내의 실제 시간은 세션 시험(`test_call_approval*.py`)이 본다.

금지 규칙(매 턴): 확인 전 confirmed, 복창 없는 CONFIRM, 한 턴 두 질문, 복창 총액 ≠ `Cart.total()`,
150자 넘는 문장, 괄호·이모지·마크다운, 대답 없는 턴. 무응답 되묻기 앞의 "여보세요?"는 질문으로
세지 않는다(부르는 말이다).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agenthan.core.contracts import Order
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.defaults import APPROVAL_WAIT_S
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import OwnerDecision
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.rules import apply_rules
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.owner.decisions import DecisionRecord
from agenthan.voice.owner.hub import OwnerHub
from agenthan.voice.store_config import StoreConfig

SCENARIO_DIR = Path(__file__).parent / "scenarios"
MAX_SENTENCE = 150

_TOTAL = re.compile(r"모두 ([\d,]+)원")
# CONFIRM으로 끝나는 턴의 질문: 최종 복창, 또는 그 자리에서 받은 "주문 취소할까요?"
_CONFIRM_QUESTION = re.compile(r"모두 [\d,]+원이에요\..*맞으세요\?|주문 취소할까요\?")
# 가게 승인 시나리오에서 CONFIRM으로 끝나도 되는 말: 확인 대기("확인 중이에요"), 늦춘 시각 질문
_APPROVAL_CONFIRM = re.compile(r"확인 중이에요|준비 가능하시대요\. 괜찮으세요\?")
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_HELLO = "여보세요?"  # 무응답 되묻기의 부르는 말 (질문 수에 넣지 않는다)
# 괄호·마크다운 기호. TTS가 읽지 않거나 이상하게 읽는다 (spec §7)
_MARKUP = re.compile(r"[()\[\]{}<>（）【】*#_`~|]")
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff☀-➿⬀-⯿️‍]"
)


# ---------------------------------------------------------------- 금지 규칙


@dataclass(frozen=True)
class TurnView:
    """금지 규칙이 보는 한 턴. 직전 턴 결과와 손님 말이 있어야 '확인 전 확정'을 판단한다."""

    guest: str  # 손님이 한 말
    result: TurnResult
    before: TurnResult  # 이 턴 직전의 결과 (첫 턴이면 인사)
    approval: bool = False  # 가게 승인 시나리오: 복창 "네"는 확정이 아니라 확인 요청이다
    # 사장님이 받아들인 장바구니(`_cart_rows`): 수락·늦춘 수락·시간 초과면 요청한 주문, 재료
    # 소진이면 떨어진 메뉴를 뺀 나머지. 없으면 None. 승인 시나리오의 confirmed는 이것과 같아야 한다
    approved: list[dict[str, Any]] | None = None


def forbidden(view: TurnView) -> list[str]:
    """이 턴이 어긴 금지 규칙. 비어 있으면 통과."""
    problems: list[str] = []
    said = view.result.utterances
    text = " ".join(said)

    if not said:
        problems.append("대답 없는 턴")
    if text.replace(_HELLO, "").count("?") >= 2:
        problems.append(f"한 턴 두 질문: {text}")
    if view.result.state is State.CONFIRM and not (
            _CONFIRM_QUESTION.search(text) or view.approval and _APPROVAL_CONFIRM.search(text)):
        problems.append(f"복창 없는 CONFIRM: {text}")
    for amount in _TOTAL.findall(text):
        if int(amount.replace(",", "")) != view.result.cart.total():
            problems.append(f"복창 총액 {amount}원 ≠ 장바구니 {view.result.cart.total():,}원")
    for utterance in said:
        for sentence in _SENTENCE_END.split(utterance):
            if len(sentence) > MAX_SENTENCE:
                problems.append(f"{len(sentence)}자 문장: {sentence}")
        if _MARKUP.search(utterance) or _EMOJI.search(utterance):
            problems.append(f"괄호·이모지·마크다운: {utterance}")

    order = view.result.order
    confirmed = order is not None and order.status == "confirmed"
    if view.approval and confirmed and view.approved != _cart_rows(view.result):
        problems.append("승인 전 confirmed: 사장님이 받아들이지 않은 주문을 확정했다")
    # 손님 확인(복창 뒤 "네")이 있어야 하는 턴: 보통은 확정, 가게 승인이면 확인 요청을 여는 턴
    gate = view.result.approval_request is not None if view.approval else confirmed
    if gate:
        read_back = view.before.state is State.CONFIRM and "맞으세요?" in " ".join(
            view.before.utterances
        )
        clean_yes = apply_rules(view.guest) == [Act("yes")]
        what = "확인 요청" if view.approval else "confirmed"
        if not (read_back and clean_yes):
            problems.append(f"확인 전 {what}: 복창 뒤 '네'가 아닌데 주문을 넘겼다")
        elif view.result.cart != view.before.cart:
            problems.append(f"확인 전 {what}: 복창한 장바구니와 넘긴 장바구니가 다르다")
    return problems


# ---------------------------------------------------------------- 시나리오


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    now: datetime
    turns: list[dict[str, Any]]
    expect_order: dict[str, Any] | None
    sold_out: tuple[str, ...] = ()
    xfail: str = ""
    approval: bool = False  # 사장님 화면이 켜진 가게 (주문 승인 티켓 11, 모듈 설명)
    after_call_s: float = 0.0  # 통화가 끝난 뒤 허브 시계를 옮길 초 (자동 취소 마감)
    expect_owner: dict[str, Any] | None = None  # 통화(와 after_call_s) 뒤 사장님 쪽 기대값
    path: Path = field(default=Path(), compare=False)

    @classmethod
    def load(cls, path: Path) -> Scenario:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            id=raw["id"],
            title=raw.get("title", ""),
            now=datetime.fromisoformat(raw["now"]),
            turns=raw["turns"],
            expect_order=raw["expect_order"],
            sold_out=tuple(raw.get("sold_out", ())),
            xfail=raw.get("xfail", ""),
            approval=raw.get("approval", False),
            after_call_s=raw.get("after_call_s", 0.0),
            expect_owner=raw.get("expect_owner"),
            path=path,
        )


def load_all(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    return [Scenario.load(p) for p in sorted(directory.glob("*.json"))]


def _cart_rows(result: TurnResult) -> list[dict[str, Any]]:
    return [
        {
            "menu_id": line.item.id,
            "qty": line.qty,
            "options": [o.name for o in line.options],
            "request": line.request,
        }
        for line in result.cart.lines
    ]


def _expected_cart(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"options": [], "request": "", **row} for row in rows]


def _order_view(order: Order | None) -> dict[str, Any] | None:
    if order is None:
        return None
    return {
        "lines": [
            {
                "menu_id": line.menu_id,
                "quantity": line.quantity,
                "options": list(line.options),
                "request": line.request,
            }
            for line in order.lines
        ],
        "pickup_time": order.pickup_time,
        "status": order.status,
    }


def _expected_order(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    return {
        "lines": [{"options": [], "request": "", **line} for line in raw["lines"]],
        "pickup_time": raw.get("pickup_time", ""),
        "status": raw["status"],
    }


def _store(scenario: Scenario) -> FakeStoreKnowledge:
    store = FakeStoreKnowledge.from_fixture()
    sold = set(scenario.sold_out)
    return FakeStoreKnowledge(
        [replace(m, sold_out=True) if m.id in sold else m for m in store.list_menu()])


def make_engine(scenario: Scenario) -> DialogEngine:
    return DialogEngine(
        _store(scenario), FakeNlu(), StoreConfig.from_fixture(), order_id=scenario.id
    )


def _talk(transcript: list[str]) -> list[str]:
    return [line for line in transcript if not line.startswith("사장님: ")]


def _owner_label(turn: dict[str, Any]) -> str:
    extra = [str(turn[k]) for k in ("delay_min", "reason") if turn.get(k)]
    return " ".join([turn["owner"], *extra, *turn.get("menu_ids", ())])


class _Owner:
    """가게 승인 시나리오의 사장님 쪽: 진짜 허브(가짜 시계)와 세션이 하던 연결 (모듈 설명)."""

    def __init__(self, scenario: Scenario, clock: FakeClock) -> None:
        self.call_id = scenario.id  # = 주문 id
        self.clock = clock
        self.records: list[DecisionRecord] = []  # 허브가 내보낸 결정 기록 (DB로 가는 것)
        self.hub = OwnerHub(store=_store(scenario), clock=clock, decision_sink=self)
        self.inbox: list[OwnerDecision] = []  # 허브가 통화에 넘긴 결정
        self.open: int | None = None  # 열린 확인 요청 번호
        self.requested: list[dict[str, Any]] = []  # 그 요청의 장바구니
        self.approved: list[dict[str, Any]] | None = None
        self.hub.call_started(self.call_id)

    def record(self, decision: DecisionRecord) -> None:  # DecisionSink
        self.records.append(decision)

    def took(self, result: TurnResult) -> None:
        """엔진 턴 결과를 허브에 넘긴다 (세션 `_open_approval`·`_withdraw_approval`·주문서)."""
        if (gone := result.approval_withdrawn) is not None:
            self.hub.withdraw_approval(self.call_id, gone.number, gone.reason)
            self.open = None
        if (request := result.approval_request) is not None:
            self.hub.open_approval(self.call_id, request, self.inbox.append)
            self.open, self.requested, self.approved = request.number, _cart_rows(result), None
        if result.order is not None:
            self.hub.submit(result.order)

    def decide(self, turn: dict[str, Any]) -> OwnerDecision:
        """사장님 화면 버튼(허브 공개 함수) → 허브가 통화에 넘긴 결정. timeout은 세션 몫이다."""
        kind = turn["owner"]
        if kind == "timeout":
            assert self.open is not None, "열린 확인 요청이 없는데 시간 초과"
            self.clock.set(self.clock.now() + timedelta(seconds=APPROVAL_WAIT_S))
            decision = OwnerDecision(self.open, "timeout")
        else:
            if kind == "accept":
                self.hub.accept(self.call_id, turn.get("delay_min", 0))
            elif kind == "reject":
                self.hub.reject(self.call_id, turn["reason"], menu_ids=turn.get("menu_ids", ()),
                                text=turn.get("text", ""))
            else:
                raise ValueError(f"모르는 사장님 결정: {kind!r}")
            assert len(self.inbox) == 1, f"허브가 통화에 넘긴 결정 {self.inbox}"
            decision = self.inbox.pop()
            # 세션처럼 결정을 말하는 턴을 열었다고 알린다(허브가 문자·기록으로 굳힌다)
            self.hub.approval_said(self.call_id, decision.request)
        if decision.kind in ("accept", "delay", "timeout"):
            self.approved = self.requested
        elif decision.reason == "out_of_stock":  # 떨어진 메뉴만 빼면 재승인 없이 수락
            self.approved = [r for r in self.requested if r["menu_id"] not in decision.menu_ids]
        self.open = None
        return decision

    def ended(self, order: Order | None, after_s: float) -> dict[str, Any]:
        """통화 끝 → (허브 시계를 옮기고 자동 취소 검사) → 사장님 쪽에서 보이는 것."""
        self.hub.call_ended(self.call_id, order.status if order is not None else "cancelled")
        if after_s:
            self.clock.set(self.clock.now() + timedelta(seconds=after_s))
            self.hub.expire_due()
        last = self.records[-1] if self.records else None
        return {
            "status": last.owner_status if last else "new",
            "reject_reason": last.reject_reason if last else "",
            "owner_pickup_time": last.owner_pickup_time if last else "",
            "sms": [t["kind"] for t in self.hub.customer_texts(self.call_id)],
            "sold_out_today": sorted(self.hub.sold_out_today()),
        }


@dataclass
class Run:
    """한 시나리오를 돌린 결과. `failures`가 비어 있으면 통과."""

    transcript: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    order: Order | None = None


def run(scenario: Scenario) -> Run:
    engine = make_engine(scenario)
    clock = FakeClock(scenario.now)
    owner = _Owner(scenario, clock) if scenario.approval else None
    approval = owner is not None
    out = Run()
    before = engine.start(clock.now())
    out.transcript += [f"AI: {u}" for u in before.utterances]
    last = len(scenario.turns) - 1
    for i, turn in enumerate(scenario.turns):
        event = turn.get("event") or turn.get("owner")
        guest = turn.get("guest", "")
        if "owner" in turn:
            assert owner is not None, "사장님 결정 턴은 \"approval\": true 시나리오에만"
            decision = owner.decide(turn)
            engine.apply_sold_out(owner.hub.sold_out_today())  # 세션처럼 결정 직전에 덮어 쓴다
            result = engine.owner_decided(decision, clock.now())
            out.transcript.append(f"사장님: [{_owner_label(turn)}]")
        elif event == "no_input":
            result = engine.no_input(clock.now())
        elif event == "not_heard":
            result = engine.not_heard(clock.now())
        elif turn.get("wait_request"):
            result = engine.handle(guest, clock.now(), wait_request=True, approval=approval)
            out.transcript.append(f"손님: {guest}")
        else:
            result = engine.handle(guest, clock.now(), approval=approval)
            out.transcript.append(f"손님: {guest}")
        if owner is not None:
            owner.took(result)
        out.transcript += [f"AI: {u}" for u in result.utterances]
        where = f"턴 {i + 1} ({event or guest!r})"

        view = TurnView(guest, result, before, approval=approval,
                        approved=owner.approved if owner is not None else None)
        out.failures += [f"{where}: {p}" for p in forbidden(view)]
        if "expect_request" in turn:
            opened = result.approval_request.number if result.approval_request else None
            if opened != turn["expect_request"]:
                out.failures.append(f"{where}: 확인 요청 {opened} ≠ {turn['expect_request']}")
        if "expect_state" in turn and result.state.value != turn["expect_state"]:
            out.failures.append(f"{where}: 상태 {result.state.value} ≠ {turn['expect_state']}")
        # 장바구니는 턴마다 본다 (spec §9 L2). 기대값이 없으면 직전 턴에서 바뀌지 않아야 한다
        want_cart = (
            _expected_cart(turn["expect_cart"]) if "expect_cart" in turn else _cart_rows(before)
        )
        if _cart_rows(result) != want_cart:
            out.failures.append(f"{where}: 장바구니 {_cart_rows(result)} ≠ {want_cart}")
        said = " ".join(result.utterances)
        for piece in turn.get("expect_say", ()):
            if piece not in said:
                out.failures.append(f"{where}: 대사에 {piece!r} 없음 → {said!r}")
        if "expect_say_exact" in turn and result.utterances != turn["expect_say_exact"]:
            out.failures.append(f"{where}: 대사 {result.utterances} ≠ {turn['expect_say_exact']}")
        if result.order is not None:
            if i != last:
                out.failures.append(f"{where}: 마지막 턴 전에 주문서가 나왔다")
            out.order = result.order
        before = result

    got, want = _order_view(out.order), _expected_order(scenario.expect_order)
    if got != want:
        out.failures.append(f"최종 Order {got} ≠ {want}")
    # 사장님 결정 줄은 엔진("사장님: 수락")과 러너("사장님: [accept]")가 따로 적는다. 말만 견준다
    if out.order is not None and _talk(out.order.transcript) != _talk(out.transcript):
        out.failures.append("Order.transcript가 실제 대화와 다르다")
    if owner is not None:
        seen = owner.ended(out.order, scenario.after_call_s)
        for key, value in (scenario.expect_owner or {}).items():
            if seen[key] != value:
                out.failures.append(f"사장님 쪽 {key}: {seen[key]!r} ≠ {value!r}")
    return out
