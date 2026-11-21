"""빵·과자 그림(티켓 07): 데모 가게엔 없지만 손님·심사위원이 넣을 법한 빵·구움과자.

크루아상·바게트·마카롱·쿠키처럼 모양이 저마다 달라 그림마다 함수 하나로 그린다(티켓 04 방식).
브라우니처럼 케이크 조각 모양으로 그릴 수 있는 것은 cakes.cake(...)를 그대로 쓴다. 크렘 브륄레처럼
분류는 케이크지만 조각 모양이 아닌 디저트도 함수로 그려 여기에 둔다.
러시아·조지아 카페에 흔한 하차푸리·바트루시카·프랴니크도 여기에 둔다.

키워드는 그 빵 이름(과 흔한 다른 표기)만 둔다. "빵"처럼 넓은 말은 넣지 않는다: 이름 끝말로
빵·과자 기본 그림을 고르는 일은 매처의 마지막 단계가 한다.
"""

import math

from agenthan.illustration.art import cakes
from agenthan.illustration.art.style import (
    TILT,
    Illustration,
    circle,
    cylinder,
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

FAMILY = "bakery"


# ── 가족 전용 도형 ─────────────────────────────────────────────────────────────


def _board(cx, cy, w, h, angle) -> str:
    """나무 도마: 두께가 보이는 둥근 네모 + 손잡이 구멍."""
    return group(
        [
            rect(-w / 2, -h / 2 + 7, w, h, "nut", rx=16, shaded=True),
            rect(-w / 2, -h / 2, w, h, "nut", rx=16),
            rect(-w / 2 + 10, -h / 2 + 8, w - 20, 5, "almond", rx=2.5, opacity=0.35),
            circle(w / 2 - 18, 0, 5, "nut", shaded=True),
        ],
        f"translate({cx} {cy}) rotate({angle})",
    )


def _torus(cx, cy, rx, body, top, *, hole=0.32, height=12) -> list[str]:
    """도넛·베이글 몸통: 옆면 그늘 + 윗면 + 가운데 구멍(접시 색)."""
    ry = rx * 0.62
    return [
        shadow(cx, cy + height + 4, rx * 1.02, ry * 0.7),
        ellipse(cx, cy + height * 0.6, rx, ry, body, shaded=True),
        ellipse(cx, cy, rx, ry, top),
        ellipse(cx, cy + 2, rx * hole, ry * hole, body, shaded=True),
        ellipse(cx, cy + 4, rx * hole * 0.9, ry * hole * 0.7, "porcelain_shade"),
    ]


# ── 빵 ────────────────────────────────────────────────────────────────────────


def _croissant_shape(x, y, angle, scale=1.0, glaze="honey") -> str:
    """크루아상 한 개: 가운데가 두툼하고 양 끝으로 가늘어지는 마디가 초승달처럼 굽는다."""
    segments = [  # (x, y, rx, ry, 기울기) 바깥 끝부터 가운데로. 마디는 결 방향으로 길쭉하다
        (-76, 38, 7, 11, -62), (76, 38, 7, 11, 62),
        (-62, 20, 12, 18, -48), (62, 20, 12, 18, 48),
        (-42, 4, 16, 25, -30), (42, 4, 16, 25, 30),
        (-18, -4, 18, 30, -12), (18, -4, 18, 30, 12),
        (0, -6, 19, 32, 0),
    ]  # fmt: skip
    parts = []
    for sx, sy, rx, ry, turn in segments:
        spin = f"rotate({turn} {sx} {sy})"
        parts += [
            ellipse(sx, sy + 5, rx, ry, "crust", shaded=True, transform=spin),
            ellipse(sx, sy, rx, ry, "crust", transform=spin),
            ellipse(sx - rx * 0.1, sy - ry * 0.3, rx * 0.55, ry * 0.5, glaze, transform=spin),
        ]
    parts.append(ellipse(-4, -22, 5, 9, "white", opacity=0.4))
    return group(parts, f"translate({x} {y}) rotate({angle}) scale({scale})")


def _croissant(recipe: dict) -> str:
    return svg(
        recipe["card"],
        plate(128, 164, 106),
        _croissant_shape(128, 140, -6, 1.2),
    )


def _baguette(recipe: dict) -> str:
    """바게트: 도마 위 긴 빵(칼집 다섯) + 썰어 놓은 두 조각."""
    loaf = [
        rect(-104, -14, 208, 40, "crust", rx=20, shaded=True),
        rect(-104, -20, 208, 38, "crust", rx=19),
        *(
            ellipse(x, -2, 15, 5, "sponge", transform=f"rotate(-20 {x} -2)")
            for x in (-66, -30, 6, 42, 76)
        ),
        rect(-90, -16, 150, 4, "white", rx=2, opacity=0.3),
    ]
    slice_ = [
        ellipse(0, 4, 22, 16, "crust", shaded=True),
        ellipse(0, 0, 22, 16, "crust"),
        ellipse(0, 0, 18, 12.5, "almond"),
        *(
            ellipse(x, y, 2.4, 1.6, "sponge")
            for x, y in ((-7, -3), (5, -5), (2, 4), (-4, 5), (9, 1))
        ),
    ]
    return svg(
        recipe["card"],
        shadow(128, 186, 104, 26),
        _board(128, 160, 196, 104, -18),
        group(loaf, "translate(126 140) rotate(-26) scale(0.92)"),
        group(slice_, "translate(170 194)"),
        group(slice_, "translate(206 180) rotate(12)"),
    )


def _salt_bread(recipe: dict) -> str:
    """소금빵: 버터로 반질한 통통한 롤빵 셋, 위에 굵은 소금."""

    def roll(x, y, angle, scale=1.0) -> str:
        ridges = d("M", -18, -16, "Q", -24, 0, -16, 16, "M", 6, -19, "Q", 0, 0, 8, 19)
        salt = [
            rect(sx - 2, sy - 2, 4, 4, "white", transform=f"rotate(30 {sx} {sy})")
            for sx, sy in ((-26, -6), (-6, -12), (14, -8), (28, -2), (-2, 2), (20, 6))
        ]
        return group(
            [
                ellipse(0, 6, 44, 24, "crust", shaded=True),
                ellipse(0, 0, 44, 22, "crust"),
                ellipse(-4, -6, 34, 12, "honey", opacity=0.7),
                line(ridges, "crust_shade", 3),
                ellipse(-18, -12, 10, 3.5, "white", opacity=0.5),
                *salt,
            ],
            f"translate({x} {y}) rotate({angle}) scale({scale})",
        )

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        roll(98, 132, -12),
        roll(166, 140, 10),
        roll(128, 180, -2, 1.05),
    )


