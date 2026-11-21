"""음료 그림(티켓 03): 잔 모양 × 음료 색 × 고명을 조합한다.

레시피 하나가 그림 하나다(`recipe(...)`로 만든다):

- vessel: 잔 모양. VESSELS 중 하나(머그·커피잔·찻잔·유리잔·낮은 유리잔·손잡이 유리잔·테이크아웃 컵
  ·에스프레소 잔).
- liquid: 음료 색. 아래에서 위로 [색, 두께] 목록(층진 음료). 유리잔은 층이 보이고, 도자기 잔은
  맨 위 색만 보인다. foam: 맨 위 거품 색(없으면 거품 없음).
- garnish: 고명. GARNISHES 중 이름 목록(레몬·베리·얼음·라떼아트·찻잎·꿀 …). 고명마다 그릴 자리
  (잔 뒤, 음료 안, 음료 위, 잔 옆 바닥)가 정해져 있다.
- cup: 잔(테이크아웃 컵은 슬리브) 색. straw: 빨대 색. card: 배경 카드 색.

티켓 07(가까운 메뉴)은 여기의 `recipe`와 `draw`로 음료 변형을 더한다:
`Illustration(id, name, "coffee", draw, recipe("glass", ["coffee"], garnish=["ice"]), ...)`.
"""

from collections.abc import Callable
from dataclasses import dataclass

from agenthan.illustration.art.style import (
    CATEGORY_CARD,
    Illustration,
    circle,
    citrus_wheel,
    d,
    ellipse,
    group,
    line,
    num,
    path,
    plate,
    rect,
    shadow,
    svg,
)

FAMILY = "drinks"

GLASS_RATIO = 0.3  # 유리잔 입구 타원(세로/가로): 기본 에이드 그림과 같다
CUP_RATIO = 0.42  # 도자기 잔 입구 타원: 기본 커피 그림과 같다


@dataclass(frozen=True)
class Spot:
    """고명을 놓을 자리. 잔 모양마다 하나."""

    cx: float
    rim_y: float  # 잔 입구 타원 중심
    rx: float
    ry: float
    level: float  # 음료 윗면 타원 중심
    level_rx: float
    level_ry: float
    bottom: float  # 음료(유리잔이면 잔 속) 바닥
    table: tuple[float, float]  # 잔 옆 바닥(찻잎·꿀 막대·원두를 놓는 자리)


Layers = dict[str, list[str]]  # back(잔 뒤) / inside(음료 안) / top(음료 위·입구) / table(잔 옆)
LAYERS = ("back", "inside", "top", "table")


def recipe(
    vessel: str,
    liquid: list,
    *,
    garnish: tuple[str, ...] | list[str] = (),
    foam: str | None = None,
    cup: str | None = None,
    straw: str | None = None,
    card: str | None = None,
) -> dict:
    """레시피 dict(카탈로그 JSON에 그대로 실린다). liquid: 색 이름 또는 [색, 두께] 목록(아래→위)."""
    layers = [[item, 1] if isinstance(item, str) else list(item) for item in liquid]
    out: dict = {"vessel": vessel, "liquid": layers}
    if foam:
        out["foam"] = foam
    if garnish:
        out["garnish"] = list(garnish)
    if cup:
        out["cup"] = cup
    if straw:
        out["straw"] = straw
    if card:
        out["card"] = card
    return out


# ── 작은 부품 ─────────────────────────────────────────────────────────────────

LEAF = "M0 0C10 -14 32 -14 42 0C32 14 10 14 0 0Z"


def _leaf(x, y, angle, scale=1.0, fill="leaf") -> str:
    return group(
        [path(LEAF, fill), line("M5 0L36 0", "leaf_shade", 2.5)],
        f"translate({x} {y}) rotate({angle}) scale({scale})",
    )


def _raspberry(x, y, r, fill="berry") -> str:
    """산딸기: 작은 알갱이가 모인 둥근 열매."""
    parts = [circle(x, y, r, fill, shaded=True)]
    for dx, dy in ((-0.4, -0.35), (0.35, -0.4), (-0.45, 0.25), (0.4, 0.25), (0, 0.5), (0, -0.05)):
        parts.append(circle(x + dx * r, y + dy * r, r * 0.42, fill))
    parts.append(circle(x - r * 0.35, y - r * 0.4, r * 0.15, "white", opacity=0.6))
    return "".join(parts)


def _berry(x, y, r, fill) -> str:
    """작은 둥근 열매(크랜베리·오미자·블랙커런트·비타민나무 열매) + 반짝임."""
    return circle(x, y, r, fill) + circle(x - r * 0.3, y - r * 0.35, r * 0.3, "white", opacity=0.55)


def _heart(x, y, size, fill) -> str:
    """윗면에 눕힌 하트(라떼아트)."""
    heart = "M0 0.9C-1.3 0 -0.9 -1 0 -0.4C0.9 -1 1.3 0 0 0.9Z"
    return path(heart, fill, transform=f"translate({x} {y}) scale({size} {size * 0.62})")


def _ice(x, y, s, angle, opacity=0.85) -> str:
    return rect(
        x - s / 2, y - s / 2, s, s * 0.9, "ice", rx=s * 0.24, opacity=opacity,
        transform=f"rotate({angle} {x} {y})",
    )  # fmt: skip


# ── 고명 ─────────────────────────────────────────────────────────────────────


def _steam(s: Spot, _r: dict) -> Layers:
    y = s.rim_y - s.ry - 46
    return {
        "top": [
            line(f"M{s.cx - 14} {y}c-10 10 10 18 0 30", "white", 6, opacity=0.85),
            line(f"M{s.cx + 14} {y - 6}c-10 10 10 18 0 30", "white", 6, opacity=0.85),
        ]
    }


def _latte_art(s: Spot, _r: dict) -> Layers:
    return {
        "top": [
            ellipse(s.cx, s.level + 1, s.level_rx * 0.62, s.level_ry * 0.62, "cream", opacity=0.5),
            _heart(s.cx, s.level + 1, s.level_rx * 0.44, "cream"),
        ]
    }


