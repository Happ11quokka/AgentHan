"""엔진 스냅숏·되돌리기 (6단계 티켓 02 "앞 턴 되감기", `research/s6-turn-split.md` 1절·6절 #6).

세션은 손님 턴을 엔진에 넘기기 전에 `snapshot()`을 떠 두고, 봇 답의 첫 소리 전에 손님이 말을
이으면 `restore()`로 되돌린 뒤 합친 말로 다시 처리한다. 되돌린 엔진은 그 턴을 처리하지 않은
엔진과 구별되지 않아야 한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from agenthan.core.contracts import MenuItem
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import (
    SNAPSHOT_FIELDS,
    DialogEngine,
    TurnResult,
    _MenuClarify,
)
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
NUTS = "메도빅에 견과류 들어가요?"
CAKES = [
    MenuItem("c1", "치즈 케이크", 7000, category="케이크"),
    MenuItem("c2", "초코 케이크", 7500, category="케이크"),
]


def make_engine(menu: list[MenuItem] | None = None, *, ask_owner: bool = False) -> DialogEngine:
    store = FakeStoreKnowledge(menu) if menu is not None else FakeStoreKnowledge.from_fixture()
    engine = DialogEngine(store, FakeNlu(), StoreConfig.from_fixture(), order_id="o-test",
                          ask_owner=ask_owner)
    engine.start(NOW)
    return engine


def view(engine: DialogEngine) -> dict[str, Any]:
    """엔진 필드 전부의 그 순간 모습. 바뀌는 그릇(list·되묻기)은 사본으로 떠 둔다."""
    out = dict(vars(engine))
    out["_transcript"] = list(out["_transcript"])
    pending = out["_pending"]
    if isinstance(pending, _MenuClarify):
        out["_pending"] = replace(pending, deferred=list(pending.deferred))
    return out


def outcome(r: TurnResult) -> tuple[Any, ...]:
    return (r.utterances, r.state, r.cart, r.order, r.readback, r.owner_question,
            r.knowledge_requests)


def test_every_engine_field_is_classified_for_the_snapshot() -> None:
    # 엔진에 필드를 더하면 여기서 깨진다. `SNAPSHOT_FIELDS`에 그 필드를 어떻게 되돌릴지 적는다:
    # fixed(통화 동안 안 바뀜) · value(불변 값, 참조만) · copy(바뀌는 그릇, 사본) ·
    # cache(다시 읽어도 같은 캐시, 되돌리지 않음)
    engine = make_engine()
    assert set(vars(engine)) == set(SNAPSHOT_FIELDS)
    assert set(SNAPSHOT_FIELDS.values()) <= {"fixed", "value", "copy", "cache"}
    assert SNAPSHOT_FIELDS["_transcript"] == SNAPSHOT_FIELDS["_pending"] == "copy"


# (이름, 메뉴, 사장님께 묻기, 앞선 손님 말들, 되돌릴 턴의 말, 되돌린 뒤 할 말)
CASES = [
    ("anything_else", None, False, ["메도빅 하나요"], "아니요", "아니요 그게 다예요"),
    ("add_another", None, False, ["메도빅 하나요"], "블린 두 개요", "그게 다예요"),
    ("required_option", None, False, ["아메리카노 하나요"], "차갑게요", "따뜻하게요"),
    ("menu_confirm", None, False, ["메도비 하나요"], "네", "아니요"),
    ("menu_confirm_deferred", None, False, ["메도비 하나요"], "6시에 갈게요", "네"),
    ("menu_choice", CAKES, False, ["초즈 케이크 하나요"], "초코 케이크요", "치즈 케이크요"),
    ("ask_pickup", None, False, ["메도빅 하나요", "그게 다예요"], "6시 반이요", "7시요"),
    ("pickup_offer", None, False, ["메도빅 하나요", "그게 다예요", "4시 5분이요"], "네", "아니요"),
    ("pickup_offer_declined", None, False, ["메도빅 하나요", "그게 다예요", "4시 5분이요"],
     "아니요", "네"),
    ("readback", None, False, ["메도빅 하나요", "그게 다예요", "6시 반이요"], "아니요",
     "아니요, 두 개요"),
    ("fixing", None, False, ["메도빅 하나요", "그게 다예요", "6시 반이요", "아니요"],
     "두 개로 해 주세요", "7시로 해 주세요"),
    ("cancel_question", None, False, ["메도빅 하나요", "취소할게요"], "네", "아니요"),
    ("first_failure", None, False, ["메도빅 하나요"], "으라차차", "으라차차"),
    ("unknown_fail_count", None, False, ["메도빅 하나요", "으라차차"], "으라차차", "그게 다예요"),
    ("time_while_ordering", None, False, ["메도빅 하나요"], "3시에 갈게요", "그게 다예요"),
    ("no_input_count", None, False, ["메도빅 하나요"], None, "그게 다예요"),
    ("owner_question", None, True, ["메도빅 하나요"], NUTS, "그게 다예요"),
    ("menu_question", None, False, ["메도빅 하나요"], "메도빅 얼마예요?", "그게 다예요"),
]


@pytest.mark.parametrize(("menu", "ask_owner", "before", "undone", "after"),
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_restore_puts_every_field_back_and_the_engine_goes_on_as_if_the_turn_never_happened(
    menu, ask_owner, before, undone, after,
) -> None:
    engine = make_engine(menu, ask_owner=ask_owner)
    twin = make_engine(menu, ask_owner=ask_owner)  # 그 턴을 겪지 않은 엔진
    for text in before:
        engine.handle(text, NOW)
        twin.handle(text, NOW)
    was = view(engine)
    snap = engine.snapshot()

    # 되돌릴 턴: 손님 말, 없으면 무응답 되묻기(봇이 먼저 말하는 턴)
    changed = engine.no_input(NOW) if undone is None else engine.handle(undone, NOW)
    assert changed.utterances, "되돌릴 턴이 아무 일도 안 했으면 시험이 아니다"
    assert view(engine) != was
    engine.restore(snap)

    assert view(engine) == was
    assert outcome(engine.handle(after, NOW)) == outcome(twin.handle(after, NOW))
    assert view(engine)["_transcript"] == view(twin)["_transcript"]


def test_the_snapshot_does_not_share_the_deferred_list_with_the_live_clarify() -> None:
    # 되묻기(`_MenuClarify`)는 미뤄 둔 act 목록을 제자리에서 고친다. 스냅숏이 같은 목록을 들고
    # 있으면 되돌려도 미뤄 둔 act가 남는다
    engine = make_engine()
    engine.handle("메도비 하나요", NOW)
    snap = engine.snapshot()
    engine.handle("6시에 갈게요", NOW)  # 질문의 답이 아닌 시각: 미뤄 둔다
    engine.restore(snap)
    r = engine.handle("네", NOW)
    assert r.state is State.ORDERING  # 시각을 받았다면 다음 단계로 넘어갔을 것이다
    assert r.utterances[-1].endswith("더 필요하신 거 있으세요?")


def test_a_snapshot_can_be_restored_after_a_closing_turn_too() -> None:
    # 세션은 확정 턴을 되감지 않지만, 엔진의 되돌리기 자체는 상태를 가리지 않는다
    engine = make_engine()
    for text in ("메도빅 하나요", "그게 다예요", "6시 반이요"):
        engine.handle(text, NOW)
    was = view(engine)
    snap = engine.snapshot()
    assert engine.handle("네", NOW).order is not None
    engine.restore(snap)
    assert view(engine) == was
    assert engine.handle("네", NOW).state is State.CLOSING