def _bagel(recipe: dict) -> str:
    """베이글: 참깨 베이글 하나 + 반 갈라 크림치즈 바른 하나."""
    seeds = [
        ellipse(x, y, 2.6, 1.4, "meringue", transform=f"rotate({(i * 53) % 180} {x} {y})")
        for i, (x, y) in enumerate(
            ((96, 108), (116, 100), (140, 102), (158, 112), (164, 128), (150, 142),
             (126, 148), (102, 144), (88, 128), (108, 120), (146, 124), (132, 110))
        )
    ]  # fmt: skip
    half = [
        ellipse(0, 6, 40, 24, "crust", shaded=True),
        ellipse(0, 0, 40, 24, "sponge"),
        ellipse(0, 0, 34, 19, "meringue"),
        ellipse(0, 1, 12, 7, "porcelain_shade"),
        ellipse(-12, -6, 8, 3, "white", opacity=0.7),
    ]
    return svg(
        recipe["card"],
        plate(128, 160, 106),
        *_torus(126, 124, 62, "crust", "crust", hole=0.3, height=16),
        ellipse(110, 110, 26, 9, "honey", opacity=0.5),
        *seeds,
        group(half, "translate(176 190)"),
    )


def _donut(recipe: dict) -> str:
    """도넛: 분홍 글레이즈 + 스프링클, 뒤에 초코 글레이즈 하나."""

    def donut(cx, cy, rx, glaze, sprinkles) -> list[str]:
        ry = rx * 0.62
        out = _torus(cx, cy, rx, "crust", "sponge", height=12)
        wave = []
        for i in range(25):
            a = 2 * math.pi * i / 24
            r = 0.88 + (0.06 if i % 2 else 0)
            wave.append((cx + rx * r * math.cos(a), cy - 2 + ry * r * math.sin(a)))
        out += [
            polygon(wave, glaze),
            ellipse(cx, cy + 2, rx * 0.36, ry * 0.36, glaze, shaded=True),
            ellipse(cx, cy + 4, rx * 0.29, ry * 0.22, "porcelain_shade"),
            ellipse(cx - rx * 0.45, cy - ry * 0.45, rx * 0.2, ry * 0.12, "white", opacity=0.45),
        ]
        colors = ("lemon", "white", "leaf", "cup_blue", "berry")
        for i in range(sprinkles):
            a = i * 2.39996
            r = 0.5 + (i * 0.37 % 0.3)
            x, y = cx + rx * r * math.cos(a), cy - 2 + ry * r * math.sin(a)
            out.append(
                rect(x - 3.5, y - 1.2, 7, 2.4, colors[i % 5], rx=1.2,
                     transform=f"rotate({(i * 61) % 180} {x:.1f} {y:.1f})")
            )  # fmt: skip
        return out

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        *donut(162, 122, 48, "choco", 0),
        *donut(112, 158, 60, "berry_cream", 22),
    )


