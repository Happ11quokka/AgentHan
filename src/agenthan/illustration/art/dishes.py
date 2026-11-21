"""러시아 요리·과자 그림(티켓 04): 블린·펠메니·보르시처럼 그림마다 함수 하나로 그린다.

style.py의 도형을 조합하고 ILLUSTRATIONS에 Illustration을 올리면 build가
SVG·카탈로그(catalog/dishes.json)·갤러리에 넣는다.

같은 계열끼리도 한눈에 갈리게 그린다: 블린은 얇게 쌓은 넓은 팬케이크, 시르니키는 도톰한
작은 패티 셋, 펠메니는 하얀 만두 무더기, 피로시키는 길쭉한 튀김빵, 감자 피로그는 네모난
파이 조각. 키워드는 이 가게 요리 이름(과 흔한 다른 표기)만 둔다. "만두"·"팬케이크"처럼
넓은 말은 다른 메뉴를 가로막으니 넣지 않는다.
"""

from agenthan.illustration.art.style import (
    Illustration,
    circle,
    citrus_wheel,
    d,
    ellipse,
    group,
    line,
    path,
    plate,
    polygon,
    rect,
    shadow,
    svg,
)

FAMILY = "dishes"


# ── 가족 전용 도형 ─────────────────────────────────────────────────────────────


def _sour_cream(cx, cy, r) -> str:
    """사워크림 한 숟갈: 납작한 흰 봉우리."""
    return (
        ellipse(cx, cy + r * 0.22, r, r * 0.58, "cream", shaded=True)
        + ellipse(cx, cy, r, r * 0.56, "white")
        + ellipse(cx - r * 0.12, cy - r * 0.28, r * 0.5, r * 0.3, "white")
        + ellipse(cx - r * 0.3, cy - r * 0.1, r * 0.22, r * 0.12, "cream", opacity=0.8)
    )


def _dill(cx, cy, size=1.0, angle=0) -> str:
    """딜 한 줄기: 가는 줄기와 깃털 같은 잎."""
    s = size
    stem = d("M", 0, 0, "q", 6 * s, -8 * s, 18 * s, -12 * s)
    leaves = d(
        "M", 5 * s, -4 * s, "l", -2 * s, -7 * s,
        "M", 9 * s, -7 * s, "l", 1 * s, -7 * s,
        "M", 13 * s, -9 * s, "l", 5 * s, -5 * s,
        "M", 7 * s, -5 * s, "l", 6 * s, 2 * s,
        "M", 12 * s, -9 * s, "l", 6 * s, 3 * s,
    )  # fmt: skip
    return group(
        [line(stem, "leaf_shade", 2.2 * s), line(leaves, "leaf", 2 * s)],
        f"translate({cx} {cy}) rotate({angle})",
    )


def _bowl(cx, rim_y, rx, depth, body="porcelain") -> list[str]:
    """깊은 그릇: 바닥 굽 + 몸통 + 테두리. 속 내용물은 rim_y 근처에 그린다."""
    body_path = d("M", cx - rx, rim_y, "A", rx, depth, 0, 0, 0, cx + rx, rim_y, "Z")
    return [
        shadow(cx, rim_y + depth - 4, rx * 0.86, 18),
        rect(cx - rx * 0.32, rim_y + depth - 18, rx * 0.64, 16, body, rx=8, shaded=True),
        path(body_path, body, shaded=True),
        ellipse(cx, rim_y, rx, rx * 0.42, body),
    ]


def _berry_dot(cx, cy, r, fill="berry") -> str:
    """작은 알(연어알·체리): 동그라미 + 반짝이."""
    return circle(cx, cy, r, fill) + circle(cx - r * 0.35, cy - r * 0.35, r * 0.32, "white")


# ── 그림 ──────────────────────────────────────────────────────────────────────


