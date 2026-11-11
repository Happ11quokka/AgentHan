"""가게 데이터만 바꾸면 같은 엔진이 돈다 (5단계 티켓 05).

업종·메뉴·옵션·꼭 고를 것이 샘플 베이커리와 전혀 다른 더미 쌀국수집으로 주문 한 통화를 끝까지.
NLU는 HCX가 낼 법한 act를 대본으로 준다(가짜 NLU의 정규식은 샘플 가게용이라 쓰지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.store_config import KST, StoreConfig

PHO = Path(__file__).parents[2] / "fixtures" / "phone" / "sample_pho_store.json"
NOW = datetime(2026, 11, 11, 18, 0, tzinfo=KST)


class ScriptedNlu:
    def __init__(self, script: dict[str, Sequence[Act]]) -> None:
        self._script = script
        self.contexts: list[NluContext] = []

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        self.contexts.append(ctx)
        return list(self._script.get(text, [Act("unknown", free_text=text)]))


def _engine(script: dict[str, Sequence[Act]]) -> tuple[DialogEngine, ScriptedNlu]:
    nlu = ScriptedNlu(script)
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(PHO), nlu, StoreConfig.from_fixture(PHO),
                          order_id="o-pho")
    return engine, nlu


def _lines(r: TurnResult) -> list[tuple[str, int, tuple[str, ...]]]:
    return [(line.item.id, line.qty, tuple(o.name for o in line.options)) for line in r.cart.lines]


SCRIPT = {
    "쌀국수 두 개 고수 빼 주시고 연유 커피 하나요": [
        Act("add_item", menu_text="쌀국수", menu_guess="소고기 쌀국수", qty=2,
            option_text="고수 빼"),
        Act("add_item", menu_text="연유 커피", menu_guess="연유 커피", qty=1),
    ],
    "아이스로요": [Act("set_option", option_text="아이스")],
    "반미 하나 계란 추가요": [
        Act("add_item", menu_text="반미", menu_guess="반미", qty=1, option_text="계란 추가"),
    ],
    "그게 다예요": [Act("done_ordering")],
    "일곱 시에 갈게요": [Act("set_pickup_time", time_text="일곱 시")],
    "네": [Act("yes")],
}


def test_a_whole_order_at_another_store_uses_only_that_store_data() -> None:
    engine, nlu = _engine(SCRIPT)
    first = engine.start(NOW)
    assert first.utterances[0].startswith("네, 샘플 쌀국수집 AI 전화 주문입니다.")

    r = engine.handle("쌀국수 두 개 고수 빼 주시고 연유 커피 하나요", NOW)
    # 국수 양은 기본값(보통)이 있어 묻지 않고, 커피 온도는 기본값이 없어 묻는다 (가게 JSON `phone`)
    assert _lines(r) == [("p01", 2, ("보통", "고수 빼기")), ("p04", 1, ())]
    assert r.state is State.CLARIFY
    assert r.utterances[-1] == "연유 커피는 따뜻하게 드릴까요, 차갑게 드릴까요?"

    r = engine.handle("아이스로요", NOW)
    assert _lines(r)[1] == ("p04", 1, ("차갑게",))

    r = engine.handle("반미 하나 계란 추가요", NOW)
    assert _lines(r)[2] == ("p03", 1, ("계란 추가",))

    engine.handle("그게 다예요", NOW)
    r = engine.handle("일곱 시에 갈게요", NOW)
    assert r.state is State.CONFIRM
    total = 2 * 11_000 + 5_000 + (7_000 + 1_000)
    assert f"{total:,}원" in " ".join(r.utterances)

    r = engine.handle("네", NOW)
    assert r.order is not None and r.order.status == "confirmed"
    assert r.order.pickup_time == "2026-11-11T19:00:00+09:00"

    # NLU는 이 가게 메뉴 정보만 받았다
    catalog = nlu.contexts[0].menu_catalog
    assert catalog[0].startswith("소고기 쌀국수 | 다른 이름: 쌀국수, 퍼보 | 꼭 고를 것 양:")
    assert not any("메도빅" in line for line in catalog)


def test_another_store_hours_come_from_its_data() -> None:
    """쌀국수집은 22시까지(가게 JSON). 샘플 베이커리(21시)였으면 21시 반 픽업은 안 된다."""
    script = {
        "반미 하나요": [Act("add_item", menu_text="반미", menu_guess="반미", qty=1)],
        "그게 다예요": [Act("done_ordering")],
        "아홉 시 반에 갈게요": [Act("set_pickup_time", time_text="아홉 시 반")],
    }
    engine, _ = _engine(script)
    now = NOW.replace(hour=19)  # 3시간 안이라 "오시는 거죠?" 확인 없이 바로 복창
    engine.start(now)
    for text in ("반미 하나요", "그게 다예요"):
        engine.handle(text, now)
    r = engine.handle("아홉 시 반에 갈게요", now)
    assert r.state is State.CONFIRM
    assert "9시 반" in " ".join(r.utterances)


def test_a_default_choice_the_customer_did_not_pick_is_not_read_back() -> None:
    """국수 양 "보통"은 기본값이다. 주문서·사장님 카드에는 남기고, 손님에게는 말하지 않는다."""
    script = {
        "쌀국수 두 개요": [Act("add_item", menu_text="쌀국수", menu_guess="소고기 쌀국수", qty=2)],
        "곱빼기로 바꿔 주세요": [Act("set_option", option_text="곱빼기")],
        "그게 다예요": [Act("done_ordering")],
        "일곱 시요": [Act("set_pickup_time", time_text="일곱 시")],
        "네": [Act("yes")],
    }
    engine, _ = _engine(script)
    engine.start(NOW)
    r = engine.handle("쌀국수 두 개요", NOW)
    assert _lines(r) == [("p01", 2, ("보통",))]
    assert r.utterances[0].startswith("소고기 쌀국수 두 개요.")
    assert "보통" not in " ".join(r.utterances)

    r = engine.handle("곱빼기로 바꿔 주세요", NOW)
    assert _lines(r) == [("p01", 2, ("곱빼기",))]
    assert "소고기 쌀국수 곱빼기로 두 개" in " ".join(r.utterances)

    engine.handle("그게 다예요", NOW)
    engine.handle("일곱 시요", NOW)
    r = engine.handle("네", NOW)
    assert r.order is not None
    assert r.order.lines[0].options == ("곱빼기",)


def test_the_default_stays_on_the_order_even_though_it_is_not_said() -> None:
    script = {
        "반미 하나랑 쌀국수 하나요": [
            Act("add_item", menu_text="반미", menu_guess="반미", qty=1),
            Act("add_item", menu_text="쌀국수", menu_guess="소고기 쌀국수", qty=1),
        ],
        "그게 다예요": [Act("done_ordering")],
        "일곱 시요": [Act("set_pickup_time", time_text="일곱 시")],
        "네": [Act("yes")],
    }
    engine, _ = _engine(script)
    engine.start(NOW)
    for text in ("반미 하나랑 쌀국수 하나요", "그게 다예요"):
        engine.handle(text, NOW)
    r = engine.handle("일곱 시요", NOW)
    assert r.state is State.CONFIRM
    assert "보통" not in " ".join(r.utterances)
    r = engine.handle("네", NOW)
    assert r.order is not None
    assert [line.options for line in r.order.lines] == [(), ("보통",)]
