"""주문 승인 티켓 05: 사장님 거절 — 바쁨·가게 사정·직접 쓰기 (엔진 이음매, spec "엔진").

통화 중에 거절이 오면 사유별 고정 사과를 말하고 통화는 취소(주문서 없음)로 끝난다. 직접 쓰기
사정은 손님에게 읽지 않는다(허브가 번역해 문자로만 보낸다). 거둔 요청의 거절은 버린다.
"""

from __future__ import annotations

import pytest
from test_engine_approval import NOW, asked, make_engine, talk

from agenthan.voice.dialog.approval import OwnerDecision
from agenthan.voice.dialog.states import State

BUSY = "죄송해요, 지금 가게가 바빠서 주문을 받기 어렵대요. 다음에 다시 주문해 주세요."
CLOSED = "죄송해요, 지금은 가게 사정으로 주문을 받을 수 없대요."
FREE = "사장님이 사정을 문자로 남기셨어요. 죄송해요."


@pytest.mark.parametrize(("reason", "line"), [("busy", BUSY), ("closed", CLOSED), ("free", FREE)])
def test_a_rejection_apologises_and_cancels_the_call(reason, line) -> None:
    engine = make_engine()
    asked(engine)
    r = engine.owner_decided(OwnerDecision(1, "reject", reason=reason), NOW)

    assert r.utterances == [line]
    assert r.state is State.DONE  # 취소로 끝 (세션 call.ended reason cancelled)
    assert r.order is None  # 주문서는 넘어가지 않는다
    assert r.approval_withdrawn is None


def test_after_a_rejection_the_call_takes_no_more_turns() -> None:
    engine = make_engine()
    asked(engine)
    engine.owner_decided(OwnerDecision(1, "reject", reason="busy"), NOW)

    late = engine.owner_decided(OwnerDecision(1, "accept"), NOW)  # 늦게 온 다른 결정
    assert late.utterances == [] and late.order is None


def test_a_rejection_for_a_withdrawn_request_is_dropped() -> None:
    engine = make_engine()
    asked(engine)
    talk(engine, "메도빅 두 개로요")  # 기다리는 중에 고침 → 요청 1을 거둠

    r = engine.owner_decided(OwnerDecision(1, "reject", reason="closed"), NOW)
    assert r.utterances == []
    assert r.state is not State.DONE
