"""케이크·디저트 그림 표(티켓 02): 이 메뉴 이름이면 이 그림.

임베딩(티켓 06)이 끼어들지 않게 늘 실패하는 임베딩을 넘긴다.
"""

import pytest

from agenthan.illustration import library
from agenthan.illustration.art import build
from agenthan.illustration.matcher import pick_illustration

# (메뉴 이름, 분류 또는 None, 기대하는 그림 id)
TABLE: list[tuple[str, str | None, str]] = [
    # 시드 가게(fixtures/dummy_store/menus.json)
    ("메도빅", "Cake", "cake_medovik"),
    ("초코 메도빅", "Cake", "cake_medovik_choco"),
    ("사워베리 메도빅", "Cake", "cake_medovik_sourberry"),
    ("메도빅 홀케이크", "Cake", "cake_medovik_whole"),
    ("나폴레옹", "Cake", "cake_napoleon"),
    ("버드밀크 케이크", "Cake", "cake_bird_milk"),
    ("키예프 케이크", "Cake", "cake_kiev"),
    ("프라하", "Cake", "cake_praha"),
    ("스메타닉", "Cake", "cake_smetannik"),
    ("제브라 케이크", "Cake", "cake_zebra"),
    ("당근 케이크", "Cake", "cake_carrot"),
    ("바닐라 치즈케이크", "Cake", "cake_cheesecake_vanilla"),
    ("미니 에클레어", "Eclair", "cake_eclair_mini"),
    ("빅 에클레어", "Eclair", "cake_eclair_big"),
    ("머랭 롤", "Roll", "cake_meringue_roll"),
    ("베리 타르트", "Tart", "cake_berry_tart"),
    ("파블로바", "Dessert", "cake_pavlova"),
    # 실제 가게 메뉴 이름은 커밋하지 않는다(git 밖 별칭 파일, test_illustration_coverage가 본다).
    # 아래는 같은 그림을 부르는 일반 이름이다.
    ("월넛 케이크", "Cake", "cake_walnut"),
    ("아몬드 베리 크림 케이크", "Cake", "cake_almond_berry_cream"),
    ("카라멜 머랭 홀케이크", "Cake", "cake_caramel_meringue_whole"),
    ("초콜릿 마스카포네 케이크", "Cake", "cake_choco_mascarpone"),
    ("초콜릿 돔 케이크", "Cake", "cake_choco_dome"),
    ("카라멜 초코 레이어 케이크", "Cake", "cake_caramel_choco_layer"),
    ("크랜베리 파인애플 타르트", "Tart", "cake_tart"),
    ("메도빅 홀 케이크", "Cake", "cake_medovik_whole"),
    # 파일럿 가게(분류 없음)
    ("메도빅", None, "cake_medovik"),
    ("나폴레옹", None, "cake_napoleon"),
    ("메도빅 홀케이크", None, "cake_medovik_whole"),
    # 사장님이 달리 적을 법한 이름
    ("메도빅 초코", None, "cake_medovik_choco"),
    ("초코 돔", None, "cake_choco_dome"),
    ("꿀케이크 (1조각)", None, "cake_medovik"),
    ("크랜베리 타르트", None, "cake_tart"),
    ("블루베리 타르트", None, "cake_berry_tart"),
    ("치즈 케이크", None, "cake_cheesecake_vanilla"),
    ("에클레어", None, "cake_eclair_big"),
    ("Medovik", None, "cake_medovik"),
    ("Наполеон", None, "cake_napoleon"),
]


@pytest.mark.parametrize(("name", "category", "expected"), TABLE)
def test_cake_illustrations(name, category, expected, broken_embed):
    assert pick_illustration(name, category, broken_embed) == expected


def test_the_four_medoviks_are_different_pictures(broken_embed):
    names = ("메도빅", "초코 메도빅", "사워베리 메도빅", "메도빅 홀케이크")
    assert len({pick_illustration(name, "Cake", broken_embed) for name in names}) == 4


CAKE_CATEGORIES = {"Cake", "Eclair", "Roll", "Tart"}


def test_every_demo_cake_menu_gets_its_own_picture(broken_embed):
    """데모 가게 메뉴 파일을 직접 읽어, 메뉴가 늘어도 케이크가 기본 그림으로 빠지지 않게."""
    cakes = [
        (source, name)
        for source, name, category in build.demo_menus()
        if category in CAKE_CATEGORIES
    ]
    assert cakes  # 시드 가게는 늘 있다
    fallbacks = set(library.DEFAULT_IDS.values())
    missing = [
        (source, name)
        for source, name in cakes
        if pick_illustration(name, "Cake", broken_embed) in fallbacks
    ]
    assert missing == []
