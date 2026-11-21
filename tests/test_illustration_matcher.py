"""그림 고르기: 메뉴 이름(+분류) → 그림 id. 단계마다의 규칙과 늘 같은 결과.

가족별 "이 이름이면 이 그림" 표는 test_illustration_<가족>.py에 따로 둔다.
"""

import json

import pytest

from agenthan.illustration import embedding_index, library
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
    """예전에 기본 그림을 받은 메뉴도 카탈로그에 자기 그림이 생기면(카탈로그 지문이 바뀐 뒤
    처음 세션을 열 때) 바뀐다. 지문이 그대로면 기본 그림을 다시 고르지 않는다."""
    menus = [{"id": "a", "name": "메도빅", "illustration": "default_cake"}]
    assert assign_missing(menus, broken_embed) is False
    assert menus[0]["illustration"] == "default_cake"
    assert assign_missing(menus, broken_embed, repick_defaults=True) is True
    assert menus[0]["illustration"] == "cake_medovik"


def test_an_own_illustration_stays_until_the_name_changes(fake_catalog):
    menu = {"id": "a", "name": "메도빅 홀케이크", "illustration": "cake_medovik"}
    assert assign_missing([menu], broken_embed) is False
    assert menu["illustration"] == "cake_medovik"  # 세션을 열어도 그대로
    menu["name"] = "초코 메도빅"
    assert assign_illustration(menu, broken_embed) is True
    assert menu["illustration"] == "cake_medovik_choco"
    assert assign_illustration(menu, broken_embed) is False  # 같은 이름이면 같은 그림


# --- 임베딩 단계(티켓 06): 키워드 다음, 분류 기본 그림 전 ---------------------------------

AXES = ("cake_medovik", "drink_latte", "dish_borsch")


def axis(*weights):
    """AXES 순서의 축에 무게를 둔 1024차원 벡터."""
    return [*weights, *[0.0] * (1024 - len(weights))]


