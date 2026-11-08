"""티켓 05: 메뉴 질문 답하기. `MenuItem` 필드에 근거가 있을 때만 답하고, 없으면 모른다고 한다."""

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.menu_info import answer_menu_question

DONT_KNOW = "그건 제가 정확히 모르겠어요."


def item(menu_id: str):
    found = FakeStoreKnowledge.from_fixture().get_menu(menu_id)
    assert found is not None
    return found


@pytest.mark.parametrize(
    "q", ["메도빅 많이 달아요?", "메도빅은 어떤 맛이에요?", "메도빅이 뭐예요?"]
)
def test_taste_or_what_is_answers_from_description(q: str) -> None:
    assert answer_menu_question(item("m01"), q) == (
        "메도빅은 꿀을 넣은 얇은 시트를 여러 겹 쌓은 러시아 꿀 케이크예요."
    )


def test_description_with_batchim_and_period() -> None:
    assert answer_menu_question(item("m04"), "블린은 뭐가 들어가요?") == (
        "블린은 얇은 러시아식 팬케이크. 연어·사워크림 토핑이에요."
    )


def test_no_description_means_dont_know() -> None:
    assert answer_menu_question(item("m03"), "아메리카노 어떤 맛이에요?") == DONT_KNOW


def test_price() -> None:
    assert answer_menu_question(item("m02"), "나폴레옹 얼마예요?") == "나폴레옹은 7,000원이에요."


def test_option_list() -> None:
    assert answer_menu_question(item("m03"), "아메리카노 옵션 뭐 있어요?") == (
        "아메리카노는 디카페인 변경, 샷 추가 할 수 있어요."
    )


def test_specific_option_with_extra_price() -> None:
    assert answer_menu_question(item("m03"), "아메리카노 디카페인 돼요?") == (
        "네, 아메리카노는 디카페인 변경 할 수 있어요. 500원 추가예요."
    )


def test_no_options() -> None:
    assert answer_menu_question(item("m02"), "나폴레옹 옵션 있어요?") == (
        "나폴레옹은 따로 고를 수 있는 옵션이 없어요."
    )


def test_allergen_list() -> None:
    assert answer_menu_question(item("m01"), "메도빅 알레르기 성분 있어요?") == (
        "메도빅에는 밀, 우유, 계란이 들어가요."
    )


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("메도빅 우유 들어가요?", "네, 메도빅에는 우유가 들어가요."),
        ("메도빅에 달걀 들어가요?", "네, 메도빅에는 계란이 들어가요."),  # 달걀 → 계란
        ("밀가루 들어가요?", "네, 메도빅에는 밀이 들어가요."),
    ],
)
def test_specific_allergen_present(q: str, expected: str) -> None:
    assert answer_menu_question(item("m01"), q) == expected


@pytest.mark.parametrize(
    "q",
    [
        "메도빅 글루텐 프리예요?",  # 필드에 근거가 없다 (밀이 있어도 글루텐을 추론하지 않는다)
        "메도빅 땅콩 들어가요?",  # 알레르기 목록에 없다고 안 들어간다는 근거는 아니다
        "아메리카노 알레르기 있어요?",  # 목록이 비어 있다
        "메도빅 비건이에요?",
        "메도빅 몇 인분이에요?",
    ],
)
def test_ungrounded_questions_say_dont_know(q: str) -> None:
    menu_id = "m03" if "아메리카노" in q else "m01"
    assert answer_menu_question(item(menu_id), q) == DONT_KNOW


@pytest.mark.parametrize("q", ["메도빅 얼마나 달아요?", "메도빅은 얼마나 부드러워요?"])
def test_eolmana_is_not_a_price_question(q: str) -> None:
    """P2: "얼마나 달아요"는 설명으로 답한다."""
    assert answer_menu_question(item("m01"), q) == (
        "메도빅은 꿀을 넣은 얇은 시트를 여러 겹 쌓은 러시아 꿀 케이크예요."
    )


@pytest.mark.parametrize("q", ["메도빅 얼마예요?", "메도빅 얼마에요", "메도빅 얼마죠?", "얼마야"])
def test_eolma_is_still_price(q: str) -> None:
    assert answer_menu_question(item("m01"), q) == "메도빅은 7,500원이에요."