def _cinnamon_roll(recipe: dict) -> str:
    """시나몬롤: 위에서 보이는 소용돌이 + 흘러내린 아이싱."""
    cx, cy, rx = 126, 128, 70
    ry = rx * 0.62
    spiral = ["M", cx, cy]
    for i in range(1, 40):
        t = i * 0.45
        reach = t / 17.5
        spiral += ["L", cx + rx * 0.86 * reach * math.cos(t), cy + ry * 0.86 * reach * math.sin(t)]
    icing = d(
        "M", cx - 46, cy - 18, "Q", cx - 10, cy - 34, cx + 30, cy - 22,
        "M", cx - 52, cy + 4, "Q", cx, cy - 12, cx + 50, cy + 2,
        "M", cx - 38, cy + 24, "Q", cx + 6, cy + 10, cx + 44, cy + 22,
    )  # fmt: skip
    return svg(
        recipe["card"],
        plate(128, 162, 106),
        ellipse(cx, cy + 20, rx, ry, "crust", shaded=True),
        rect(cx - rx, cy, rx * 2, 20, "crust", shaded=True),
        ellipse(cx, cy, rx, ry, "sponge"),
        line(d(*spiral), "caramel", 7),
        line(icing, "meringue", 6),
        line(d("M", cx - 52, cy + 4, "q", -4, 14, 0, 26), "meringue", 6),
        line(d("M", cx + 44, cy + 22, "q", 4, 10, 0, 20), "meringue", 6),
    )


def _muffin(recipe: dict) -> str:
    """블루베리 머핀: 주름진 종이컵 + 부푼 윗면 + 블루베리."""
    cx, top, bottom = 126, 124, 196
    liner = polygon([(cx - 56, top), (cx + 56, top), (cx + 42, bottom), (cx - 42, bottom)],
                    "cup_blue")  # fmt: skip
    pleats = [
        line(d("M", cx + x, top + 4, "L", cx + x * 0.76, bottom - 2), "cup_blue", 3, opacity=0.6)
        for x in (-44, -30, -16, -2, 12, 26, 40)
    ]
    berries = [
        part
        for x, y in ((100, 96), (138, 84), (156, 110), (116, 118), (84, 120), (128, 104))
        for part in (circle(x, y, 7, "blueberry"), circle(x - 2, y - 2, 2, "white", opacity=0.5))
    ]
    return svg(
        recipe["card"],
        shadow(cx, bottom + 4, 62, 14),
        liner,
        polygon([(cx, top), (cx + 56, top), (cx + 42, bottom), (cx, bottom)], "cup_blue",
                shaded=True),
        *pleats,
        ellipse(cx, top + 6, 66, 24, "crust", shaded=True),
        path(d("M", cx - 68, top + 2, "C", cx - 70, 50, cx + 70, 50, cx + 68, top + 2,
               "Q", cx, top + 22, cx - 68, top + 2, "Z"), "crust"),
        ellipse(cx - 24, 84, 22, 10, "honey", opacity=0.55, transform=f"rotate(-14 {cx - 24} 84)"),
        *berries,
        ellipse(cx - 30, 78, 8, 4, "white", opacity=0.4),
    )  # fmt: skip