def _blini(recipe: dict) -> str:
    """블린: 얇은 팬케이크를 여러 장 쌓고 사워크림·연어알·딜을 얹는다."""
    cx = 128
    parts = [plate(cx, 170, 106)]
    for i, dx in enumerate((-5, 4, -3, 5, -2, 2, 0)):  # 아래에서 위로
        y = 172 - i * 6
        parts.append(ellipse(cx + dx, y + 3, 80, 46, "crust"))
        parts.append(ellipse(cx + dx, y, 80, 46, "sponge" if i % 2 else "lemon_light"))
    top = 136
    parts.append(ellipse(cx, top, 80, 46, "sponge"))
    for x, y, rx, ry in (
        (-46, -6, 9, 5), (-24, 20, 7, 4), (30, 22, 10, 5), (50, -10, 7, 4),
        (8, -30, 8, 4), (-10, 32, 6, 3), (58, 12, 6, 3),
    ):  # fmt: skip
        parts.append(ellipse(cx + x, top + y, rx, ry, "crust", opacity=0.45))
    parts.append(_sour_cream(cx - 22, top - 4, 22))
    for x, y in (
        (16, -2), (24, -6), (32, 0), (20, 6), (28, 8), (36, 6), (40, -4),
        (12, 8), (24, 14), (34, 14), (44, 4),
    ):  # fmt: skip
        parts.append(_berry_dot(cx + x, top + y, 4.2, "tomato"))
    parts.append(_dill(cx - 6, top + 16, 1.1, -10))
    return svg(recipe["card"], *parts)


def _pelmen(x, y, scale=1.0, angle=0) -> str:
    """펠메니 한 알: 납작한 챙(오므려 붙인 귀) 위에 통통한 둥근 몸통."""
    return group(
        [
            ellipse(0, 8, 23, 10, "cream", shaded=True),
            ellipse(0, 6, 23, 9, "cream"),
            ellipse(0, 2, 16, 12, "cream", shaded=True),
            ellipse(-1, -1, 15, 11, "cream"),
            line("M-14 4C-8 9 8 9 14 4", "cream_shade", 2.4),
            ellipse(-5, -5, 6, 3.5, "white", opacity=0.85),
        ],
        f"translate({x} {y}) rotate({angle}) scale({scale})",
    )


def _pelmeni(recipe: dict) -> str:
    """펠메니: 하얀 작은 만두를 수북이 담고 사워크림·딜·후추."""
    parts = [plate(128, 162, 108, "teal")]  # 하얀 만두가 도드라지게 색 접시
    for x, y, a in (
        (90, 126, -8), (132, 122, 6), (172, 134, 10),
        (62, 152, 6), (108, 148, -4), (150, 152, 8), (194, 160, -6),
        (82, 180, -6), (126, 178, 4), (168, 184, -8),
    ):  # fmt: skip
        parts.append(_pelmen(x, y, 1.2, a))
    parts.append(_sour_cream(118, 132, 18))
    parts.append(_dill(92, 168, 1.1, -20))
    parts.append(_dill(150, 172, 1.0, 30))
    for x, y in ((112, 168), (150, 140), (138, 196), (76, 140), (190, 176), (100, 192)):
        parts.append(circle(x, y, 1.8, "choco"))
    return svg(recipe["card"], *parts)


def _borscht(recipe: dict) -> str:
    """보르시: 비트 빛 붉은 수프 + 사워크림 + 딜, 옆에 호밀빵 한 쪽."""
    cx, rim_y, rx = 114, 112, 84
    slice_shape = "M-26 24L-26 -4C-26 -32 26 -32 26 -4L26 24Z"
    crumb = "M-21 19L-21 -4C-21 -25 21 -25 21 -4L21 19Z"
    bread = [
        path(slice_shape, "choco", shaded=True, transform="translate(0 6)"),
        path(slice_shape, "choco"),
        path(crumb, "meat_shade"),
        *(circle(x, y, 1.8, "choco") for x, y in ((-10, -6), (8, 2), (2, -16), (-6, 10), (12, 12))),
    ]
    return svg(
        recipe["card"],
        *_bowl(cx, rim_y, rx, 80),
        ellipse(cx, rim_y + 3, rx - 10, (rx - 10) * 0.4, "beet"),
        ellipse(cx - 12, rim_y + 6, 36, 12, "beet_light", opacity=0.6),
        ellipse(cx + 26, rim_y - 6, 14, 5, "beet", shaded=True),
        ellipse(cx - 40, rim_y + 4, 10, 4, "beet", shaded=True),
        _sour_cream(cx + 6, rim_y, 20),
        _dill(cx - 34, rim_y - 4, 1.0, 10),
        _dill(cx + 34, rim_y + 10, 0.9, -30),
        line("M96 56c-8 10 8 16 0 28", "white", 6, opacity=0.8),
        line("M130 50c-8 10 8 16 0 28", "white", 6, opacity=0.8),
        shadow(196, 214, 40, 10),
        group(bread, "translate(196 196) rotate(-16) scale(1.1 0.8)"),
    )


