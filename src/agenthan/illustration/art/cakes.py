"""케이크·디저트 그림(티켓 02): 조각 모양 × 층 색 배열 × 위 장식을 조합한다.

레시피 하나가 그림 하나다:

    {"shape": "slice", "layers": [["honey", 1], ["cream", 0.5], ...], "top": "cream",
     "toppings": [["crumbs", "honey_shade"]], "card": "card_cream"}

- shape: 조각 모양. SHAPES의 함수가 접시·몸통을 그리고, 위 장식을 올릴 윗면 자리(Top)를 돌려준다.
  slice(삼각 조각) · whole(홀케이크, cut이면 한 조각 잘라 낸 단면) · bar(네모 조각) · roll(롤)
  · tart(타르트) · tartlets(작은 타르트 셋, scorch면 구운 자국) · nest(머랭 둥지)
  · eclair(에클레어 1~3개) · dome(반구)
- layers: 자른 면에 보이는 층, 위에서부터 [색 이름, 두께 비율]. wave가 있으면 층 경계가 물결.
- top: 윗면 색. toppings: 윗면 장식 [종류, 색 이름]을 차례로(TOPPINGS 참고).
- 그 밖에 모양마다 고르는 값(side, side_crumbs, drip, cut, glazes …)은 각 함수 설명에.

다른 가족(티켓 07의 티라미수·롤케이크 등)도 cake(...)와 draw를 그대로 써서 그림을 더할 수 있다.
"""

import math
from collections.abc import Callable, Iterator, Sequence
from typing import NamedTuple

