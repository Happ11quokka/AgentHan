"""2026-11-22 새벽 실통화 세 건에서 걸린 곳 (테스트 통화, 서버 `phone-20261122.jsonl`).

1. 픽업 시각에 "몰라" → "잘 못 알아들었어요"가 두 번. 이제 가장 빨리 되는 시각을 권한다
2. 없는 음료("아이스 아메리카노") → 메뉴 아홉 개를 다 읽음. 이제 마실 것만, 아니면 네 개까지
3. "음료 뭐 있어?" "마실 거 뭐 있냐고" → "모르겠어요, 사장님께 전해 둘게요"가 세 번.
   답변기 지시문에 종류 질문을 더했고(라이브 HCX로 확인), 메뉴 이름만 늘어놓은 답은
   물은 종류로 줄인다
4. 쿠폰 권유 네 문장을 되물을 때마다 다 읽음. 두 문장으로 줄이고 되물을 때는 한 문장
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agenthan.core.contracts import MenuItem
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.answerer import AnswerReply, AnswerRequest, checked, store_facts
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)


def _engine(nlu: object | None = None, answerer: object | None = None) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), nlu or FakeNlu(),  # type: ignore[arg-type]
                          StoreConfig.from_fixture(), order_id="o-tuning",
                          answerer=answerer)  # type: ignore[arg-type]
    engine.start(NOW)
    return engine


# ---------------------------------------------------------------- 1. 픽업 시각을 모를 때


def test_dont_know_the_time_hears_the_earliest_time_and_yes_takes_it() -> None:
    engine = _engine()
    engine.handle("메도빅 하나요", NOW)
    assert engine.handle("그게 다예요", NOW).state is State.ASK_PICKUP
    r = engine.handle("몰라", NOW)
    assert r.utterances == ["가장 빨리 되는 시간은 오후 4시 15분이에요. 그때 오시겠어요?"]
    r = engine.handle("네", NOW)
    assert r.state is State.CONFIRM and "4시 15분에 찾으러 오시고" in r.utterances[-1]


def test_any_time_is_the_same_as_dont_know() -> None:
    engine = _engine()
    engine.handle("메도빅 하나요", NOW)
    engine.handle("그게 다예요", NOW)
    assert engine.handle("아무 때나요", NOW).utterances[-1].startswith("가장 빨리 되는 시간은")


def test_dont_know_before_opening_hears_the_first_time_after_opening() -> None:
    """밤 2시에 건 전화(가게는 10시에 연다): 문 연 뒤 준비 시간이 지난 10시 15분을 권한다."""
    night = datetime(2026, 11, 8, 2, 0, tzinfo=KST)
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="o-night")
    engine.start(night)
    engine.handle("메도빅 하나요", night)
    engine.handle("그게 다예요", night)
    r = engine.handle("몰라", night)
    assert r.utterances == ["가장 빨리 되는 시간은 오전 10시 15분이에요. 그때 오시겠어요?"]
    assert "10시 15분에 찾으러 오시고" in engine.handle("네", night).utterances[-1]


def test_dont_know_after_closing_is_asked_again() -> None:
    late = datetime(2026, 11, 8, 20, 55, tzinfo=KST)
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="o-late")
    engine.start(late)
    engine.handle("메도빅 하나요", late)
    engine.handle("그게 다예요", late)
    assert not engine.handle("몰라", late).utterances[-1].startswith("가장 빨리")


def test_a_real_time_is_still_taken_as_said() -> None:
    engine = _engine()
    engine.handle("메도빅 하나요", NOW)
    engine.handle("그게 다예요", NOW)
    assert "6시 반" in engine.handle("6시 반이요", NOW).utterances[-1]


# ---------------------------------------------------------------- 2. 없는 메뉴


def test_a_missing_drink_hears_only_the_drinks() -> None:
    r = _engine().handle("녹차 스무디 하나 주세요", NOW)
    assert r.utterances[0] == "죄송해요, 그 메뉴는 없어요. 마실 거는 아메리카노가 있어요."


def test_a_missing_food_hears_at_most_four_menus() -> None:
    names = ["메도빅", "나폴레옹", "블리니", "피로시키", "시르니키", "보르시"]
    assert templates.not_on_menu(names) == (
        "죄송해요, 그 메뉴는 없어요. 저희는 메도빅, 나폴레옹, 블리니, 피로시키 같은 메뉴가 있어요.")
    assert templates.not_on_menu(names[:2]) == (
        "죄송해요, 그 메뉴는 없어요. 저희는 메도빅, 나폴레옹이 있어요.")


class _Nlu:
    def __init__(self, script: dict[str, Sequence[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        return list(self._script.get(text, [Act("unknown", free_text=text)]))


class _Unsure:
    def answer(self, request: AnswerRequest) -> AnswerReply:
        return AnswerReply("unsure")


def test_asking_for_a_drink_we_do_not_sell_is_not_passed_to_the_owner() -> None:
    nlu = _Nlu({"녹차 있어요?": [Act("ask_other", free_text="녹차 있어요?")],
                "라떼 돼요?": [Act("ask_menu_info", menu_text="라떼", menu_guess="라떼",
                                 free_text="라떼 돼요?")]})
    engine = _engine(nlu, _Unsure())
    for said in ("녹차 있어요?", "라떼 돼요?"):
        r = engine.handle(said, NOW)
        assert r.utterances[0] == "죄송해요, 그 메뉴는 없어요. 마실 거는 아메리카노가 있어요.", said
        assert not any("사장님께 전해 둘게요" in u for u in r.utterances)


# ---------------------------------------------------------------- 3. 종류로 묻는 질문


MENU = [
    MenuItem("m1", "메도빅", 7500, "꿀을 넣은 얇은 시트와 크림을 여러 겹 쌓은 러시아 꿀 케이크"),
    MenuItem("m2", "나폴레옹", 7000, "페이스트리 사이에 커스터드 크림을 넣은 러시아식 케이크"),
    MenuItem("m3", "블리니", 9000, "얇게 부친 러시아식 팬케이크"),
    MenuItem("m4", "시르니키", 8500, "코티지치즈로 반죽해 도톰하게 구운 팬케이크"),
    MenuItem("m5", "펠메니", 11000, "다진 고기를 얇은 반죽으로 싸서 삶은 러시아식 작은 물만두"),
    MenuItem("m6", "콤포트", 5000, "과일을 끓여 차게 식힌 러시아식 과일 음료"),
    MenuItem("m7", "머랭롤", 8500, "머랭 롤 케이크"),
]
FACTS = store_facts(StoreConfig(store_name="샘플"), MENU)


def _list_answer(question: str, say: str) -> str:
    request = AnswerRequest(question, FACTS)
    return checked(AnswerReply("answer", say=say), request).say


def test_a_kind_question_keeps_only_menus_of_that_kind() -> None:
    every = "메도빅, 나폴레옹, 블리니, 시르니키, 펠메니, 콤포트, 머랭롤 있어요."
    # "팬케이크"는 케이크가 아니다(낱말 앞이 띄어 있어야)
    assert _list_answer("케이크 종류 뭐 있어요", every) == "메도빅, 나폴레옹, 머랭롤 있어요."
    assert _list_answer("음료는 뭐 있어요", every) == "콤포트 있어요."


def test_a_long_list_is_cut_to_four() -> None:
    every = "메도빅, 나폴레옹, 블리니, 시르니키, 펠메니, 콤포트, 머랭롤 있어요."
    assert _list_answer("디저트 뭐 있어요", every) == "메도빅, 나폴레옹, 블리니, 시르니키 있어요."


def test_a_short_list_is_left_alone() -> None:
    short = "메도빅이랑 나폴레옹 있어요."
    assert _list_answer("뭐가 맛있어요", short) == short


# ---------------------------------------------------------------- 4. 쿠폰 권유


def test_the_coupon_offer_is_two_sentences_and_short_when_asked_again() -> None:
    offer = templates.coupon_offer("전화 주문 1,000원 할인", 2_500, "피로시키")
    assert offer == ("2,500원만 더 담으시면 전화 주문 1,000원 할인 쿠폰을 쓸 수 있어요. "
                     "피로시키 하나 더 담으실까요?")
    assert templates.coupon_offer_again("피로시키") == "피로시키 하나 더 담으실까요?"
