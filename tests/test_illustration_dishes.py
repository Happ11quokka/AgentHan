"""러시아 요리·과자 그림 표(티켓 04): 이 메뉴 이름이면 이 그림.

임베딩(티켓 06)이 끼어들지 않게 늘 실패하는 임베딩을 넘긴다.
"""

import pytest

from agenthan.illustration.matcher import pick_illustration


def broken_embed(_text):
    raise RuntimeError("임베딩 서버가 응답하지 않음")


# (메뉴 이름, 분류 또는 None, 기대하는 그림 id)
TABLE: list[tuple[str, str | None, str]] = [
    # 파일럿 가게(src/agenthan/app/pilot_store.json)의 러시아 요리·음료
    ("블린", None, "dish_blini"),
    ("피로시키", None, "dish_pirozhki"),
    ("시르니키", None, "dish_syrniki"),
    ("보르시", None, "dish_borscht"),
    ("펠메니", None, "dish_pelmeni"),
    ("감자 피로그", None, "dish_pirog_potato"),
    ("러시아 홍차", None, "dish_russian_tea"),
    ("콤포트", None, "dish_compote"),
    # 시드 가게(fixtures/dummy_store/menus.json): 분류 필드가 있어도 자기 그림이 먼저
    ("시르니키", "Dessert", "dish_syrniki"),
    ("오레쉬키", "Cookie", "dish_oreshki"),
    # 파일럿 가게 기록의 별칭과 흔한 다른 표기
    ("블리니", None, "dish_blini"),
    ("러시아 팬케이크", None, "dish_blini"),
    ("러시아 만두", None, "dish_pelmeni"),
    ("보르쉬", None, "dish_borscht"),
    ("비트 수프", None, "dish_borscht"),
    ("피로즈키", None, "dish_pirozhki"),
    ("러시아 고로케", None, "dish_pirozhki"),
    ("피로그", None, "dish_pirog_potato"),
    ("치즈 팬케이크", None, "dish_syrniki"),
    ("오레시키", None, "dish_oreshki"),
    ("러시안 티", None, "dish_russian_tea"),
    ("차이", None, "dish_russian_tea"),
    ("과일 음료", None, "dish_compote"),
    # 이름에 들어 있으면(키워드)
    ("연어 블린", None, "dish_blini"),
    ("소고기 펠메니 10개", None, "dish_pelmeni"),
    ("우크라이나식 보르쉬치", "Soup", "dish_borscht"),
    ("양배추 피로시키", None, "dish_pirozhki"),
    ("버섯 피로그", None, "dish_pirog_potato"),
    ("러시아 홍차 (잼 포함)", None, "dish_russian_tea"),
    ("체리 콤포트", "Ade", "dish_compote"),
]


@pytest.mark.parametrize(("name", "category", "expected"), TABLE)
def test_dish_illustrations(name, category, expected):
    assert pick_illustration(name, category, broken_embed) == expected


def test_dishes_that_look_alike_get_different_pictures():
    """수프·만두·팬케이크·빵끼리 서로 다른 그림이고, 분류 기본 그림으로 떨어지지 않는다."""
    names = ["보르시", "펠메니", "블린", "시르니키", "피로시키", "감자 피로그", "오레쉬키"]
    picked = [pick_illustration(name, None, broken_embed) for name in names]
    assert len(set(picked)) == len(names)
    assert not any(id.startswith("default_") for id in picked)