def _pirozhok(x, y, angle, scale=1.0) -> str:
    """피로시키 한 개: 길쭉한 튀김빵 + 가운데 오므린 이음매 + 윤기."""
    seam = "M-34 -4C-20 -12 20 -12 34 -4"
    return group(
        [
            ellipse(0, 6, 46, 24, "crust", shaded=True),
            ellipse(0, 0, 46, 22, "crust"),
            ellipse(-6, -4, 34, 12, "honey", opacity=0.5),
            line(seam, "crust_shade", 4),
            ellipse(-18, -10, 10, 3.5, "white", opacity=0.45),
        ],
        f"translate({x} {y}) rotate({angle}) scale({scale})",
    )


def _pirozhki(recipe: dict) -> str:
    """피로시키: 튀긴 빵 셋, 앞의 하나는 반으로 갈라 고기·양배추 속이 보인다."""
    half = [
        ellipse(0, 6, 34, 24, "crust", shaded=True),
        path("M0 -22A34 22 0 0 0 0 22A34 22 0 0 0 0 -22Z", "crust"),
        ellipse(12, 0, 14, 21, "crust_shade"),
        ellipse(13, 0, 11, 17, "sponge"),
        ellipse(14, 1, 7, 12, "meat"),
        circle(12, -5, 2.6, "leaf"),
        circle(16, 5, 2.4, "leaf"),
        circle(13, 1, 2, "meat", shaded=True),
    ]
    return svg(
        recipe["card"],
        plate(128, 160, 106),
        _pirozhok(96, 128, -14),
        _pirozhok(166, 132, 12),
        _pirozhok(118, 172, -4, 1.04),
        group(half, "translate(186 182) rotate(-8)"),
        _dill(170, 112, 0.9, -40),
    )


def _slab_face(p, q, bands) -> list[str]:
    """위 모서리 p→q에서 아래로 띠를 쌓은 면. bands: (색, 두께, 그늘)."""
    out, depth = [], 0.0
    for color, height, shaded in bands:
        lower = depth + height
        corners = [(p[0], p[1] + depth), (q[0], q[1] + depth)]
        corners += [(q[0], q[1] + lower), (p[0], p[1] + lower)]
        out.append(polygon(corners, color, shaded=shaded))
        depth = lower
    return out