@pytest.fixture
def embedded_catalog(tmp_path, monkeypatch):
    """기본 그림 8개 + 메도빅·라떼·보르시, 그리고 가짜 임베딩 인덱스(그림마다 축 하나)."""
    rows = [
        {"id": i, "name": c, "aliases": [], "keywords": [], "category": c, "recipe": {}}
        for c, i in library.DEFAULT_IDS.items()
    ]
    for key, name, keyword, category in (
        ("cake_medovik", "메도빅", "메도빅", "cake"),
        ("drink_latte", "카페라떼", "라떼", "coffee"),
        ("dish_borsch", "보르시", "보르시", "soup"),
    ):
        rows.append(
            {"id": key, "name": name, "aliases": [], "keywords": [keyword],
             "category": category, "recipe": {}}
        )
    (tmp_path / "fake.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(library, "CATALOG_DIR", tmp_path)
    library.load_catalog.cache_clear()
    order = [*AXES, *(row["id"] for row in rows if row["id"] not in AXES)]
    items = {}
    for row in rows:
        vector = [0.0] * 1024
        vector[order.index(row["id"])] = 1.0  # 그림마다 서로 다른 축: AXES가 0·1·2번 축
        items[row["id"]] = {"text": embedding_index.embedding_text(row), "vector": vector}
    monkeypatch.setattr(embedding_index, "stored", lambda: items)
    yield items
    library.load_catalog.cache_clear()


def embed_from(vectors, calls=None):
    """이름 → 벡터 표로 만든 가짜 임베딩. calls에 부른 이름을 쌓는다."""

    def embed(text):
        if calls is not None:
            calls.append(text)
        return vectors[text]

    return embed


def test_an_unknown_name_gets_the_nearest_illustration(embedded_catalog):
    embed = embed_from({"보르쉬": axis(0.1, 0.0, 0.95)})
    assert pick_illustration("보르쉬", None, embed) == "dish_borsch"


def test_a_far_name_falls_back_to_the_category_default(embedded_catalog):
    embed = embed_from({"가나다": axis(1.0, 1.0, 1.0)})  # 세 그림과 모두 0.58: 기준값 미만
    assert pick_illustration("가나다", "Tea", embed) == "default_tea"
    assert pick_illustration("가나다", None, embed) == "default_other"


def test_category_defaults_are_never_embedding_picks(embedded_catalog):
    """짧은 이름은 "케이크" 같은 기본 그림 쪽으로 가깝게 나오곤 한다(치즈케이크 → default_cake).
    기본 그림은 임베딩 후보가 아니고, 분류 단서로만 고른다."""
    order = [*AXES, *(key for key in embedded_catalog if key not in AXES)]
    near_default_cake = [0.0] * 1024
    near_default_cake[order.index("default_cake")] = 1.0
    near_default_cake[0] = 0.6  # 메도빅과는 0.51: 기준값 미만
    embed = embed_from({"가나다": near_default_cake})
    assert pick_illustration("가나다", None, embed) == "default_other"
    assert pick_illustration("가나다", "Cake", embed) == "default_cake"  # 분류 단서로


def test_a_category_clue_keeps_the_match_in_its_group(embedded_catalog):
    """케이크 그림이 조금 더 가까워도 분류가 커피면 커피 그림 안에서 고른다
    ("바닐라 라떼"가 "바닐라 치즈케이크"로 가지 않게)."""
    embed = embed_from({"가나다": axis(0.72, 0.69)})
    assert pick_illustration("가나다", "Coffee", embed) == "drink_latte"
    assert pick_illustration("가나다", None, embed) == "cake_medovik"


def test_a_failing_embedding_or_missing_key_falls_back_without_raising(
    embedded_catalog, monkeypatch
):
    from agenthan.core import embedding

    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def no_network(*_args, **_kwargs):
        pytest.fail("그림 고르기 테스트는 네트워크를 쓰지 않는다")

    monkeypatch.setattr(embedding.urllib.request, "urlopen", no_network)
    assert pick_illustration("보르쉬", "Soup", embedding.embed_text) == "default_soup"
    assert pick_illustration("보르쉬", "Soup", broken_embed) == "default_soup"
    assert pick_illustration("보르쉬", None, lambda _: [0.0] * 3) == "default_other"  # 깨진 벡터


def test_a_stale_index_line_is_not_a_candidate(embedded_catalog):
    """그림 이름·별칭이 바뀌었는데 인덱스를 다시 만들지 않았으면 그 벡터는 쓰지 않는다."""
    embedded_catalog["dish_borsch"]["text"] = "예전 이름"
    embed = embed_from({"보르쉬": axis(0.1, 0.0, 0.95)})
    assert pick_illustration("보르쉬", None, embed) == "default_other"


def test_exact_names_and_keywords_never_call_the_embedding(embedded_catalog):
    calls = []
    embed = embed_from({}, calls)
    assert pick_illustration("메도빅", None, embed) == "cake_medovik"
    assert pick_illustration("바닐라 라떼", None, embed) == "drink_latte"
    assert calls == []


def test_each_menu_is_embedded_once_and_not_again_on_reopen(embedded_catalog):
    calls = []
    embed = embed_from({"보르쉬": axis(0.1, 0.0, 0.95), "가나다": axis(1.0, 1.0, 1.0)}, calls)
    menus = [
        {"id": "a", "name": "보르쉬"},
        {"id": "b", "name": "가나다"},
        {"id": "c", "name": "가나다", "category": "Tea"},  # 같은 이름은 한 번만 임베딩
        {"id": "d", "name": "메도빅"},  # 키워드로 정해지면 부르지 않는다
    ]
    assert assign_missing(menus, embed) is True
    assert [m["illustration"] for m in menus] == [
        "dish_borsch",
        "default_other",
        "default_tea",
        "cake_medovik",
    ]
    assert calls == ["보르쉬", "가나다"]
    assert assign_missing(menus, embed) is False  # 세션을 다시 열 때: 그림 id가 저장돼 있다
    assert calls == ["보르쉬", "가나다"]


def test_once_the_embedding_fails_the_rest_of_the_batch_skips_it(embedded_catalog):
    """임베딩 서버가 멈추면 메뉴마다 기다리지 않는다."""
    calls = []

    def failing(text):
        calls.append(text)
        raise TimeoutError("임베딩 서버가 멈춤")

    menus = [{"id": str(i), "name": f"가나다{i} 케이크"} for i in range(5)]
    assert assign_missing(menus, failing) is True
    assert {m["illustration"] for m in menus} == {"default_cake"}
    assert len(calls) == 1


def test_the_embedding_pick_is_stable(embedded_catalog):
    embed = embed_from({"보르쉬": axis(0.5, 0.5)})  # 메도빅·라떼와 똑같이 0.71 → id 순
    assert {pick_illustration("보르쉬", None, embed) for _ in range(5)} == {"cake_medovik"}
