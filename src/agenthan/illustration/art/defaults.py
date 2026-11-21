"""분류 기본 그림 8장: 라이브러리에 맞는 그림이 없을 때 분류로 고르는 그림.

이름은 카탈로그 이름(정확히 같으면 붙는다). 키워드는 두지 않는다: 이름 끝말·분류로 기본 그림을
고르는 일은 매처의 마지막 단계가 하고, 키워드가 있으면 더 맞는 그림(임베딩)을 가로막는다.
"""

from agenthan.illustration.art.style import (
    CATEGORY_CARD,
    Illustration,
    cake_slice,
    circle,
    citrus_wheel,
    d,
    ellipse,
    group,
    line,
    path,
    plate,
    rect,
    shadow,
    svg,
)

FAMILY = "defaults"


def _cake() -> list[str]:
    layers = [
        ("sponge", 1),
        ("cream", 0.55),
        ("sponge", 1),
        ("cream", 0.55),
        ("sponge", 1),
    ]
    return [
        plate(128, 170, 100),
        cake_slice(128, 158, 112, 58, layers, "cream", span=74),
        circle(110, 98, 11, "berry", shaded=True),
        circle(146, 98, 11, "berry", shaded=True),
        circle(128, 106, 12, "berry"),
        ellipse(124, 102, 4, 3, "white", opacity=0.6),
    ]


def _bakery() -> list[str]:
    loaf = [
        ellipse(0, 10, 76, 44, "crust", shaded=True),
        ellipse(0, 0, 74, 40, "crust"),
        *(
            ellipse(x, 0, 9, 26, "sponge", transform=f"rotate(35 {x} 0)")
            for x in (-36, 0, 36)
        ),
    ]
    cookie = [
        ellipse(0, 6, 32, 20, "crust", shaded=True),
        ellipse(0, 0, 32, 20, "sponge"),
        circle(-12, -4, 4.5, "choco"),
        circle(8, -8, 4, "choco"),
        circle(12, 6, 4.5, "choco"),
        circle(-4, 8, 4, "choco"),
    ]
    return [
        shadow(120, 168, 92, 30),
        group(loaf, "translate(112 128) rotate(-18)"),
        shadow(182, 196, 38, 14),
        group(cookie, "translate(180 186)"),
    ]


def _savory() -> list[str]:
    return [
        plate(128, 146, 104),
        ellipse(110, 150, 50, 30, "meat", shaded=True, transform="rotate(-12 110 150)"),
        ellipse(108, 142, 48, 28, "meat", transform="rotate(-12 108 142)"),
        ellipse(96, 134, 18, 7, "crust", opacity=0.55, transform="rotate(-12 96 134)"),
        ellipse(172, 128, 22, 12, "leaf", shaded=True, transform="rotate(-30 172 128)"),
        ellipse(160, 118, 22, 12, "leaf", transform="rotate(20 160 118)"),
        ellipse(186, 142, 18, 10, "leaf", transform="rotate(-60 186 142)"),
        circle(166, 168, 13, "tomato"),
        circle(186, 162, 11, "tomato"),
        ellipse(162, 164, 4, 3, "white", opacity=0.5),
        ellipse(183, 158, 3.5, 2.5, "white", opacity=0.5),
    ]


def _soup() -> list[str]:
    rim_y, rx = 118, 86
    bowl = d("M", 128 - rx, rim_y, "A", rx, 78, 0, 0, 0, 128 + rx, rim_y, "Z")
    return [
        shadow(128, 196, 74, 18),
        rect(100, 182, 56, 16, "porcelain", rx=8, shaded=True),
        path(bowl, "porcelain", shaded=True),
        ellipse(128, rim_y, rx, rx * 0.42, "porcelain"),
        ellipse(128, rim_y + 3, rx - 10, (rx - 10) * 0.4, "soup"),
        ellipse(120, rim_y + 2, 34, 13, "soup_light", opacity=0.7),
        ellipse(140, rim_y + 6, 16, 8, "white"),
        circle(110, rim_y - 6, 4, "leaf"),
        circle(150, rim_y - 10, 3.5, "leaf"),
        circle(160, rim_y + 10, 4, "leaf_shade"),
        circle(96, rim_y + 12, 3.5, "leaf_shade"),
        line("M106 60c-8 10 8 16 0 28", "white", 6, opacity=0.8),
        line("M140 54c-8 10 8 16 0 28", "white", 6, opacity=0.8),
    ]


