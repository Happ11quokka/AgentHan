"""가까운 메뉴 그림 표(티켓 07): 데모 가게엔 없지만 손님·심사위원이 넣을 법한 이름이면 이 그림.

임베딩(티켓 06)이 끼어들지 않게 늘 실패하는 임베딩을 넘긴다: 짧은 한국어 이름은 임베딩이
약해서, 이 이름들은 이름·별칭·키워드만으로 붙어야 한다.
"""

import pytest

from agenthan.illustration.matcher import pick_illustration


def broken_embed(_text):
    raise RuntimeError("임베딩 서버가 응답하지 않음")


# (메뉴 이름, 분류 또는 None, 기대하는 그림 id)
TABLE: list[tuple[str, str | None, str]] = [
    # 케이크: 넓은 키워드("치즈케이크", "타르트")보다 긴 키워드가 이긴다
    ("티라미수", None, "cake_tiramisu"),
    ("티라미스", "Cake", "cake_tiramisu"),
    ("뉴욕 치즈케이크", None, "cake_cheesecake_newyork"),
    ("뉴욕치즈 케익", None, "cake_cheesecake_newyork"),
    ("바스크 치즈케이크", None, "cake_cheesecake_basque"),
    ("치즈케이크", None, "cake_cheesecake_vanilla"),
    ("레드벨벳 케이크", None, "cake_red_velvet"),
    ("롤케이크", None, "cake_roll"),
    ("초코 롤 케이크", None, "cake_roll"),
    ("딸기 생크림 케이크", None, "cake_strawberry"),
    ("딸기케이크", "Cake", "cake_strawberry"),
    ("초코 케이크", None, "cake_chocolate"),
    ("가토 쇼콜라", None, "cake_chocolate"),
    ("밀크레이프", None, "cake_crepe"),
    ("녹차 케이크", None, "cake_matcha"),
    ("에그타르트", None, "cake_egg_tart"),
    ("에그 타르트 (2개)", None, "cake_egg_tart"),
    ("레몬 타르트", None, "cake_lemon_tart"),
    ("타르트", None, "cake_tart"),
    # 빵·구움과자
    ("크루아상", None, "bakery_croissant"),
    ("크로와상", None, "bakery_croissant"),
    ("아몬드 크루아상", None, "bakery_croissant"),
    ("바게트", None, "bakery_baguette"),
    ("소금빵", None, "bakery_salt_bread"),
    ("플레인 베이글", None, "bakery_bagel"),
    ("글레이즈드 도넛", None, "bakery_donut"),
    ("시나몬롤", None, "bakery_cinnamon_roll"),
    ("블루베리 머핀", None, "bakery_muffin"),
    ("초코칩 쿠키", None, "bakery_cookie"),
    ("마카롱", None, "bakery_macaron"),
    ("뚱카롱 3종", None, "bakery_macaron"),
    ("브라우니", None, "bakery_brownie"),
    ("와플", None, "bakery_waffle"),
    ("크로플", None, "bakery_waffle"),
    ("마들렌", None, "bakery_madeleine"),
    ("프레첼", None, "bakery_pretzel"),
    ("하차푸리", None, "bakery_khachapuri"),
    ("바트루시카", None, "bakery_vatrushka"),
    ("프랴니크", None, "bakery_pryanik"),
    ("스콘", None, "bakery_scone"),
    ("휘낭시에", None, "bakery_financier"),
    ("크렘 브륄레", None, "cake_creme_brulee"),
    # 음료
    ("에스프레소", None, "drink_espresso"),
    ("에스프레소 도피오", "Coffee", "drink_espresso"),
    ("핫초코", None, "drink_hot_chocolate"),
    ("초코", None, "drink_hot_chocolate"),
    ("코코아", None, "drink_hot_chocolate"),
    ("초코 라떼", None, "drink_hot_chocolate"),
    ("아이스 초코", None, "drink_iced_chocolate"),
    ("바닐라 라떼", None, "drink_vanilla_latte"),
    ("아이스 바닐라 라떼", "Coffee", "drink_vanilla_latte"),
    ("카페모카", None, "drink_mocha"),
    ("콜드브루", None, "drink_cold_brew"),
    ("아인슈페너", None, "drink_einspanner"),
    ("녹차 라떼", None, "drink_matcha_latte"),
    ("말차라떼", None, "drink_matcha_latte"),
    ("녹차", None, "drink_green_tea"),
    ("카모마일 티", None, "drink_chamomile"),
    ("캐모마일", "Tea", "drink_chamomile"),
    ("페퍼민트 티", None, "drink_peppermint"),
    ("유자차", None, "drink_citron_tea"),
    ("복숭아 아이스티", None, "drink_iced_tea"),
    ("딸기 스무디", None, "drink_strawberry_smoothie"),
    ("딸기 라떼", None, "drink_strawberry_smoothie"),
    ("망고 스무디", None, "drink_mango_smoothie"),
    ("요거트 스무디", None, "drink_yogurt_smoothie"),
    ("키위 스무디", None, "drink_yogurt_smoothie"),  # 과일 스무디는 기본 스무디 그림
    ("오렌지 주스", None, "drink_orange_juice"),
    ("크바스", None, "drink_kvass"),
]


@pytest.mark.parametrize(("name", "category", "expected"), TABLE)
def test_nearby_illustrations(name, category, expected):
    assert pick_illustration(name, category, broken_embed) == expected


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("티라미수", "나폴레옹"),  # 둘 다 네모 조각
        ("뉴욕 치즈케이크", "바닐라 치즈케이크"),
        ("롤케이크", "머랭 롤"),
        ("에그타르트", "타르트"),
        ("에스프레소", "아메리카노"),
        ("핫초코", "카페모카"),
        ("녹차 라떼", "녹차"),
        ("카모마일 티", "블랙티"),
        ("딸기 스무디", "스트로베리레몬 에이드"),
        ("크루아상", "소금빵"),
        ("도넛", "베이글"),
    ],
)
def test_similar_menus_get_different_illustrations(one, other):
    assert pick_illustration(one, None, broken_embed) != pick_illustration(
        other, None, broken_embed
    )
