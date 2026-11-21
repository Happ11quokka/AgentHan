"""음료 그림 표(티켓 03): 이 메뉴 이름이면 이 그림.

임베딩(티켓 06)이 끼어들지 않게 늘 실패하는 임베딩을 넘긴다.
"""

import pytest

from agenthan.illustration.matcher import pick_illustration

# (메뉴 이름, 분류 또는 None, 기대하는 그림 id)
TABLE: list[tuple[str, str | None, str]] = [
    # 시드 가게(fixtures/dummy_store)
    ("아메리카노", "Coffee", "drink_americano"),
    ("카페라떼", "Coffee", "drink_latte"),
    ("라프 커피", "Coffee", "drink_raf"),
    ("카푸치노", "Coffee", "drink_cappuccino"),
    ("블랙티", "Tea", "drink_black_tea"),
    ("얼그레이", "Tea", "drink_earl_grey"),
    ("밀크티", "Tea", "drink_milk_tea"),
    ("오블레피하 차", "Tea", "drink_sea_buckthorn_tea"),
    ("허니레몬티", "Tea", "drink_honey_lemon_tea"),
    ("레몬에이드", "Ade", "drink_lemonade"),
    ("모르스", "Ade", "drink_mors"),
    ("자몽에이드", "Ade", "drink_grapefruit_ade"),
    ("오미자 에이드", "Ade", "drink_omija_ade"),
    # 실제 가게 메뉴 이름은 커밋하지 않는다(git 밖 별칭 파일, test_illustration_coverage가 본다).
    # 아래는 같은 그림을 부르는 일반 이름이다.
    ("디카페인 커피", "Coffee", "drink_decaf"),
    ("캐러멜 마키아토", "Coffee", "drink_caramel_macchiato"),
    ("베리차", "Tea", "drink_berry_tea"),
    ("산딸기 에이드", "Ade", "drink_raspberry_ade"),
    ("딸기레몬 에이드", "Ade", "drink_strawberry_lemon_ade"),
    # 파일럿 가게(분류 없음)
    ("아메리카노", None, "drink_americano"),
    ("카페라떼", None, "drink_latte"),
    # 다르게 부른 이름(별칭·키워드)
    ("카페 라테", None, "drink_latte"),
    ("Cafe Latte", None, "drink_latte"),
    ("따뜻한 아메리카노", None, "drink_americano"),
    ("아이스 아메리카노", None, "drink_iced_americano"),
    ("아이스 카페라떼", None, "drink_iced_latte"),
    ("디카페인 아메리카노", None, "drink_decaf"),
    ("아이스 카라멜 마키아토", None, "drink_caramel_macchiato"),
    ("홍차", None, "drink_black_tea"),
    ("얼그레이 티", None, "drink_earl_grey"),
    ("로얄 밀크티", None, "drink_milk_tea"),
    ("꿀레몬차", None, "drink_honey_lemon_tea"),
    ("레모네이드", None, "drink_lemonade"),
    ("딸기레몬에이드", None, "drink_strawberry_lemon_ade"),
    ("오미자에이드 (L)", None, "drink_omija_ade"),
    ("크랜베리 모르스", None, "drink_mors"),
]


@pytest.mark.parametrize(("name", "category", "expected"), TABLE)
def test_drink_illustrations(name, category, expected, broken_embed):
    assert pick_illustration(name, category, broken_embed) == expected


@pytest.mark.parametrize(
    ("one", "other"),
    [("아메리카노", "라떼"), ("레몬에이드", "오미자 에이드"), ("블랙티", "얼그레이")],
)
def test_similar_drinks_get_different_illustrations(one, other, broken_embed):
    assert pick_illustration(one, None, broken_embed) != pick_illustration(
        other, None, broken_embed
    )
