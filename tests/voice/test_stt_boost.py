"""STT 키워드 부스팅 단어 목록 (티켓 10 #5). 통화 시작 CONFIG에 들어간다."""

from __future__ import annotations

from agenthan.core.contracts import MenuItem, MenuOption
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.stt.boost import BOOST_WEIGHT, menu_boost_words


def test_boost_words_are_menu_names_aliases_and_option_targets() -> None:
    words = menu_boost_words(FakeStoreKnowledge.from_fixture().list_menu())
    assert words == (
        "메도빅", "꿀케이크", "허니케이크", "메도비크", "홀케이크",
        "나폴레옹", "나폴레옹케이크",
        "아메리카노", "디카페인",
        "블린", "러시아 팬케이크", "블리니", "사워크림",
    )


def test_boost_words_skip_one_syllable_targets_duplicates_and_commas() -> None:
    # 한 음절("샷")은 비슷한 소리를 끌어오기 쉽다(과부스팅). 콤마는 CLOVA 단어 구분자다
    menu = [
        MenuItem("x1", "라떼", 5000, aliases=("라떼", " 카페,라떼 "),
                 options=(MenuOption("샷 추가"), MenuOption("시럽 추가"))),
    ]
    assert menu_boost_words(menu) == ("라떼", "카페라떼", "시럽")


def test_boost_weight_starts_at_three() -> None:
    # 실측 A/B: 5는 3보다 나은 점이 없었다 (research s2-stt-misrecognition Q1)
    assert BOOST_WEIGHT == 3.0