def _cocoa(s: Spot, _r: dict) -> Layers:
    dots = ((-0.45, -0.2), (-0.15, 0.3), (0.2, -0.35), (0.4, 0.15), (0.05, -0.05), (-0.3, 0.05),
            (0.15, 0.4), (-0.05, -0.45), (0.5, -0.15))  # fmt: skip
    return {
        "top": [
            circle(s.cx + dx * s.level_rx, s.level + dy * s.level_ry, 2.6, "choco", opacity=0.75)
            for dx, dy in dots
        ]
    }


def _caramel(s: Spot, _r: dict) -> Layers:
    """카라멜 소스를 지그재그로 뿌린 윗면 + 잔 안쪽으로 흐른 줄기."""
    points = []
    for i in range(7):
        x = s.cx - s.level_rx * 0.7 + i * s.level_rx * 1.4 / 6
        y = s.level + (0.45 if i % 2 else -0.45) * s.level_ry
        points += ["M" if i == 0 else "L", x, y]
    return {
        "inside": [
            line(
                d("M", s.cx - s.level_rx * 0.55, s.level + 6, "q", 3, 16, -2, 30), "honey_shade", 4
            ),
            line(
                d("M", s.cx + s.level_rx * 0.35, s.level + 8, "q", -3, 12, 1, 22), "honey_shade", 4
            ),
        ],
        "top": [line(d(*points), "honey_shade", 3.5)],
    }


def _tea_leaves(s: Spot, _r: dict) -> Layers:
    x, y = s.table
    return {"table": [_leaf(x - 8, y + 2, -18, 0.85), _leaf(x - 6, y - 4, -62, 0.7)]}


def _tea_tag(s: Spot, _r: dict) -> Layers:
    """찻잔 왼쪽으로 걸친 티백 줄과 꼬리표."""
    x0, y0 = s.cx - s.level_rx * 0.25, s.level
    over = (s.cx - s.rx * 0.92, s.rim_y - 3)
    end = (s.cx - s.rx - 12, s.rim_y + 30)
    string = d("M", x0, y0, "Q", over[0], over[1] - 14, over[0], over[1], "L", end[0], end[1])
    turn = f"rotate(-10 {end[0]} {end[1]})"
    return {
        "top": [
            line(string, "choco_shade", 2),
            rect(end[0] - 12, end[1], 24, 28, "honey", rx=3, transform=turn),
            rect(end[0] - 7, end[1] + 6, 14, 12, "white", rx=2, opacity=0.85, transform=turn),
        ]
    }


def _lemon_rim(s: Spot, _r: dict) -> Layers:
    return {"top": [citrus_wheel(s.cx - s.rx + 4, s.rim_y + 6, 24, "lemon", "lemon_light")]}


def _grapefruit_rim(s: Spot, _r: dict) -> Layers:
    x, y = s.cx - s.rx + 4, s.rim_y + 6
    return {
        "top": [
            circle(x, y, 27, "buckthorn"),
            citrus_wheel(x, y, 25, "grapefruit", "grapefruit_light", segments=10),
        ]
    }


def _lemon_float(s: Spot, _r: dict) -> Layers:
    """음료 위에 뜬 레몬 조각(눕혀서 납작하게)."""
    x, y = s.cx + s.level_rx * 0.2, s.level + 1
    wheel = citrus_wheel(0, 0, s.level_rx * 0.42, "lemon", "lemon_light")
    return {"top": [group([wheel], f"translate({x} {y}) scale(1 {GLASS_RATIO * 1.6})")]}


def _lemon_slices(s: Spot, _r: dict) -> Layers:
    """유리잔 속에 잠긴 레몬 조각(비스듬히 서 있다)."""
    out = []
    for dx, fy, angle in ((-0.35, 0.3, -20), (0.3, 0.62, 16)):
        x, y = s.cx + dx * s.level_rx, s.level + fy * (s.bottom - s.level)
        wheel = citrus_wheel(0, 0, 20, "lemon", "lemon_light")
        out.append(group([wheel], f"translate({x} {y}) rotate({angle}) scale(0.6 1)"))
    return {"inside": out}


def _ice_cubes(s: Spot, _r: dict) -> Layers:
    span = s.bottom - s.level
    size = s.level_rx * 0.5
    spots = [(-0.3, 0.2, -12), (0.3, 0.45, 14)]
    if span > 80:
        spots.append((-0.15, 0.72, 6))
    cubes = [
        _ice(s.cx + dx * s.level_rx, s.level + fy * span, size, angle) for dx, fy, angle in spots
    ]
    return {"inside": cubes, "top": [_ice(s.cx + 0.12 * s.level_rx, s.level - 2, size * 0.9, 22)]}


def _straw(s: Spot, r: dict) -> Layers:
    start = (s.cx + 8, s.level + 30)
    end = (s.cx + s.rx * 0.55 + 14, s.rim_y - 40)
    return {"back": [line(d("M", *start, "L", *end), r.get("straw", "berry"), 9)]}


def _raspberries(s: Spot, _r: dict) -> Layers:
    span = s.bottom - s.level
    return {
        "inside": [
            _raspberry(s.cx - s.level_rx * 0.4, s.level + span * 0.35, 9),
            _raspberry(s.cx + s.level_rx * 0.3, s.level + span * 0.7, 9),
        ],
        "top": [_raspberry(s.cx - s.level_rx * 0.3, s.level - 3, 10)],
    }


def _strawberry_rim(s: Spot, _r: dict) -> Layers:
    """잔 입구 오른쪽에 꽂은 딸기."""
    x, y = s.cx + s.rx - 6, s.rim_y + 2
    body = "M0 -16C14 -18 20 -4 0 18C-20 -4 -14 -18 0 -16Z"
    seeds = [
        circle(dx, dy, 1.6, "lemon_light")
        for dx, dy in ((-6, -6), (5, -8), (-3, 4), (6, 2), (0, 11))
    ]
    calyx = [
        ellipse(-6, -16, 7, 3.5, "leaf", transform="rotate(-25 -6 -16)"),
        ellipse(6, -16, 7, 3.5, "leaf", transform="rotate(25 6 -16)"),
        ellipse(0, -17, 3, 5, "leaf_shade"),
    ]
    return {"top": [group([path(body, "berry"), *seeds, *calyx], f"translate({x} {y}) rotate(18)")]}


