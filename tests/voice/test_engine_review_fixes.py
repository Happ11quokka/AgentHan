"""1단계 리뷰(review-correctness.md, review-standards-spec.md)에서 찾은 결함의 회귀 테스트.

테스트 이름 앞의 번호는 정확성 리뷰의 항목 번호다 (C1 = 1번).
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice import cli
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)
MORE_Q = "더 필요하신 거 있으세요?"


class ScriptNlu:
    """정해 둔 act를 차례로 돌려준다. 다 쓰면 FakeNlu로 읽는다 (HCX가 낼 수 있는 묶음 재현용)."""

    def __init__(self, *scripted: Iterable[Act]) -> None:
        self._scripted = [list(acts) for acts in scripted]
        self._fake = FakeNlu()

    def queue(self, *acts: Act) -> None:
        self._scripted.append(list(acts))

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if self._scripted:
            return self._scripted.pop(0)
        return self._fake.parse(text, ctx)


def make(nlu: object | None = None, now: datetime = NOW) -> DialogEngine:
    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(),
        nlu or FakeNlu(),  # type: ignore[arg-type]
        StoreConfig.from_fixture(),
        order_id="o-review",
    )
    engine.start(now)
    return engine


def talk(engine: DialogEngine, *texts: str, now: datetime = NOW) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, now)
    assert result is not None
    return result


def lines(r: TurnResult) -> list[tuple[str, int]]:
    return [(line.item.id, line.qty) for line in r.cart.lines]


def has_readback(r: TurnResult) -> bool:
    return any("맞으세요?" in u and "총 " in u for u in r.utterances)


# ---------------------------------------------------------------- C1: 복창 없는 CONFIRM


def test_c1_menu_and_time_in_ask_pickup_adds_item_and_reads_back() -> None:
    engine = make()
    talk(engine, "메도빅 하나", "그게 다예요")
    r = talk(engine, "블린 하나랑 6시 반에 갈게요")
    assert r.state is State.CONFIRM
    assert lines(r) == [("m01", 1), ("m04", 1)]
    assert has_readback(r), r.utterances
    r = talk(engine, "네")
    assert r.order is not None and r.order.status == "confirmed"
    assert [(ln.menu_id, ln.quantity) for ln in r.order.lines] == [("m01", 1), ("m04", 1)]


def test_c1_item_plus_yes_to_offered_time_still_reads_back() -> None:
    nlu = ScriptNlu()
    engine = make(nlu)
    talk(engine, "메도빅 하나", "그게 다예요")
    r = talk(engine, "5시 15분이요")
    assert r.utterances == ["준비에 15분 정도 걸려요. 5시 25분 괜찮으세요?"]
    nlu.queue(Act("add_item", menu_text="블린", menu_guess="블린", qty=1), Act("yes"))
    r = talk(engine, "블린 하나 추가하고 네 괜찮아요")
    assert r.state is State.CONFIRM
    assert has_readback(r), r.utterances
    assert lines(r) == [("m01", 1), ("m04", 1)]


def test_c1_every_turn_ending_in_confirm_has_a_readback() -> None:
    """불변식: CONFIRM으로 끝난 턴의 마지막 질문은 복창이다."""
    nlu = ScriptNlu()
    engine = make(nlu)
    talk(engine, "메도빅 하나", "그게 다예요")
    nlu.queue(Act("unknown", free_text="어"), Act("set_pickup_time", time_text="6시 반"))
    r = talk(engine, "어 6시 반")
    assert r.state is State.CONFIRM
    assert has_readback(r), r.utterances


# ---------------------------------------------------------------- C2 / P1: 정정 실패 뒤 "네"


def _to_readback_with_three_lines(engine: DialogEngine) -> TurnResult:
    talk(engine, "메도빅 하나랑 나폴레옹 하나랑 블린 하나요", "블린 빼 주세요", "그게 다예요")
    r = talk(engine, "6시 반이요")
    assert r.state is State.CONFIRM
    return r


def test_c2_unhandled_qty_correction_repeats_the_readback() -> None:
    engine = make()
    _to_readback_with_three_lines(engine)
    r = talk(engine, "두 개로 해 주세요")
    assert r.state is State.CONFIRM
    assert MORE_Q not in r.utterances
    assert has_readback(r), r.utterances


def test_p1_unhandled_remove_then_yes_confirms_only_after_readback() -> None:
    engine = make()
    talk(engine, "메도빅 하나랑 블린 하나랑 나폴레옹 하나요", "나폴레옹 빼 주세요", "그게 다예요")
    talk(engine, "6시 반이요")
    r = talk(engine, "그거 빼 주세요")
    assert r.state is State.CONFIRM
    assert MORE_Q not in r.utterances
    assert has_readback(r), r.utterances
    r = talk(engine, "네")
    assert r.order is not None and r.order.status == "confirmed"


# ---------------------------------------------------------------- C3: now의 시간대


def _order_at(now: datetime) -> TurnResult:
    engine = make(now=now)
    talk(engine, "메도빅 하나", "그게 다예요", now=now)
    r = talk(engine, "6시 반", now=now)
    if r.utterances == ["오후 6시 반에 오시는 거죠?"]:  # 3시간 넘게 뒤면 확인한다 (티켓 04)
        r = talk(engine, "네", now=now)
    assert r.state is State.CONFIRM, r.utterances
    return talk(engine, "네", now=now)


def test_c3_naive_now_is_read_as_store_time() -> None:
    r = _order_at(datetime(2026, 11, 8, 16, 0))
    assert r.order is not None
    assert r.order.pickup_time == "2026-11-08T18:30:00+09:00"


def test_c3_utc_now_is_converted_to_store_time() -> None:
    r = _order_at(datetime(2026, 11, 8, 7, 0, tzinfo=UTC))  # = KST 16:00
    assert r.order is not None
    assert r.order.pickup_time == "2026-11-08T18:30:00+09:00"


def test_c3_other_offset_crossing_midnight_uses_store_date() -> None:
    # UTC 전날 23:00 = KST 08:00. 오늘(KST) 날짜로 판단한다
    now = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    r = _order_at(now)
    assert r.order is not None
    assert r.order.pickup_time == "2026-11-08T18:30:00+09:00"


def test_c3_cli_accepts_naive_now() -> None:
    stdin = io.StringIO("메도빅 하나요\n그게 다예요\n6시 반이요\n네\n")
    stdout = io.StringIO()
    code = cli.main(["--now", "2026-11-08T16:00:00"], stdin=stdin, stdout=stdout)
    assert code == 0
    assert "pickup_time='2026-11-08T18:30:00+09:00'" in stdout.getvalue()


def test_c3_cli_converts_utc_now() -> None:
    stdin = io.StringIO("메도빅 하나요\n그게 다예요\n6시 반이요\n네\n")
    stdout = io.StringIO()
    code = cli.main(["--now", "2026-11-08T07:00:00+00:00"], stdin=stdin, stdout=stdout)
    assert code == 0
    assert "pickup_time='2026-11-08T18:30:00+09:00'" in stdout.getvalue()


def test_c3_store_tz_is_configurable() -> None:
    utc_store = StoreConfig(store_name="UTC 가게", tz=UTC)
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), utc_store, order_id="o")
    now = datetime(2026, 11, 8, 21, 0, tzinfo=timezone(timedelta(hours=9)))  # = UTC 12:00
    engine.start(now)
    talk(engine, "메도빅 하나", "그게 다예요", now=now)
    r = talk(engine, "1시 반", now=now)  # 가게 시각 12:00 기준 13:30
    assert r.state is State.CONFIRM, r.utterances
    r = talk(engine, "네", now=now)
    assert r.order is not None
    assert r.order.pickup_time == "2026-11-08T13:30:00+00:00"


# ---------------------------------------------------------------- C4 / P3: 개점 전 "지금 갈게요"


@pytest.mark.parametrize("hm", [(8, 0), (9, 30)])
def test_c4_asap_before_opening_is_not_booked_before_open(hm: tuple[int, int]) -> None:
    now = datetime(2026, 11, 8, *hm, tzinfo=KST)
    engine = make(now=now)
    talk(engine, "메도빅 하나", "그게 다예요", now=now)
    r = talk(engine, "지금 갈게요", now=now)
    assert r.state is State.ASK_PICKUP
    assert "10시에 문을 열어요" in " ".join(r.utterances)


def test_c4_asap_while_ordering_before_opening_is_not_kept() -> None:
    now = datetime(2026, 11, 8, 9, 0, tzinfo=KST)
    engine = make(now=now)
    talk(engine, "메도빅 하나 지금 갈게요", now=now)
    r = talk(engine, "그게 다예요", now=now)
    assert r.state is State.ASK_PICKUP


def test_c4_asap_just_before_opening_rounds_up_to_open() -> None:
    now = datetime(2026, 11, 8, 9, 50, tzinfo=KST)  # 준비 끝 10:05 → 받는다
    engine = make(now=now)
    talk(engine, "메도빅 하나", "그게 다예요", now=now)
    r = talk(engine, "지금 갈게요", now=now)
    assert r.state is State.CONFIRM
    assert "10시 5분 픽업" in " ".join(r.utterances)


# ---------------------------------------------------------------- C5: 복창에 메뉴 이름만


def _readback_one_medovik(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나", "6시 반에 갈게요")
    r = talk(engine, "그게 다예요")
    assert r.state is State.CONFIRM


def test_c5_repeating_a_cart_menu_name_in_confirm_does_not_add_one() -> None:
    engine = make()
    _readback_one_medovik(engine)
    r = talk(engine, "메도빅이요")
    assert r.state is State.CONFIRM
    assert lines(r) == [("m01", 1)]
    assert has_readback(r), r.utterances
    assert "총 7,500원" in " ".join(r.utterances)


def test_c5_explicit_more_in_confirm_still_adds() -> None:
    engine = make()
    _readback_one_medovik(engine)
    r = talk(engine, "메도빅 두 개로 해 주세요")
    assert lines(r) == [("m01", 2)]
    r = talk(engine, "블린 하나 추가요")
    assert lines(r) == [("m01", 2), ("m04", 1)]


# ---------------------------------------------------------------- C10: ASK_PICKUP에서 항목 고치기


def test_c10_add_item_while_asking_pickup_is_taken_and_time_asked_again() -> None:
    engine = make()
    talk(engine, "메도빅 하나", "그게 다예요")
    r = talk(engine, "블린 하나 추가요")
    assert r.state is State.ASK_PICKUP
    assert lines(r) == [("m01", 1), ("m04", 1)]
    assert r.utterances == ["블린 하나요.", "몇 시쯤 오세요?"]


def test_c10_item_while_offer_pending_keeps_the_offer() -> None:
    engine = make()
    talk(engine, "메도빅 하나", "그게 다예요", "5시 15분이요")
    r = talk(engine, "블린 하나요")
    assert r.utterances == ["블린 하나요.", "준비에 15분 정도 걸려요. 5시 25분 괜찮으세요?"]
    r = talk(engine, "네")
    assert r.state is State.CONFIRM
    assert has_readback(r)


def test_c10_removing_last_item_while_asking_pickup_goes_back_to_ordering() -> None:
    engine = make()
    talk(engine, "메도빅 하나", "그게 다예요")
    r = talk(engine, "메도빅 빼 주세요")
    assert r.state is State.ORDERING
    assert r.cart.is_empty()
    assert r.utterances == ["메도빅 뺐어요.", "어떤 걸로 드릴까요?"]


def test_c10_item_needing_option_while_asking_pickup_asks_option_first() -> None:
    engine = make()
    talk(engine, "메도빅 하나", "그게 다예요")
    r = talk(engine, "아메리카노 하나요")
    assert r.state is State.CLARIFY
    assert r.utterances[-1] == "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"
    r = talk(engine, "따뜻하게요")
    assert r.state is State.ASK_PICKUP


# ---------------------------------------------------------------- C14·C16 (낮음)


def test_c14_unreadable_time_during_option_question_keeps_the_known_time() -> None:
    engine = make()
    talk(engine, "아메리카노 하나 6시 반에 갈게요")
    r = talk(engine, "이따 갈게요")
    assert r.state is State.CLARIFY
    r = talk(engine, "따뜻하게요", "그게 다예요")
    assert r.state is State.CONFIRM
    assert "6시 반 픽업" in r.utterances[-1]


def test_c16_time_taken_while_ordering_is_checked_again_at_readback() -> None:
    engine = make()
    talk(engine, "메도빅 하나 5시 25분에 갈게요")  # 17:10 + 준비 15분 = 경계값
    later = NOW + timedelta(minutes=10)
    r = talk(engine, "그게 다예요", now=later)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["준비에 15분 정도 걸려요. 5시 35분 괜찮으세요?"]


# ---------------------------------------------------------------- S11: 처리기 겹쳐 등록


def test_s11_registering_the_same_handler_twice_is_an_error() -> None:
    from agenthan.voice.dialog.engine import on

    with pytest.raises(ValueError):
        on("yes", State.CONFIRM)(lambda engine, turn, act: None)



# ---------------------------------------------------------------- C6: "아니요, 취소할게요"


@pytest.mark.parametrize(
    "text", ["아니요, 취소할게요", "아니요 주문 취소해 주세요", "네, 취소할래요"]
)
def test_c6_rules_see_cancel_after_yes_no_lead(text: str) -> None:
    from agenthan.voice.dialog.rules import apply_rules

    assert [a.type for a in apply_rules(text) or []] == ["cancel_order"]


def test_c6_rules_see_owner_after_lead() -> None:
    from agenthan.voice.dialog.rules import apply_rules

    assert [a.type for a in apply_rules("아니요, 사장님 바꿔 주세요") or []] == ["want_owner"]


def test_c6_lead_with_other_content_still_goes_to_nlu() -> None:
    from agenthan.voice.dialog.rules import apply_rules

    assert apply_rules("아니요, 블린 취소할게요") is None
    assert apply_rules("아니요, 두 개요") is None


def test_c6_no_cancel_in_confirm_asks_to_cancel_the_whole_order() -> None:
    engine = make()
    talk(engine, "메도빅 하나랑 블린 하나요", "6시 반에 갈게요", "그게 다예요")
    r = talk(engine, "아니요, 취소할게요")
    assert r.utterances == ["주문 취소할까요?"]
    assert lines(r) == [("m01", 1), ("m04", 1)]
    r = talk(engine, "네")
    assert r.state is State.DONE
    assert r.order is None


def test_c6_fake_nlu_bare_cancel_is_cancel_order() -> None:
    ctx = NluContext(
        menu_names=("메도빅", "블린"), cart_summary="블린 하나", last_question="", state="confirm"
    )
    assert [a.type for a in FakeNlu().parse("음 취소할게요", ctx)] == ["cancel_order"]
    assert FakeNlu().parse("그거 취소해 주세요", ctx) == [Act("remove_item")]


# ---------------------------------------------------------------- C8: 필수 옵션에 없는 값


def test_c8_unknown_choice_for_required_option_counts_as_failure() -> None:
    nlu = ScriptNlu()
    engine = make(nlu)
    talk(engine, "아메리카노 하나요")
    for _ in range(2):
        nlu.queue(Act("set_option", option_text="미지근하게"))
        r = talk(engine, "미지근하게요")
        assert r.state is State.CLARIFY
        assert r.utterances[-1] == "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"
        assert "디카페인" not in " ".join(r.utterances)
    nlu.queue(Act("set_option", option_text="미지근하게"))
    r = talk(engine, "미지근하게요")
    assert r.state is State.FALLBACK
    assert r.order is not None and r.order.status == "handoff"


def test_c8_optional_option_during_required_question_is_still_taken() -> None:
    engine = make()
    talk(engine, "아메리카노 하나요")
    r = talk(engine, "샷 추가요")
    assert r.state is State.CLARIFY
    assert [o.name for o in r.cart.lines[0].options] == ["샷 추가"]


# ---------------------------------------------------------------- C9: 되묻기에 "네, 두 개요"


def test_c9_yes_plus_qty_to_menu_confirm_takes_menu_with_that_qty() -> None:
    nlu = ScriptNlu()
    engine = make(nlu)
    r = talk(engine, "메도비 하나요")
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]
    nlu.queue(Act("yes"), Act("change_qty", qty=2))
    r = talk(engine, "네, 두 개요")
    assert r.state is State.ORDERING
    assert lines(r) == [("m01", 2)]


def test_c9_no_plus_menu_to_menu_confirm_still_takes_the_new_menu() -> None:
    nlu = ScriptNlu()
    engine = make(nlu)
    talk(engine, "메도비 하나요")
    nlu.queue(Act("no"), Act("add_item", menu_text="블린", menu_guess="블린", qty=1))
    r = talk(engine, "아니요, 블린이요")
    assert lines(r) == [("m04", 1)]


# ---------------------------------------------------------------- C15: 한 묶음에 두 값


def test_c15_two_values_of_one_required_group_ask_again() -> None:
    engine = make()
    talk(engine, "아메리카노 두 잔이요")
    r = talk(engine, "하나는 따뜻하게 하나는 차갑게요")
    assert r.state is State.CLARIFY
    assert r.cart.lines[0].options == ()
    assert r.utterances[-1] == "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"
    assert "한 가지로만" in r.utterances[0]


def test_c15_two_values_in_one_add_are_not_silently_overwritten() -> None:
    engine = make()
    r = talk(engine, "아메리카노 따뜻하게 차갑게 하나요")
    assert r.state is State.CLARIFY
    assert r.cart.lines[0].options == ()


# ---------------------------------------------------------------- C17: 수량 0과 상한


def test_c17_zero_qty_add_is_not_one() -> None:
    engine = make()
    r = talk(engine, "메도빅 0개요")
    assert r.cart.is_empty()
    assert r.state is State.ORDERING


def test_c17_huge_qty_is_refused() -> None:
    engine = make()
    r = talk(engine, "메도빅 1000개요")
    assert r.cart.is_empty()
    assert "99개까지" in " ".join(r.utterances)
    talk(engine, "메도빅 하나요")
    r = talk(engine, "메도빅 500개로 해 주세요")
    assert lines(r) == [("m01", 1)]
    assert "99개까지" in " ".join(r.utterances)


# ---------------------------------------------------------------- P4: 이미 지난 시각


def test_p4_past_time_says_it_has_passed() -> None:
    engine = make()
    talk(engine, "메도빅 하나", "그게 다예요")
    r = talk(engine, "두 시요")  # 17:10에 2시
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["그 시간은 이미 지났어요. 가장 빠른 시간은 5시 25분인데 괜찮으세요?"]
    r = talk(engine, "네")
    assert r.state is State.CONFIRM
