"""티켓 06: 대본 시나리오를 돌리다 찾은 엔진 버그의 회귀 테스트."""

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)
TEMP_Q = "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"
MORE_Q = "더 필요하신 거 있으세요?"


@pytest.fixture
def engine() -> DialogEngine:
    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(), order_id="o-06"
    )
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


# ---------------------------------------------------------------- 되받기 조사: "잔이요"


@pytest.mark.parametrize(
    ("phrases", "text"),
    [
        (["메도빅 하나"], "메도빅 하나요."),
        (["블린 두 개"], "블린 두 개요."),
        (["아메리카노 한 잔"], "아메리카노 한 잔이요."),
        (["메도빅 하나", "아메리카노 차갑게 두 잔"], "메도빅 하나, 아메리카노 차갑게 두 잔이요."),
    ],
)
def test_items_noted_uses_iyo_after_batchim(phrases: list[str], text: str) -> None:
    assert templates.items_noted(phrases) == text


# ---------------------------------------- "더 필요하신 거 있으세요?"에 네/아니요


def test_plain_no_to_anything_else_finishes_ordering(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요")
    r = talk(engine, "아니요")
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["몇 시쯤 오세요?"]


def test_plain_no_with_pickup_already_known_goes_to_readback(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "6시 반에 갈게요")
    r = talk(engine, "아니요")
    assert r.state is State.CONFIRM
    assert r.utterances == ["메도빅 하나, 6시 반 픽업, 총 7,500원이에요. 맞으세요?"]


def test_plain_yes_to_anything_else_asks_what_to_add(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요")
    r = talk(engine, "네")
    assert r.state is State.ORDERING
    assert r.utterances == ["어떤 걸로 드릴까요?"]
    r = talk(engine, "블린 하나요")
    assert lines(r) == [("m01", 1), ("m04", 1)]


def test_plain_no_with_empty_cart_is_still_not_understood(engine: DialogEngine) -> None:
    r = talk(engine, "아니요")
    assert r.utterances == ["죄송해요, 잘 못 알아들었어요.", "어떤 걸로 드릴까요?"]


def test_plain_no_after_what_to_fix_is_not_done_ordering(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요", "아니요")
    r = talk(engine, "아니요")
    assert r.state is State.ORDERING
    assert r.utterances == ["죄송해요, 잘 못 알아들었어요.", "어떤 걸 고쳐 드릴까요?"]


# ---------------------------------------------------------------- 없는 메뉴 안내 뒤 질문


def test_unknown_menu_after_items_asks_anything_else(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요")
    r = talk(engine, "짜장면 하나요")
    assert r.utterances[-1] == MORE_Q
    assert r.utterances[0].startswith("죄송해요, 그 메뉴는 없어요.")


def test_unknown_menu_while_fixing_asks_what_to_fix_again(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요", "아니요")
    r = talk(engine, "짜장면 하나요")
    assert r.state is State.ORDERING
    assert r.utterances[-1] == "어떤 걸 고쳐 드릴까요?"


# ---------------------------------------- 필수 옵션 질문 중 "그게 다예요"·시각


def test_done_ordering_during_option_question_is_kept(engine: DialogEngine) -> None:
    talk(engine, "아메리카노 하나 주세요")
    r = talk(engine, "그게 다예요")
    assert r.state is State.CLARIFY
    assert r.utterances == [TEMP_Q]  # "잘 못 알아들었어요"가 아니다
    r = talk(engine, "따뜻하게요")
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["아메리카노 따뜻하게 한 잔이요.", "몇 시쯤 오세요?"]


def test_pickup_time_during_option_question_is_kept(engine: DialogEngine) -> None:
    talk(engine, "아메리카노 하나 주세요")
    r = talk(engine, "6시 반에 갈게요")
    assert r.state is State.CLARIFY
    assert r.utterances == [TEMP_Q]
    r = talk(engine, "차갑게", "그게 다예요")
    assert r.state is State.CONFIRM
    assert r.utterances == ["아메리카노 차갑게 한 잔, 6시 반 픽업, 총 4,500원이에요. 맞으세요?"]


def test_option_question_hands_off_when_not_counted_as_failure(engine: DialogEngine) -> None:
    """"그게 다예요"는 실패가 아니다. 그 뒤 못 알아들은 말 3번이면 그때 넘긴다."""
    talk(engine, "아메리카노 하나 주세요", "그게 다예요", "음", "어")
    r = talk(engine, "음")
    assert r.state is State.FALLBACK


# ---------------------------------------- 복창 뒤 정정이 메뉴 되묻기로 갈 때


def test_correction_through_menu_clarify_reads_back_again(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요")
    r = talk(engine, "나폴래옹 하나요")
    assert r.state is State.CLARIFY
    assert r.utterances == ["혹시 나폴레옹 말씀하신 거 맞으세요?"]
    r = talk(engine, "네")
    assert r.state is State.CONFIRM
    assert r.utterances == [
        "나폴레옹 하나요.",
        "그럼 메도빅 하나, 나폴레옹 하나, 6시 반 픽업, 총 14,500원이에요. 맞으세요?",
    ]


def test_correction_menu_clarify_declined_reads_back_again(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요", "나폴래옹 하나요")
    r = talk(engine, "아니요")
    assert r.state is State.CONFIRM
    assert lines(r) == [("m01", 1)]
    assert r.utterances[-1] == "그럼 메도빅 하나, 6시 반 픽업, 총 7,500원이에요. 맞으세요?"
