"""대기 없는 주문 (전화 메뉴·주문 현황·쿠폰 티켓 04, spec "주문 현황: 통화 중 대기를 없앤다").

통화 중 대기가 0이면(기본) 복창에 "네"가 온 턴에 확인 요청을 통화 뒤 단계로 열고, 바로 마무리
대사("주문 넣어 드렸어요. …")로 통화를 끝낸다. 대기 대사·재촉이 없다. 주문서는 그 턴에 넘어가고
결정은 통화 뒤 사장님 화면에서 난다. 통화 중 대기를 켜면(`wait_in_call`) 예전처럼 기다린다.
"""

from __future__ import annotations

from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
PLACED = "주문 넣어 드렸어요. 가게에서 확인하면 화면으로 알려 드릴게요. 감사합니다."
HOLD = "사장님께 주문 확인 중이에요. 잠시만요."
CLOSING = "네, 6시 반까지 준비해 둘게요. 감사합니다."
TO_READBACK = ("메도빅 하나요", "그게 다예요", "6시 반이요")


def say_yes(*, approval: bool = True, wait_in_call: bool = False) -> TurnResult:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o-test")
    engine.start(NOW)
    for text in TO_READBACK:
        engine.handle(text, NOW, approval=approval, wait_in_call=wait_in_call)
    return engine.handle("네", NOW, approval=approval, wait_in_call=wait_in_call)


def test_yes_places_the_order_and_ends_the_call_without_waiting() -> None:
    r = say_yes()

    assert r.utterances == [PLACED]
    assert r.state is State.CLOSING
    request = r.approval_request
    assert request is not None and request.number == 1
    assert request.in_call is False  # 사장님 결정은 통화 뒤에
    assert r.order is not None and r.order.status == "confirmed"  # 주문서는 지금 넘어간다
    assert [(line.menu_id, line.quantity) for line in r.order.lines] == [("m01", 1)]
    assert r.order.pickup_time == request.order.pickup_time == "2026-11-08T18:30:00+09:00"


def test_the_closing_line_never_calls_the_request_jeopsu() -> None:
    assert "접수" not in " ".join(say_yes().utterances)  # 용어집: 확인 요청은 "접수"가 아니다


def test_without_an_owner_screen_the_order_confirms_with_the_pickup_time() -> None:
    r = say_yes(approval=False)  # 사장님 화면 꺼짐·승인 꺼짐: 자동 확정

    assert r.utterances == [CLOSING]
    assert r.approval_request is None
    assert r.order is not None and r.order.status == "confirmed"


def test_waiting_in_call_still_holds_for_the_owner() -> None:
    r = say_yes(wait_in_call=True)

    assert r.utterances == [HOLD]
    assert r.state is State.CONFIRM and r.order is None
    assert r.approval_request is not None and r.approval_request.in_call is True