from agenthan.illustration.art.style import (
    TILT,
    Illustration,
    circle,
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

FAMILY = "cakes"

Point = tuple[float, float]


class Top(NamedTuple):
    """위 장식을 올릴 윗면 자리: 가운데(cx, cy), 반지름(rx, ry). slope: 윗면이 기운 정도(롤)."""

    cx: float
    cy: float
    rx: float
    ry: float
    slope: float = 0.0

    def at(self, u: float, v: float) -> Point:
        """u, v ∈ [-1, 1] → 그림 좌표(왼쪽 −1, 뒤쪽 −1)."""
        return self.cx + self.rx * u, self.cy + self.ry * v + self.slope * self.rx * u

    def scatter(self, count: int, turn: float = 0.0) -> Iterator[Point]:
        """해바라기 씨 배열로 고르게 흩뿌린 자리(늘 같은 자리)."""
        for i in range(count):
            radius = math.sqrt((i + 0.5) / count)
            angle = i * 2.39996 + turn
            yield self.at(radius * math.cos(angle), radius * math.sin(angle))


# ── 자른 면(층) ────────────────────────────────────────────────────────────────


def _bands(layers: Sequence[Sequence], height: float) -> list[tuple[str, float, float]]:
    total = sum(weight for _, weight in layers) or 1
    bands, depth = [], 0.0
    for color, weight in layers:
        lower = depth + height * weight / total
        bands.append((color, depth, lower))
        depth = lower
    return bands


def _face(a: Point, b: Point, layers, height: float, *, shaded: bool, wave: float = 0) -> str:
    """세로 단면 하나: 윗변 a→b에서 height만큼 내려가며 층을 띠로 칠한다."""
    steps = 8 if wave else 1

    def edge(depth: float, index: int) -> list[Point]:
        out = []
        for i in range(steps + 1):
            t = i / steps
            y = a[1] + (b[1] - a[1]) * t + depth
            if wave and 0 < depth < height - 0.01:
                y += wave * math.sin(t * math.pi * 3 + index * 1.7)
            out.append((a[0] + (b[0] - a[0]) * t, y))
        return out

    return "".join(
        polygon(edge(upper, i) + edge(lower, i + 1)[::-1], color, shaded=shaded)
        for i, (color, upper, lower) in enumerate(_bands(layers, height))
    )


def _arc(cx, cy, rx, ry, start: float, end: float, count: int = 12) -> list[Point]:
    """타원 둘레의 점들(각도는 라디안, π/2가 앞쪽)."""
    return [
        (cx + rx * math.cos(a), cy + ry * math.sin(a))
        for a in (start + (end - start) * i / count for i in range(count + 1))
    ]


# ── 조각 모양 ──────────────────────────────────────────────────────────────────


def _slice(r: dict) -> tuple[list[str], Top]:
    """삼각 조각: 뾰족한 끝이 앞, 양쪽 자른 면에 층. height(층 높이), span(조각 각도), wave."""
    cx, apex_y, radius = 128, 160, 110
    height, span = r.get("height", 58), math.radians(r.get("span", 68))
    wave = r.get("wave", 0)

    def rim(angle: float) -> Point:
        return cx + radius * math.cos(angle), apex_y + radius * math.sin(angle) * TILT

    start = 1.5 * math.pi - span / 2
    apex = (cx, apex_y)
    arc = [rim(start + span * i / 12) for i in range(13)]
    parts = [
        plate(128, 172, 100),
        _face(apex, arc[0], r["layers"], height, shaded=True, wave=wave),
        _face(apex, arc[-1], r["layers"], height, shaded=False, wave=wave),
        polygon([apex, *arc], r["top"]),
    ]
    return parts, Top(cx, apex_y - radius * TILT * 0.66, radius * math.sin(span / 2) * 0.56, 12)


def _whole(r: dict) -> tuple[list[str], Top]:
    """홀케이크. rx·height(크기), side(옆면 색; 없으면 층이 띠로 보임), side_crumbs(옆면 부스러기
    색), drip(그 색 글레이즈가 윗가장자리에서 흘러내림), cut(앞쪽 한 조각을 잘라 낸 반각, 도)."""
    cx, top_y = 128, r.get("top_y", 112)
    rx, height = r.get("rx", 84), r.get("height", 56)
    ry = rx * TILT
    cut = math.radians(r.get("cut", 0))
    front = math.pi / 2
    pieces = [(0, front - cut), (front + cut, math.pi)] if cut else [(0, math.pi)]
    side = [[r["side"], 1]] if r.get("side") else r["layers"]
    parts = [plate(cx, top_y + height + 2, rx + 20)]
    for start, end in pieces:
        for color, upper, lower in _bands(side, height):
            rim = _arc(cx, top_y + upper, rx, ry, start, end)
            parts.append(
                polygon(rim + _arc(cx, top_y + lower, rx, ry, end, start), color, shaded=True)
            )
    if r.get("side_crumbs"):
        for i in range(30):
            angle = 0.12 + (i * 0.618034 % 1) * (math.pi - 0.24)
            if cut and abs(angle - front) < cut + 0.08:
                continue
            depth = (0.08 + (i * 0.754878 % 1) * 0.84) * height
            x, y = cx + rx * math.cos(angle), top_y + ry * math.sin(angle) + depth
            parts.append(circle(x, y, 1.8 + i % 3 * 0.5, r["side_crumbs"]))
    if r.get("drip") and not cut:
        rim = _arc(cx, top_y, rx, ry, 0, math.pi)
        parts.append(polygon(rim + _arc(cx, top_y + 7, rx, ry, math.pi, 0), r["drip"]))
        for i, angle in enumerate((0.3, 0.7, 1.1, 1.5, 1.95, 2.45, 2.85)):
            x, y = cx + rx * math.cos(angle), top_y + ry * math.sin(angle)
            parts.append(rect(x - 4.5, y, 9, 12 + (i * 7) % 15, r["drip"], rx=4.5))
    if cut:
        center = (cx, top_y)
        rim = _arc(cx, top_y, rx, ry, front + cut, front - cut + 2 * math.pi, 24)
        parts += [
            polygon([center, *rim], r["top"]),
            _face(center, rim[0], r["layers"], height, shaded=False),
            _face(center, rim[-1], r["layers"], height, shaded=True),
        ]
        return parts, Top(cx, top_y - ry * 0.3, rx * 0.62, ry * 0.45)
    parts.append(ellipse(cx, top_y, rx, ry, r["top"]))
    return parts, Top(cx, top_y, rx * 0.62, ry * 0.6)


def _bar(r: dict) -> tuple[list[str], Top]:
    """네모 조각(나폴레옹 같은 직육면체): 앞의 두 면에 층, 위는 top."""
    height = r.get("height", 60)
    top = (150, 198 - height)
    left, right = (top[0] - 84, top[1] - 34), (top[0] + 32, top[1] - 27)
    back = (left[0] + 32, left[1] - 27)
    parts = [
        plate(124, 176, 100),
        _face(top, left, r["layers"], height, shaded=True),
        _face(top, right, r["layers"], height, shaded=False),
        polygon([top, left, back, right], r["top"]),
    ]
    return parts, Top((top[0] + back[0]) / 2, (top[1] + back[1]) / 2, 30, 9, slope=0.4)


def _roll(r: dict) -> tuple[list[str], Top]:
    """롤: 옆으로 누운 통나무, 앞 단면에 소용돌이. outer(겉), inner(속 크림)."""
    (ex, ey), radius = (86, 154), 44
    bx, by = ex + 92, ey - 36
    face_rx = radius * 0.72
    outer, inner = r["outer"], r["inner"]
    spiral = ["M", ex, ey]
    for i in range(1, 30):
        t = i * 0.5
        reach = radius * 0.8 * t / 14.5
        spiral += ["L", ex + reach * math.cos(t) * 0.72, ey + reach * math.sin(t)]
    parts = [
        plate(128, 170, 104),
        ellipse(bx, by, face_rx, radius, outer, shaded=True),
        polygon(
            [(ex, ey - radius), (bx, by - radius), (bx, by + radius), (ex, ey + radius)],
            outer,
            shaded=True,
        ),
        polygon(
            [
                (ex, ey - radius),
                (bx, by - radius),
                (bx, by - radius * 0.2),
                (ex, ey - radius * 0.2),
            ],
            outer,
        ),
        ellipse(ex, ey, face_rx, radius, outer),
        ellipse(ex, ey, face_rx * 0.86, radius * 0.86, inner),
        line(d(*spiral), outer, 5),
    ]
    slope = (by - ey) / (bx - ex)
    return parts, Top((ex + bx) / 2 + 6, (ey + by) / 2 - radius + 2, 44, 5, slope=slope)


def _tart(r: dict) -> tuple[list[str], Top]:
    """타르트: 낮은 원판 크러스트에 주름 테두리, 속(filling) 위에 장식."""
    cx, top_y, rx, height = 128, 122, 88, 22
    parts = [plate(cx, top_y + height + 2, rx + 16)]
    parts += _tart_body(cx, top_y, rx, height, r.get("crust", "crust"), r["filling"])
    return parts, Top(cx, top_y + 2, rx * 0.58, rx * TILT * 0.55)


def _tartlets(r: dict) -> tuple[list[str], Top]:
    """작은 타르트 셋(에그타르트). scorch: 속 위 구운 자국 색. 장식은 앞의 하나에 올린다."""
    crust, filling = r.get("crust", "crust"), r["filling"]
    parts = [plate(128, 166, 106)]
    for cx, top_y in ((88, 118), (168, 118), (128, 160)):
        rx = 46
        parts += _tart_body(cx, top_y, rx, 16, crust, filling, rim=4.5, dents=6)
        if r.get("scorch"):
            for i, (u, v) in enumerate(((-0.3, -0.2), (0.25, 0.15), (0.05, -0.35), (-0.1, 0.3))):
                x, y = cx + u * rx * 0.6, top_y + 2 + v * rx * TILT * 0.6
                parts.append(ellipse(x, y, 6 - i % 2 * 2, 3.5 - i % 2, r["scorch"], opacity=0.7))
    return parts, Top(128, 162, 46 * 0.5, 46 * TILT * 0.45)


def _tart_body(cx, top_y, rx, height, crust, filling, *, rim=6, dents=12) -> list[str]:
    """타르트 몸통: 주름진 옆면, 크러스트 윗면, 속(filling), 테두리 구슬."""
    ry = rx * TILT
    k = rx / 88  # 큰 타르트(rx 88) 기준 비율
    parts = [
        polygon(
            _arc(cx, top_y, rx, ry, 0, math.pi) + _arc(cx, top_y + height, rx, ry, math.pi, 0),
            crust,
            shaded=True,
        ),
    ]
    for i in range(1, dents):
        angle = math.pi * i / dents
        x, y = cx + rx * math.cos(angle), top_y + ry * math.sin(angle)
        parts.append(line(d("M", x, y + 3, "L", x, y + height - 2), crust, 3, opacity=0.7))
    parts += [
        ellipse(cx, top_y, rx, ry, crust),
        ellipse(cx, top_y + 2, rx - 12 * k, ry - 9 * k, filling),
    ]
    beads = _arc(cx, top_y, rx - 4 * k, ry - 3 * k, 0, 2 * math.pi, 22 if k == 1 else 11)
    parts += [circle(x, y, rim, crust) for x, y in beads]
    return parts


def _nest(r: dict) -> tuple[list[str], Top]:
    """머랭 둥지(파블로바): 높은 머랭 받침, 물결 테두리, 위에 크림."""
    cx, top_y, rx, height = 128, 116, 80, 40
    ry = rx * TILT
    shell, filling = r.get("shell", "meringue"), r.get("filling", "cream")
    parts = [
        plate(cx, top_y + height + 2, rx + 20),
        polygon(
            _arc(cx, top_y, rx, ry, 0, math.pi)
            + _arc(cx, top_y + height, rx * 0.92, ry * 0.92, math.pi, 0),
            shell,
            shaded=True,
        ),
    ]
    for i in range(1, 9):
        angle = math.pi * i / 9
        x, y = cx + rx * math.cos(angle), top_y + ry * math.sin(angle)
        parts.append(line(d("M", x, y + 4, "L", x * 0.96 + cx * 0.04, y + height - 4), shell, 5))
    parts.append(ellipse(cx, top_y, rx, ry, shell))
    parts += [
        circle(x, y, 11, shell) for x, y in _arc(cx, top_y, rx - 4, ry - 3, 0, 2 * math.pi, 16)
    ]
    parts += [
        ellipse(cx, top_y + 2, rx - 20, ry - 14, filling, shaded=True),
        ellipse(cx, top_y - 1, rx - 24, ry - 17, filling),
    ]
    return parts, Top(cx, top_y - 2, rx * 0.5, ry * 0.42)


def _eclair(r: dict) -> tuple[list[str], Top]:
    """에클레어: glazes 개수만큼(1이면 큰 것 하나, 3이면 작은 것 셋). drizzle: 위에 줄무늬 색."""
    glazes = r["glazes"]
    if len(glazes) == 1:
        specs = [(128, 146, 176, 48)]
    else:
        specs = [(100, 116, 92, 32), (162, 128, 92, 32), (118, 170, 92, 32)][: len(glazes)]
    parts = [plate(128, 160, 104)]
    for (x, y, length, thick), glaze in zip(specs, glazes, strict=True):
        half, radius = length / 2, thick / 2
        body = [
            rect(-half, -radius + 5, length, thick, "crust", rx=radius, shaded=True),
            rect(-half, -radius, length, thick - 3, "crust", rx=radius),
            rect(-half + 2, -radius - 1, length - 4, thick * 0.62, glaze, rx=thick * 0.31),
            rect(
                -half + thick * 0.4, -radius + 3, length * 0.42, 3.5, "white", rx=1.75, opacity=0.4
            ),
        ]
        if r.get("drizzle"):
            zig = ["M", -half + 14, -radius + 2]
            for i in range(1, 9):
                zig += [
                    "L",
                    -half + 14 + (length - 28) * i / 8,
                    -radius + (2 if i % 2 == 0 else 14),
                ]
            body.append(line(d(*zig), r["drizzle"], 2.5))
        parts += [
            shadow(x, y + radius + 3, half * 0.95, 7),
            group(body, f"translate({x} {y}) rotate(-14)"),
        ]
    x, y, length, _ = specs[0]
    return parts, Top(x, y - 10, length * 0.3, 4, slope=-0.25)


def _dome(r: dict) -> tuple[list[str], Top]:
    """반구(초콜릿 돔 케이크): 위가 둥근 돔. color(몸통)."""
    cx, base, rad = 128, 170, 68
    ry, peak = rad * TILT, base - rad * 0.95
    color = r["color"]
    dome = d(
        "M", cx - rad, base, "A", rad, rad * 0.95, 0, 0, 1, cx + rad, base,
        "A", rad, ry, 0, 0, 1, cx - rad, base, "Z",
    )  # fmt: skip
    dark = d(
        "M", cx, peak, "A", rad, rad * 0.95, 0, 0, 1, cx + rad, base,
        "A", rad, ry, 0, 0, 1, cx + rad * 0.35, base + ry * 0.94,
        "Q", cx + rad * 0.62, base - rad * 0.4, cx, peak, "Z",
    )  # fmt: skip
    parts = [plate(cx, base + 4, 102), path(dome, color), path(dark, color, shaded=True)]
    parts.append(
        ellipse(
            cx - rad * 0.42,
            base - rad * 0.55,
            rad * 0.17,
            rad * 0.09,
            "white",
            opacity=0.4,
            transform=f"rotate(-40 {cx - rad * 0.42:.1f} {base - rad * 0.55:.1f})",
        )
    )
    return parts, Top(cx, peak + 12, rad * 0.4, 8)


SHAPES: dict[str, Callable[[dict], tuple[list[str], Top]]] = {
    "slice": _slice,
    "whole": _whole,
    "bar": _bar,
    "roll": _roll,
    "tart": _tart,
    "tartlets": _tartlets,
    "nest": _nest,
    "eclair": _eclair,
    "dome": _dome,
}


# ── 위 장식 ───────────────────────────────────────────────────────────────────


def _crumbs(top: Top, color: str) -> list[str]:
    return [circle(x, y, 2 + i % 3 * 0.6, color) for i, (x, y) in enumerate(top.scatter(26))]


def _sparse_crumbs(top: Top, color: str) -> list[str]:
    return [circle(x, y, 2.2, color) for x, y in top.scatter(10, turn=1)]


def _dust(top: Top, color: str) -> list[str]:
    return [circle(x, y, 1.4, color, opacity=0.85) for x, y in top.scatter(24, turn=0.5)]


def _raspberries(top: Top, color: str) -> list[str]:
    parts = []
    for u, v in ((-0.55, -0.45), (0.5, -0.5), (0.02, 0.35)):
        x, y = top.at(u, v)
        parts += [
            circle(x, y + 1.5, 9, color, shaded=True),
            circle(x, y, 8.5, color),
            circle(x - 3, y - 2, 1.6, color, shaded=True),
            circle(x + 3, y + 1, 1.6, color, shaded=True),
            circle(x - 1, y + 4, 1.6, color, shaded=True),
            ellipse(x - 3, y - 4, 2.6, 1.6, "white", opacity=0.55),
        ]
    return parts


def _blueberries(top: Top, color: str) -> list[str]:
    parts = []
    for u, v in ((-0.15, -0.7), (0.78, 0.05), (-0.8, 0.15), (0.35, 0.75), (-0.35, 0.6)):
        x, y = top.at(u, v)
        parts += [circle(x, y, 6, color), circle(x - 2, y - 2, 1.5, "white", opacity=0.5)]
    return parts


def _strawberries(top: Top, color: str) -> list[str]:
    berry = "M0 -9C9 -9 11 2 0 12C-11 2 -9 -9 0 -9Z"
    parts = []
    for (u, v), turn in (((-0.4, -0.25), -20), ((0.45, -0.1), 18)):
        x, y = top.at(u, v)
        parts.append(
            group(
                [
                    path(berry, color),
                    path("M0 -5C5 -5 6 1 0 7C-6 1 -5 -5 0 -5Z", "berry_cream"),
                    ellipse(0, -9, 6, 2.5, "leaf"),
                ],
                f"translate({x:.1f} {y:.1f}) rotate({turn})",
            )
        )
    return parts


def _currants(top: Top, color: str) -> list[str]:
    parts = []
    for u, v in ((-0.55, -0.2), (0.15, 0.1), (0.7, -0.3)):
        x, y = top.at(u, v)
        parts.append(line(d("M", x - 5, y - 4, "Q", x, y - 12, x + 7, y - 6), "leaf_shade", 1.8))
        for dx, dy in ((-5, -2), (1, -4), (6, -1), (-1, 2)):
            parts += [
                circle(x + dx, y + dy, 4.4, color),
                circle(x + dx - 1, y + dy - 1, 1, "white"),
            ]
    return parts


def _curls(top: Top, color: str) -> list[str]:
    parts = []
    for (u, v), turn in (
        ((-0.5, -0.4), -25),
        ((0.35, -0.55), 20),
        ((0.05, 0.3), -8),
        ((0.7, 0.3), 35),
    ):
        x, y = top.at(u, v)
        spin = f"rotate({turn} {x:.1f} {y:.1f})"
        parts += [
            rect(x - 10, y - 2, 20, 7, color, rx=3.5, shaded=True, transform=spin),
            rect(x - 10, y - 3.5, 20, 6, color, rx=3, transform=spin),
        ]
    return parts


def _walnuts(top: Top, color: str) -> list[str]:
    parts = []
    for u, v in ((-0.55, -0.35), (0.5, -0.45), (0.0, 0.4)):
        x, y = top.at(u, v)
        parts += [
            ellipse(x, y + 1.5, 9, 7, color, shaded=True),
            ellipse(x, y, 9, 6.5, color),
            line(d("M", x, y - 5, "L", x, y + 5), f"{color}_shade", 1.6),
            line(d("M", x - 6, y - 1, "Q", x - 3, y + 2, x - 5, y + 4), f"{color}_shade", 1.2),
            line(d("M", x + 6, y - 1, "Q", x + 3, y + 2, x + 5, y + 4), f"{color}_shade", 1.2),
        ]
    return parts


def _hazelnuts(top: Top, color: str) -> list[str]:
    parts = []
    for u, v in ((-0.7, -0.1), (-0.2, -0.6), (0.35, -0.4), (0.75, 0.1), (0.1, 0.45)):
        x, y = top.at(u, v)
        parts += [circle(x, y, 5.5, color), ellipse(x - 1, y - 3, 3.5, 2, "almond")]
    return parts


def _almonds(top: Top, color: str) -> list[str]:
    parts = []
    for i, (x, y) in enumerate(top.scatter(9, turn=2)):
        turn = (i * 47) % 180
        parts.append(ellipse(x, y, 5, 2.4, color, transform=f"rotate({turn} {x:.1f} {y:.1f})"))
    return parts


def _carrots(top: Top, color: str) -> list[str]:
    parts = []
    for (u, v), turn in (((-0.45, -0.2), 20), ((0.45, -0.35), -15)):
        x, y = top.at(u, v)
        parts.append(
            group(
                [
                    line("M0 -8L-4 -14M0 -8L0 -15M0 -8L4 -14", "leaf", 2),
                    polygon([(-4.5, -8), (4.5, -8), (0, 10)], color),
                ],
                f"translate({x:.1f} {y:.1f}) rotate({turn})",
            )
        )
    return parts


def _rosettes(top: Top, color: str) -> list[str]:
    parts = []
    for u in (-0.8, -0.27, 0.27, 0.8):
        x, y = top.at(u, -0.6 + abs(u) * 0.5)
        parts += [
            ellipse(x, y + 1, 8, 5.5, color, shaded=True),
            ellipse(x, y - 1.5, 6.5, 4.5, color),
            ellipse(x + 0.5, y - 4.5, 4.5, 3.2, color, shaded=True),
            ellipse(x + 0.5, y - 5.5, 3.8, 2.6, color),
            circle(x + 1, y - 8, 1.8, color),
        ]
    return parts


def _drizzle(top: Top, color: str) -> list[str]:
    zig = ["M", *top.at(-0.85, -0.55)]
    for i in range(1, 8):
        zig += ["L", *top.at(-0.85 + 1.7 * i / 7, 0.55 if i % 2 else -0.55)]
    return [line(d(*zig), color, 2.8)]


def _shine(top: Top, color: str) -> list[str]:
    x, y = top.at(-0.3, -0.2)
    return [ellipse(x, y, top.rx * 0.45, top.ry * 0.35, color, opacity=0.35)]


def _honeycomb(top: Top, color: str) -> list[str]:
    def hexagon(x: float, y: float, size: float) -> list[Point]:
        return [
            (x + size * math.cos(a), y + size * math.sin(a) * 0.75)
            for a in (math.pi / 3 * i for i in range(6))
        ]

    parts = []
    x0, y0 = top.at(0, -0.1)
    for dx, dy in ((-7, 0), (7, 0), (0, -9)):
        parts += [
            polygon(hexagon(x0 + dx, y0 + dy, 8), color),
            polygon(hexagon(x0 + dx, y0 + dy, 4.5), color, shaded=True),
        ]
    return parts


def _vanilla(top: Top, color: str) -> list[str]:
    a, b = top.at(-0.7, 0.1), top.at(0.6, -0.4)
    bend = top.at(0, 0.4)
    return [line(d("M", *a, "Q", *bend, *b), color, 4)]


def _cranberries(top: Top, color: str) -> list[str]:
    parts = []
    for x, y in top.scatter(9, turn=3):
        parts += [circle(x, y, 5, color), circle(x - 1.5, y - 1.5, 1.3, "white", opacity=0.55)]
    return parts


def _pineapple(top: Top, color: str) -> list[str]:
    parts = []
    for i, (u, v) in enumerate(((-0.45, -0.15), (0.2, -0.55), (0.55, 0.35), (-0.15, 0.5))):
        x, y = top.at(u, v)
        spin = f"rotate({i * 23} {x:.1f} {y:.1f})"
        parts += [
            rect(x - 5, y - 3.5, 10, 10, color, rx=2, shaded=True, transform=spin),
            rect(x - 5, y - 5, 10, 9, color, rx=2, transform=spin),
        ]
    return parts


def _mint(top: Top, color: str) -> list[str]:
    x, y = top.at(0.15, -0.05)
    return [
        ellipse(x - 5, y, 7, 3.6, color, transform=f"rotate(-30 {x - 5:.1f} {y:.1f})"),
        ellipse(x + 5, y, 7, 3.6, color, shaded=True, transform=f"rotate(30 {x + 5:.1f} {y:.1f})"),
    ]


def _zest(top: Top, color: str) -> list[str]:
    parts = []
    for u, v in ((-0.25, -0.5), (0.4, 0.3), (-0.75, 0.4)):
        x, y = top.at(u, v)
        parts.append(line(d("M", x - 5, y, "Q", x, y - 5, x + 5, y), color, 2.6))
    return parts


TOPPINGS: dict[str, Callable[[Top, str], list[str]]] = {
    "crumbs": _crumbs,
    "sparse_crumbs": _sparse_crumbs,
    "dust": _dust,
    "raspberries": _raspberries,
    "blueberries": _blueberries,
    "strawberries": _strawberries,
    "currants": _currants,
    "curls": _curls,
    "walnuts": _walnuts,
    "hazelnuts": _hazelnuts,
    "almonds": _almonds,
    "carrots": _carrots,
    "rosettes": _rosettes,
    "drizzle": _drizzle,
    "shine": _shine,
    "honeycomb": _honeycomb,
    "vanilla": _vanilla,
    "cranberries": _cranberries,
    "pineapple": _pineapple,
    "mint": _mint,
    "zest": _zest,
}


def draw(recipe: dict) -> str:
    parts, top = SHAPES[recipe["shape"]](recipe)
    for kind, color in recipe.get("toppings", []):
        parts += TOPPINGS[kind](top, color)
    return svg(recipe["card"], *parts)


def cake(
    id: str,
    name: str,
    recipe: dict,
    *,
    aliases: Sequence[str] = (),
    keywords: Sequence[str] = (),
    category: str = "cake",
) -> Illustration:
    """레시피 하나 → 카탈로그 그림 하나(분류 기본값은 케이크)."""
    return Illustration(
        id=id,
        name=name,
        category=category,
        draw=draw,
        recipe=recipe,
        aliases=tuple(aliases),
        keywords=tuple(keywords),
    )


def _alternate(sheet: str, cream: str, count: int, sheet_w: float = 1, cream_w: float = 0.5):
    """시트·크림을 번갈아 count장(맨 위·맨 아래가 시트)."""
    layers = []
    for i in range(count):
        if i:
            layers.append([cream, cream_w])
        layers.append([sheet, sheet_w])
    return layers


MEDOVIK_LAYERS = _alternate("honey", "cream", 5)

ILLUSTRATIONS = [
    # ── 메도빅 4종: 같은 꿀 케이크라도 색·모양으로 한눈에 갈린다 ──
    cake(
        "cake_medovik",
        "메도빅",
        {
            "shape": "slice",
            "layers": MEDOVIK_LAYERS,
            "top": "cream",
            "toppings": [["crumbs", "honey"]],
            "card": "card_cream",
        },
        aliases=("꿀 케이크", "허니 케이크", "Medovik", "Медовик"),
        keywords=("메도빅", "꿀케이크", "꿀케익", "medovik", "медовик", "honeycake"),
    ),
    cake(
        "cake_medovik_choco",
        "초코 메도빅",
        {
            "shape": "slice",
            "layers": _alternate("cocoa", "choco", 5),
            "top": "choco",
            "toppings": [["curls", "cocoa"], ["drizzle", "caramel"]],
            "card": "card_sand",
        },
        aliases=("초콜릿 메도빅", "초코 꿀케이크"),
        keywords=("초코메도빅", "메도빅초코", "초콜릿메도빅", "꿀케이크초코", "초코꿀케이크"),
    ),
    cake(
        "cake_medovik_sourberry",
        "사워베리 메도빅",
        {
            "shape": "slice",
            "layers": _alternate("honey", "berry_cream", 5, cream_w=0.6),
            "top": "berry_cream",
            "toppings": [["sparse_crumbs", "honey"], ["raspberries", "berry"]],
            "card": "card_rose",
        },
        aliases=("사워베리 꿀케이크",),
        keywords=("사워베리", "메도빅사워베리", "꿀케이크사워베리", "사워베리꿀케이크"),
    ),
    cake(
        "cake_medovik_whole",
        "메도빅 홀케이크",
        {
            "shape": "whole",
            "cut": 26,
            "layers": MEDOVIK_LAYERS,
            "side": "honey",
            "side_crumbs": "sponge",
            "top": "cream",
            "toppings": [["crumbs", "honey"], ["honeycomb", "honey"]],
            "card": "card_peach",
        },
        aliases=("메도빅 홀 케이크", "메도빅 한 판", "꿀케이크 홀"),
        keywords=("메도빅홀", "홀메도빅", "꿀케이크홀", "홀꿀케이크"),
    ),
    # ── 러시아·동유럽 케이크 ──
    cake(
        "cake_napoleon",
        "나폴레옹",
        {
            "shape": "bar",
            "layers": _alternate("crust", "custard", 6, sheet_w=0.55, cream_w=0.7),
            "top": "custard",
            "toppings": [["crumbs", "sponge"], ["dust", "white"]],
            "card": "card_sand",
        },
        aliases=("나폴레옹 케이크", "Napoleon", "Наполеон"),
        keywords=("나폴레옹", "밀푀유", "napoleon", "наполеон"),
    ),
    cake(
        "cake_bird_milk",
        "버드밀크 케이크",
        {
            "shape": "slice",
            "layers": [
                ["choco", 0.16],
                ["meringue", 1],
                ["sponge", 0.3],
                ["meringue", 1],
                ["sponge", 0.3],
            ],
            "top": "choco",
            "toppings": [["shine", "white"], ["drizzle", "meringue"]],
            "card": "card_sky",
        },
        aliases=("버드밀크", "새의 우유", "Птичье молоко"),
        keywords=("버드밀크", "birdsmilk", "птичьемолоко"),
    ),
    cake(
        "cake_kiev",
        "키예프 케이크",
        {
            "shape": "slice",
            "layers": _alternate("almond", "cocoa", 3, cream_w=0.5),
            "top": "cream",
            "toppings": [["rosettes", "berry_cream"], ["hazelnuts", "nut"]],
            "card": "card_lilac",
        },
        aliases=("키예프", "키이우 케이크", "Киевский торт"),
        keywords=("키예프", "키에프", "키이우케이크", "kievcake", "kyivcake", "киевский"),
    ),
    cake(
        "cake_praha",
        "프라하",
        {
            "shape": "slice",
            "layers": [["choco_shade", 0.18], *_alternate("cocoa", "choco", 3, cream_w=0.55)],
            "top": "choco",
            "toppings": [["shine", "white"], ["rosettes", "cocoa"]],
            "card": "card_peach",
        },
        aliases=("프라하 케이크", "Praga", "Прага"),
        keywords=("프라하", "prague", "praga", "прага"),
    ),
    cake(
        "cake_smetannik",
        "스메타닉",
        {
            "shape": "slice",
            "layers": _alternate("sponge", "meringue", 4, sheet_w=0.6, cream_w=0.75),
            "top": "meringue",
            "toppings": [["sparse_crumbs", "cocoa"], ["mint", "leaf"]],
            "card": "card_mint",
        },
        aliases=("스메타닉 케이크", "Сметанник"),
        keywords=("스메타닉", "스메따닉", "smetannik", "сметанник"),
    ),
    cake(
        "cake_zebra",
        "제브라 케이크",
        {
            "shape": "slice",
            "wave": 3,
            "layers": _alternate("sponge", "cocoa", 5, cream_w=1),
            "top": "sponge",
            "toppings": [["dust", "white"]],
            "card": "card_sky",
        },
        aliases=("제브라", "얼룩말 케이크", "Зебра"),
        keywords=("제브라", "얼룩말케이크", "zebracake", "зебра"),
    ),
    cake(
        "cake_carrot",
        "당근 케이크",
        {
            "shape": "slice",
            "layers": _alternate("carrot_sponge", "cream", 3, cream_w=0.45),
            "top": "cream",
            "toppings": [["walnuts", "nut"], ["carrots", "carrot"]],
            "card": "card_peach",
        },
        aliases=("캐럿 케이크", "Carrot cake"),
        keywords=("당근케이크", "당근케익", "캐럿케이크", "캐롯케이크", "carrotcake"),
    ),
    cake(
        "cake_cheesecake_vanilla",
        "바닐라 치즈케이크",
        {
            "shape": "slice",
            "height": 62,
            "layers": [["cheese_top", 0.08], ["cheese", 1], ["crust", 0.22]],
            "top": "cheese_top",
            "toppings": [["vanilla", "choco_shade"]],
            "card": "card_cream",
        },
        aliases=("치즈케이크", "바닐라 치즈 케이크"),
        keywords=("치즈케이크", "치즈케익", "바닐라치즈", "cheesecake"),
    ),
    cake(
        "cake_walnut",
        "월넛 케이크",
        {
            "shape": "slice",
            "layers": [
                ["sponge", 1],
                ["custard", 0.5],
                ["choco", 0.35],
                ["sponge", 1],
                ["custard", 0.5],
                ["sponge", 1],
            ],
            "top": "custard",
            "toppings": [["drizzle", "choco"], ["walnuts", "nut"]],
            "card": "card_sand",
        },
        aliases=("호두 크림 케이크", "Walnut cake"),
        keywords=("호두", "월넛", "walnut"),
    ),
    cake(
        "cake_almond_berry_cream",
        "아몬드 베리 크림 케이크",
        {
            "shape": "slice",
            "layers": [
                ["cream", 1],
                ["berry", 0.22],
                ["almond", 0.6],
                ["cream", 1],
                ["lemon", 0.22],
                ["almond", 0.6],
            ],
            "top": "cream",
            "toppings": [["almonds", "almond"], ["strawberries", "berry"]],
            "card": "card_sky",
        },
        aliases=("아몬드 크림 케이크",),
        keywords=("아몬드베리", "아몬드크림케이크"),
    ),
    cake(
        "cake_caramel_meringue_whole",
        "카라멜 머랭 홀케이크",
        {
            "shape": "whole",
            "rx": 66,
            "height": 54,
            "top_y": 118,
            "side": "cream",
            "drip": "caramel",
            "top": "meringue",
            "toppings": [["curls", "cocoa"], ["raspberries", "berry"]],
            "card": "card_lilac",
        },
        aliases=("머랭 카라멜 홀케이크",),
        keywords=("카라멜머랭", "머랭카라멜"),
    ),
    cake(
        "cake_choco_mascarpone",
        "초콜릿 마스카포네 케이크",
        {
            "shape": "slice",
            "layers": _alternate("choco", "meringue", 3, cream_w=0.8),
            "top": "meringue",
            "toppings": [["dust", "cocoa"], ["raspberries", "berry"]],
            "card": "card_rose",
        },
        aliases=("마스카포네 초콜릿 케이크",),
        keywords=("초콜릿마스카포네", "마스카포네"),
    ),
    cake(
        "cake_choco_dome",
        "초콜릿 돔 케이크",
        {
            "shape": "dome",
            "color": "choco",
            "toppings": [["drizzle", "caramel"], ["raspberries", "berry"]],
            "card": "card_cream",
        },
        aliases=("초콜릿 돔", "초코 돔 케이크"),
        keywords=("초콜릿돔", "초코돔", "chocolatedome"),
    ),
    cake(
        "cake_caramel_choco_layer",
        "카라멜 초코 레이어 케이크",
        {
            "shape": "slice",
            "layers": [
                ["choco", 0.3],
                ["cream", 0.35],
                ["caramel", 0.3],
                ["cocoa", 1],
                ["caramel", 0.3],
                ["cocoa", 1],
            ],
            "top": "choco",
            "toppings": [["almonds", "almond"], ["drizzle", "caramel"]],
            "card": "card_cream",
        },
        aliases=("초코 카라멜 레이어 케이크",),
        keywords=("카라멜초코", "초코카라멜"),
    ),
    # ── 에클레어·롤·타르트·머랭 ──
    cake(
        "cake_eclair_mini",
        "미니 에클레어",
        {"shape": "eclair", "glazes": ["choco", "meringue", "caramel"], "card": "card_sky"},
        aliases=("미니 에클레르", "에클레어 세트"),
        keywords=("미니에클레어", "미니에클레르", "minieclair"),
    ),
    cake(
        "cake_eclair_big",
        "빅 에클레어",
        {"shape": "eclair", "glazes": ["choco"], "drizzle": "meringue", "card": "card_cream"},
        aliases=("에클레어", "에클레르", "Eclair", "Эклер"),
        keywords=("에클레어", "에클레르", "eclair", "эклер"),
    ),
    cake(
        "cake_meringue_roll",
        "머랭 롤",
        {
            "shape": "roll",
            "outer": "meringue",
            "inner": "berry_cream",
            "toppings": [["currants", "berry"], ["zest", "lemon"]],
            "card": "card_rose",
        },
        aliases=("머랭 롤케이크", "머랭롤 케이크"),
        keywords=("머랭롤", "meringueroll"),
    ),
    cake(
        "cake_berry_tart",
        "베리 타르트",
        {
            "shape": "tart",
            "filling": "custard",
            "toppings": [["raspberries", "berry"], ["blueberries", "blueberry"]],
            "card": "card_lilac",
        },
        aliases=("과일 타르트", "블루베리 타르트", "산딸기 타르트"),
        keywords=("베리타르트", "블루베리타르트", "산딸기타르트", "berrytart"),
    ),
    cake(
        "cake_tart",
        "크랜베리 파인애플 타르트",
        {
            "shape": "tart",
            "filling": "custard",
            "toppings": [["pineapple", "lemon"], ["cranberries", "berry_shade"]],
            "card": "card_cream",
        },
        aliases=("크랜베리 타르트",),
        keywords=("타르트", "tart", "тарт"),
    ),
    cake(
        "cake_pavlova",
        "파블로바",
        {
            "shape": "nest",
            "toppings": [["raspberries", "berry"], ["blueberries", "blueberry"], ["mint", "leaf"]],
            "card": "card_mint",
        },
        aliases=("파브로바", "파블로바 케이크", "Pavlova", "Павлова"),
        keywords=("파블로바", "파브로바", "pavlova", "павлова"),
    ),
    # ── 가까운 메뉴(티켓 07): 데모 가게엔 없지만 손님·심사위원이 넣을 법한 케이크 ──
    # 넓은 키워드("치즈케이크", "타르트")가 이미 있으니 변형은 더 긴 키워드로 이긴다.
    cake(
        "cake_tiramisu",
        "티라미수",
        {
            "shape": "bar",
            "height": 58,
            "layers": [
                ["cocoa", 0.14],
                ["cream", 0.9],
                ["crema", 0.55],
                ["cream", 0.9],
                ["crema", 0.55],
            ],
            "top": "cocoa",
            "toppings": [["dust", "choco_shade"], ["mint", "leaf"]],
            "card": "card_peach",
        },
        aliases=("티라미스", "Tiramisu"),
        keywords=("티라미수", "티라미스", "tiramisu"),
    ),
    cake(
        "cake_cheesecake_newyork",
        "뉴욕 치즈케이크",
        {
            "shape": "slice",
            "height": 72,
            "layers": [["cheese", 1], ["crust", 0.3]],
            "top": "cheese",
            "toppings": [["shine", "white"], ["strawberries", "berry"]],
            "card": "card_rose",
        },
        aliases=("뉴욕치즈케익", "New York cheesecake"),
        keywords=("뉴욕치즈케이크", "뉴욕치즈케익", "뉴욕치즈", "newyorkcheesecake"),
    ),
    cake(
        "cake_cheesecake_basque",
        "바스크 치즈케이크",
        {
            "shape": "slice",
            "height": 64,
            "layers": [["burnt", 0.1], ["cheese", 1]],
            "top": "burnt",
            "toppings": [["shine", "white"]],
            "card": "card_sand",
        },
        aliases=("바스크 치즈케익", "Basque cheesecake"),
        keywords=("바스크치즈케이크", "바스크치즈케익", "바스크치즈", "바스크", "basque"),
    ),
    cake(
        "cake_red_velvet",
        "레드벨벳",
        {
            "shape": "slice",
            "layers": _alternate("red_velvet", "cream", 3, cream_w=0.45),
            "top": "cream",
            "toppings": [["crumbs", "red_velvet"]],
            "card": "card_lilac",
        },
        aliases=("레드벨벳 케이크", "레드 벨벳 케이크", "Red velvet"),
        keywords=("레드벨벳", "redvelvet"),
    ),
    cake(
        "cake_roll",
        "롤케이크",
        {
            "shape": "roll",
            "outer": "sponge",
            "inner": "cream",
            "toppings": [["dust", "white"], ["strawberries", "berry"]],
            "card": "card_cream",
        },
        aliases=("롤 케이크", "스위스롤", "생크림 롤케이크", "Roll cake"),
        keywords=("롤케이크", "롤케익", "스위스롤", "rollcake"),
    ),
    cake(
        "cake_strawberry",
        "딸기 생크림 케이크",
        {
            "shape": "slice",
            "layers": [
                ["cream", 0.3],
                ["sponge", 1],
                ["cream", 0.4],
                ["berry", 0.18],
                ["cream", 0.4],
                ["sponge", 1],
            ],
            "top": "cream",
            "toppings": [["rosettes", "cream"], ["strawberries", "berry"]],
            "card": "card_mint",
        },
        aliases=("딸기 케이크", "생크림 케이크", "딸기 쇼트케이크", "Strawberry cake"),
        keywords=("딸기케이크", "딸기케익", "딸기생크림", "생크림케이크", "쇼트케이크"),
    ),
    cake(
        "cake_chocolate",
        "초코 케이크",
        {
            "shape": "whole",
            "cut": 26,
            "layers": [["choco_shade", 0.15], *_alternate("cocoa", "choco", 3, cream_w=0.45)],
            "side": "choco",
            "top": "choco",
            "toppings": [["shine", "white"], ["strawberries", "berry"]],
            "card": "card_cream",
        },
        aliases=("초콜릿 케이크", "초콜렛 케이크", "가토 쇼콜라", "Chocolate cake"),
        keywords=(
            "초코케이크",
            "초코케익",
            "초콜릿케이크",
            "초콜렛케이크",
            "가토쇼콜라",
            "가나슈케이크",
            "chocolatecake",
        ),
    ),
    cake(
        "cake_crepe",
        "크레이프 케이크",
        {
            "shape": "slice",
            "height": 62,
            "layers": _alternate("custard", "cream", 9, sheet_w=0.3, cream_w=0.45),
            "top": "crust",
            "toppings": [["shine", "white"], ["dust", "white"]],
            "card": "card_sky",
        },
        aliases=("밀크레이프", "밀 크레이프", "크레페 케이크", "Mille crepe"),
        keywords=("크레이프케이크", "밀크레이프", "크레페케이크", "밀크레페", "millecrepe"),
    ),
    cake(
        "cake_matcha",
        "녹차 케이크",
        {
            "shape": "slice",
            "layers": _alternate("matcha", "cream", 3, cream_w=0.5),
            "top": "matcha_light",
            "toppings": [["dust", "matcha"], ["hazelnuts", "nut"]],
            "card": "card_cream",
        },
        aliases=("말차 케이크", "그린티 케이크", "Matcha cake"),
        keywords=("녹차케이크", "녹차케익", "말차케이크", "말차케익", "그린티케이크"),
    ),
    cake(
        "cake_egg_tart",
        "에그타르트",
        {"shape": "tartlets", "filling": "custard", "scorch": "burnt", "card": "card_sky"},
        aliases=("에그 타르트", "에그타트", "포르투갈 에그타르트", "Egg tart"),
        keywords=("에그타르트", "에그타트", "eggtart"),
    ),
    cake(
        "cake_lemon_tart",
        "레몬 타르트",
        {
            "shape": "tart",
            "filling": "lemon",
            "toppings": [["rosettes", "meringue"], ["zest", "lemon_light"], ["mint", "leaf"]],
            "card": "card_mint",
        },
        aliases=("레몬 머랭 타르트", "Lemon tart"),
        keywords=("레몬타르트", "레몬머랭타르트", "lemontart"),
    ),
]