def _pirog(recipe: dict) -> str:
    """감자 피로그: 네모난 파이 한 조각. 자른 면에 감자·버섯 속, 윗면엔 반죽 잎 장식."""
    a, b, c, top_d = (56, 124), (150, 150), (204, 112), (110, 86)  # 윗면 네 모서리
    front = [("crust", 9, False), ("sponge", 30, False), ("crust", 8, False)]
    side = [("crust", 9, True), ("sponge", 30, True), ("crust", 8, True)]
    leaf = "M0 0C8 -10 24 -10 32 0C24 10 8 10 0 0Z"
    parts = [
        shadow(132, 182, 98, 30),
        plate(132, 168, 104),
        *_slab_face(a, b, front),
        *_slab_face(b, c, side),
        polygon([a, b, c, top_d], "crust"),
        polygon([(70, 120), (148, 142), (192, 112), (114, 92)], "honey", opacity=0.55),
    ]
    for x, y in ((74, 146), (96, 156), (118, 158), (136, 166), (86, 160), (128, 152)):
        parts.append(ellipse(x, y, 5, 4, "lemon_light"))
    for x, y in ((106, 150), (80, 152), (140, 158)):
        parts.append(ellipse(x, y, 4.5, 3.2, "meat"))
    parts += [
        group([path(leaf, "sponge"), line("M4 0L28 0", "crust_shade", 2)],
              "translate(100 112) rotate(14) scale(1 0.7)"),
        group([path(leaf, "sponge"), line("M4 0L28 0", "crust_shade", 2)],
              "translate(132 106) rotate(-24) scale(1 0.7)"),
        line("M86 106l14 4M156 122l14 4M146 96l12 3", "crust_shade", 3),
        ellipse(160, 108, 16, 5, "white", opacity=0.3, transform="rotate(-30 160 108)"),
    ]  # fmt: skip
    return svg(recipe["card"], *parts)


def _syrnik(cx, cy, rx=34) -> list[str]:
    """시르니키 한 장: 도톰한 패티. 옆면은 연하고 윗면 가운데가 노릇하다."""
    ry, height = rx * 0.6, 16
    side = d(
        "M", cx - rx, cy, "L", cx - rx, cy + height,
        "A", rx, ry, 0, 0, 0, cx + rx, cy + height, "L", cx + rx, cy, "Z",
    )  # fmt: skip
    return [
        path(side, "sponge", shaded=True),
        ellipse(cx, cy, rx, ry, "honey"),
        ellipse(cx, cy + 1, rx * 0.72, ry * 0.68, "crust"),
        ellipse(cx - rx * 0.2, cy - ry * 0.25, rx * 0.3, ry * 0.18, "sponge", opacity=0.7),
    ]


def _syrniki(recipe: dict) -> str:
    """시르니키: 도톰한 치즈 패티 셋 + 베리잼 + 사워크림 + 슈거파우더."""
    jam = [
        ellipse(0, 3, 26, 14, "berry", shaded=True),
        ellipse(0, 0, 26, 13, "berry"),
        ellipse(-8, -3, 8, 3.5, "white", opacity=0.45),
    ]
    parts = [
        plate(128, 166, 106),
        *_syrnik(92, 132),
        *_syrnik(162, 128),
        *_syrnik(126, 160, 36),
        group(jam, "translate(70 184)"),
        _berry_dot(60, 178, 6),
        _berry_dot(78, 186, 5.5),
        _sour_cream(186, 180, 18),
    ]
    for x, y in ((112, 152), (134, 166), (122, 170), (146, 156), (84, 128), (98, 136),
                 (156, 124), (170, 132), (140, 172)):  # fmt: skip
        parts.append(circle(x, y, 2, "white", opacity=0.9))
    return svg(recipe["card"], *parts)


def _oreshek(x, y, angle=0, scale=1.0) -> str:
    """오레쉬키 한 알: 호두 모양 과자 두 쪽을 캐러멜로 붙였다. 두 쪽은 볼록한 혹, 겉엔 주름."""
    wrinkles = d(
        "M", -15, -6, "q", 3, -5, 7, -1, "M", -14, 6, "q", 4, 4, 8, 0,
        "M", 6, -9, "q", 4, 0, 7, 5, "M", 7, 8, "q", 4, 0, 7, -5,
        "M", -4, -12, "q", -3, 4, 0, 7,
    )  # fmt: skip
    return group(
        [
            ellipse(0, 5, 22, 16, "crust", shaded=True),
            ellipse(-9, 0, 13, 15, "sponge"),
            ellipse(9, 0, 13, 15, "sponge"),
            ellipse(10, 2, 9, 11, "sponge", shaded=True, opacity=0.6),
            rect(-1.8, -15, 3.6, 30, "crust_shade", rx=1.8),
            line(wrinkles, "crust", 2.2),
            ellipse(-11, -7, 5, 3, "white", opacity=0.55),
        ],
        f"translate({x} {y}) rotate({angle}) scale({scale})",
    )


