"""엔진 + 메뉴 매처: 되묻기(CLARIFY)·둘 중 하나·없는 메뉴·품절 대안 (티켓 02)."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

import pytest

from agenthan.core.contracts import MenuItem, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

# 15시면 "6시 반"이 3시간 넘게 뒤라 픽업 확인 질문이 붙는다 (티켓 04)
NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
MENU = FakeStoreKnowledge.from_fixture().list_menu()
MENU_LIST = "저희는 메도빅, 나폴레옹, 아메리카노, 블린이 있어요."


def _engine(menu: list[MenuItem] | None = None, nlu: object | None = None) -> DialogEngine:
    engine = DialogEngine(
        FakeStoreKnowledge(menu if menu is not None else MENU),
        nlu or FakeNlu(),  # type: ignore[arg-type]
        StoreConfig.from_fixture(),
        order_id="o-test",
    )
    engine.start(NOW)
    return engine


def _cart(result) -> list[tuple[str, int]]:  # type: ignore[no-untyped-def]
    return [(line.item.id, line.qty) for line in result.cart.lines]


class ScriptedNlu:
    """발화마다 정해 둔 act를 돌려준다. 가짜 NLU가 아직 못 뽑는 조합을 시험할 때 쓴다."""

    def __init__(self, script: dict[str, Sequence[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if text in self._script:
            return list(self._script[text])
        return FakeNlu().parse(text, ctx)


# ---------------------------------------------------------------- 되묻기 (confirm)


def test_fuzzy_menu_is_confirmed_then_added_on_yes() -> None:
    engine = _engine()
    r = engine.handle("메도비 하나요", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]
    assert r.cart.is_empty()

    r = engine.handle("네", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m01", 1)]
    assert r.utterances == ["메도빅 하나요. 더 필요하신 거 있으세요?"]


def test_clarified_order_runs_to_confirmed_order() -> None:
    engine = _engine()
    for text in ["메도비 두 개요", "네", "그게 다예요", "6시 반이요"]:
        engine.handle(text, NOW)
    r = engine.handle("네", NOW)
    assert r.order is not None
    assert r.order.status == "confirmed"
    assert r.order.lines == [OrderLine(menu_id="m01", quantity=2, name="메도빅", unit_price=7500)]


def test_no_to_confirm_lists_menu_and_asks_again() -> None:
    engine = _engine()
    engine.handle("메도비 하나요", NOW)
    r = engine.handle("아니요", NOW)
    assert r.state is State.ORDERING
    assert r.cart.is_empty()
    assert r.utterances == [f"죄송해요. {MENU_LIST}", "어떤 걸로 드릴까요?"]


def test_other_menu_in_reply_replaces_the_guess() -> None:
    engine = _engine()
    engine.handle("메도비 하나요", NOW)
    r = engine.handle("나폴레옹 두 개요", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m02", 2)]


def test_reply_with_menu_name_keeps_original_qty() -> None:
    engine = _engine()
    engine.handle("메도비 두 개요", NOW)
    r = engine.handle("메도빅이요", NOW)
    assert _cart(r) == [("m01", 2)]
    assert r.utterances == ["메도빅 두 개요. 더 필요하신 거 있으세요?"]


def test_unintelligible_reply_reasks_the_clarify_question() -> None:
    engine = _engine()
    engine.handle("메도비 하나요", NOW)
    r = engine.handle("음", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["죄송해요, 잘 못 알아들었어요.", "혹시 메도빅 말씀하신 거 맞으세요?"]


def test_pickup_time_during_clarify_is_kept_and_question_repeated() -> None:
    engine = _engine()
    engine.handle("메도비 하나요", NOW)
    r = engine.handle("6시에 갈게요", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]

    r = engine.handle("네", NOW)
    assert _cart(r) == [("m01", 1)]
    r = engine.handle("그게 다예요", NOW)
    assert r.state is State.CONFIRM
    assert "6시 픽업" in r.utterances[0]


def test_pickup_time_deferred_by_clarify_goes_through_pickup_rules() -> None:
    """되묻는 동안 미뤄 둔 시각도 가게 규칙을 탄다: 3시간 넘게 뒤면 ASK_PICKUP에서 확인한다."""
    engine = _engine()
    engine.handle("메도비 하나요", NOW)
    engine.handle("8시에 갈게요", NOW)
    engine.handle("네", NOW)
    r = engine.handle("그게 다예요", NOW)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["오후 8시에 오시는 거죠?"]
    r = engine.handle("네", NOW)
    assert r.state is State.CONFIRM
    assert "8시 픽업" in r.utterances[0]


def test_exact_alias_is_not_asked_again() -> None:
    engine = _engine()
    r = engine.handle("메도비크 하나요", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m01", 1)]


def test_agreeing_menu_guess_skips_the_question() -> None:
    nlu = ScriptedNlu({"메도비 하나요": [Act("add_item", "메도비", "메도빅", qty=1)]})
    r = _engine(nlu=nlu).handle("메도비 하나요", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m01", 1)]


# ---------------------------------------------------------------- 둘 중 하나 (choose)

CAKES = [
    MenuItem("c1", "치즈 케이크", 7000, category="케이크"),
    MenuItem("c2", "초코 케이크", 7500, category="케이크"),
]


def test_close_candidates_ask_which_one() -> None:
    engine = _engine(CAKES)
    r = engine.handle("초즈 케이크 하나요", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["치즈 케이크랑 초코 케이크 중에 어떤 거요?"]

    r = engine.handle("초코 케이크요", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("c2", 1)]


def test_yes_to_choose_question_asks_again() -> None:
    engine = _engine(CAKES)
    engine.handle("초즈 케이크 하나요", NOW)
    r = engine.handle("네", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["치즈 케이크랑 초코 케이크 중에 어떤 거요?"]
    assert r.cart.is_empty()


# ---------------------------------------------------------------- 없는 메뉴 (reject)


@pytest.mark.parametrize("text", ["티라미수 하나요", "짜장면 두 개요"])
def test_unknown_menu_lists_menu_and_stays_ordering(text: str) -> None:
    r = _engine().handle(text, NOW)
    assert r.state is State.ORDERING
    assert r.cart.is_empty()
    assert r.utterances == [f"죄송해요, 그 메뉴는 없어요. {MENU_LIST}", "어떤 걸로 드릴까요?"]


def test_menu_list_skips_sold_out_items() -> None:
    menu = [replace(m, sold_out=m.id == "m03") for m in MENU]
    r = _engine(menu).handle("티라미수 하나요", NOW)
    assert r.utterances == [
        "죄송해요, 그 메뉴는 없어요. 저희는 메도빅, 나폴레옹, 블린이 있어요.",
        "어떤 걸로 드릴까요?",
    ]


# ---------------------------------------------------------------- 품절 (sold_out)

SOLD_OUT_M01 = [replace(m, sold_out=m.id == "m01") for m in MENU]


def test_sold_out_menu_offers_alternatives() -> None:
    engine = _engine(SOLD_OUT_M01)
    r = engine.handle("메도빅 하나요", NOW)
    assert r.state is State.CLARIFY
    assert r.cart.is_empty()
    assert r.utterances == [
        "죄송해요, 메도빅은 오늘 다 떨어졌어요.",
        "나폴레옹이나 아메리카노는 어떠세요?",
    ]

    r = engine.handle("나폴레옹 주세요", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m02", 1)]


def test_single_alternative_accepted_with_yes() -> None:
    menu = [replace(m, sold_out=m.id in ("m01", "m03")) for m in MENU]
    engine = _engine(menu)
    r = engine.handle("메도빅 두 개요", NOW)
    assert r.utterances[-1] == "나폴레옹이나 블린은 어떠세요?"

    only_one = [replace(m, sold_out=m.id != "m02") for m in MENU]
    engine = _engine(only_one)
    r = engine.handle("메도빅 두 개요", NOW)
    assert r.utterances[-1] == "나폴레옹은 어떠세요?"
    r = engine.handle("네", NOW)
    assert _cart(r) == [("m02", 2)]


def test_sold_out_after_accepted_item_reads_back_first() -> None:
    nlu = ScriptedNlu({"블린 두 개랑 메도빅": [BLIN_2, Act("add_item", "메도빅", "메도빅")]})
    r = _engine(SOLD_OUT_M01, nlu).handle("블린 두 개랑 메도빅", NOW)
    assert _cart(r) == [("m04", 2)]
    assert r.utterances == [
        "블린 두 개요.",
        "죄송해요, 메도빅은 오늘 다 떨어졌어요.",
        "나폴레옹이나 아메리카노는 어떠세요?",
    ]


def test_no_to_alternatives_asks_for_order() -> None:
    engine = _engine(SOLD_OUT_M01)
    engine.handle("메도빅 하나요", NOW)
    r = engine.handle("아니요", NOW)
    assert r.state is State.ORDERING
    assert r.utterances == [
        "죄송해요. 저희는 나폴레옹, 아메리카노, 블린이 있어요.",
        "어떤 걸로 드릴까요?",
    ]


def test_everything_sold_out_says_so_without_alternatives() -> None:
    menu = [replace(m, sold_out=True) for m in MENU]
    r = _engine(menu).handle("메도빅 하나요", NOW)
    assert r.state is State.ORDERING
    assert r.utterances == ["죄송해요, 메도빅은 오늘 다 떨어졌어요."]


# ---------------------------------------------------------------- 한 발화 여러 항목

MEDOBI_1 = Act("add_item", "메도비", qty=1)  # 메도빅의 변형, NLU 추정 없음
NAPPOLEON_1 = Act("add_item", "나뽈레옹", qty=1)
BLIN_2 = Act("add_item", "블린", "블린", qty=2)


def test_items_after_the_question_wait_until_it_is_answered() -> None:
    nlu = ScriptedNlu({"메도비 하나랑 블린 두 개요": [MEDOBI_1, BLIN_2]})
    engine = _engine(nlu=nlu)
    r = engine.handle("메도비 하나랑 블린 두 개요", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]
    assert r.cart.is_empty()

    r = engine.handle("네", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m01", 1), ("m04", 2)]
    assert r.utterances == ["메도빅 하나, 블린 두 개요. 더 필요하신 거 있으세요?"]


def test_items_before_the_question_are_read_back_first() -> None:
    nlu = ScriptedNlu({"블린 두 개랑 메도비 하나요": [BLIN_2, MEDOBI_1]})
    r = _engine(nlu=nlu).handle("블린 두 개랑 메도비 하나요", NOW)
    assert r.state is State.CLARIFY
    assert _cart(r) == [("m04", 2)]
    assert r.utterances == ["블린 두 개요.", "혹시 메도빅 말씀하신 거 맞으세요?"]


def test_items_kept_when_the_guess_is_declined() -> None:
    nlu = ScriptedNlu({"메도비 하나랑 블린 두 개요": [MEDOBI_1, BLIN_2]})
    engine = _engine(nlu=nlu)
    engine.handle("메도비 하나랑 블린 두 개요", NOW)
    r = engine.handle("아니요", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m04", 2)]
    assert r.utterances == [f"죄송해요. {MENU_LIST}", "블린 두 개요. 더 필요하신 거 있으세요?"]


def test_done_ordering_in_same_turn_waits_for_the_answer() -> None:
    nlu = ScriptedNlu({"메도비 하나면 돼요": [MEDOBI_1, Act("done_ordering")]})
    engine = _engine(nlu=nlu)
    r = engine.handle("메도비 하나면 돼요", NOW)
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]

    r = engine.handle("네", NOW)
    assert r.state is State.ASK_PICKUP
    assert _cart(r) == [("m01", 1)]
    assert r.utterances == ["메도빅 하나요.", "몇 시쯤 오세요?"]


def test_second_fuzzy_item_is_asked_after_the_first() -> None:
    nlu = ScriptedNlu({"메도비랑 나뽈레옹": [MEDOBI_1, NAPPOLEON_1]})
    engine = _engine(nlu=nlu)
    r = engine.handle("메도비랑 나뽈레옹", NOW)
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]

    r = engine.handle("네", NOW)
    assert r.state is State.CLARIFY
    assert _cart(r) == [("m01", 1)]
    assert r.utterances == ["메도빅 하나요.", "혹시 나폴레옹 말씀하신 거 맞으세요?"]

    r = engine.handle("네", NOW)
    assert r.state is State.ORDERING
    assert _cart(r) == [("m01", 1), ("m02", 1)]
    assert r.utterances == ["나폴레옹 하나요. 더 필요하신 거 있으세요?"]


# ---------------------------------------------------------------- 템플릿


@pytest.mark.parametrize(
    ("names", "text"),
    [
        (["메도빅", "나폴레옹"], "메도빅이랑 나폴레옹 중에 어떤 거요?"),
        (["아메리카노", "블린"], "아메리카노랑 블린 중에 어떤 거요?"),
        (["메도빅", "블린", "나폴레옹"], "메도빅, 블린, 나폴레옹 중에 어떤 거요?"),
    ],
)
def test_choose_template_particles(names: list[str], text: str) -> None:
    assert templates.choose_menu(names) == text


def test_sold_out_templates_particles() -> None:
    assert templates.sold_out("아메리카노") == "죄송해요, 아메리카노는 오늘 다 떨어졌어요."
    assert templates.suggest_menu(["블린", "아메리카노"]) == "블린이나 아메리카노는 어떠세요?"
    assert templates.suggest_menu(["아메리카노", "블린"]) == "아메리카노나 블린은 어떠세요?"


# ---------------------------------------------------------------- 되묻기 + 필수 옵션 (02·03·04)

TEMP_Q = "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"


def test_confirmed_menu_then_asks_required_option() -> None:
    engine = _engine()
    r = engine.handle("아메리카누 하나요", NOW)
    assert r.utterances == ["혹시 아메리카노 말씀하신 거 맞으세요?"]
    r = engine.handle("네", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["아메리카노 한 잔이요.", TEMP_Q]
    r = engine.handle("차갑게요", NOW)
    assert r.utterances == ["아메리카노 차갑게 한 잔이요. 더 필요하신 거 있으세요?"]
    r = engine.handle("그게 다예요", NOW)
    assert r.state is State.ASK_PICKUP


def test_deferred_done_and_pickup_wait_for_required_option() -> None:
    """되묻기에 미뤄 둔 "그게 다예요"·시각: 필수 옵션을 먼저 묻고, 답을 받으면 바로 복창."""
    americano = Act("add_item", "아메리카노", "아메리카노", qty=1)
    pickup = Act("set_pickup_time", time_text="6시 반")
    nlu = ScriptedNlu({"전부": [MEDOBI_1, americano, Act("done_ordering"), pickup]})
    engine = _engine(nlu=nlu)
    r = engine.handle("전부", NOW)
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]
    r = engine.handle("네", NOW)
    assert r.utterances == ["메도빅 하나, 아메리카노 한 잔이요.", TEMP_Q]
    r = engine.handle("차갑게요", NOW)
    assert r.state is State.CONFIRM
    assert r.utterances[-1].startswith("메도빅 하나, 아메리카노 차갑게 한 잔, 6시 반 픽업")
    r = engine.handle("네", NOW)
    assert r.order is not None
    assert r.order.lines == [
        OrderLine(menu_id="m01", quantity=1, name="메도빅", unit_price=7500),
        OrderLine(menu_id="m03", quantity=1, options=("차갑게",), name="아메리카노",
                  unit_price=4500),
    ]


def test_new_menu_during_option_question_is_added_and_option_asked_again() -> None:
    engine = _engine()
    engine.handle("아메리카노 하나요", NOW)
    r = engine.handle("블린 하나요", NOW)
    assert r.utterances == ["블린 하나요.", TEMP_Q]
    r = engine.handle("메도비 하나요", NOW)
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]
    r = engine.handle("네", NOW)
    assert r.utterances == ["메도빅 하나요.", TEMP_Q]
