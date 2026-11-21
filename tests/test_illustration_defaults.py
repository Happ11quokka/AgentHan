"""분류 기본 그림 표: 라이브러리에 없는 메뉴는 분류 필드 → 이름 끝말 → "기타" 순으로 고른다.

임베딩(티켓 06)이 끼어들지 않게 늘 실패하는 임베딩을 넘긴다. 이름은 다른 가족 그림의 이름·
키워드에 걸리지 않는 말("가나다")로 짓는다.
"""

import pytest

from agenthan.illustration.matcher import pick_illustration

TABLE = [
    # 카탈로그 이름과 정확히 같으면
    ("케이크", None, "default_cake"),
    ("빵·과자", None, "default_bakery"),
    ("짭짤한 요리", None, "default_savory"),
    (" 수 프 ", None, "default_soup"),
    ("커피", None, "default_coffee"),
    ("차", None, "default_tea"),
    ("에이드·주스", None, "default_ade"),
    ("기타", None, "default_other"),
    # 메뉴 분류 필드(시드 가게 픽스처의 영어 분류, 한국어 분류)
    ("가나다", "Cake", "default_cake"),
    ("가나다", "Dessert", "default_cake"),
    ("가나다", "Tart", "default_cake"),
    ("가나다", "Eclair", "default_cake"),
    ("가나다", "Roll", "default_cake"),
    ("가나다", "Cookie", "default_bakery"),
    ("가나다", "Coffee", "default_coffee"),
    ("가나다", "Tea", "default_tea"),
    ("가나다", "Ade", "default_ade"),
    ("가나다", "케이크", "default_cake"),
    ("가나다", "베이커리", "default_bakery"),
    ("가나다", "식사", "default_savory"),
    ("가나다", "커피", "default_coffee"),
    ("가나다", "음료", "default_ade"),
    ("가나다", "기타 메뉴", "default_other"),
    # 분류가 없으면 이름 끝말
    ("가나다 케이크", None, "default_cake"),
    ("가나다티", None, "default_tea"),
    ("가나다 에이드", None, "default_ade"),
    ("가나다 라떼", None, "default_coffee"),
    ("가나다 수프", None, "default_soup"),
    ("가나다 비스킷", None, "default_bakery"),
    ("가나다 만두", None, "default_savory"),
    ("가나다 티라떼", None, "default_tea"),  # 더 긴 끝말이 이긴다("라떼"는 커피)
    ("가나다 스파게티", None, "default_savory"),  # "티"로 끝나도 차가 아니다
    ("Gana Lemonade", None, "default_ade"),
    # 아무 단서도 없으면 기타
    ("가나다", None, "default_other"),
]


@pytest.mark.parametrize(("name", "category", "expected"), TABLE)
def test_category_defaults(name, category, expected, broken_embed):
    assert pick_illustration(name, category, broken_embed) == expected