def _oreshki(recipe: dict) -> str:
    """오레쉬키: 호두 모양 과자를 수북이, 앞에 반쪽 하나는 캐러멜 속이 보인다."""
    half = [
        ellipse(0, 3, 22, 15, "crust", shaded=True),
        ellipse(0, 0, 22, 15, "sponge"),
        ellipse(0, 0, 16, 10, "crust_shade"),
        ellipse(0, -1, 14, 8, "honey"),
        ellipse(-4, -3, 5, 2.5, "white", opacity=0.45),
    ]
    parts = [plate(128, 164, 106)]
    for x, y, a in (
        (98, 126, -20), (146, 124, 16), (72, 154, 10), (122, 150, -8),
        (172, 152, 24), (96, 182, 14), (142, 182, -14),
    ):  # fmt: skip
        parts.append(_oreshek(x, y, a, 1.15))
    parts.append(group(half, "translate(188 186) rotate(-6) scale(1.1)"))
    return svg(recipe["card"], *parts)


def _compote(recipe: dict) -> str:
    """콤포트: 배 불룩한 유리 주전자에 말린 과일(살구·체리·사과)이 뜬 과일 음료 + 잔."""
    cx = 104
    jug = d(
        "M", cx - 30, 62, "C", cx - 34, 92, cx - 62, 108, cx - 62, 150,
        "C", cx - 62, 188, cx - 44, 208, cx, 208,
        "C", cx + 44, 208, cx + 62, 188, cx + 62, 150,
        "C", cx + 62, 108, cx + 34, 92, cx + 30, 62, "Z",
    )  # fmt: skip
    drink = d(
        "M", cx - 47, 102, "C", cx - 56, 116, cx - 57, 130, cx - 57, 150,
        "C", cx - 57, 184, cx - 41, 202, cx, 202,
        "C", cx + 41, 202, cx + 57, 184, cx + 57, 150,
        "C", cx + 57, 130, cx + 56, 116, cx + 47, 102, "Z",
    )  # fmt: skip
    glass_cx, g_top, g_bottom = 192, 150, 206
    cup = d(
        "M", glass_cx - 26, g_top, "L", glass_cx - 22, g_bottom,
        "Q", glass_cx, g_bottom + 6, glass_cx + 22, g_bottom, "L", glass_cx + 26, g_top, "Z",
    )  # fmt: skip
    cup_drink = d(
        "M", glass_cx - 24, g_top + 14, "L", glass_cx - 20, g_bottom - 3,
        "Q", glass_cx, g_bottom + 2, glass_cx + 20, g_bottom - 3, "L", glass_cx + 24, g_top + 14,
        "Z",
    )  # fmt: skip
    parts = [
        shadow(cx, 210, 62, 14),
        line(d("M", cx + 34, 76, "c", 44, 0, 50, 60, 18, 84), "glass", 9, opacity=0.6),
        path(jug, "glass", opacity=0.5),
        path(drink, "compote"),
        ellipse(cx, 102, 47, 11, "compote_light"),
    ]
    for x, y, r in ((-26, 140, 10), (22, 170, 11), (-10, 184, 9), (30, 126, 9)):
        parts.append(circle(cx + x, y, r, "apricot"))
        parts.append(circle(cx + x + r * 0.2, y + r * 0.1, r * 0.4, "honey"))
    for x, y in ((6, 140), (-34, 172), (12, 116), (40, 152), (-14, 160)):
        parts.append(_berry_dot(cx + x, y, 6))
    for x, y, a in ((-6, 126, -20), (-30, 118, 30), (8, 190, 10)):
        parts.append(
            path("M-10 0A10 6 0 0 0 10 0A12 9 0 0 1 -10 0Z", "cream",
                 transform=f"translate({cx + x} {y}) rotate({a})")
        )  # fmt: skip
    parts += [
        path(d("M", cx - 52, 112, "C", cx - 58, 140, cx - 54, 170, cx - 40, 190), "white",
             opacity=0.35),
        ellipse(cx, 62, 30, 8, "glass", opacity=0.7),
        polygon([(cx - 26, 58), (cx - 44, 50), (cx - 30, 66)], "glass", opacity=0.7),
        shadow(glass_cx, 210, 30, 8),
        path(cup, "glass", opacity=0.5),
        path(cup_drink, "compote"),
        ellipse(glass_cx, g_top + 14, 24, 6, "compote_light"),
        _berry_dot(glass_cx - 6, g_top + 30, 5),
        circle(glass_cx + 8, g_top + 40, 7, "apricot"),
        ellipse(glass_cx, g_top, 26, 6, "glass", opacity=0.6),
    ]  # fmt: skip
    return svg(recipe["card"], *parts)


