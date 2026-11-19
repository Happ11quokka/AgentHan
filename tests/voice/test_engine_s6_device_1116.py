"""6단계: 2026-11-16 실기기 통화(아이폰 사파리)에서 나온 엔진 실패.

act는 그 문장에 튜닝 HCX가 실제로 낸 것 그대로다(재현 2/2).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=KST)

# "어 블린 하나 주세요"를 STT가 "죽었어"로 들었고, HCX는 빼기로 읽었다
MISHEARD = "어 블린 하나 죽었어"
MISHEARD_ACTS = [Act("remove_item", menu_text="블린")]
MISHEARD_QTY = "블린 하나 빼 주세요"
MISHEARD_QTY_ACTS = [Act("remove_item", menu_text="블린", qty=1)]


class ScriptedNlu:
    def __init__(self, script: dict[str, Sequence[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if text in self._script:
            return list(self._script[text])
        return FakeNlu().parse(text, ctx)


def _engine() -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(),
                          ScriptedNlu({MISHEARD: MISHEARD_ACTS, MISHEARD_QTY: MISHEARD_QTY_ACTS}),
                          StoreConfig.from_fixture(), order_id="o-1116")
    engine.start(NOW)
    return engine


def test_removing_a_menu_from_an_empty_cart_offers_to_add_it() -> None:
    engine = _engine()
    said = engine.handle(MISHEARD, NOW).utterances
    assert list(said[-2:]) == ["주문하신 것 중엔 블린이 없어요.", "블린 하나 담아 드릴까요?"]
    result = engine.handle("네", NOW)
    assert [(line.item.name_ko, line.qty) for line in result.cart.lines] == [("블린", 1)]


def test_saying_no_to_the_offer_leaves_the_cart_empty() -> None:
    engine = _engine()
    engine.handle(MISHEARD, NOW)
    result = engine.handle("아니요", NOW)
    assert result.cart.lines == ()


def test_a_quantity_on_a_missing_menu_also_offers_when_the_cart_has_other_things() -> None:
    engine = _engine()
    engine.handle("메도빅 하나요", NOW)
    said = engine.handle(MISHEARD_QTY, NOW).utterances
    assert "블린 하나 담아 드릴까요?" in said


def test_removing_a_missing_menu_without_a_quantity_from_a_full_cart_just_says_so() -> None:
    engine = _engine()
    engine.handle("메도빅 하나요", NOW)
    said = engine.handle(MISHEARD, NOW).utterances
    assert "주문하신 것 중엔 블린이 없어요." in said
    assert not any("담아 드릴까요" in s for s in said)