def _mint(s: Spot, _r: dict) -> Layers:
    x, y = s.cx - s.level_rx * 0.15, s.level - 4
    return {"top": [_leaf(x, y, -150, 0.55), _leaf(x, y, -40, 0.55)]}


def _omija(s: Spot, _r: dict) -> Layers:
    """오미자 화채처럼: 꽃 모양 배 조각 두 장 + 잣 + 잔 속 오미자 열매."""
    top = []
    for bx, by, size in ((-0.3, -0.05, 1.0), (0.32, 0.2, 0.8)):
        x, y = s.cx + bx * s.level_rx, s.level + by * s.level_ry
        petals = [
            ellipse(0, -7 * size, 5.5 * size, 8 * size, "cream", transform=f"rotate({angle})")
            for angle in (0, 72, 144, 216, 288)
        ]
        top.append(
            group([*petals, circle(0, 0, 3.5 * size, "lemon")], f"translate({x} {y}) scale(1 0.55)")
        )
    top += [
        ellipse(
            s.cx + dx,
            s.level + dy,
            3.5,
            2.2,
            "cream",
            transform=f"rotate(30 {s.cx + dx} {s.level + dy})",
        )
        for dx, dy in ((4, -5), (-4, 6), (24, -4))
    ]
    span = s.bottom - s.level
    inside = [
        _berry(s.cx + dx * s.level_rx, s.level + fy * span, 4.5, "berry")
        for dx, fy in ((-0.4, 0.4), (-0.3, 0.45), (0.35, 0.6), (0.1, 0.82), (0.2, 0.85))
    ]
    return {"inside": inside, "top": top}  # fmt: skip


def _cranberries(s: Spot, _r: dict) -> Layers:
    span = s.bottom - s.level
    top = [
        _berry(s.cx + dx * s.level_rx, s.level + dy * s.level_ry, 6, "berry")
        for dx, dy in ((-0.4, -0.1), (-0.15, 0.3), (0.25, -0.2), (0.45, 0.2))
    ]
    inside = [
        _berry(s.cx + dx * s.level_rx, s.level + fy * span, 6, "berry_shade")
        for dx, fy in ((-0.3, 0.5), (0.3, 0.7))
    ]
    return {"inside": inside, "top": top}


def _buckthorn(s: Spot, _r: dict) -> Layers:
    """비타민나무(오블레피하) 가지: 좁은 잎과 줄줄이 달린 주황 열매. 몇 알은 음료 위에도."""
    x, y = s.table
    branch = [
        line(f"M{x - 30} {y + 10}L{x + 26} {y - 10}", "crust_shade", 3),
        _leaf(x - 30, y + 10, -35, 0.6),
        _leaf(x + 22, y - 8, -150, 0.6),
    ]
    branch += [
        _berry(x - 18 + i * 9, y + 4 - i * 3.4 + (3 if i % 2 else -3), 5, "buckthorn")
        for i in range(5)
    ]
    top = [
        _berry(s.cx + dx * s.level_rx, s.level + dy * s.level_ry, 5, "buckthorn")
        for dx, dy in ((-0.3, 0), (-0.15, 0.3), (0.25, -0.15))
    ]
    return {"table": branch, "top": top}  # fmt: skip


def _berry_mix(s: Spot, _r: dict) -> Layers:
    """블랙커런트·산딸기를 잔 옆에."""
    x, y = s.table
    return {
        "table": [
            _berry(x - 14, y + 2, 6, "blackcurrant"),
            _berry(x - 4, y + 6, 6, "blackcurrant"),
            _berry(x - 10, y - 6, 6, "blackcurrant"),
            _raspberry(x + 10, y, 9),
        ]
    }


def _honey_dipper(s: Spot, _r: dict) -> Layers:
    """꿀 막대: 손잡이 + 홈이 파인 머리 + 똑 떨어지는 꿀."""
    x, y = s.table
    head = [ellipse(dx, 0, 5, 11, "honey", shaded=dx % 2 == 0) for dx in (-12, -6, 0, 6, 12)]
    return {
        "table": [
            shadow(x, y + 10, 34, 6),
            line(f"M{x + 6} {y + 2}L{x + 40} {y - 14}", "crust", 5),
            group(head, f"translate({x - 10} {y + 10}) rotate(-25)"),
            ellipse(x - 14, y + 22, 6, 3, "honey", opacity=0.9),
        ]
    }


def _vanilla(s: Spot, _r: dict) -> Layers:
    """바닐라 꼬투리 두 개와 바닐라 꽃."""
    x, y = s.table
    fx, fy = x - 24, y - 12
    petals = [
        ellipse(fx, fy - 8, 5, 9, "lemon_light", transform=f"rotate({angle} {fx} {fy})")
        for angle in (0, 72, 144, 216, 288)
    ]
    return {
        "table": [
            line(f"M{x - 34} {y + 6}Q{x} {y - 8} {x + 34} {y + 2}", "choco_shade", 6),
            line(f"M{x - 30} {y + 14}Q{x + 2} {y + 2} {x + 32} {y + 12}", "choco", 6),
            *petals,
            circle(fx, fy, 4, "honey"),
        ]
    }


def _beans(s: Spot, _r: dict) -> Layers:
    x, y = s.table
    out = []
    for bx, by, angle in ((x - 12, y, -20), (x + 10, y + 6, 25)):
        out.append(
            group(
                [ellipse(0, 0, 11, 7.5, "crema"), line("M-8 0Q0 -4 8 0", "coffee", 2.2)],
                f"translate({bx} {by}) rotate({angle})",
            )
        )
    return {"table": out}


# ── 가까운 메뉴 고명(티켓 07) ──────────────────────────────────────────────────


def _whipped(s: Spot, _r: dict) -> Layers:
    """음료 위로 솟은 휘핑크림(핫초코·아인슈페너·스무디): 아래가 넓은 층층 봉우리."""
    base, width = s.level, s.level_rx
    peak = min(s.rim_y, s.level) - 34
    tiers = ((0.0, 1.0), (0.25, 0.86), (0.5, 0.66), (0.72, 0.44), (0.9, 0.22))
    out = []
    for fy, fw in tiers:
        y, rx = base + (peak - base) * fy, width * fw
        out += [
            ellipse(s.cx, y + 3, rx, rx * 0.36, "cream", shaded=True),
            ellipse(s.cx, y, rx, rx * 0.34, "cream"),
        ]
    out.append(ellipse(s.cx - width * 0.2, base + (peak - base) * 0.5, 6, 3, "white", opacity=0.8))
    return {"top": out}