def _russian_tea(recipe: dict) -> str:
    """러시아 홍차: 놋쇠 사모바르 옆에 은 컵 받침(포드스타칸니크)에 끼운 유리잔 홍차와 잼."""
    sx = 90  # 사모바르 가운데
    urn = d(
        "M", sx - 28, 96, "C", sx - 52, 108, sx - 52, 164, sx - 32, 184,
        "L", sx + 32, 184, "C", sx + 52, 164, sx + 52, 108, sx + 28, 96, "Z",
    )  # fmt: skip
    urn_right = d(
        "M", sx + 8, 96, "L", sx + 28, 96, "C", sx + 52, 108, sx + 52, 164, sx + 32, 184,
        "L", sx + 8, 184, "Z",
    )  # fmt: skip
    teapot = [
        line("M-16 -2c-10 -2 -14 -10 -16 -14", "porcelain", 5),
        line("M16 -4c8 0 10 10 2 12", "porcelain_shade", 4),
        ellipse(0, 0, 18, 13, "porcelain"),
        path("M0 -13A18 13 0 0 1 18 0A18 13 0 0 1 0 13Z", "porcelain", shaded=True),
        ellipse(0, -12, 9, 3.5, "berry"),
        circle(0, -16, 3, "berry", shaded=True),
        ellipse(0, 1, 18, 3, "berry", opacity=0.8),
    ]
    gx, g_top, g_bottom = 186, 108, 186
    glass = d(
        "M", gx - 22, g_top, "L", gx - 19, g_bottom, "L", gx + 19, g_bottom,
        "L", gx + 22, g_top, "Z",
    )  # fmt: skip
    holder = d(
        "M", gx - 21, 142, "L", gx - 20, g_bottom, "C", gx - 16, g_bottom + 8,
        gx + 16, g_bottom + 8, gx + 20, g_bottom, "L", gx + 21, 142, "Z",
    )  # fmt: skip
    holder_right = d(
        "M", gx + 4, 142, "L", gx + 21, 142, "L", gx + 20, g_bottom,
        "C", gx + 18, g_bottom + 5, gx + 10, g_bottom + 6, gx + 4, g_bottom + 6, "Z",
    )  # fmt: skip
    zigzag = d("M", gx - 12, 166, "l", 6, -8, "l", 6, 8, "l", 6, -8, "l", 6, 8)
    tea = [(gx - 20, g_top + 10), (gx + 20, g_top + 10), (gx + 18, 170), (gx - 18, 170)]
    foot = [(sx - 34, 202), (sx - 20, 184), (sx + 20, 184), (sx + 34, 202)]
    jam = [
        ellipse(0, 4, 24, 11, "porcelain", shaded=True),
        ellipse(0, 0, 24, 10, "porcelain"),
        ellipse(0, 1, 18, 7, "berry"),
        ellipse(-5, -1, 5, 2, "white", opacity=0.45),
    ]
    return svg(
        recipe["card"],
        shadow(sx, 204, 56, 12),
        line(d("M", sx - 44, 118, "c", -14, 0, -14, 18, -4, 22), "brass_shade", 5),
        line(d("M", sx + 44, 118, "c", 14, 0, 14, 18, 4, 22), "brass_shade", 5),
        polygon(foot, "brass", shaded=True),
        ellipse(sx, 202, 34, 6, "brass", shaded=True),
        path(urn, "brass"),
        path(urn_right, "brass", shaded=True),
        ellipse(sx - 22, 140, 6, 30, "white", opacity=0.35),
        ellipse(sx, 96, 28, 8, "brass", shaded=True),
        rect(sx - 12, 80, 24, 16, "brass"),
        ellipse(sx, 80, 12, 4, "brass", shaded=True),
        group(teapot, f"translate({sx} 64)"),
        rect(sx - 4, 150, 8, 14, "brass_shade", rx=3),
        line(d("M", sx, 162, "l", 0, 8), "brass_shade", 4),
        circle(sx, 152, 4, "brass"),
        shadow(gx, g_bottom + 10, 32, 8),
        line(d("M", gx + 20, 148, "c", 18, 0, 18, 28, 0, 30), "silver_shade", 7),
        path(glass, "glass", opacity=0.5),
        polygon(tea, "tea"),
        ellipse(gx, g_top + 10, 20, 5, "tea_light"),
        ellipse(gx + 4, g_top + 9, 8, 3, "lemon"),
        line(d("M", gx + 12, g_top + 8, "L", gx + 18, g_top - 30), "silver_shade", 3),
        path(holder, "silver"),
        path(holder_right, "silver", shaded=True),
        line(d("M", gx - 20, 150, "L", gx + 20, 150), "silver_shade", 2.5),
        line(zigzag, "white", 2, opacity=0.7),
        ellipse(gx, g_top, 22, 5, "glass", opacity=0.6),
        shadow(150, 222, 26, 6),
        group(jam, "translate(148 216)"),
        citrus_wheel(220, 216, 12, "lemon", "lemon_light", segments=6),
    )