def _cookie(recipe: dict) -> str:
    """초코칩 쿠키 둘 + 초코 쿠키 하나(하얀 칩), 하나는 반으로 쪼갰다."""

    def cookie(cx, cy, r, dough, chip) -> list[str]:
        ry = r * 0.62
        out = [
            ellipse(cx, cy + 6, r, ry, dough, shaded=True),
            ellipse(cx, cy, r, ry, dough),
            ellipse(cx - r * 0.2, cy - ry * 0.2, r * 0.55, ry * 0.45, "white", opacity=0.12),
        ]
        for i in range(7):
            a = i * 2.39996 + 0.4
            k = math.sqrt((i + 0.6) / 7) * 0.72
            x, y = cx + r * k * math.cos(a), cy + ry * k * math.sin(a)
            out.append(ellipse(x, y, 5, 3.6, chip, transform=f"rotate({i * 40} {x:.1f} {y:.1f})"))
        out.append(
            line(d("M", cx - r * 0.5, cy + 2, "q", 8, -6, 16, 2, "t", 14, 0), dough, 2.4,
                 opacity=0.5)
        )  # fmt: skip
        return out

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        *cookie(92, 136, 46, "sponge", "choco"),
        *cookie(166, 128, 42, "cocoa", "meringue"),
        *cookie(128, 178, 48, "crust", "choco_shade"),
    )


def _macaron(recipe: dict) -> str:
    """마카롱 다섯: 색이 다른 껍질 두 장 사이에 크림."""

    def macaron(x, y, color, filling="cream") -> list[str]:
        return [
            ellipse(x, y + 16, 30, 15, color, shaded=True),
            ellipse(x, y + 11, 29, 14, color),
            ellipse(x, y + 6, 27, 13, filling, shaded=True),
            ellipse(x, y + 4, 27, 13, filling),
            ellipse(x, y + 1, 30, 15, color, shaded=True),
            ellipse(x, y - 3, 30, 14.5, color),
            ellipse(x - 9, y - 7, 10, 4, "white", opacity=0.4),
        ]

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        *macaron(96, 112, "matcha_light"),
        *macaron(160, 112, "custard"),
        *macaron(68, 150, "berry_cream"),
        *macaron(128, 150, "cup_blue"),
        *macaron(188, 150, "cocoa", "choco"),
    )