def _marshmallows(s: Spot, _r: dict) -> Layers:
    out = []
    for i, (dx, dy, angle) in enumerate(
        ((-0.45, -0.1, -15), (-0.05, -0.4, 10), (0.38, -0.05, 25), (0.05, 0.3, -5), (-0.4, 0.4, 30))
    ):
        x, y = s.cx + dx * s.level_rx, s.level + dy * s.level_ry
        fill = "berry_cream" if i % 2 else "white"
        spin = f"rotate({angle} {num(x)} {num(y)})"
        out += [
            rect(x - 8, y - 4, 16, 12, fill, rx=4, shaded=True, transform=spin),
            rect(x - 8, y - 6, 16, 10, fill, rx=4, transform=spin),
        ]
    return {"top": out}


def _daisy(x, y, size, center="honey") -> str:
    petals = [
        ellipse(0, -9, 3.6, 7, "white", transform=f"rotate({angle})") for angle in range(0, 360, 40)
    ]
    return group([*petals, circle(0, 0, 4.5, center)], f"translate({x} {y}) scale({size})")


def _chamomile(s: Spot, _r: dict) -> Layers:
    """카모마일 꽃: 잔 옆에 두 송이, 찻물 위에 한 송이."""
    x, y = s.table
    return {
        "table": [_daisy(x - 14, y + 2, 1.1), _daisy(x + 8, y - 8, 0.85)],
        "top": [
            group(
                [_daisy(0, 0, 0.9, "herbal")],
                f"translate({s.cx + s.level_rx * 0.3} {s.level}) scale(1 0.5)",
            )
        ],
    }


def _orange_rim(s: Spot, _r: dict) -> Layers:
    return {"top": [citrus_wheel(s.cx - s.rx + 4, s.rim_y + 6, 24, "apricot", "custard")]}


def _mango_cubes(s: Spot, _r: dict) -> Layers:
    out = []
    for i, (dx, dy) in enumerate(((-0.4, -0.1), (0.05, -0.35), (0.35, 0.15), (-0.1, 0.35))):
        x, y = s.cx + dx * s.level_rx, s.level + dy * s.level_ry
        spin = f"rotate({i * 20 - 15} {num(x)} {num(y)})"
        out += [
            rect(x - 7, y - 5, 14, 13, "apricot", rx=3, shaded=True, transform=spin),
            rect(x - 7, y - 7, 14, 11, "apricot", rx=3, transform=spin),
        ]
    return {"top": out}


def _blueberries(s: Spot, _r: dict) -> Layers:
    span = s.bottom - s.level
    top = [
        _berry(s.cx + dx * s.level_rx, s.level + dy * s.level_ry, 6, "blueberry")
        for dx, dy in ((-0.4, 0.0), (-0.1, -0.35), (0.2, 0.25), (0.45, -0.1))
    ]
    inside = [
        _berry(s.cx + dx * s.level_rx, s.level + fy * span, 6, "blueberry")
        for dx, fy in ((-0.35, 0.75), (0.3, 0.85))
    ]
    return {"inside": inside, "top": top}


def _mint_sprig(s: Spot, _r: dict) -> Layers:
    x, y = s.table
    return {
        "table": [
            line(f"M{x - 26} {y + 8}L{x + 20} {y - 8}", "leaf_shade", 2.5),
            _leaf(x - 24, y + 8, -40, 0.6),
            _leaf(x - 8, y + 2, -120, 0.6),
            _leaf(x + 4, y - 2, -30, 0.55),
            _leaf(x + 18, y - 8, -140, 0.5),
        ]
    }


GARNISHES: dict[str, Callable[[Spot, dict], Layers]] = {
    "steam": _steam,
    "latte_art": _latte_art,
    "cocoa": _cocoa,
    "caramel": _caramel,
    "tea_leaves": _tea_leaves,
    "tea_tag": _tea_tag,
    "lemon_rim": _lemon_rim,
    "grapefruit_rim": _grapefruit_rim,
    "lemon_float": _lemon_float,
    "lemon_slices": _lemon_slices,
    "ice": _ice_cubes,
    "straw": _straw,
    "raspberries": _raspberries,
    "strawberry_rim": _strawberry_rim,
    "mint": _mint,
    "omija": _omija,
    "cranberries": _cranberries,
    "buckthorn": _buckthorn,
    "berry_mix": _berry_mix,
    "honey_dipper": _honey_dipper,
    "vanilla": _vanilla,
    "beans": _beans,
    "whipped": _whipped,
    "marshmallows": _marshmallows,
    "chamomile": _chamomile,
    "orange_rim": _orange_rim,
    "mango": _mango_cubes,
    "blueberries": _blueberries,
    "mint_sprig": _mint_sprig,
}


# ── 잔 ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Glass:
    """유리잔(속이 보인다): 입구 top·바닥 bottom, 입구·바닥 반폭, 음료 높이."""

    cx: float
    top: float
    bottom: float
    top_rx: float
    bottom_rx: float
    level: float
    handle: bool
    table: tuple[float, float]

    def rx_at(self, y: float) -> float:
        t = (y - self.top) / (self.bottom - self.top)
        return self.top_rx + (self.bottom_rx - self.top_rx) * t

    def spot(self, inset: float = 4) -> Spot:
        level_rx = self.rx_at(self.level) - inset
        return Spot(
            self.cx, self.top, self.top_rx, self.top_rx * GLASS_RATIO,
            self.level, level_rx, level_rx * GLASS_RATIO, self.bottom - inset, self.table,
        )  # fmt: skip


def _band(cx, y, rx, bottom, brx, ratio, fill) -> str:
    """잔 속 음료 한 층(윗면 앞쪽 곡선부터 바닥까지)."""
    shape = d(
        "M", cx - rx, y, "A", rx, rx * ratio, 0, 0, 0, cx + rx, y,
        "L", cx + brx, bottom, "A", brx, brx * ratio, 0, 0, 1, cx - brx, bottom, "Z",
    )  # fmt: skip
    return path(shape, fill)