def _cup(cx, rim_y, rx, height, bottom_rx, body, liquid, liquid_light) -> list[str]:
    ry = rx * 0.42
    bottom, belly = rim_y + height, rim_y + height * 0.8
    side = d(
        "M", cx - rx, rim_y,
        "C", cx - rx, belly, cx - bottom_rx, bottom, cx, bottom,
        "C", cx + bottom_rx, bottom, cx + rx, belly, cx + rx, rim_y, "Z",
    )  # fmt: skip
    right_half = d(
        "M", cx, rim_y, "L", cx + rx, rim_y,
        "C", cx + rx, belly, cx + bottom_rx, bottom, cx, bottom, "Z",
    )  # fmt: skip
    handle = d(
        "M", cx + rx - 4, rim_y + height * 0.18,
        "c", rx * 0.62, -height * 0.05, rx * 0.62, height * 0.62, -rx * 0.12, height * 0.58,
    )  # fmt: skip
    return [
        line(handle, body, 11),
        path(side, body),
        path(right_half, body, shaded=True),
        ellipse(cx, rim_y, rx, ry, body, shaded=True),
        ellipse(cx, rim_y + 2, rx - 6, ry - 4, liquid),
        ellipse(cx - 6, rim_y + 1, (rx - 6) * 0.55, (ry - 4) * 0.5, liquid_light, opacity=0.6),
    ]


def _coffee() -> list[str]:
    return [
        plate(128, 182, 92),
        *_cup(122, 112, 58, 76, 40, "porcelain", "coffee", "crema"),
        line("M110 46c-10 10 10 18 0 30", "white", 6, opacity=0.85),
        line("M136 40c-10 10 10 18 0 30", "white", 6, opacity=0.85),
    ]


def _tea() -> list[str]:
    leaf = "M0 0C10 -14 32 -14 42 0C32 14 10 14 0 0Z"
    return [
        plate(128, 180, 96),
        *_cup(118, 124, 66, 54, 44, "teal", "tea", "tea_light"),
        group(
            [path(leaf, "leaf"), line("M5 0L36 0", "leaf_shade", 2.5)],
            "translate(166 196) rotate(-18)",
        ),
        line("M106 60c-10 10 10 18 0 30", "white", 6, opacity=0.85),
        line("M132 54c-10 10 10 18 0 30", "white", 6, opacity=0.85),
    ]


def _ade() -> list[str]:
    top, bottom, tx, bx = 66, 206, 50, 40
    glass = d(
        "M", 128 - tx, top, "L", 128 - bx, bottom,
        "Q", 128, bottom + 10, 128 + bx, bottom, "L", 128 + tx, top, "Z",
    )  # fmt: skip
    drink_top = 96
    drink_rx = tx - 3 - (drink_top - top) * (tx - bx) / (bottom - top)
    drink = d(
        "M", 128 - drink_rx, drink_top, "L", 128 - bx + 3, bottom - 4,
        "Q", 128, bottom + 5, 128 + bx - 3, bottom - 4, "L", 128 + drink_rx, drink_top, "Z",
    )  # fmt: skip
    return [
        shadow(128, 210, 62, 14),
        line("M150 92L176 30", "berry", 9),
        path(glass, "glass", opacity=0.55),
        path(drink, "ade"),
        ellipse(128, drink_top, drink_rx, drink_rx * 0.3, "ade_light"),
        rect(100, 120, 26, 24, "ice", rx=6, opacity=0.85, transform="rotate(-12 113 132)"),
        rect(132, 146, 24, 22, "ice", rx=6, opacity=0.85, transform="rotate(14 144 157)"),
        rect(108, 168, 22, 20, "ice", rx=6, opacity=0.7, transform="rotate(6 119 178)"),
        ellipse(128, top, tx, tx * 0.3, "glass", opacity=0.5),
        citrus_wheel(84, 74, 24, "lemon", "lemon_light"),
    ]


def _other() -> list[str]:
    dome = "M50 168A78 88 0 0 1 206 168Z"
    return [
        plate(128, 174, 104),
        path(dome, "silver"),
        path("M64 168A64 84 0 0 1 104 92A70 92 0 0 0 82 168Z", "white", opacity=0.45),
        ellipse(128, 168, 78, 12, "silver", shaded=True),
        ellipse(128, 76, 13, 8, "silver", shaded=True),
        ellipse(128, 72, 13, 8, "silver"),
    ]


SCENES = {
    "cake": _cake,
    "bakery": _bakery,
    "savory": _savory,
    "soup": _soup,
    "coffee": _coffee,
    "tea": _tea,
    "ade": _ade,
    "other": _other,
}


def draw(recipe: dict) -> str:
    return svg(recipe["card"], *SCENES[recipe["scene"]]())


def _default(category: str, name: str) -> Illustration:
    return Illustration(
        id=f"default_{category}",
        name=name,
        category=category,
        draw=draw,
        recipe={"scene": category, "card": CATEGORY_CARD[category]},
    )


ILLUSTRATIONS = [
    _default("cake", "케이크"),
    _default("bakery", "빵·과자"),
    _default("savory", "짭짤한 요리"),
    _default("soup", "수프"),
    _default("coffee", "커피"),
    _default("tea", "차"),
    _default("ade", "에이드·주스"),
    _default("other", "기타"),
]
