"""그림 고르기: 메뉴 이름(+분류) → 그림 id. 단계마다의 규칙과 늘 같은 결과.

가족별 "이 이름이면 이 그림" 표는 test_illustration_<가족>.py에 따로 둔다.
"""

import json

import pytest

from agenthan.illustration import library
from agenthan.illustration.matcher import assign_illustration, assign_missing, pick_illustration


def broken_embed(_text):
    raise RuntimeError("임베딩 서버가 응답하지 않음")


@pytest.fixture
def fake_catalog(tmp_path, monkeypatch):
    """기본 그림 8개 + 메도빅 3종만 있는 카탈로그."""
    rows = [
        {"id": i, "name": c, "aliases": [], "keywords": [], "category": c, "recipe": {}}
        for c, i in library.DEFAULT_IDS.items()
    ]
    rows += [
        {
            "id": "cake_medovik",
            "name": "메도빅",
            "aliases": ["메도빅 꿀 케이크"],
            "keywords": ["메도빅"],
            "category": "cake",
            "recipe": {},
        },
        {
            "id": "cake_medovik_choco",
            "name": "초코 메도빅",
            "aliases": [],
            "keywords": ["초코 메도빅", "메도빅 초코"],
            "category": "cake",
            "recipe": {},
        },
        {
            "id": "cake_medovik_whole",
            "name": "메도빅 홀케이크",
            "aliases": [],
            "keywords": ["메도빅 홀"],
            "category": "cake",
            "recipe": {},
        },
    ]
    (tmp_path / "fake.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(library, "CATALOG_DIR", tmp_path)
    library.load_catalog.cache_clear()
    yield
    library.load_catalog.cache_clear()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("메도빅", "cake_medovik"),
        ("메도빅꿀케이크", "cake_medovik"),  # 별칭: 띄어쓰기는 보지 않는다
        ("초코 메도빅", "cake_medovik_choco"),
        ("메도빅 초코", "cake_medovik_choco"),  # 가장 긴 키워드가 이긴다
        ("메도빅 홀 케이크", "cake_medovik_whole"),
        ("오늘의 메도빅 (1조각)", "cake_medovik"),  # 키워드가 들어 있으면
    ],
)
def test_exact_name_then_longest_keyword(fake_catalog, name, expected):
    assert pick_illustration(name) == expected


def test_a_keyword_beats_the_category_default(fake_catalog):
    assert pick_illustration("메도빅 라떼", "Coffee") == "cake_medovik"


def test_no_clue_at_all_is_other():
    assert pick_illustration("가나다", None, broken_embed) == "default_other"
    assert pick_illustration("", None, broken_embed) == "default_other"
    assert pick_illustration("  !! ", "  ", broken_embed) == "default_other"


def test_a_failing_embedding_falls_back_to_the_category_default():
    assert pick_illustration("가나다", "Tea", broken_embed) == "default_tea"


def test_category_field_comes_before_the_name_ending():
    assert pick_illustration("가나다 티", "Coffee", broken_embed) == "default_coffee"


def test_same_input_same_illustration():
    picks = {pick_illustration("가나다 에이드", None, broken_embed) for _ in range(5)}
    assert picks == {"default_ade"}


def test_every_pick_is_in_the_catalog():
    ids = library.catalog_ids()
    for name in ("메도빅", "아메리카노", "보르시", "가나다", "스파게티", "Сырники"):
        assert pick_illustration(name, None, broken_embed) in ids


def test_illustration_url_is_the_web_static_path_or_none():
    assert library.illustration_url("default_cake") == "/illustrations/default_cake.svg"
    assert library.illustration_url("cake_that_was_removed") is None
    assert library.illustration_url(None) is None


def test_assign_missing_fills_menus_without_their_own_illustration():
    menus = [
        {"id": "a", "name": "가나다 케이크"},
        {"id": "b", "name_ko": "가나다 수프"},  # 픽스처 기록은 name_ko
        # 기본 그림은 다시 고른다. 같은 그림이 나오면 그대로 둔다
        {"id": "c", "name": "가나다 티", "illustration": "default_tea"},
        {"id": "d", "name": "가나다 라떼", "illustration": "removed_one"},  # 없어진 그림은 다시
        {"id": "e", "name": "가나다", "category": "Ade"},
    ]
    assert assign_missing(menus, broken_embed) is True
    assert [m["illustration"] for m in menus] == [
        "default_cake",
        "default_soup",
        "default_tea",
        "default_coffee",
        "default_ade",
    ]
    assert assign_missing(menus, broken_embed) is False


def test_a_category_default_is_upgraded_when_the_catalog_grows(fake_catalog):
    """예전에 기본 그림을 받은 메뉴도 카탈로그에 자기 그림이 생기면 세션을 열 때 바뀐다."""
    menus = [{"id": "a", "name": "메도빅", "illustration": "default_cake"}]
    assert assign_missing(menus, broken_embed) is True
    assert menus[0]["illustration"] == "cake_medovik"


def test_an_own_illustration_stays_until_the_name_changes(fake_catalog):
    menu = {"id": "a", "name": "메도빅 홀케이크", "illustration": "cake_medovik"}
    assert assign_missing([menu], broken_embed) is False
    assert menu["illustration"] == "cake_medovik"  # 세션을 열어도 그대로
    menu["name"] = "초코 메도빅"
    assert assign_illustration(menu, broken_embed) is True
    assert menu["illustration"] == "cake_medovik_choco"
    assert assign_illustration(menu, broken_embed) is False  # 같은 이름이면 같은 그림
