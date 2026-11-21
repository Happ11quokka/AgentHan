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


# ---------------------------------------------------------------- 5. 음료만 23개인 메뉴
# 같은 날 02:30 실통화: 답변기가 "음료 뭐 있어요"에 "모름", "마실 거 뭐 있냐고요"에 형식 틀림,
# "커피 있어요?"에 열 개를 다 읽었다


DRINKS = [
    MenuItem("d1", "아메리카노", 4500, "에스프레소에 물을 탄 커피 음료"),
    MenuItem("d2", "카페라떼", 5000, "음료로, 에스프레소에 스팀 밀크를 섞어 만든 커피."),
    MenuItem("d3", "바닐라라떼", 5500, "커피와 우유가 어우러진 음료로, 바닐라 향이 더해져요"),
    MenuItem("d4", "헤이즐넛라떼", 5500, "음료로, 에스프레소에 우유와 헤이즐넛 시럽을 섞어요"),
    MenuItem("d5", "카페모카", 5500, "초콜릿을 섞은 커피 음료"),
    MenuItem("d6", "레몬에이드", 5500, "레몬 음료"),
    MenuItem("d7", "메도빅", 7500, "러시아 꿀 케이크"),
    MenuItem("d8", "블리니", 9000, "러시아식 팬케이크"),
]


def test_a_kind_question_is_answered_from_the_menu_descriptions() -> None:
    from agenthan.voice.dialog.answerer import menus_of_kind

    assert menus_of_kind("음료는 뭐 있어요.", DRINKS) == (
        "음료", ["아메리카노", "카페라떼", "바닐라라떼", "헤이즐넛라떼", "카페모카", "레몬에이드"])
    assert menus_of_kind("마실 거 뭐 있냐고요", DRINKS)[0] == "음료"
    assert menus_of_kind("케이크 종류는 뭐 있어요.", DRINKS) == ("케이크", ["메도빅"])
    for not_kind in ("메도빅 달아요?", "주차 돼요?", "뭐가 맛있어요", "디저트 뭐 있어요"):
        assert menus_of_kind(not_kind, DRINKS) == ("", []), not_kind
    assert templates.menus_of_kind("음료", ["아메리카노", "카페라떼", "바닐라라떼", "헤이즐넛라떼",
                                          "카페모카"]) == (
        "음료는 아메리카노, 카페라떼, 바닐라라떼, 헤이즐넛라떼 같은 메뉴가 있어요.")
    assert templates.menus_of_kind("케이크", ["메도빅"]) == "케이크는 메도빅이 있어요."


def test_a_long_list_answer_with_evidence_is_cut_too() -> None:
    facts = store_facts(StoreConfig(store_name="샘플"), DRINKS)
    said = "아메리카노, 카페라떼, 바닐라라떼, 헤이즐넛라떼, 카페모카, 레몬에이드 있어요."
    reply = checked(AnswerReply("answer", say=said, evidence="음료"),
                    AnswerRequest("커피 있어요.", facts))
    assert reply.kind == "answer"
    assert reply.say == "아메리카노, 카페라떼, 바닐라라떼, 카페모카 있어요."


def test_the_engine_answers_a_kind_question_the_answerer_could_not() -> None:
    nlu = _Nlu({"커피 뭐 있어요?": [Act("ask_other", free_text="커피 뭐 있어요?")]})
    r = _engine(nlu, _Unsure()).handle("커피 뭐 있어요?", NOW)
    assert r.utterances[0] == "커피는 아메리카노가 있어요."  # 시험 가게: 아메리카노만 종류가 커피


# ---------------------------------------------------------------- 6. 메뉴 51개
# 같은 날 04:20 실통화: "음료는뭐 있어요"(띄어 쓰지 않고 받아 적음)와 "커피 있어요"에
# "그 메뉴는 없어요" + 음료 33개를 읽었다


def test_a_glued_kind_question_is_still_a_kind_question() -> None:
    from agenthan.voice.dialog.answerer import menus_of_kind

    assert menus_of_kind("음료는뭐 있어요.", DRINKS)[0] == "음료"


def test_a_kind_word_said_like_a_menu_name_lists_that_kind() -> None:
    from agenthan.voice.dialog.answerer import menus_of_kind

    assert menus_of_kind("커피", DRINKS, asked=True) == (
        "커피", ["아메리카노", "카페라떼", "바닐라라떼", "카페모카"])
    assert menus_of_kind("녹차 스무디", DRINKS, asked=True) == ("", [])


def test_the_engine_does_not_say_a_kind_is_missing() -> None:
    nlu = _Nlu({"커피 있어요?": [Act("ask_menu_info", menu_text="커피", menu_guess="커피",
                                   free_text="커피 있어요?")]})
    r = _engine(nlu, _Unsure()).handle("커피 있어요?", NOW)
    assert r.utterances[0] == "커피는 아메리카노가 있어요."  # 시험 가게: 종류가 커피인 메뉴


def test_many_drinks_are_cut_to_four() -> None:
    names = ["아메리카노", "카페라떼", "바닐라라떼", "헤이즐넛라떼", "카페모카", "레몬에이드"]
    assert templates.not_on_menu(["메도빅"], names) == (
        "죄송해요, 그 메뉴는 없어요. 마실 거는 아메리카노, 카페라떼, 바닐라라떼, 헤이즐넛라떼 같은 "
        "메뉴가 있어요.")
