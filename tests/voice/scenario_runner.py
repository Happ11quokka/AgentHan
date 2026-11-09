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

금지 규칙(매 턴): 확인 전 confirmed, 복창 없는 CONFIRM, 한 턴 두 질문, 복창 총액 ≠ `Cart.total()`,
150자 넘는 문장, 괄호·이모지·마크다운, 대답 없는 턴. 무응답 되묻기 앞의 "여보세요?"는 질문으로
세지 않는다(부르는 말이다).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agenthan.core.contracts import Order
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.rules import apply_rules
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import StoreConfig

SCENARIO_DIR = Path(__file__).parent / "scenarios"
MAX_SENTENCE = 150

_TOTAL = re.compile(r"총 ([\d,]+)원")
# CONFIRM으로 끝나는 턴의 질문: 최종 복창, 또는 그 자리에서 받은 "주문 취소할까요?"
_CONFIRM_QUESTION = re.compile(r"총 [\d,]+원이에요\..*맞으세요\?|주문 취소할까요\?")
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


def forbidden(view: TurnView) -> list[str]:
    """이 턴이 어긴 금지 규칙. 비어 있으면 통과."""
    problems: list[str] = []
    said = view.result.utterances
    text = " ".join(said)

    if not said:
        problems.append("대답 없는 턴")
    if text.replace(_HELLO, "").count("?") >= 2:
        problems.append(f"한 턴 두 질문: {text}")
    if view.result.state is State.CONFIRM and not _CONFIRM_QUESTION.search(text):
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
    if order is not None and order.status == "confirmed":
        read_back = view.before.state is State.CONFIRM and "맞으세요?" in " ".join(
            view.before.utterances
        )
        clean_yes = apply_rules(view.guest) == [Act("yes")]
        if not (read_back and clean_yes):
            problems.append("확인 전 confirmed: 복창 뒤 '네'가 아닌데 주문을 확정했다")
        elif view.result.cart != view.before.cart:
            problems.append("확인 전 confirmed: 복창한 장바구니와 확정한 장바구니가 다르다")
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


def make_engine(scenario: Scenario) -> DialogEngine:
    store = FakeStoreKnowledge.from_fixture()
    sold = set(scenario.sold_out)
    menu = [replace(m, sold_out=True) if m.id in sold else m for m in store.list_menu()]
    return DialogEngine(
        FakeStoreKnowledge(menu), FakeNlu(), StoreConfig.from_fixture(), order_id=scenario.id
    )


@dataclass
class Run:
    """한 시나리오를 돌린 결과. `failures`가 비어 있으면 통과."""

    transcript: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    order: Order | None = None


def run(scenario: Scenario) -> Run:
    engine = make_engine(scenario)
    out = Run()
    before = engine.start(scenario.now)
    out.transcript += [f"AI: {u}" for u in before.utterances]
    last = len(scenario.turns) - 1
    for i, turn in enumerate(scenario.turns):
        event = turn.get("event")
        guest = turn.get("guest", "")
        if event == "no_input":
            result = engine.no_input(scenario.now)
        elif event == "not_heard":
            result = engine.not_heard(scenario.now)
        elif turn.get("wait_request"):
            result = engine.handle(guest, scenario.now, wait_request=True)
            out.transcript.append(f"손님: {guest}")
        else:
            result = engine.handle(guest, scenario.now)
            out.transcript.append(f"손님: {guest}")
        out.transcript += [f"AI: {u}" for u in result.utterances]
        where = f"턴 {i + 1} ({event or guest!r})"

        out.failures += [f"{where}: {p}" for p in forbidden(TurnView(guest, result, before))]
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
    if out.order is not None and out.order.transcript != out.transcript:
        out.failures.append("Order.transcript가 실제 대화와 다르다")
    return out