def _liquid_layers(r: dict) -> list[tuple[str, float]]:
    return [(color, float(weight)) for color, weight in r["liquid"]]


def _draw_glass(g: Glass, r: dict, layers: Layers) -> list[str]:
    inset = 4
    s = g.spot(inset)
    bottom, brx = g.bottom - inset, g.bottom_rx - inset
    body = d(
        "M", g.cx - g.top_rx, g.top, "L", g.cx - g.bottom_rx, g.bottom,
        "A", g.bottom_rx, g.bottom_rx * GLASS_RATIO, 0, 0, 0, g.cx + g.bottom_rx, g.bottom,
        "L", g.cx + g.top_rx, g.top, "Z",
    )  # fmt: skip
    parts = [shadow(g.cx, g.bottom + 6, g.bottom_rx * 1.5, 13), *layers["back"]]
    if g.handle:
        mid = (g.top + g.bottom) / 2
        hx = g.cx + g.rx_at(mid)
        handle = d("M", hx - 4, g.top + 22, "c", 40, -4, 40, 74, -2, 72)
        parts.append(line(handle, "glass", 12, opacity=0.7))
    parts.append(path(body, "glass", opacity=0.55))
    # 음료 층: 위층부터 바닥까지 칠하고, 그 위에 아래층을 덮어 칠한다.
    stack = _liquid_layers(r)
    foam = r.get("foam")
    height = bottom - s.level
    foam_height = 12 if foam else 0
    total = sum(weight for _, weight in stack) or 1
    y = s.level + foam_height
    tops = []
    for color, weight in reversed(stack):
        tops.append((color, y))
        y += (height - foam_height) * weight / total
    if foam:
        parts.append(_band(g.cx, s.level, s.level_rx, bottom, brx, GLASS_RATIO, foam))
    for color, top in tops:
        parts.append(_band(g.cx, top, g.rx_at(top) - inset, bottom, brx, GLASS_RATIO, color))
    surface = foam or stack[-1][0]
    parts += [
        ellipse(g.cx, s.level, s.level_rx, s.level_ry, surface),
        ellipse(g.cx - 4, s.level - 1, s.level_rx * 0.7, s.level_ry * 0.6, "white", opacity=0.22),
        *layers["inside"],
        rect(g.cx - g.top_rx + 9, g.top + 18, 7, (g.bottom - g.top) * 0.62, "white", rx=3.5,
             opacity=0.4),
        ellipse(g.cx, g.top, g.top_rx, g.top_rx * GLASS_RATIO, "glass", opacity=0.45),
        *layers["top"],
        *layers["table"],
    ]  # fmt: skip
    return parts


@dataclass(frozen=True)
class Porcelain:
    """도자기 잔(속이 안 보인다): 입구 rim_y·반폭 rx, 몸통 높이, 바닥 반폭, 받침 접시."""

    cx: float
    rim_y: float
    rx: float
    height: float
    bottom_rx: float
    straight: bool  # 머그(곧은 옆면) / 둥근 찻잔
    saucer: tuple[float, float, float] | None  # (cx, cy, rx)
    table: tuple[float, float]
    band: bool = False  # 찻잔 입구 아래 띠

    def spot(self) -> Spot:
        ry = self.rx * CUP_RATIO
        return Spot(
            self.cx, self.rim_y, self.rx, ry, self.rim_y + 3, self.rx - 7, ry - 4.5,
            self.rim_y + self.height, self.table,
        )  # fmt: skip


def _draw_porcelain(p: Porcelain, r: dict, layers: Layers) -> list[str]:
    s = p.spot()
    cup = r.get("cup", "porcelain")
    cx, rim, rx, brx = p.cx, p.rim_y, p.rx, p.bottom_rx
    bottom = rim + p.height
    if p.straight:
        bry = brx * CUP_RATIO
        side = d(
            "M", cx - rx, rim, "L", cx - brx, bottom,
            "A", brx, bry, 0, 0, 0, cx + brx, bottom, "L", cx + rx, rim, "Z",
        )  # fmt: skip
        right = d(
            "M", cx, rim, "L", cx + rx, rim, "L", cx + brx, bottom,
            "A", brx, bry, 0, 0, 1, cx, bottom + bry, "Z",
        )  # fmt: skip
        handle = d("M", cx + rx - 4, rim + 20, "c", 46, -6, 46, 66, -4, 62)
        handle_width = 13
    else:
        belly = rim + p.height * 0.8
        side = d(
            "M", cx - rx, rim, "C", cx - rx, belly, cx - brx, bottom, cx, bottom,
            "C", cx + brx, bottom, cx + rx, belly, cx + rx, rim, "Z",
        )  # fmt: skip
        right = d(
            "M", cx, rim, "L", cx + rx, rim,
            "C", cx + rx, belly, cx + brx, bottom, cx, bottom, "Z",
        )  # fmt: skip
        handle = d(
            "M", cx + rx - 4, rim + p.height * 0.18,
            "c", rx * 0.62, -p.height * 0.05, rx * 0.62, p.height * 0.62,
            -rx * 0.12, p.height * 0.58,
        )  # fmt: skip
        handle_width = 11
    parts = []
    if p.saucer:
        parts.append(plate(*p.saucer, fill="porcelain"))
    else:
        parts.append(shadow(cx + 6, bottom + brx * CUP_RATIO * 0.6, brx * 1.25, 14))
    parts += [
        *layers["back"],
        line(handle, cup, handle_width),
        path(side, cup),
        path(right, cup, shaded=True),
    ]
    if p.band:
        y = rim + 6
        band = rx - 2
        arc = d("M", cx - band, y, "A", band, band * CUP_RATIO, 0, 0, 0, cx + band, y)
        parts.append(line(arc, "honey", 3))
    foam = r.get("foam")
    surface = r["liquid"][-1][0]
    parts += [
        ellipse(cx, rim, rx, s.ry, cup, shaded=True),
        ellipse(cx, s.level, s.level_rx, s.level_ry, surface),
    ]
    if foam:
        parts.append(ellipse(cx, s.level + 0.5, s.level_rx * 0.86, s.level_ry * 0.84, foam))
    else:
        parts.append(
            ellipse(cx - 6, s.level - 1, s.level_rx * 0.55, s.level_ry * 0.5, "white", opacity=0.18)
        )
    return [*parts, *layers["inside"], *layers["top"], *layers["table"]]


