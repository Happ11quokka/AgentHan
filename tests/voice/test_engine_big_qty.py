"""6단계 품질: 큰 수량 확인 (엔진).

한 줄 수량이 가게 기준(`StoreConfig.confirm_qty_from`, 기본 10) 이상이면 바로 담지 않고
"○○ 12개 맞으세요?"로 따로 확인한다. 장난 주문과 수량 오인식("열"↔"열두")을 막는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
MORE_Q = "더 필요하신 거 있으세요?"
ASK_12 = "메도빅 12개 맞으세요?"
SORRY = "죄송해요, 제가 잘 못 알아들었어요."


class ScriptedNlu:
    """정해 둔 말은 정해 둔 act로, 나머지는 가짜 NLU로."""

    def __init__(self, script: dict[str, list[Act]]) -> None:
        self._script = script
        self._fake = FakeNlu()

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        return self._script[text] if text in self._script else self._fake.parse(text, ctx)


def make_engine(nlu: object | None = None, **config: object) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), nlu or FakeNlu(),  # type: ignore[arg-type]
                          replace(StoreConfig.from_fixture(), **config), order_id="o-test")
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


def lines(r: TurnResult) -> list[tuple[str, int]]:
    return [(line.item.id, line.qty) for line in r.cart.lines]


def test_a_large_quantity_is_confirmed_before_it_goes_in_the_cart() -> None:
    engine = make_engine()
    asked = talk(engine, "메도빅 12개 주세요")
    assert asked.utterances == [ASK_12]
    assert asked.state is State.CLARIFY and lines(asked) == []
    added = talk(engine, "네")
    assert lines(added) == [("m01", 12)]
    assert added.utterances == [f"메도빅 12개요. {MORE_Q}"]
    assert added.state is State.ORDERING and added.route == "rule"


def test_quantities_below_the_threshold_go_straight_in() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 9개 주세요")
    assert lines(r) == [("m01", 9)] and r.state is State.ORDERING


def test_ten_is_already_large_by_default() -> None:
    engine = make_engine()
    r = talk(engine, "나폴레옹 10개 주세요")
    assert r.utterances == ["나폴레옹 열 개 맞으세요?"]
    assert lines(r) == []


def test_threshold_comes_from_the_store_config_and_zero_turns_it_off() -> None:
    engine = make_engine(confirm_qty_from=5)
    assert talk(engine, "메도빅 5개 주세요").utterances == ["메도빅 다섯 개 맞으세요?"]

    engine = make_engine(confirm_qty_from=0)
    assert lines(talk(engine, "메도빅 12개 주세요")) == [("m01", 12)]


def test_threshold_is_read_from_the_store_json_phone_block() -> None:
    config = StoreConfig.from_store_json(
        {"store": {"name": "가게"}, "phone": {"confirm_qty_from": 20, "max_reasks": 3}})
    assert (config.confirm_qty_from, config.max_reasks) == (20, 3)
    assert StoreConfig.from_store_json({"store": {"name": "가게"}}).confirm_qty_from == 10


def test_no_asks_how_many_and_the_answer_is_what_goes_in() -> None:
    engine = make_engine()
    declined = talk(engine, "메도빅 12개 주세요", "아니요")
    assert declined.utterances == ["그럼 메도빅은 몇 개 드릴까요?"]
    assert declined.state is State.CLARIFY and lines(declined) == []
    assert declined.reask == ""  # 못 알아들은 것이 아니다
    fixed = talk(engine, "두 개요")
    assert lines(fixed) == [("m01", 2)]
    assert fixed.utterances == [f"메도빅 두 개요. {MORE_Q}"]


def test_no_with_the_right_number_in_the_same_breath() -> None:
    engine = make_engine()
    fixed = talk(engine, "메도빅 12개 주세요", "아니요, 두 개요")
    assert lines(fixed) == [("m01", 2)]
    assert fixed.state is State.ORDERING


def test_yes_with_the_number_repeated_adds_it_once_without_asking_again() -> None:
    nlu = ScriptedNlu({"네 열두 개요": [Act("yes"), Act("change_qty", qty=12)]})
    engine = make_engine(nlu)
    r = talk(engine, "메도빅 12개 주세요", "네 열두 개요")
    assert lines(r) == [("m01", 12)]
    assert r.utterances == [f"메도빅 12개요. {MORE_Q}"]


def test_repeating_the_same_number_counts_as_confirming() -> None:
    nlu = ScriptedNlu({"열두 개요": [Act("change_qty", qty=12)]})
    engine = make_engine(nlu)
    r = talk(engine, "메도빅 12개 주세요", "열두 개요")
    assert lines(r) == [("m01", 12)] and r.state is State.ORDERING


def test_another_large_number_is_asked_again() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 12개 주세요", "아니요, 20개요")
    assert r.utterances == ["메도빅 20개 맞으세요?"]
    assert lines(r) == [] and r.state is State.CLARIFY
    assert lines(talk(engine, "네")) == [("m01", 20)]


def test_not_understanding_the_answer_asks_again_and_adds_nothing() -> None:
    engine = make_engine()
    again = talk(engine, "메도빅 12개 주세요", "음 그러니까")
    assert again.utterances == [SORRY, ASK_12]
    assert lines(again) == []
    second = talk(engine, "어 저기")
    assert second.utterances == [SORRY, "네 또는 아니요로 말씀해 주세요.", ASK_12]
    assert lines(talk(engine, "네")) == [("m01", 12)]


def test_taking_it_back_drops_the_item() -> None:
    engine = make_engine()
    r = talk(engine, "나폴레옹 하나요", "메도빅 12개 주세요", "메도빅 빼 주세요")
    assert lines(r) == [("m02", 1)]
    assert r.state is State.ORDERING
    assert r.utterances == ["메도빅은 빼 드렸어요.", MORE_Q]


def test_changing_a_line_to_a_large_quantity_is_confirmed_and_keeps_the_old_one_until_yes() -> None:
    engine = make_engine()
    asked = talk(engine, "메도빅 하나요", "메도빅 12개로 바꿔 주세요")
    assert asked.utterances == [ASK_12]
    assert lines(asked) == [("m01", 1)]
    changed = talk(engine, "네")
    assert lines(changed) == [("m01", 12)]


def test_items_said_with_the_large_one_are_kept() -> None:
    engine = make_engine()
    asked = talk(engine, "나폴레옹 하나랑 메도빅 12개 주세요")
    assert asked.utterances == ["나폴레옹 하나요.", ASK_12]
    assert lines(asked) == [("m02", 1)]
    assert lines(talk(engine, "네")) == [("m02", 1), ("m01", 12)]


def test_done_ordering_said_with_the_large_item_waits_for_the_answer() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 12개 주세요", "네")
    assert r.state is State.ORDERING
    r = talk(engine, "그게 다예요", "6시 반이요")
    assert r.readback and "메도빅 12개" in r.utterances[-1]
    assert "모두 90,000원이에요." in r.utterances[-1]


def test_correcting_to_a_large_quantity_at_the_readback_confirms_then_reads_back_again() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요")
    assert r.readback
    asked = talk(engine, "아니요, 메도빅 12개로 바꿔 주세요")
    assert asked.utterances == [ASK_12]
    assert not asked.readback and lines(asked) == [("m01", 1)]
    again = talk(engine, "네")
    assert again.readback and lines(again) == [("m01", 12)]
    assert again.utterances[0] == "메도빅 12개로 바꿔 드렸어요."
    done = talk(engine, "네")
    assert done.order is not None and done.order.status == "confirmed"
    assert [(line.menu_id, line.quantity) for line in done.order.lines] == [("m01", 12)]


def test_the_phone_limit_still_wins_over_the_confirmation() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 100개 주세요")
    assert r.utterances[0] == "죄송해요, 전화로는 한 번에 99개까지 받을 수 있어요."
    assert lines(r) == []


def test_snapshot_restores_an_open_quantity_question() -> None:
    engine = make_engine()
    talk(engine, "메도빅 12개 주세요")
    snap = engine.snapshot()
    assert talk(engine, "아니요").utterances == ["그럼 메도빅은 몇 개 드릴까요?"]
    engine.restore(snap)
    assert lines(talk(engine, "네")) == [("m01", 12)]
