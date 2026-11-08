"""티켓 03: 여러 항목·수량·변경·삭제·필수 옵션·선택 옵션·요청사항."""

from dataclasses import replace
from datetime import datetime

import pytest

from agenthan.core.contracts import MenuOption, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, RequiredOption, StoreConfig

NOW = datetime(2026, 11, 8, 15, 0, tzinfo=KST)
TEMP_Q = "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"
MORE_Q = "더 필요하신 거 있으세요?"


def make_engine(config: StoreConfig | None = None, nlu=None) -> DialogEngine:
    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(),
        nlu or FakeNlu(),
        config or StoreConfig.from_fixture(),
        order_id="o-test",
    )
    engine.start(NOW)
    return engine


@pytest.fixture
def engine() -> DialogEngine:
    return make_engine()


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


def lines(r: TurnResult) -> list[tuple[str, int, tuple[str, ...]]]:
    return [(line.item.id, line.qty, tuple(o.name for o in line.options)) for line in r.cart.lines]


def questions(r: TurnResult) -> int:
    return sum(u.count("?") for u in r.utterances)


# ---------------------------------------------------------------- 완료 기준


def test_add_two_then_remove_one_in_one_utterance(engine: DialogEngine) -> None:
    r = talk(engine, "아메리카노 둘이랑 블린 하나, 아 블린 빼 주세요")
    assert lines(r) == [("m03", 2, ())]
    assert r.utterances == ["아메리카노 두 잔요.", TEMP_Q]
    assert r.state is State.CLARIFY


def test_required_option_is_asked_then_applied(engine: DialogEngine) -> None:
    r = talk(engine, "아메리카노 하나요")
    assert r.state is State.CLARIFY
    assert r.utterances == ["아메리카노 한 잔요.", TEMP_Q]
    assert questions(r) == 1

    r = talk(engine, "차갑게요")
    assert r.state is State.ORDERING
    assert lines(r) == [("m03", 1, ("차갑게",))]
    assert r.utterances == [f"아메리카노 차갑게 한 잔요. {MORE_Q}"]
    assert questions(r) == 1


def test_decaf_adds_500_to_total(engine: DialogEngine) -> None:
    r = talk(engine, "아메리카노 하나요", "차갑게요", "디카페인으로요")
    assert lines(r) == [("m03", 1, ("차갑게", "디카페인 변경"))]
    assert r.cart.total() == 4500 + 500
    r = talk(engine, "그게 다예요", "6시 반이요")
    assert "총 5,000원" in r.utterances[-1]


# ---------------------------------------------------------------- 여러 항목·수량