@dataclass(frozen=True)
class Takeaway:
    """테이크아웃 종이컵: 뚜껑 + 슬리브(cup 색). 음료는 안 보인다."""

    cx: float = 128
    top: float = 78
    bottom: float = 214
    top_rx: float = 50
    bottom_rx: float = 38
    table: tuple[float, float] = (196, 212)

    def rx_at(self, y: float) -> float:
        t = (y - self.top) / (self.bottom - self.top)
        return self.top_rx + (self.bottom_rx - self.top_rx) * t

    def spot(self) -> Spot:
        ry = self.top_rx * GLASS_RATIO
        return Spot(
            self.cx, self.top - 14, self.top_rx, ry, self.top - 14, self.top_rx, ry,
            self.bottom, self.table,
        )  # fmt: skip


def _ring(cx, y1, rx1, y2, rx2, fill, *, shaded=False) -> str:
    """원뿔대 옆면의 띠(y1 윗선부터 y2 아랫선까지, 앞쪽 곡선)."""
    shape = d(
        "M", cx - rx1, y1, "L", cx - rx2, y2,
        "A", rx2, rx2 * GLASS_RATIO, 0, 0, 0, cx + rx2, y2,
        "L", cx + rx1, y1, "A", rx1, rx1 * GLASS_RATIO, 0, 0, 1, cx - rx1, y1, "Z",
    )  # fmt: skip
    return path(shape, fill, shaded=shaded)


def _draw_takeaway(t: Takeaway, r: dict, layers: Layers) -> list[str]:
    cx = t.cx
    sleeve = r.get("cup", "kraft")
    s1, s2 = t.top + 44, t.top + 96
    right_shade = d(
        "M", cx + t.top_rx * 0.35, t.top, "L", cx + t.top_rx, t.top,
        "L", cx + t.bottom_rx, t.bottom,
        "A", t.bottom_rx, t.bottom_rx * GLASS_RATIO, 0, 0, 1, cx + t.bottom_rx * 0.35,
        t.bottom + t.bottom_rx * GLASS_RATIO * 0.94, "Z",
    )  # fmt: skip
    lid_rx = t.top_rx + 5
    lid_ry = lid_rx * GLASS_RATIO
    return [
        shadow(cx + 4, t.bottom + 8, t.bottom_rx * 1.5, 13),
        *layers["back"],
        _ring(cx, t.top, t.top_rx, t.bottom, t.bottom_rx, "white"),
        _ring(cx, s1, t.rx_at(s1) + 1.5, s2, t.rx_at(s2) + 1.5, sleeve),
        path(right_shade, "porcelain_dark", opacity=0.35),
        # 뚜껑: 테두리 띠 + 솟은 윗면 + 마시는 구멍
        rect(cx - lid_rx, t.top - 8, lid_rx * 2, 12, "porcelain_shade", rx=4),
        ellipse(cx, t.top + 4, lid_rx, lid_ry, "porcelain_shade"),
        ellipse(cx, t.top - 8, lid_rx, lid_ry, "white"),
        ellipse(cx, t.top - 14, lid_rx - 10, lid_ry - 4, "porcelain"),
        ellipse(cx + 14, t.top - 20, 9, 3.5, "coffee"),
        *layers["inside"],
        *layers["top"],
        *layers["table"],
    ]


VESSELS: dict[str, tuple[Glass | Porcelain | Takeaway, Callable]] = {
    # 머그: 곧은 옆면, 받침 없음
    "mug": (Porcelain(118, 92, 56, 96, 52, True, None, (60, 214)), _draw_porcelain),
    # 커피잔: 둥근 잔 + 받침
    "cup": (Porcelain(122, 114, 60, 66, 40, False, (128, 184, 98), (190, 204)), _draw_porcelain),
    # 찻잔: 넓고 낮은 잔 + 받침 + 금색 띠
    "teacup": (
        Porcelain(120, 118, 70, 62, 46, False, (128, 184, 102), (192, 208), band=True),
        _draw_porcelain,
    ),
    # 키 큰 유리잔(에이드·아이스 음료)
    "glass": (Glass(128, 64, 208, 46, 37, 94, False, (196, 214)), _draw_glass),
    # 낮고 넓은 유리잔
    "tumbler": (Glass(128, 112, 206, 56, 48, 132, False, (196, 216)), _draw_glass),
    # 손잡이 유리잔(따뜻한 과일차·라프)
    "glass_mug": (Glass(116, 84, 202, 50, 47, 104, True, (62, 218)), _draw_glass),
    # 테이크아웃 컵
    "takeaway": (Takeaway(), _draw_takeaway),
    # 작은 에스프레소 잔 + 받침(티켓 07)
    "demitasse": (
        Porcelain(122, 130, 48, 50, 30, False, (126, 180, 84), (198, 208)),
        _draw_porcelain,
    ),
}


def draw(r: dict) -> str:
    shape, painter = VESSELS[r["vessel"]]
    spot = shape.spot()
    layers: Layers = {name: [] for name in LAYERS}
    for name in r.get("garnish", ()):
        for layer, parts in GARNISHES[name](spot, r).items():
            layers[layer].extend(parts)
    return svg(r["card"], *painter(shape, r, layers))


# ── 그림 목록 ─────────────────────────────────────────────────────────────────


def _drink(id_, name, category, r, aliases=(), keywords=()) -> Illustration:
    """음료 그림 한 장. 레시피에 카드 색이 없으면 분류의 기본 카드 색."""
    r = {**r, "card": r.get("card") or CATEGORY_CARD[category]}
    return Illustration(
        id=id_,
        name=name,
        category=category,
        draw=draw,
        recipe=r,
        aliases=aliases,
        keywords=keywords,
    )