# ── 목록 ──────────────────────────────────────────────────────────────────────


def _dish(id, name, category, draw, card, keywords=(), aliases=()) -> Illustration:
    return Illustration(
        id=id,
        name=name,
        category=category,
        draw=draw,
        recipe={"dish": id.removeprefix("dish_"), "card": card},
        aliases=tuple(aliases),
        keywords=tuple(keywords),
    )


ILLUSTRATIONS = [
    _dish(
        "dish_blini", "블린", "savory", _blini, "card_sky",
        keywords=("블린", "블리니", "러시아 팬케이크"),
    ),
    _dish(
        "dish_pelmeni", "펠메니", "savory", _pelmeni, "card_peach",
        keywords=("펠메니", "뻴메니", "러시아 만두"),
    ),
    _dish(
        "dish_borscht", "보르시", "soup", _borscht, "card_mint",
        keywords=("보르시", "보르쉬", "보르스치", "비트 수프"),
    ),
    _dish(
        "dish_pirozhki", "피로시키", "bakery", _pirozhki, "card_cream",
        keywords=("피로시키", "피로즈키", "삐로시키", "피로쉬키", "러시아 고로케"),
    ),
    _dish(
        "dish_pirog_potato", "감자 피로그", "bakery", _pirog, "card_sand",
        keywords=("피로그", "삐로그"),
    ),
    _dish(
        "dish_syrniki", "시르니키", "cake", _syrniki, "card_rose",
        keywords=("시르니키", "시르니크", "쉬르니키", "치즈 팬케이크"),
    ),
    _dish(
        "dish_oreshki", "오레쉬키", "bakery", _oreshki, "card_mint",
        keywords=("오레쉬키", "오레시키", "오레슈키"),
    ),
    _dish(
        "dish_compote", "콤포트", "ade", _compote, "card_peach",
        keywords=("콤포트", "콤폿", "꼼뽀뜨"),
        aliases=("과일 음료",),
    ),
    _dish(
        "dish_russian_tea", "러시아 홍차", "tea", _russian_tea, "card_lilac",
        keywords=("러시아 홍차", "러시안 홍차", "러시안 티", "러시아 차", "사모바르"),
        aliases=("차이",),
    ),
]  # fmt: skip