def _waffle(recipe: dict) -> str:
    """와플: 네모 격자 와플 위에 생크림과 딸기, 시럽."""
    origin, u, v = (40, 128), (88, -44), (88, 44)  # 왼쪽 꼭짓점, 위로·아래로 뻗는 모서리
    n = 4

    def at(a: float, b: float) -> tuple[float, float]:
        return origin[0] + u[0] * a + v[0] * b, origin[1] + u[1] * a + v[1] * b

    thick = 18
    corners = [at(0, 0), at(1, 0), at(1, 1), at(0, 1)]

    def down(point: tuple[float, float]) -> tuple[float, float]:
        return point[0], point[1] + thick

    left_face = [at(0, 0), at(0, 1), down(at(0, 1)), down(at(0, 0))]
    right_face = [at(0, 1), at(1, 1), down(at(1, 1)), down(at(0, 1))]
    parts = [
        plate(128, 168, 106),
        polygon(left_face, "crust", shaded=True),
        polygon(right_face, "crust"),
        polygon(corners, "sponge"),
    ]
    gap = 0.16
    for i in range(n):
        for j in range(n):
            a0, b0 = (i + gap) / n, (j + gap) / n
            a1, b1 = (i + 1 - gap) / n, (j + 1 - gap) / n
            parts.append(polygon([at(a0, b0), at(a1, b0), at(a1, b1), at(a0, b1)], "crust"))
    cream = [
        ellipse(0, 6, 30, 14, "cream", shaded=True),
        ellipse(0, 2, 28, 12, "cream"),
        ellipse(0, -6, 19, 9, "cream", shaded=True),
        ellipse(0, -8, 17, 8, "cream"),
        ellipse(0, -14, 8, 5, "cream"),
    ]
    berry = "M0 -9C9 -9 11 2 0 12C-11 2 -9 -9 0 -9Z"
    parts += [
        line(d("M", 70, 128, "Q", 110, 100, 146, 126, "T", 214, 126), "caramel", 5),
        group(cream, "translate(128 120) scale(1.3)"),
        group([path(berry, "berry"), ellipse(0, -9, 6, 2.5, "leaf")],
              "translate(170 136) rotate(20) scale(1.3)"),
        group([path(berry, "berry"), ellipse(0, -9, 6, 2.5, "leaf")],
              "translate(92 138) rotate(-18) scale(1.3)"),
    ]  # fmt: skip
    return svg(recipe["card"], *parts)


def _madeleine(recipe: dict) -> str:
    """마들렌 셋: 조개 모양, 결이 부채꼴로 퍼진다."""

    def shell(x, y, angle) -> str:
        body = "M0 -34C26 -34 34 4 0 30C-34 4 -26 -34 0 -34Z"
        ridges = d(*[
            part
            for k in (-0.7, -0.35, 0, 0.35, 0.7)
            for part in ("M", 0, 24, "L", k * 26, -24 - (1 - abs(k)) * 6)
        ])  # fmt: skip
        return group(
            [
                path(body, "crust", shaded=True, transform="translate(0 5)"),
                path(body, "sponge"),
                path("M0 -34C26 -34 34 4 0 30Z", "crust", opacity=0.45),
                line(ridges, "crust", 2.6, opacity=0.8),
                ellipse(-8, -16, 6, 3, "white", opacity=0.45),
            ],
            f"translate({x} {y}) rotate({angle}) scale(1.4 1.05)",
        )

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        shell(84, 136, -70),
        shell(168, 128, 64),
        shell(128, 180, -84),
    )


def _pretzel(recipe: dict) -> str:
    """프레첼: 두 팔을 꼬아 붙인 매듭 빵 + 굵은 소금."""
    knot = d(
        "M", -46, 30, "C", -86, 10, -74, -48, -26, -44,
        "C", 8, -40, 16, 4, 0, 20,
        "C", -16, 4, -8, -40, 26, -44,
        "C", 74, -48, 86, 10, 46, 30,
        "M", -46, 30, "C", -26, 14, 14, -4, 30, -18,
        "M", 46, 30, "C", 26, 14, -14, -4, -30, -18,
    )  # fmt: skip
    salt = [
        rect(x - 2.2, y - 2.2, 4.4, 4.4, "white", transform=f"rotate(25 {x} {y})")
        for x, y in ((-60, -12), (-40, -40), (-14, -30), (14, -30), (40, -40), (62, -10),
                     (-30, 22), (30, 22), (0, 4), (-54, 14), (54, 16))
    ]  # fmt: skip
    body = [
        line(knot, "crust_shade", 22),
        group([line(knot, "crust", 18)], "translate(0 -3)"),
        group([line(knot, "honey", 5, opacity=0.5)], "translate(-2 -8)"),
        *salt,
    ]
    return svg(
        recipe["card"],
        plate(128, 164, 106),
        group(body, "translate(128 140) scale(1.2)"),
    )


