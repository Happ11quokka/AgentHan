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
