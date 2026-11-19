"""6단계: 2026-11-12 배포 서버에 녹음 대본 C를 흘렸을 때 나온 엔진 실패 두 가지.

act는 그 문장에 튜닝 HCX가 실제로 낸 것 그대로다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=KST)


class ScriptedNlu:
    def __init__(self, script: dict[str, Sequence[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if text in self._script:
            return list(self._script[text])
        return FakeNlu().parse(text, ctx)


def _engine(script: dict[str, Sequence[Act]]) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), ScriptedNlu(script),
                          StoreConfig.from_fixture(), order_id="o-s6")
    engine.start(NOW)
    return engine


CUT = "나폴 내용도 하나요"
CUT_ACTS = [Act("add_item", menu_text="나폴", menu_guess="나폴레옹", qty=1)]


def test_a_menu_word_the_nlu_cut_short_is_asked_about_not_refused() -> None:
    """STT "나폴 내용도"에서 HCX가 글은 "나폴", 추정은 "나폴레옹"으로 냈다. 없는 메뉴가 아니다."""
    engine = _engine({CUT: CUT_ACTS})
    asked = engine.handle(CUT, NOW)
    assert asked.utterances == ["혹시 나폴레옹 말씀하신 거 맞으세요?"]
    answered = engine.handle("네", NOW)
    assert [(line.item.name_ko, line.qty) for line in answered.cart.lines] == [("나폴레옹", 1)]


NOW_LEAVING = "아니요 지금 바로 갈게요"


def test_done_ordering_keeps_the_pickup_time_said_in_the_same_breath() -> None:
    """HCX가 "아니요 지금 바로 갈게요"를 done_ordering 하나로만 냈다. 시각을 다시 묻지 않는다."""
    engine = _engine({NOW_LEAVING: [Act("done_ordering")]})
    engine.handle("메도빅 하나요", NOW)
    r = engine.handle(NOW_LEAVING, NOW)
    assert r.state is State.CONFIRM
    assert "몇 시쯤 오세요?" not in r.utterances


def test_done_ordering_without_a_time_still_asks_for_one() -> None:
    said = "아니요 그거면 충분해요"
    engine = _engine({said: [Act("done_ordering")]})
    engine.handle("메도빅 하나요", NOW)
    r = engine.handle(said, NOW)
    assert r.state is State.ASK_PICKUP
    assert r.utterances[-1] == "몇 시쯤 오세요?"
