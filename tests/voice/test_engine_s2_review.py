"""2단계 통합 리뷰(phone-s2-voice-roundtrip/review.md)의 1단계 엔진·가짜 NLU 결함 회귀 테스트.

테스트 이름 앞의 번호는 리뷰 항목 번호다. STT 글에는 문장부호가 없으므로(spec Further Notes)
대본은 쉼표·물음표가 있는 것과 없는 것을 같이 본다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 30, tzinfo=KST)
MORE_Q = "더 필요하신 거 있으세요?"
MENU = ("메도빅", "나폴레옹", "블린", "아메리카노")


def make() -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="o-s2-review")
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


def lines(r: TurnResult) -> list[tuple[str, int, list[str], str]]:
    return [(ln.item.id, ln.qty, [o.name for o in ln.options], ln.request) for ln in r.cart.lines]


def parse(text: str, cart: str = "") -> list[tuple[str, str, str, str]]:
    ctx = NluContext(menu_names=MENU, cart_summary=cart, last_question="", state="ordering")
    return [(a.type, a.menu_text, a.option_text, a.free_text) for a in FakeNlu().parse(text, ctx)]


# ---------------------------------------------------------------- 5: "아니요, 지금 바로 갈게요"


@pytest.mark.parametrize("text", ["아니요, 지금 바로 갈게요", "아니요 지금 바로 갈게요"])
def test_5_no_with_a_time_after_anything_else_ends_ordering(text) -> None:
    engine = make()
    assert MORE_Q in talk(engine, "블린 하나요").utterances[-1]
    r = talk(engine, text)
    assert r.state is State.CONFIRM
    said = " ".join(r.utterances)
    assert "4시 45분" in said and MORE_Q not in said


def test_5_no_with_a_clock_time_after_anything_else_ends_ordering() -> None:
    engine = make()
    talk(engine, "블린 하나요")
    r = talk(engine, "아니요, 6시요")
    assert r.state is State.CONFIRM
    assert "6시 픽업" in " ".join(r.utterances)


def test_5_no_with_a_qty_after_anything_else_is_still_a_correction() -> None:
    engine = make()
    talk(engine, "블린 하나요")
    r = talk(engine, "아니요, 두 개요")
    assert r.state is State.ORDERING
    assert lines(r) == [("m04", 2, [], "")]


# ---------------------------------------------------------------- 6: B1 "사워크림은 빼 주세요"


@pytest.mark.parametrize("text", [
    "블린 하나요, 사워크림은 빼 주세요",  # 대본 B1 (쉼표 → 마지막 메뉴 뒤 꼬리)
    "블린 하나요 사워크림은 빼 주세요",  # STT 글 (문장부호 없음)
    "블린 사워크림은 빼고 하나요",
    "블린 하나 사워크림 없이요",
])
def test_6_sour_cream_removal_after_the_item_keeps_the_option(text) -> None:
    r = talk(make(), text)
    assert lines(r) == [("m04", 1, ["사워크림 빼기"], "")]


def test_6_a_request_after_the_last_item_goes_to_that_item() -> None:
    r = talk(make(), "메도빅 하나랑 블린 하나요, 포장해 주세요")
    assert [(m, q) for m, q, _, _ in lines(r)] == [("m01", 1), ("m04", 1)]
    assert lines(r)[-1][3] == "포장해 주세요"


def test_6_words_after_the_last_item_that_mean_nothing_add_nothing() -> None:
    assert [a[0] for a in parse("메도빅 하나요, 감사합니다")] == ["add_item"]

