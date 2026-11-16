"""말하다 거둔 말("아 아니다")은 못 알아들은 말이 아니다 (5단계 티켓 11, 실기기 통화)."""

from __future__ import annotations

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.rules import retracted
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=KST)


@pytest.mark.parametrize(("text", "expected"), [
    ("한 여섯시 아 아니다", True),
    ("음아메리카노 한 잔 줘. 아 아니다", True),
    ("메도빅 두 개 아니다", True),
    ("블린 하나 아 아니야", True),
    ("아니다", False),
    ("아 아니다", False),
    ("네 아니다", False),
    ("이스아메리카 3개 아니다 두 개", False),  # 고쳐 말한 것. NLU가 본다
    ("그거 아니야", False),
    ("메도빅 두 개요", False),
])
def test_only_words_taken_back_after_some_content_are_a_retraction(
    text: str, expected: bool
) -> None:
    assert retracted(text) is expected


def _engine() -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="o-1")
    engine.start(NOW)
    return engine


def test_a_retraction_while_ordering_or_giving_the_time_just_waits() -> None:
    engine = _engine()
    result = engine.handle("음 아메리카노 한 잔 줘. 아 아니다", NOW)
    assert result.utterances == ["네, 천천히 말씀하세요."]
    assert result.cart.is_empty() and result.state is State.ORDERING
    engine.handle("메도빅 두 개요", NOW)
    engine.handle("아니요 그게 다예요", NOW)
    result = engine.handle("한 여섯시 아 아니다", NOW)
    assert result.utterances == ["네, 천천히 말씀하세요."]
    assert result.state is State.ASK_PICKUP
    assert engine.handle("여섯 시 반이요", NOW).state is State.CONFIRM


def test_a_retraction_never_counts_toward_handing_off() -> None:
    engine = _engine()
    for _ in range(4):
        assert engine.handle("메도빅 두 개 아 아니다", NOW).state is State.ORDERING


def test_during_the_readback_it_still_means_fix_it() -> None:
    engine = _engine()
    engine.handle("메도빅 두 개요", NOW)
    engine.handle("아니요 그게 다예요", NOW)
    engine.handle("여섯 시 반이요", NOW)
    result = engine.handle("두 개 아 아니다", NOW)
    assert result.utterances != ["네, 천천히 말씀하세요."]