def _scone(recipe: dict) -> str:
    """스콘 둘(갈라진 윗면, 건포도) + 잼과 클로티드 크림."""

    def scone(x, y, w) -> list[str]:
        h = w * 0.62
        cracks = d("M", x - w * 0.4, y - 2, "q", w * 0.2, -8, w * 0.4, 0,
                   "M", x + w * 0.05, y + h * 0.25, "q", w * 0.15, -6, w * 0.32, 2)  # fmt: skip
        return [
            shadow(x, y + 30, w * 1.05, 10),
            rect(x - w, y - 4, w * 2, 30, "crust", rx=14, shaded=True),
            ellipse(x, y + 24, w, h * 0.5, "crust", shaded=True),
            ellipse(x, y - 4, w, h * 0.62, "sponge"),
            ellipse(x - w * 0.2, y - 8, w * 0.6, h * 0.35, "crust", opacity=0.55),
            line(cracks, "crust_shade", 3),
            circle(x + w * 0.35, y - 10, 3.2, "choco"),
            circle(x - w * 0.5, y + 6, 3, "choco"),
            circle(x + w * 0.05, y + 10, 3, "choco"),
        ]

    jam = [
        ellipse(0, 6, 22, 11, "porcelain", shaded=True),
        ellipse(0, 0, 22, 11, "porcelain"),
        ellipse(0, 1, 17, 8, "berry"),
        ellipse(-5, -1, 5, 2, "white", opacity=0.5),
    ]
    return svg(
        recipe["card"],
        plate(128, 164, 106),
        *scone(104, 128, 44),
        *scone(150, 160, 46),
        group(jam, "translate(196 120)"),
        ellipse(70, 180, 18, 9, "cream", shaded=True),
        ellipse(70, 176, 16, 8, "meringue"),
    )


def _financier(recipe: dict) -> str:
    """휘낭시에: 금괴 모양 구움과자 셋, 윗면에 아몬드 슬라이스."""

    def bar(x, y, angle) -> str:
        top = [(-30, -12), (30, -12), (26, 10), (-26, 10)]
        front = [(-26, 10), (26, 10), (32, 26), (-32, 26)]
        right = [(30, -12), (36, 6), (32, 26), (26, 10)]
        return group(
            [
                polygon(front, "crust", shaded=True),
                polygon(right, "crust", shaded=True),
                polygon(top, "honey"),
                ellipse(-8, -4, 14, 4, "white", opacity=0.3),
                ellipse(10, -2, 6, 2.6, "almond", transform="rotate(-20 10 -2)"),
                ellipse(-4, 4, 6, 2.6, "almond", transform="rotate(30 -4 4)"),
            ],
            f"translate({x} {y}) rotate({angle}) scale(1.3)",
        )

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        bar(94, 124, -10),
        bar(168, 130, 12),
        bar(126, 172, -4),
    )


def _creme_brulee(recipe: dict) -> str:
    """크렘 브륄레: 주름진 흰 라메킨에 캐러멜로 그을린 설탕 윗면, 베리 하나."""
    cx, top, rx, height = 128, 118, 74, 48
    ry = rx * TILT
    flutes = [
        line(d("M", cx + rx * math.cos(a), top + ry * math.sin(a) + 4,
               "l", 0, height - 6), "porcelain_dark", 3, opacity=0.6)
        for a in (math.pi * i / 10 for i in range(1, 10))
    ]  # fmt: skip
    crackle = d("M", cx - 30, top - 6, "l", 18, 8, "l", 14, -10, "l", 20, 12,
                "M", cx - 10, top + 2, "l", 6, 12, "M", cx + 22, top + 4, "l", -4, 10)  # fmt: skip
    return svg(
        recipe["card"],
        plate(cx, top + height + 4, 104),
        cylinder(cx, top, rx, height, "porcelain", "porcelain"),
        *flutes,
        ellipse(cx, top + 3, rx - 8, ry - 6, "caramel"),
        ellipse(cx - 12, top, rx * 0.5, ry * 0.35, "custard", opacity=0.45),
        line(crackle, "burnt", 2, opacity=0.5),
        circle(cx + 26, top - 4, 9, "berry", shaded=True),
        circle(cx + 25, top - 6, 8.5, "berry"),
        ellipse(cx + 12, top - 6, 8, 3.6, "leaf", transform=f"rotate(-30 {cx + 12} {top - 6})"),
    )


