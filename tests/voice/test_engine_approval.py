"""주문 승인 티켓 02: 복창 "네" → 사장님 확인 요청 → 수락 (엔진 이음매, spec "엔진").

엔진 상태는 늘리지 않는다. 열린 확인 요청 하나를 들고, 턴 결과에 요청(주문 스냅숏, 번호)을
싣는다. 사장님 결정은 `owner_decided`로 들어와 마무리 대사와 통화 결과가 된다. 세션이
`handle(approval=)`을 주지 않으면(설정 꺼짐·사장님 화면 없음) 예전처럼 바로 확정한다.
"""

from __future__ import annotations

from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.approval import ApprovalWithdrawn, OwnerDecision
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
HOLD = "사장님께 주문 확인 중이에요. 잠시만요."
CLOSING = "네, 6시 반까지 준비해 둘게요. 감사합니다."
TO_READBACK = ("메도빅 하나요", "그게 다예요", "6시 반이요")


def make_engine() -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o-test")
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str, approval: bool = True) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW, approval=approval)
    assert result is not None
    return result


def asked(engine: DialogEngine) -> TurnResult:
    """복창까지 가서 "네" (확인 요청을 연 턴)."""
    talk(engine, *TO_READBACK)
    return talk(engine, "네")


def test_yes_to_the_readback_asks_the_owner_and_holds() -> None:
    engine = make_engine()
    r = asked(engine)

    assert r.utterances == [HOLD]
    assert r.state is State.CONFIRM  # 아직 확정이 아니다
    assert r.order is None
    assert r.readback is False
    request = r.approval_request
    assert request is not None and request.number == 1
    order = request.order  # 수락되면 넘어갈 주문서
    assert (order.id, order.status) == ("o-test", "confirmed")
    assert [(line.menu_id, line.quantity) for line in order.lines] == [("m01", 1)]
    assert order.pickup_time == "2026-11-08T18:30:00+09:00"


def test_the_owner_accepting_closes_the_call_as_confirmed() -> None:
    engine = make_engine()
    asked(engine)
    r = engine.owner_decided(OwnerDecision(1, "accept"), NOW)

    assert r.utterances == [CLOSING]
    assert r.state is State.CLOSING
    assert r.order is not None and r.order.status == "confirmed"
    assert r.order.pickup_time == "2026-11-08T18:30:00+09:00"
    assert r.order.transcript[-3:] == [f"AI: {HOLD}", "사장님: 수락", f"AI: {CLOSING}"]
    assert r.ack is True  # 받아들인 대답 (`PHONE_ACK_FIRST`가 "네."를 따로 낸다)


def test_a_timeout_promises_a_text_and_still_hands_the_order_over() -> None:
    engine = make_engine()
    asked(engine)
    r = engine.owner_decided(OwnerDecision(1, "timeout"), NOW)

    assert r.utterances == ["가게에서 아직 확인 중이에요. 확인되는 대로 문자로 확정 알려 드릴게요. "
                            "확정 문자를 받으시면 주문 완료예요. 감사합니다."]
    assert r.order is not None and r.order.status == "confirmed"


def test_without_approval_yes_confirms_at_once_as_before() -> None:
    engine = make_engine()
    talk(engine, *TO_READBACK, approval=False)
    r = talk(engine, "네", approval=False)

    assert r.utterances == [CLOSING]
    assert r.approval_request is None
    assert r.order is not None and r.order.status == "confirmed"


def test_a_stale_or_unknown_decision_says_nothing() -> None:
    engine = make_engine()
    asked(engine)

    r = engine.owner_decided(OwnerDecision(2, "accept"), NOW)  # 연 적 없는 번호

    assert (r.utterances, r.order, r.state) == ([], None, State.CONFIRM)
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).order is not None
    late = engine.owner_decided(OwnerDecision(1, "accept"), NOW)  # 끝난 통화
    assert (late.utterances, late.order) == ([], None)


def test_saying_yes_again_while_waiting_does_not_open_a_second_request() -> None:
    engine = make_engine()
    asked(engine)
    r = talk(engine, "네")

    assert r.utterances == [HOLD]
    assert r.approval_request is None
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).order is not None


def test_changing_the_order_while_waiting_withdraws_the_request_and_asks_again_with_a_new_number(
) -> None:
    engine = make_engine()
    asked(engine)

    changed = talk(engine, "메도빅 두 개로요")

    assert changed.approval_withdrawn == ApprovalWithdrawn(1, "changed")
    assert changed.state is State.CONFIRM  # 고친 항목을 되받고 다시 복창
    assert changed.readback is True
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).utterances == []  # 낡은 결정
    again = talk(engine, "네")
    assert again.approval_request is not None and again.approval_request.number == 2
    assert [(line.menu_id, line.quantity) for line in again.approval_request.order.lines] == [
        ("m01", 2)]


def test_cancelling_while_waiting_withdraws_the_request() -> None:
    engine = make_engine()
    asked(engine)

    talk(engine, "취소할게요")  # "주문 취소할까요?"
    r = talk(engine, "네")

    assert r.utterances == ["네, 주문 취소했어요. 감사합니다."]
    assert r.approval_withdrawn == ApprovalWithdrawn(1, "cancelled")
    assert r.order is None
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).order is None


def test_a_rewound_turn_takes_the_open_request_back() -> None:
    """앞 턴 되감기(6단계)가 요청을 연 턴 전으로 돌리면 열린 요청도 없어진다(스냅숏 분류표)."""
    engine = make_engine()
    talk(engine, *TO_READBACK)
    before = engine.snapshot()
    asked_turn = talk(engine, "네")
    assert asked_turn.approval_request is not None
    engine.restore(before)

    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).utterances == []
