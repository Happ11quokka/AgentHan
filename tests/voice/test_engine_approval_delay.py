"""주문 승인 티켓 04: 늦춘 수락(+15분·+30분) — 엔진 이음매 (spec "엔진" 결정 목록).

통화 중 늦춘 수락이 오면 AI가 바뀐 시각을 묻는다. "네"면 그 시각으로 `confirmed`, "아니요"면 주문
취소(다른 시각 협상은 없다). 거절·취소로 끝나면 그 요청을 취소로 거둬 사장님 카드가 취소로 바뀐다.
"""

from __future__ import annotations

from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.approval import ApprovalWithdrawn, OwnerDecision
from agenthan.voice.dialog.engine import MAX_NO_INPUT, DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
TO_READBACK = ("메도빅 하나요", "그게 다예요", "6시 반이요")
OFFER_15 = "사장님이 6시 45분에 준비 가능하시대요. 괜찮으세요?"


def make_engine() -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o-test")
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW, approval=True)
    assert result is not None
    return result


def offered(engine: DialogEngine, minutes: int = 15) -> TurnResult:
    """복창 "네" → 확인 요청 1 → 사장님이 `minutes`분 늦춰 수락."""
    talk(engine, *TO_READBACK, "네")
    return engine.owner_decided(OwnerDecision(1, "delay", minutes), NOW)


def test_a_delayed_accept_asks_the_customer_about_the_new_time() -> None:
    engine = make_engine()
    r = offered(engine)

    assert r.utterances == [OFFER_15]
    assert r.state is State.CONFIRM  # 아직 확정이 아니다
    assert (r.order, r.approval_request, r.approval_withdrawn) == (None, None, None)


def test_yes_confirms_the_order_at_the_new_time() -> None:
    engine = make_engine()
    offered(engine, 30)
    r = talk(engine, "네")

    assert r.utterances == ["네, 7시까지 준비해 둘게요. 감사합니다."]
    assert r.state is State.CLOSING
    assert r.order is not None and r.order.status == "confirmed"
    assert r.order.pickup_time == "2026-11-08T19:00:00+09:00"
    assert r.order.transcript[-4:] == [
        "사장님: 30분 늦춰 수락", "AI: 사장님이 7시에 준비 가능하시대요. 괜찮으세요?", "손님: 네",
        "AI: 네, 7시까지 준비해 둘게요. 감사합니다."]
    assert r.approval_withdrawn is None


def test_no_cancels_the_order_without_negotiating_another_time() -> None:
    engine = make_engine()
    offered(engine)
    r = talk(engine, "아니요")

    assert r.utterances == ["알겠어요, 이번 주문은 취소할게요. 다음에 또 주문해 주세요."]
    assert r.state is State.DONE
    assert r.order is None
    assert r.approval_withdrawn == ApprovalWithdrawn(1, "cancelled")  # 사장님 카드는 취소로


def test_cancelling_during_the_offer_asks_first_and_comes_back_to_the_offer() -> None:
    engine = make_engine()
    offered(engine)

    assert talk(engine, "취소할게요").utterances == ["주문 취소할까요?"]
    back = talk(engine, "아니요")
    assert back.utterances == ["네, 계속 도와드릴게요.", OFFER_15]
    assert back.approval_withdrawn is None

    talk(engine, "취소할게요")
    done = talk(engine, "네")
    assert done.utterances == ["네, 주문 취소했어요. 감사합니다."]
    assert done.approval_withdrawn == ApprovalWithdrawn(1, "cancelled")


def test_other_talk_during_the_offer_repeats_the_question_instead_of_changing_the_order() -> None:
    engine = make_engine()
    offered(engine)

    r = talk(engine, "블린도 하나 추가요")

    assert r.utterances == ["죄송해요, 제가 잘 못 알아들었어요.", OFFER_15]
    assert r.approval_request is None  # 다시 복창·새 요청 없음
    assert [(line.menu_id, line.quantity) for line in r.cart.to_order_lines()] == [("m01", 1)]



def test_silence_repeats_the_offer_and_ending_on_silence_does_not_withdraw() -> None:
    """무응답으로 끝나면 손님이 끊은 것과 같다. 요청을 거두지 않는다(허브가 늦춘 수락 문자)."""

    engine = make_engine()
    offered(engine)

    again = engine.no_input(NOW)
    assert again.utterances[-1] == OFFER_15
    last = again
    for _ in range(MAX_NO_INPUT - 1):
        last = engine.no_input(NOW)
    assert last.state is State.DONE
    assert (last.order, last.approval_withdrawn) == (None, None)


def test_a_second_decision_after_the_offer_is_ignored() -> None:
    engine = make_engine()
    offered(engine)

    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).utterances == []
    assert talk(engine, "네").order is not None


def test_a_rewound_turn_keeps_the_offer() -> None:
    """손님 턴 되감기(6단계)는 늦춘 시각 질문을 지우지 않는다(스냅숏 분류표 `value`)."""
    engine = make_engine()
    offered(engine)
    before = engine.snapshot()
    talk(engine, "아니요")
    engine.restore(before)

    r = talk(engine, "네")
    assert r.order is not None and r.order.pickup_time == "2026-11-08T18:45:00+09:00"
