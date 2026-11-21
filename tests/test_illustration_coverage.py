"""데모 가게 메뉴 커버리지(티켓 07): 데모 가게 세 메뉴 벌의 모든 이름이 분류 기본 그림이 아닌
자기 그림에 붙는다. 메뉴 파일을 직접 읽으므로 메뉴가 늘면 빠진 그림을 여기서 잡는다.

임베딩이 끼어들지 않게 늘 실패하는 임베딩을 넘긴다(정확한 이름·키워드만으로 붙어야 한다).
실제 가게 정제 메뉴(data/clean/menus.json)는 git에 없어 파일이 있을 때만 본다.
"""

import json

import pytest

from agenthan.illustration import library
from agenthan.illustration.art import build
from agenthan.illustration.matcher import pick_illustration

FALLBACKS = set(library.DEFAULT_IDS.values())
SOURCES = dict(build.DEMO_MENUS)
TRACKED = ("시드 가게", "파일럿 가게")
REAL_STORE = "실제 가게"


def broken_embed(_text):
    raise RuntimeError("임베딩 서버가 응답하지 않음")


def menu_names(source: str) -> list[tuple[str, str | None]]:
    data = json.loads(SOURCES[source].read_text(encoding="utf-8"))
    menus = data["menus"] if isinstance(data, dict) else data
    return [(m.get("name_ko") or m.get("name"), m.get("category")) for m in menus]


def without_own_picture(names: list[tuple[str, str | None]]) -> list[str]:
    return [
        name
        for name, category in names
        if pick_illustration(name, category, broken_embed) in FALLBACKS
        or pick_illustration(name, None, broken_embed) in FALLBACKS  # 분류가 없어도
    ]


@pytest.mark.parametrize("source", TRACKED)
def test_every_tracked_demo_menu_gets_its_own_picture(source):
    names = menu_names(source)
    assert len(names) >= 10, f"{source} 메뉴 파일을 못 읽었어요"
    assert all(name for name, _ in names)
    assert without_own_picture(names) == []


@pytest.mark.skipif(not SOURCES[REAL_STORE].is_file(), reason="실제 가게 정제 메뉴는 git 밖")
def test_every_real_store_menu_gets_its_own_picture():
    assert without_own_picture(menu_names(REAL_STORE)) == []


def test_the_gallery_demo_table_has_no_category_defaults():
    """검수 갤러리의 "데모 가게 메뉴 → 고른 그림" 표에 노란 줄(기본 그림)이 없다."""
    page = build.gallery_html()
    assert "데모 가게 메뉴 → 고른 그림" in page
    assert 'class="fallback"' not in page