# ── 러시아·조지아 카페 빵 ──────────────────────────────────────────────────────


def _khachapuri(recipe: dict) -> str:
    """하차푸리(아자르식): 배 모양 빵에 녹은 치즈, 가운데 노른자와 버터."""
    boat = "M-100 0C-60 -52 60 -52 100 0C60 52 -60 52 -100 0Z"
    well = "M-66 0C-40 -28 40 -28 66 0C40 28 -40 28 -66 0Z"
    body = [
        path(boat, "crust", shaded=True, transform="translate(0 10)"),
        path(boat, "crust"),
        path(well, "crust_shade"),
        path(well, "cheese", transform="translate(0 -2) scale(0.94 0.86)"),
        ellipse(-30, -4, 14, 5, "white", opacity=0.35),
        circle(10, -2, 17, "white"),
        circle(10, -2, 11, "apricot"),
        circle(6, -6, 3.5, "white", opacity=0.6),
        rect(30, -12, 18, 14, "custard", rx=3, transform="rotate(-12 39 -5)"),
        ellipse(-70, -14, 16, 5, "honey", opacity=0.5, transform="rotate(-25 -70 -14)"),
    ]
    return svg(
        recipe["card"],
        shadow(128, 180, 104, 24),
        _board(128, 156, 210, 116, -8),
        group(body, "translate(124 146) rotate(-14) scale(0.96)"),
    )


def _vatrushka(recipe: dict) -> str:
    """바트루시카: 동그란 빵 가운데 하얀 트보로크(코티지치즈) 속, 건포도."""

    def bun(x, y, r) -> list[str]:
        ry = r * 0.62
        return [
            ellipse(x, y + 10, r, ry, "crust", shaded=True),
            ellipse(x, y, r, ry, "crust"),
            ellipse(x - r * 0.3, y - ry * 0.5, r * 0.4, ry * 0.25, "honey", opacity=0.6),
            ellipse(x, y + 2, r * 0.62, ry * 0.6, "crust_shade"),
            ellipse(x, y, r * 0.58, ry * 0.55, "meringue"),
            ellipse(x + r * 0.1, y + 2, r * 0.4, ry * 0.32, "meringue_shade", opacity=0.6),
            circle(x - r * 0.2, y - 2, 3, "choco"),
            circle(x + r * 0.22, y + 3, 3, "choco"),
            circle(x + r * 0.02, y - ry * 0.25, 2.6, "choco"),
        ]

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        *bun(92, 126, 44),
        *bun(166, 124, 42),
        *bun(128, 172, 50),
    )


def _pryanik(recipe: dict) -> str:
    """프랴니크: 하얀 설탕옷을 입힌 둥근 생강과자, 위에 무늬."""

    def piece(x, y, r) -> list[str]:
        ry = r * 0.62
        pattern = d(
            "M", x - r * 0.45, y, "Q", x, y - ry * 0.6, x + r * 0.45, y,
            "M", x - r * 0.45, y + ry * 0.25, "Q", x, y + ry * 0.75, x + r * 0.45, y + ry * 0.25,
        )  # fmt: skip
        return [
            ellipse(x, y + 9, r, ry, "nut", shaded=True),
            ellipse(x, y + 4, r, ry, "nut"),
            ellipse(x, y, r * 0.96, ry * 0.94, "meringue"),
            ellipse(x + r * 0.1, y + 2, r * 0.8, ry * 0.7, "meringue_shade", opacity=0.5),
            line(pattern, "honey", 2.6),
            circle(x, y + ry * 0.12, 3.2, "berry"),
        ]

    return svg(
        recipe["card"],
        plate(128, 164, 106),
        *piece(92, 128, 40),
        *piece(164, 124, 38),
        *piece(126, 174, 46),
    )


# ── 목록 ──────────────────────────────────────────────────────────────────────