def test_multi_item_readback_is_one_sentence(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 둘이랑 나폴레옹 하나요")
    assert r.utterances == [f"메도빅 두 개, 나폴레옹 하나요. {MORE_Q}"]
    assert lines(r) == [("m01", 2, ()), ("m02", 1, ())]


def test_multi_item_with_required_option_asks_once(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 둘이랑 아메리카노 하나요")
    assert r.utterances == ["메도빅 두 개, 아메리카노 한 잔요.", TEMP_Q]
    assert questions(r) == 1


def test_qty_defaults_to_one(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅요")
    assert lines(r) == [("m01", 1, ())]


def test_same_menu_again_merges_and_reads_back_total_qty(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나요", "메도빅 하나 더 주세요")
    assert lines(r) == [("m01", 2, ())]
    assert r.utterances == [f"메도빅 두 개요. {MORE_Q}"]


# ---------------------------------------------------------------- change_qty


def test_change_qty_by_menu(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나요", "나폴레옹 하나요", "메도빅 세 개로 해 주세요")
    assert lines(r) == [("m01", 3, ()), ("m02", 1, ())]
    assert r.utterances == [f"메도빅 세 개요. {MORE_Q}"]


def test_change_qty_without_menu_targets_last_item(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나요", "나폴레옹 하나요", "두 개로 바꿔 주세요")
    assert lines(r) == [("m01", 1, ()), ("m02", 2, ())]


def test_change_qty_of_menu_not_in_cart_adds_it(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나요", "나폴레옹 두 개로 해 주세요")
    assert lines(r) == [("m01", 1, ()), ("m02", 2, ())]


# ---------------------------------------------------------------- remove_item


def test_remove_item_from_earlier_turn(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나요", "블린 하나요", "블린 빼 주세요")
    assert lines(r) == [("m01", 1, ())]
    assert r.utterances == ["블린 뺐어요.", MORE_Q]


def test_remove_last_item_empties_cart(engine: DialogEngine) -> None:
    r = talk(engine, "블린 하나요", "그거 빼 주세요")
    assert r.cart.is_empty()
    assert r.utterances == ["블린 뺐어요.", "어떤 걸로 드릴까요?"]


def test_remove_item_not_in_cart(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나요", "나폴레옹 빼 주세요")
    assert lines(r) == [("m01", 1, ())]
    assert r.utterances == ["주문하신 것 중에 나폴레옹은 없어요.", MORE_Q]


def test_remove_while_option_pending_drops_the_question(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나랑 아메리카노 하나요", "아메리카노 빼 주세요")
    assert lines(r) == [("m01", 1, ())]
    assert r.state is State.ORDERING
    assert r.utterances == ["아메리카노 뺐어요.", MORE_Q]


# ---------------------------------------------------------------- 필수 옵션


def test_required_option_said_up_front_is_not_asked(engine: DialogEngine) -> None:
    r = talk(engine, "아이스 아메리카노 두 잔이요")
    assert r.state is State.ORDERING
    assert lines(r) == [("m03", 2, ("차갑게",))]
    assert r.utterances == [f"아메리카노 차갑게 두 잔요. {MORE_Q}"]


def test_non_choice_answer_is_applied_and_question_repeats(engine: DialogEngine) -> None:
    r = talk(engine, "아메리카노 하나요", "디카페인으로요")
    assert r.state is State.CLARIFY
    assert lines(r) == [("m03", 1, ("디카페인 변경",))]
    assert r.utterances == ["아메리카노 디카페인 변경 한 잔요.", TEMP_Q]


def test_unintelligible_answer_reasks_option(engine: DialogEngine) -> None:
    r = talk(engine, "아메리카노 하나요", "음")
    assert r.state is State.CLARIFY
    assert r.utterances == ["죄송해요, 잘 못 알아들었어요.", TEMP_Q]


def test_change_temperature_later_replaces_choice(engine: DialogEngine) -> None:
    r = talk(engine, "아메리카노 하나요", "차갑게요", "아메리카노는 따뜻하게 해 주세요")
    assert lines(r) == [("m03", 1, ("따뜻하게",))]


def test_different_temperature_makes_separate_lines(engine: DialogEngine) -> None:
    r = talk(engine, "아이스 아메리카노 하나랑 따뜻한 아메리카노 하나요")
    assert lines(r) == [("m03", 1, ("차갑게",)), ("m03", 1, ("따뜻하게",))]


def test_done_ordering_waits_for_required_option(engine: DialogEngine) -> None:
    r = talk(engine, "아메리카노 하나요, 그게 다예요")
    assert r.state is State.CLARIFY
    assert r.utterances == ["아메리카노 한 잔요.", TEMP_Q]
    r = talk(engine, "차갑게요")
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["아메리카노 차갑게 한 잔요.", "몇 시쯤 오세요?"]


class ScriptNlu:
    """발화 → 정해 둔 act. FakeNlu가 모르는 옵션 이름을 시험할 때 쓴다."""

    def __init__(self, script: dict[str, list[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        return self._script.get(text, [Act("unknown", free_text=text)])


def test_several_missing_required_options_are_asked_in_turn() -> None:
    base = StoreConfig.from_fixture()
    temp = base.required_options["m03"][0]
    size = RequiredOption("크기", (MenuOption("톨"), MenuOption("그란데", extra_price=500)))
    config = replace(base, required_options={"m03": (temp, size)})
    nlu = ScriptNlu(
        {
            "아메리카노 하나요": [Act("add_item", menu_text="아메리카노", qty=1)],
            "차갑게요": [Act("set_option", option_text="차갑게")],
            "그란데요": [Act("set_option", option_text="그란데")],
        }
    )
    engine = make_engine(config, nlu)

    r = talk(engine, "아메리카노 하나요")
    assert r.utterances[-1] == TEMP_Q
    r = talk(engine, "차갑게요")
    assert r.state is State.CLARIFY
    assert r.utterances == [
        "아메리카노 차갑게 한 잔요.",
        "아메리카노는 톨 드릴까요, 그란데 드릴까요?",
    ]
    r = talk(engine, "그란데요")
    assert r.state is State.ORDERING
    assert lines(r) == [("m03", 1, ("차갑게", "그란데"))]
    assert r.cart.total() == 5000


def test_required_option_with_default_is_not_asked() -> None:
    base = StoreConfig.from_fixture()
    temp = replace(base.required_options["m03"][0], default="따뜻하게")
    engine = make_engine(replace(base, required_options={"m03": (temp,)}))
    r = talk(engine, "아메리카노 하나요")
    assert r.state is State.ORDERING
    assert lines(r) == [("m03", 1, ("따뜻하게",))]


# ---------------------------------------------------------------- 선택 옵션


def test_optional_option_on_item_without_it_is_explained(engine: DialogEngine) -> None:
    r = talk(engine, "블린 디카페인으로 하나요")
    assert lines(r) == [("m04", 1, ())]
    assert r.utterances == [
        "죄송해요, 블린은 그 옵션이 없어요. 사워크림 빼기는 할 수 있어요.",
        f"블린 하나요. {MORE_Q}",
    ]


def test_option_on_item_with_no_options(engine: DialogEngine) -> None:
    r = talk(engine, "나폴레옹 하나요", "디카페인으로요")
    assert lines(r) == [("m02", 1, ())]
    assert r.utterances[0] == "죄송해요, 나폴레옹은 따로 고를 수 있는 옵션이 없어요."
    assert questions(r) == 1


def test_option_targets_last_item(engine: DialogEngine) -> None:
    r = talk(engine, "블린 하나요", "사워크림 빼 주세요")
    assert lines(r) == [("m04", 1, ("사워크림 빼기",))]
    assert r.cart.total() == 9000
    assert r.utterances == [f"블린 사워크림 빼기 하나요. {MORE_Q}"]


def test_option_with_empty_cart_asks_for_menu(engine: DialogEngine) -> None:
    r = talk(engine, "디카페인으로요")
    assert r.cart.is_empty()
    assert r.utterances == ["어떤 메뉴에 해 드릴까요?"]


def test_option_extra_price_in_total(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 홀케이크로 하나요")
    assert lines(r) == [("m01", 1, ("홀케이크 변경",))]
    assert r.cart.total() == 7500 + 38000


def test_sold_out_option_is_explained() -> None:
    store = FakeStoreKnowledge.from_fixture()
    menu = [
        replace(m, options=tuple(replace(o, available=False) for o in m.options))
        if m.id == "m04"
        else m
        for m in store.list_menu()
    ]
    engine = DialogEngine(
        FakeStoreKnowledge(menu), FakeNlu(), StoreConfig.from_fixture(), order_id="o"
    )
    engine.start(NOW)
    r = talk(engine, "블린 하나요", "사워크림 빼 주세요")
    assert lines(r) == [("m04", 1, ())]
    assert r.utterances[0] == "죄송해요, 오늘은 사워크림 빼기가 안 돼요."


# ---------------------------------------------------------------- set_request


def test_request_goes_to_order_line(engine: DialogEngine) -> None:
    r = talk(engine, "블린 하나요", "블린은 소스 따로 주세요")
    assert r.cart.lines[0].request == "소스 따로 주세요"
    assert r.utterances == ["블린에 소스 따로 주세요라고 적어 둘게요.", MORE_Q]
    r = talk(engine, "그게 다예요", "6시요", "네")
    assert r.order is not None
    assert r.order.lines == [OrderLine(menu_id="m04", quantity=1, request="소스 따로 주세요")]


def test_request_without_menu_targets_last_item(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나요", "포크 두 개 넣어 주세요")
    assert r.cart.lines[0].request == "포크 두 개 넣어 주세요"


# ---------------------------------------------------------------- 끝까지


def test_full_order_with_options_and_edits(engine: DialogEngine) -> None:
    turns = [
        "메도빅 둘이랑 아메리카노 하나요",
        "차갑게요",
        "디카페인으로 해 주세요",
        "아메리카노 두 잔으로 해 주세요",
        "메도빅 하나로 바꿔 주세요",
        "그게 다예요",
        "6시 반이요",
    ]
    results = [engine.handle(t, NOW) for t in turns]
    assert all(questions(r) == 1 for r in results)
    last = results[-1]
    assert last.state is State.CONFIRM
    assert last.utterances == [
        "메도빅 하나, 아메리카노 차갑게 디카페인 변경 두 잔, 6시 반 픽업, "
        "총 17,500원이에요. 맞으세요?"
    ]
    order = engine.handle("네", NOW).order
    assert order is not None
    assert order.lines == [
        OrderLine(menu_id="m01", quantity=1),
        OrderLine(menu_id="m03", quantity=2, options=("차갑게", "디카페인 변경")),
    ]
