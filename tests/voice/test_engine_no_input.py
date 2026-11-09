"""엔진 무응답 진입점과 기다림 요청 (3단계 티켓 04). 대본 흐름은 시나리오 s22~s25에 있다.

여기에는 대본으로 쓰기 어려운 것만 둔다: 복창 중 "네, 잠시만요"가 확정이 되지 않는 것,
기다림 요청의 앞부분 가르기.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.turn_end import classify_turn_end
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


# 필러(끝말 규칙의 "필러만 있는 말")와 기다림 요청 앞 머리말은 같은 목록이다 (리뷰 S9)
_FILLER_WORDS = ["음", "으음", "음음", "어", "어어", "으", "아", "그", "저", "저기", "뭐더라",
                 "그러니까"]


@pytest.mark.parametrize("filler", _FILLER_WORDS)
def test_a_filler_is_both_filler_only_and_no_content_before_a_wait_request(filler: str) -> None:
    verdict = classify_turn_end(filler, state="ordering", question_kind="open", menu_names=())
    assert (verdict.label, verdict.filler_only) == ("continue", True)
    assert wait_request_rest(f"{filler} 잠깐만요") == ""
    assert wait_request_rest(f"네 {filler} 잠시만요") == ""  # 예 말 + 필러도 내용이 아니다


def test_um_um_wait_is_a_plain_wait_request_in_the_rule_and_the_engine() -> None:
    # 리뷰 S9: 끝말 규칙은 "음음 잠깐만요"를 기다림 요청으로 보는데 엔진은 "음음"을 내용으로
    # NLU에 넘겼다. 둘 다 내용 없는 기다림 요청으로 본다
    verdict = classify_turn_end("음음 잠깐만요", state="ordering", question_kind="open",
                                menu_names=())
    assert verdict.label == "wait_request"
    engine = engine_at_confirm()
    result = engine.handle("음음 잠깐만요", NOW, wait_request=True)
    assert result.utterances == ["네, 천천히 말씀하세요."]


def test_the_engine_marks_only_turns_that_end_with_the_final_readback() -> None:
    # 리뷰 C6: CONFIRM 상태로 끝난 턴이 모두 복창은 아니다. 세션은 이 표시로 말 끊기 버튼과
    # 복창 끼어들기 규칙(금액 전 "네" 무시)을 건다
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="t")
    assert engine.start(NOW).readback is False
    assert engine.handle("메도빅 하나요", NOW).readback is False
    assert engine.handle("그게 다예요", NOW).readback is False
    readback = engine.handle("여섯시 반이요", NOW)
    assert readback.state is State.CONFIRM and readback.readback is True
    wait = engine.handle("잠깐만요", NOW, wait_request=True)
    assert wait.state is State.CONFIRM and wait.readback is False
    heard = engine.not_heard(NOW)
    assert heard.state is State.CONFIRM and heard.readback is False
    again = engine.no_input(NOW)  # "여보세요?" + 지금 장바구니 복창
    assert again.state is State.CONFIRM and again.readback is True
    assert engine.handle("네", NOW).readback is False  # 확정 인사