def _bake(id, name, draw, card, keywords=(), aliases=(), category="bakery") -> Illustration:
    return Illustration(
        id=id,
        name=name,
        category=category,
        draw=draw,
        recipe={"bake": id.split("_", 1)[1], "card": card},
        aliases=tuple(aliases),
        keywords=tuple(keywords),
    )


ILLUSTRATIONS = [
    _bake(
        "bakery_croissant", "크루아상", _croissant, "card_cream",
        keywords=("크루아상", "크로와상", "크로아상", "크루와상", "croissant"),
        aliases=("Croissant",),
    ),
    _bake(
        "bakery_baguette", "바게트", _baguette, "card_sand",
        keywords=("바게트", "바게뜨", "baguette"),
    ),
    _bake(
        "bakery_salt_bread", "소금빵", _salt_bread, "card_sky",
        keywords=("소금빵", "시오빵", "시오팡"),
    ),
    _bake(
        "bakery_bagel", "베이글", _bagel, "card_mint",
        keywords=("베이글", "bagel"),
    ),
    _bake(
        "bakery_donut", "도넛", _donut, "card_sky",
        keywords=("도넛", "도너츠", "도나쓰", "donut", "doughnut"),
    ),
    _bake(
        "bakery_cinnamon_roll", "시나몬롤", _cinnamon_roll, "card_peach",
        keywords=("시나몬롤", "시나몬번", "cinnamonroll"),
    ),
    _bake(
        "bakery_muffin", "머핀", _muffin, "card_cream",
        keywords=("머핀", "muffin"),
    ),
    _bake(
        "bakery_cookie", "쿠키", _cookie, "card_sky",
        keywords=("쿠키", "cookie"),
        aliases=("초코칩 쿠키",),
    ),
    _bake(
        "bakery_macaron", "마카롱", _macaron, "card_lilac",
        keywords=("마카롱", "뚱카롱", "macaron"),
    ),
    _bake(
        "bakery_waffle", "와플", _waffle, "card_rose",
        keywords=("와플", "크로플", "waffle"),
    ),
    _bake(
        "bakery_madeleine", "마들렌", _madeleine, "card_sky",
        keywords=("마들렌", "madeleine"),
    ),
    _bake(
        "bakery_pretzel", "프레첼", _pretzel, "card_mint",
        keywords=("프레첼", "프렛젤", "pretzel"),
    ),
    _bake(
        "bakery_scone", "스콘", _scone, "card_rose",
        keywords=("스콘", "scone"),
    ),
    _bake(
        "bakery_financier", "휘낭시에", _financier, "card_lilac",
        keywords=("휘낭시에", "피낭시에", "financier"),
    ),
    _bake(
        "cake_creme_brulee", "크렘 브륄레", _creme_brulee, "card_sky",
        keywords=("크렘브륄레", "크림브륄레", "브륄레", "cremebrulee"),
        category="cake",
    ),
    _bake(
        "bakery_khachapuri", "하차푸리", _khachapuri, "card_peach",
        keywords=("하차푸리", "하차뿌리", "카차푸리", "khachapuri", "хачапури"),
    ),
    _bake(
        "bakery_vatrushka", "바트루시카", _vatrushka, "card_lilac",
        keywords=("바트루시카", "바뜨루시까", "바트루쉬카", "vatrushka", "ватрушка"),
    ),
    _bake(
        "bakery_pryanik", "프랴니크", _pryanik, "card_rose",
        keywords=("프랴니크", "프랴니키", "쁘랴니끼", "pryanik", "пряник"),
    ),
    cakes.cake(
        "bakery_brownie",
        "브라우니",
        {
            "shape": "bar",
            "height": 44,
            "layers": [["choco_shade", 0.12], ["choco", 1]],
            "top": "choco",
            "toppings": [["shine", "white"], ["walnuts", "nut"], ["dust", "cocoa"]],
            "card": "card_mint",
        },
        keywords=("브라우니", "brownie"),
        category="bakery",
    ),
]  # fmt: skip
