"""ASK_PICKUP 시나리오: 준비 시간, 지금 갈게요, 영업시간, 오전·오후 확인 (티켓 04)."""

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig


def at(hm: str) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, 8, h, m, tzinfo=KST)


@pytest.fixture
def engine() -> DialogEngine:
    return DialogEngine(
        FakeStoreKnowledge.from_fixture(),
        FakeNlu(),
        StoreConfig.from_fixture(),
        order_id="o-pickup",
    )


def to_ask_pickup(engine: DialogEngine, now: datetime) -> None:
    engine.start(now)
    engine.handle("메도빅 하나요", now)
    r = engine.handle("그게 다예요", now)
    assert r.state is State.ASK_PICKUP


def finish(engine: DialogEngine, now: datetime) -> TurnResult:
    r = engine.handle("네", now)
    assert r.state is State.CLOSING
    assert r.order is not None
    return r


# ---------------------------------------------------------------- 준비 시간


def test_too_early_offers_prep_time_and_yes_takes_it(engine: DialogEngine) -> None:
    now = at("17:10")
    to_ask_pickup(engine, now)

    r = engine.handle("5시 15분이요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["준비에 15분 정도 걸려요. 5시 25분 괜찮으세요?"]

    r = engine.handle("네", now)
    assert r.state is State.CONFIRM
    assert len(r.utterances) == 1
    assert "5시 25분 픽업" in r.utterances[0]

    order = finish(engine, now).order
    assert order is not None
    assert order.pickup_time == "2026-11-08T17:25:00+09:00"


def test_too_early_offer_declined_asks_again(engine: DialogEngine) -> None:
    now = at("17:10")
    to_ask_pickup(engine, now)
    engine.handle("10분 뒤에 갈게요", now)

    r = engine.handle("아니요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["그럼 몇 시쯤 오세요?"]

    r = engine.handle("6시요", now)
    assert r.state is State.CONFIRM
    assert "6시 픽업" in r.utterances[0]


def test_new_time_replaces_offer(engine: DialogEngine) -> None:
    now = at("17:10")
    to_ask_pickup(engine, now)
    engine.handle("5시 15분이요", now)
    r = engine.handle("그럼 6시 반이요", now)
    assert r.state is State.CONFIRM
    assert "6시 반 픽업" in r.utterances[0]


def test_now_takes_prep_time_without_asking(engine: DialogEngine) -> None:
    now = at("17:10")
    to_ask_pickup(engine, now)

    r = engine.handle("지금 갈게요", now)
    assert r.state is State.CONFIRM
    assert r.utterances[0] == "준비에 15분 정도 걸려서 5시 25분으로 잡아 둘게요."
    assert "5시 25분 픽업" in r.utterances[1]
    assert len(r.utterances) == 2

    order = finish(engine, now).order
    assert order is not None
    assert order.pickup_time == "2026-11-08T17:25:00+09:00"


# ---------------------------------------------------------------- 영업시간


def test_after_close_is_refused_and_asked_again(engine: DialogEngine) -> None:
    now = at("17:00")
    to_ask_pickup(engine, now)

    r = engine.handle("9시 반이요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["죄송해요, 저희가 9시에 문을 닫아요. 그 전에 몇 시쯤 오세요?"]

    r = engine.handle("8시요", now)  # 딱 3시간 뒤: 확인 없이 받는다
    assert r.state is State.CONFIRM
    assert "8시 픽업" in r.utterances[0]


def test_now_too_close_to_closing(engine: DialogEngine) -> None:
    now = at("20:50")
    to_ask_pickup(engine, now)
    r = engine.handle("지금 갈게요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["죄송해요, 저희가 9시에 문을 닫아요. 그 전에 몇 시쯤 오세요?"]


def test_before_open_is_refused(engine: DialogEngine) -> None:
    now = at("08:00")
    to_ask_pickup(engine, now)
    r = engine.handle("오전 9시 반이요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["죄송해요, 저희는 10시에 문을 열어요. 몇 시쯤 오세요?"]


# ---------------------------------------------------------------- 확인이 필요한 시각


def test_ambiguous_meridiem_is_confirmed(engine: DialogEngine) -> None:
    now = at("09:00")
    to_ask_pickup(engine, now)

    r = engine.handle("열한 시요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["오전 11시에 오시는 거죠?"]

    r = engine.handle("네", now)
    assert r.state is State.CONFIRM
    assert "11시 픽업" in r.utterances[0]
    order = finish(engine, now).order
    assert order is not None
    assert order.pickup_time == "2026-11-08T11:00:00+09:00"


def test_more_than_3_hours_ahead_is_confirmed(engine: DialogEngine) -> None:
    now = at("10:30")
    to_ask_pickup(engine, now)

    r = engine.handle("두 시요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["오후 2시에 오시는 거죠?"]

    r = engine.handle("아니요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["그럼 몇 시쯤 오세요?"]


def test_noon_is_spoken_as_daytime(engine: DialogEngine) -> None:
    now = at("08:30")
    to_ask_pickup(engine, now)
    r = engine.handle("열두 시 반이요", now)
    assert r.utterances == ["낮 12시 반에 오시는 거죠?"]


# ---------------------------------------------------------------- 기타


def test_unparseable_time_asks_again(engine: DialogEngine) -> None:
    now = at("15:00")
    to_ask_pickup(engine, now)
    r = engine.handle("이따 갈게요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["죄송해요, 몇 시쯤 오실지 한 번 더 말씀해 주세요."]


def test_yes_without_offer_is_not_understood(engine: DialogEngine) -> None:
    now = at("15:00")
    to_ask_pickup(engine, now)
    r = engine.handle("네", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["죄송해요, 잘 못 알아들었어요.", "몇 시쯤 오세요?"]


def test_native_numeral_time(engine: DialogEngine) -> None:
    now = at("16:00")
    to_ask_pickup(engine, now)
    r = engine.handle("여섯 시 반에 갈게요", now)
    assert r.state is State.CONFIRM
    order = finish(engine, now).order
    assert order is not None
    assert order.pickup_time == "2026-11-08T18:30:00+09:00"


# ---------------------------------------------------------------- 주문 중에 말한 시각


def test_too_early_time_said_while_ordering_is_checked_at_ask_pickup(
    engine: DialogEngine,
) -> None:
    now = at("17:10")
    engine.start(now)
    engine.handle("메도빅 하나요", now)
    engine.handle("5시 15분에 갈게요", now)
    r = engine.handle("그게 다예요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["준비에 15분 정도 걸려요. 5시 25분 괜찮으세요?"]


def test_now_said_while_ordering_is_kept(engine: DialogEngine) -> None:
    now = at("17:10")
    engine.start(now)
    engine.handle("메도빅 하나요", now)
    engine.handle("지금 갈게요", now)
    r = engine.handle("그게 다예요", now)
    assert r.state is State.CONFIRM
    assert "5시 25분 픽업" in r.utterances[0]


def test_unreadable_time_while_ordering_is_ignored(engine: DialogEngine) -> None:
    now = at("15:00")
    engine.start(now)
    engine.handle("메도빅 하나요", now)
    engine.handle("이따 갈게요", now)
    r = engine.handle("그게 다예요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["몇 시쯤 오세요?"]


def test_later_time_while_ordering_replaces_earlier_one(engine: DialogEngine) -> None:
    now = at("17:10")
    engine.start(now)
    engine.handle("메도빅 하나요", now)
    engine.handle("6시에 갈게요", now)
    engine.handle("아, 5시 15분에 갈게요", now)
    r = engine.handle("그게 다예요", now)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["준비에 15분 정도 걸려요. 5시 25분 괜찮으세요?"]