ILLUSTRATIONS: list[Illustration] = [
    # ── 커피 ──
    _drink(
        "drink_americano", "아메리카노", "coffee",
        recipe("mug", ["coffee"], garnish=["steam", "beans"]),
        aliases=("핫 아메리카노", "따뜻한 아메리카노", "americano", "블랙커피"),
        keywords=("아메리카노",),
    ),
    _drink(
        "drink_iced_americano", "아이스 아메리카노", "coffee",
        recipe("glass", ["coffee"], garnish=["straw", "ice"], straw="white"),
        aliases=("iced americano", "아아"),
        keywords=("아이스아메리카노",),
    ),
    _drink(
        "drink_decaf", "디카페인", "coffee",
        recipe("takeaway", ["coffee"], garnish=["beans"], cup="leaf"),
        aliases=("디카페인 커피", "decaf", "디카프"),
        keywords=("디카페인", "디카페인아메리카노"),
    ),
    _drink(
        "drink_latte", "카페라떼", "coffee",
        recipe("cup", ["latte"], garnish=["latte_art"]),
        aliases=("라떼", "라테", "카페 라테", "카페라테", "caffe latte", "cafe latte", "latte"),
        keywords=("카페라떼", "카페라테"),
    ),
    _drink(
        "drink_iced_latte", "아이스 라떼", "coffee",
        recipe("glass", [["milk", 2], ["coffee", 1]], garnish=["straw", "ice"], straw="white"),
        aliases=("iced latte",),
        keywords=("아이스라떼", "아이스라테", "아이스카페라떼"),
    ),
    _drink(
        "drink_cappuccino", "카푸치노", "coffee",
        recipe("cup", ["latte"], foam="cream", cup="cup_clay", garnish=["cocoa"]),
        aliases=("cappuccino", "카프치노"),
        keywords=("카푸치노",),
    ),
    _drink(
        "drink_caramel_macchiato", "카라멜 마끼아또", "coffee",
        recipe("glass", [["milk", 2], ["coffee", 1.2]], foam="cream", garnish=["caramel"]),
        aliases=("캐러멜 마키아토", "caramel macchiato"),
        keywords=("마끼아또", "마키아토", "마키아또"),
    ),
    _drink(
        "drink_raf", "라프 커피", "coffee",
        recipe("glass_mug", ["milk"], foam="cream", garnish=["vanilla"]),
        aliases=("라프", "raf coffee", "라프 라떼"),
        keywords=("라프커피", "바닐라라프"),
    ),
    # ── 차 ──
    _drink(
        "drink_black_tea", "블랙티", "tea",
        recipe("teacup", ["tea"], garnish=["tea_leaves", "steam"]),
        aliases=("홍차", "black tea", "블랙 티"),
        keywords=("블랙티",),
    ),
    _drink(
        "drink_earl_grey", "얼그레이", "tea",
        recipe("teacup", ["earl_grey"], cup="cup_blue", garnish=["tea_tag"], card="card_lilac"),
        aliases=("earl grey", "얼그레이 티"),
        keywords=("얼그레이",),
    ),
    _drink(
        "drink_milk_tea", "밀크티", "tea",
        recipe("mug", ["milk_tea"], cup="cup_clay", garnish=["tea_leaves"]),
        aliases=("milk tea", "로얄 밀크티"),
        keywords=("밀크티",),
    ),
    _drink(
        "drink_sea_buckthorn_tea", "오블레피하 차", "tea",
        recipe("glass_mug", ["buckthorn"], garnish=["buckthorn"], card="card_cream"),
        aliases=("오블레피하", "sea buckthorn tea", "비타민나무 차", "산자나무 차", "씨벅톤 차"),
        keywords=("오블레피하", "비타민나무", "산자나무", "씨벅톤"),
    ),
    _drink(
        "drink_honey_lemon_tea", "허니레몬티", "tea",
        recipe("glass_mug", ["honey"], garnish=["lemon_float", "honey_dipper"]),
        aliases=("꿀레몬차", "레몬차", "honey lemon tea"),
        keywords=("허니레몬", "꿀레몬", "레몬티"),
    ),
    _drink(
        "drink_berry_tea", "베리티", "tea",
        recipe("teacup", ["berry_tea"], garnish=["berry_mix", "steam"], card="card_rose"),
        aliases=("베리 티", "berry tea", "베리차"),
        keywords=("블랙커런트티",),
    ),
    # ── 에이드·주스 ──
    _drink(
        "drink_lemonade", "레몬에이드", "ade",
        recipe("glass", ["ade"], garnish=["straw", "lemon_slices", "ice", "mint"], straw="leaf",
               card="card_cream"),
        aliases=("레모네이드",),
        keywords=("레몬에이드", "레모네이드"),
    ),
    _drink(
        "drink_grapefruit_ade", "자몽에이드", "ade",
        recipe("glass", ["grapefruit"], garnish=["straw", "ice", "grapefruit_rim"], straw="white",
               card="card_peach"),
        aliases=("grapefruit ade",),
        keywords=("자몽에이드", "자몽주스"),
    ),
    _drink(
        "drink_raspberry_ade", "라즈베리 에이드", "ade",
        recipe("glass", ["raspberry_ade"], garnish=["straw", "ice", "raspberries", "mint"],
               straw="white", card="card_rose"),
        aliases=("raspberry ade", "산딸기 에이드"),
        keywords=("라즈베리에이드", "산딸기에이드"),
    ),
    _drink(
        "drink_strawberry_lemon_ade", "스트로베리레몬 에이드", "ade",
        recipe("glass", [["berry", 1], ["ade", 1.3]], garnish=["straw", "ice", "lemon_rim",
               "strawberry_rim"], straw="white"),
        aliases=("딸기레몬에이드", "strawberry lemonade"),
        keywords=("스트로베리레몬", "딸기레몬"),
    ),
    _drink(
        "drink_omija_ade", "오미자 에이드", "ade",
        recipe("glass", ["omija"], garnish=["straw", "ice", "omija"], straw="white",
               card="card_lilac"),
        aliases=("omija ade",),
        keywords=("오미자",),
    ),
    _drink(
        "drink_mors", "모르스", "ade",
        recipe("tumbler", ["mors"], garnish=["cranberries", "mint"], card="card_mint"),
        aliases=("mors", "크랜베리 모르스"),
        keywords=("모르스", "크랜베리주스"),
    ),
    # ── 가까운 메뉴(티켓 07): 카페에 흔한 음료 변형 ──
    _drink(
        "drink_espresso", "에스프레소", "coffee",
        recipe("demitasse", ["coffee"], foam="crema", garnish=["beans", "steam"]),
        aliases=("espresso", "에스프레소 샷", "도피오", "리스트레토"),
        keywords=("에스프레소", "espresso"),
    ),
    _drink(
        "drink_hot_chocolate", "핫초코", "coffee",
        recipe("mug", ["choco"], cup="cup_blue", garnish=["marshmallows", "steam"],
               card="card_rose"),
        aliases=("초코", "핫 초코", "코코아", "hot chocolate", "초코 음료"),
        keywords=("핫초코", "핫초콜릿", "핫초콜렛", "코코아", "초코라떼", "초코라테",
                  "초콜릿라떼", "초콜렛라떼", "hotchocolate", "cocoa"),
    ),
    _drink(
        "drink_iced_chocolate", "아이스 초코", "coffee",
        recipe("glass", ["cocoa"], garnish=["straw", "ice", "whipped"], straw="berry",
               card="card_peach"),
        aliases=("iced chocolate", "초코 프라페"),
        keywords=("아이스초코", "아이스초콜릿", "아이스초콜렛", "아이스코코아", "초코프라페"),
    ),
    _drink(
        "drink_vanilla_latte", "바닐라 라떼", "coffee",
        recipe("mug", ["latte"], cup="white", garnish=["latte_art", "vanilla"],
               card="card_cream"),
        aliases=("vanilla latte", "바닐라라테"),
        keywords=("바닐라라떼", "바닐라라테", "vanillalatte"),
    ),
    _drink(
        "drink_mocha", "카페모카", "coffee",
        recipe("glass_mug", [["coffee", 1], ["choco", 0.7]], foam="cream", garnish=["cocoa"],
               card="card_peach"),
        aliases=("모카", "카페 모카", "cafe mocha", "mocha"),
        keywords=("카페모카", "모카라떼", "모카커피"),
    ),
    _drink(
        "drink_cold_brew", "콜드브루", "coffee",
        recipe("tumbler", ["coffee"], garnish=["ice", "beans"], card="card_sky"),
        aliases=("콜드 브루", "더치 커피", "cold brew"),
        keywords=("콜드브루", "더치커피", "coldbrew"),
    ),
    _drink(
        "drink_einspanner", "아인슈페너", "coffee",
        recipe("glass", ["coffee"], garnish=["whipped", "cocoa"], card="card_cream"),
        aliases=("비엔나 커피", "einspanner"),
        keywords=("아인슈페너", "아인슈패너", "비엔나커피"),
    ),
    _drink(
        "drink_matcha_latte", "녹차 라떼", "tea",
        recipe("cup", ["matcha"], garnish=["latte_art"], card="card_mint"),
        aliases=("말차 라떼", "그린티 라떼", "matcha latte"),
        keywords=("녹차라떼", "녹차라테", "말차라떼", "말차라테", "그린티라떼", "matchalatte"),
    ),
    _drink(
        "drink_green_tea", "녹차", "tea",
        recipe("teacup", ["matcha_light"], garnish=["tea_leaves", "steam"], card="card_cream"),
        aliases=("그린티", "녹차 티", "말차", "green tea"),
        keywords=("그린티", "greentea"),
    ),
    _drink(
        "drink_chamomile", "카모마일 티", "tea",
        recipe("teacup", ["herbal"], cup="white", garnish=["chamomile"], card="card_sky"),
        aliases=("캐모마일", "카모마일 차", "chamomile tea"),
        keywords=("카모마일", "캐모마일", "chamomile"),
    ),
    _drink(
        "drink_peppermint", "페퍼민트 티", "tea",
        recipe("glass_mug", ["mint_tea"], garnish=["mint", "mint_sprig", "steam"],
               card="card_mint"),
        aliases=("민트 티", "박하차", "peppermint tea"),
        keywords=("페퍼민트", "민트티", "박하차", "peppermint"),
    ),
    _drink(
        "drink_citron_tea", "유자차", "tea",
        recipe("mug", ["lemon"], cup="cup_clay", garnish=["lemon_float", "steam"],
               card="card_peach"),
        aliases=("유자 티", "유자", "청귤차", "자몽차"),
        keywords=("유자차", "유자티", "청귤차", "자몽차"),
    ),
    _drink(
        "drink_iced_tea", "아이스티", "tea",
        recipe("glass", ["tea_light"], garnish=["straw", "ice", "lemon_rim"], straw="white",
               card="card_sand"),
        aliases=("아이스 티", "복숭아 아이스티", "iced tea"),
        keywords=("아이스티", "icedtea"),
    ),
    _drink(
        "drink_strawberry_smoothie", "딸기 스무디", "ade",
        recipe("glass", ["berry_cream"], garnish=["straw", "strawberry_rim"], straw="white",
               card="card_lilac"),
        aliases=("딸기 라떼", "strawberry smoothie"),
        keywords=("딸기스무디", "스트로베리스무디", "딸기라떼", "딸기주스", "딸기쉐이크"),
    ),
    _drink(
        "drink_mango_smoothie", "망고 스무디", "ade",
        recipe("glass", ["mango"], garnish=["straw", "mango", "mint"], straw="leaf",
               card="card_sky"),
        aliases=("mango smoothie",),
        keywords=("망고스무디", "망고주스", "망고에이드", "망고"),
    ),
    _drink(
        "drink_yogurt_smoothie", "요거트 스무디", "ade",
        recipe("glass", [["blueberry", 0.7], ["meringue", 2]], garnish=["straw", "blueberries"],
               straw="blueberry", card="card_rose"),
        aliases=("요구르트 스무디", "블루베리 요거트 스무디", "smoothie"),
        keywords=("스무디", "요거트", "요구르트", "블루베리스무디", "smoothie"),
    ),
    _drink(
        "drink_orange_juice", "오렌지 주스", "ade",
        recipe("glass", ["apricot"], garnish=["straw", "orange_rim"], straw="white",
               card="card_mint"),
        aliases=("오렌지", "오렌지 쥬스", "orange juice"),
        keywords=("오렌지주스", "오렌지쥬스", "오렌지에이드", "착즙오렌지", "orangejuice"),
    ),
    _drink(
        "drink_kvass", "크바스", "ade",
        recipe("glass_mug", ["nut_shade"], foam="cream", card="card_sand"),
        aliases=("끄바스", "kvass", "Квас"),
        keywords=("크바스", "끄바스", "kvass", "квас"),
    ),
]  # fmt: skip
