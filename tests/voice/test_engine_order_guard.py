"""주문 가드 (6단계): 한 통화의 총 수량·총액 상한. 장난 주문이 사장님 화면에 가지 않게.

상한은 가게 JSON `phone.max_order_qty`·`phone.max_order_total`(기본 넉넉함), 서버 설정
`PHONE_MAX_ORDER_QTY`·`PHONE_MAX_ORDER_TOTAL`이 있으면 그 값. 넘는 말은 담지 않고 정중히 알린다
(한 줄 수량 상한 99개와 같은 방식). 통화는 이어지고, 손님은 상한 안에서 주문을 마칠 수 있다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)


def make(**limits: int) -> DialogEngine:
    # 큰 수량 확인(6단계 품질, 10개부터 되묻기)은 이 파일이 보는 것이 아니라 끈다
    config = replace(StoreConfig.from_fixture(), **{"confirm_qty_from": 0, **limits})
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), config, order_id="o1")
    engine.start(NOW)
    return engine


def lines(result: TurnResult) -> list[tuple[str, int]]:
    return [(line.item.id, line.qty) for line in result.cart.lines]


def said(result: TurnResult) -> str:
    return " ".join(result.utterances)


def test_an_order_over_the_total_quantity_is_not_added() -> None:
    engine = make(max_order_qty=5)
    engine.handle("메도빅 네 개요", NOW)
    r = engine.handle("블린 세 개요", NOW)
    assert lines(r) == [("m01", 4)]  # 넘는 줄은 담지 않고, 담은 것은 그대로
    assert "모두 합쳐 5개까지" in said(r)
    assert r.state is State.ORDERING  # 통화는 이어진다
    ok = engine.handle("블린 하나요", NOW)
    assert sum(qty for _, qty in lines(ok)) == 5


def test_raising_a_quantity_past_the_limit_is_refused_too() -> None:
    engine = make(max_order_qty=5)
    engine.handle("메도빅 두 개요", NOW)
    r = engine.handle("메도빅 여덟 개로 해 주세요", NOW)
    assert lines(r) == [("m01", 2)]
    assert "모두 합쳐 5개까지" in said(r)


def test_an_order_over_the_total_amount_is_not_added() -> None:
    engine = make(max_order_total=20_000)  # 메도빅 7,500원
    engine.handle("메도빅 두 개요", NOW)
    r = engine.handle("메도빅 세 개로 해 주세요", NOW)
    assert lines(r) == [("m01", 2)]
    assert "20,000원까지" in said(r)


def test_after_a_refusal_the_caller_can_still_finish_an_order_inside_the_limit() -> None:
    engine = make(max_order_qty=2)
    for _ in range(3):  # 같은 말을 되풀이해도 통화를 끊지 않는다
        r = engine.handle("메도빅 다섯 개요", NOW)
        assert r.cart.is_empty() and r.state is State.ORDERING and r.order is None
    assert lines(engine.handle("메도빅 두 개요", NOW)) == [("m01", 2)]
    engine.handle("그게 다예요", NOW)
    engine.handle("6시 반이요", NOW)
    order = engine.handle("네", NOW).order
    assert order is not None and [(ln.menu_id, ln.quantity) for ln in order.lines] == [("m01", 2)]


def test_defaults_are_roomy_and_zero_switches_a_limit_off() -> None:
    config = StoreConfig.from_fixture()
    assert (config.max_order_qty, config.max_order_total) == (100, 1_000_000)
    engine = make()
    assert lines(engine.handle("메도빅 30개요", NOW)) == [("m01", 30)]
    free = make(max_order_qty=0, max_order_total=0)
    free.handle("메도빅 99개요", NOW)
    assert sum(qty for _, qty in lines(free.handle("블린 99개요", NOW))) == 198


def test_limits_come_from_the_store_json_and_the_server_settings_win() -> None:
    raw = {"store": {"name": "가게"}, "phone": {"max_order_qty": 12, "max_order_total": 90_000}}
    config = StoreConfig.from_store_json(raw)
    assert (config.max_order_qty, config.max_order_total) == (12, 90_000)
    unset = VoiceSettings.from_env({}, env_file=None)
    assert unset.with_order_limits(config) == config
    env = VoiceSettings.from_env({"PHONE_MAX_ORDER_QTY": "30", "PHONE_MAX_ORDER_TOTAL": "0"},
                                 env_file=None)
    changed = env.with_order_limits(config)
    assert (changed.max_order_qty, changed.max_order_total) == (30, 0)


def test_a_confirmed_big_quantity_still_meets_the_order_limit() -> None:
    """큰 수량을 "네"로 확인해도 주문 전체 상한은 그대로 건다 (두 장치가 만나는 곳)."""
    engine = make(confirm_qty_from=10, max_order_qty=20)
    asked = engine.handle("메도빅 30개요", NOW)
    assert lines(asked) == [] and "맞으세요" in said(asked)
    answered = engine.handle("네", NOW)
    assert lines(answered) == [] and "20개까지만" in said(answered)
