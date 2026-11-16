"""NLU에 줄 가게 메뉴 정보 (5단계 티켓 05)."""

from __future__ import annotations

from pathlib import Path

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.nlu.catalog import menu_catalog
from agenthan.voice.store_config import StoreConfig

PHO = Path(__file__).parents[2] / "fixtures" / "phone" / "sample_pho_store.json"


def test_each_menu_line_carries_other_names_required_choices_and_options() -> None:
    lines = menu_catalog(FakeStoreKnowledge.from_fixture().list_menu(),
                         StoreConfig.from_fixture().required_options)
    assert lines[0] == "메도빅 | 다른 이름: 꿀케이크, 허니케이크, 메도비크 | 옵션: 홀케이크 변경"
    assert lines[2] == ("아메리카노 | 꼭 고를 것 온도: 따뜻하게/차갑게 | "
                        "옵션: 디카페인 변경, 샷 추가")
    assert lines[3] == "블린 | 다른 이름: 러시아 팬케이크, 블리니 | 옵션: 사워크림 빼기"


def test_another_store_gets_its_own_lines_from_its_own_data() -> None:
    lines = menu_catalog(FakeStoreKnowledge.from_fixture(PHO).list_menu(),
                         StoreConfig.from_fixture(PHO).required_options)
    assert lines[0] == ("소고기 쌀국수 | 다른 이름: 쌀국수, 퍼보 | 꼭 고를 것 양: 보통/곱빼기 | "
                        "옵션: 고수 빼기")
    assert lines[3] == "연유 커피 | 다른 이름: 카페 쓰어 | 꼭 고를 것 온도: 따뜻하게/차갑게"
