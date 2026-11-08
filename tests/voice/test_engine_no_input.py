"""엔진 무응답 진입점과 기다림 요청 (3단계 티켓 04). 대본 흐름은 시나리오 s22~s25에 있다.

여기에는 대본으로 쓰기 어려운 것만 둔다: 복창 중 "네, 잠시만요"가 확정이 되지 않는 것,
기다림 요청의 앞부분 가르기.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.rules import wait_request_rest
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)


def engine_at_confirm() -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="t")
    engine.start(NOW)
    for text in ("메도빅 하나요", "그게 다예요", "여섯시 반이요"):
        result = engine.handle(text, NOW)
    assert result.state is State.CONFIRM
    return engine


@pytest.mark.parametrize("text", ["네, 잠시만요", "네 잠깐만요", "잠깐만요", "아니요 잠시만요"])
def test_a_wait_request_during_the_readback_does_not_confirm(text: str) -> None:
    engine = engine_at_confirm()
    result = engine.handle(text, NOW, wait_request=True)
    assert result.order is None
    assert result.state is State.CONFIRM
    assert result.utterances == ["네, 천천히 말씀하세요."]
    again = engine.no_input(NOW)  # 기다린 뒤 되묻기는 지금 장바구니 복창
    assert again.utterances[-1].endswith("총 7,500원이에요. 맞으세요?")


def test_the_call_ends_on_silence_without_an_order_even_with_items_in_the_cart() -> None:
    engine = engine_at_confirm()
    results = [engine.no_input(NOW) for _ in range(3)]
    assert [r.state for r in results] == [State.CONFIRM, State.CONFIRM, State.DONE]
    assert all(r.order is None for r in results)
    assert engine.no_input(NOW).utterances == []  # 끝난 뒤에는 말하지 않는다
    assert engine.not_heard(NOW).utterances == []


@pytest.mark.parametrize(("text", "rest"), [
    ("잠깐만요", ""),
    ("잠시만요.", ""),
    ("잠깐만 기다려 주세요", ""),
    ("음, 잠깐만요", ""),
    ("네 잠시만요", ""),
    ("메도빅 하나랑, 잠시만요", "메도빅 하나랑"),
    ("아메리카노 한 잔이랑 잠깐만요", "아메리카노 한 잔이랑"),
    ("잠깐만요 메도빅 하나요", None),  # 끝에 있지 않으면 기다림 요청이 아니다
    ("메도빅 하나요", None),
])
def test_wait_request_rest(text: str, rest: str | None) -> None:
    assert wait_request_rest(text) == rest
