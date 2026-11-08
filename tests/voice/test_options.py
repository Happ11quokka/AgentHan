import pytest

from agenthan.core.contracts import MenuOption
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.options import find_options
from agenthan.voice.store_config import RequiredOption, StoreConfig

STORE = FakeStoreKnowledge.from_fixture()
AMERICANO = STORE.get_menu("m03")
BLIN = STORE.get_menu("m04")
MEDOVIK = STORE.get_menu("m01")
TEMP = StoreConfig.from_fixture().required_options["m03"][0]
CANDIDATES = (*TEMP.choices, *AMERICANO.options)


def names(found: list[MenuOption]) -> list[str]:
    return [o.name for o in found]


def test_required_option_table_has_americano_temperature() -> None:
    assert TEMP == RequiredOption(
        name="온도", choices=(MenuOption("따뜻하게"), MenuOption("차갑게")), default=""
    )


def test_required_option_choice_lookup() -> None:
    assert TEMP.choice("차갑게") == MenuOption("차갑게")
    assert TEMP.choice("미지근하게") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("차갑게", ["차갑게"]),
        ("아이스", ["차갑게"]),
        ("시원한", ["차갑게"]),
        ("따뜻하게", ["따뜻하게"]),
        ("뜨거운", ["따뜻하게"]),
        ("디카페인", ["디카페인 변경"]),
        ("디카페인 변경", ["디카페인 변경"]),
        ("샷 추가", ["샷 추가"]),
        ("아이스 디카페인 샷추가", ["차갑게", "디카페인 변경", "샷 추가"]),
        ("시럽", []),
        ("", []),
    ],
)
def test_find_options_americano(text: str, expected: list[str]) -> None:
    assert names(find_options(CANDIDATES, text)) == expected


@pytest.mark.parametrize("text", ["사워크림 빼", "사워크림 없이", "사워크림 빼기"])
def test_find_options_removal_phrases(text: str) -> None:
    assert names(find_options(BLIN.options, text)) == ["사워크림 빼기"]


def test_find_options_strips_change_suffix() -> None:
    assert names(find_options(MEDOVIK.options, "홀케이크")) == ["홀케이크 변경"]


# ---------------------------------------------------------------- STT 오류 셋 (티켓 10)
# "사워크림"을 CLOVA가 "샤워크림"으로 적는다(07 실측 B1). 조사가 끼면 오인식이 없어도 놓쳤다.


@pytest.mark.parametrize(
    "text",
    [
        "샤워크림은 빼 주세요",  # 실측 변형 + 조사
        "블린 하나요 샤워크림은 빼주세요.",  # 실측 문장 그대로
        "사워크림 없이요",
        "사워크림은 빼",  # 오인식 없이 조사만 끼어도 놓쳤다
        "사워크림을 빼 주세요",
        "사워크림 말고요",
        "사워크림은 좀 빼 주실래요",
        "사워크림 제외해 주세요",
        "사워크림 넣지 말아 주세요",
        "“사워크림”은… 빼 주세요",  # 문장부호
    ],
)
def test_sour_cream_removal_is_found_through_stt_slips_and_particles(text: str) -> None:
    assert names(find_options(BLIN.options, text)) == ["사워크림 빼기"]


@pytest.mark.parametrize(
    "text",
    [
        "사워크림 빼지 마세요",  # 부정의 부정
        "사워크림은 빼지 말아 주세요",
        "사워크림 많이 주세요",  # 대상어만 있고 빼는 말이 없다
        "블루베리는 빼 주세요",  # 다른 대상어
        "샤워 하고 올게요",
        "크림 빼 주세요",  # 대상어 일부
        "블린 하나요",
    ],
)
def test_sour_cream_removal_is_not_found_without_a_removal_cue(text: str) -> None:
    assert find_options(BLIN.options, text) == []


def test_short_targets_need_an_exact_match() -> None:
    # 2음절 이하 대상어("샷")는 퍼지로 받지 않는다
    assert find_options(AMERICANO.options, "삿 추가") == []
    assert names(find_options(AMERICANO.options, "샷 추가요")) == ["샷 추가"]


def test_fuzzy_target_window_for_other_long_targets() -> None:
    # 받아쓰기가 한 자모 틀린 긴 대상어도 받는다 (디카페인 → 디까페인)
    assert names(find_options(CANDIDATES, "디까페인으로 해 주세요")) == ["디카페인 변경"]
    # 상관없는 말은 그 메뉴 옵션과 비슷해도 받지 않는다
    assert find_options(CANDIDATES, "카페인 많은 걸로") == []


def test_change_target_followed_by_a_removal_cue_is_not_chosen() -> None:
    # "디카페인 말고요"는 디카페인을 고른 말이 아니다
    assert find_options(CANDIDATES, "디카페인 말고요") == []
    assert names(find_options(MEDOVIK.options, "홀케이크로 바꿔 주세요")) == ["홀케이크 변경"]
