"""그림체 바탕: 모든 대표 그림이 같이 쓰는 팔레트·배경 카드·도형.

그림체 규칙(spec): 플랫, 외곽선 없음, 위에서 45도로 내려다본 각도, 정사각형 viewBox,
연한 단색 배경 카드, 한정 팔레트, 그림 안에 글자 없음.

색은 PALETTE 이름으로만 쓴다(없는 이름이면 KeyError). 새 색이 꼭 필요하면 여기에 더하고
갤러리에서 다른 그림과 어울리는지 본다. 가족 파일(defaults·cakes·drinks·dishes)은 이 모듈의
도형을 조합해 그림을 만들고, 자기 ILLUSTRATIONS 목록에 Illustration을 올린다.
"""

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from agenthan.illustration.library import CATEGORIES

SIZE = 256
CARD_RADIUS = 36
# 45도로 내려다보면 바닥의 원이 세로로 눌린 타원이 된다(세로/가로 비).
TILT = 0.6

PALETTE = {
    # 배경 카드(연한 단색)
    "card_rose": "#F9E3E3",
    "card_cream": "#FBF0DA",
    "card_peach": "#FCE6D8",
    "card_sand": "#F2EADF",
    "card_sky": "#E1EDF6",
    "card_mint": "#E2F0E3",
    "card_lilac": "#ECE6F4",
    # 그릇·잔
    "white": "#FFFFFF",
    "porcelain": "#FAF7F2",
    "porcelain_shade": "#E6DFD3",
    "porcelain_dark": "#CFC5B5",
    "silver": "#DDE1E7",
    "silver_shade": "#B8BFCA",
    "teal": "#7FB6B0",
    "teal_shade": "#5E9790",
    # 케이크·빵
    "cream": "#FFF5E1",
    "cream_shade": "#F0DDB9",
    "sponge": "#F2C97E",
    "sponge_shade": "#DDAE5C",
    "honey": "#E59E37",
    "honey_shade": "#C48124",
    "crust": "#D89148",
    "crust_shade": "#B56F2E",
    "choco": "#6E4128",
    "choco_shade": "#4F2D1B",
    # 음료
    "coffee": "#5A3724",
    "crema": "#B27B4C",
    "milk": "#EFDDC3",
    "tea": "#C8732B",
    "tea_light": "#E2A156",
    "ade": "#F7C948",
    "ade_light": "#FBE38E",
    "glass": "#FFFFFF",
    "ice": "#F4FBFD",
    # 과일·채소·요리
    "berry": "#D6465C",
    "berry_shade": "#AE3048",
    "lemon": "#F5D04A",
    "lemon_light": "#FCEBA2",
    "tomato": "#E4573F",
    "soup": "#D4553C",
    "soup_light": "#E8875F",
    "meat": "#B5683A",
    "meat_shade": "#8E4E2A",
    "leaf": "#6CA95A",
    "leaf_shade": "#4E8944",
}

# 분류마다 기본 배경 카드 색. 가족 그림은 recipe의 card로 바꿀 수 있다.
CATEGORY_CARD = {
    "cake": "card_rose",
    "bakery": "card_cream",
    "savory": "card_peach",
    "soup": "card_sky",
    "coffee": "card_sand",
    "tea": "card_mint",
    "ade": "card_sky",
    "other": "card_lilac",
}


@dataclass(frozen=True)
class Illustration:
    """카탈로그 한 줄 + 그리는 법.

    id: 파일 이름(영문 소문자·숫자·밑줄). name: 한국어 이름. aliases: 정확히 같아야 붙는 다른
    이름(띄어쓰기·기호는 무시). keywords: 메뉴 이름에 들어 있으면 붙는 말(가장 긴 말이 이긴다).
    category: library.CATEGORIES 중 하나. recipe: 그림 재료(JSON으로 카탈로그에 실린다).
    draw: recipe를 받아 SVG 문서 전체를 돌려준다(보통 svg(card, ...)).
    """

    id: str
    name: str
    category: str
    draw: Callable[[dict], str] = field(repr=False, compare=False)
    recipe: dict = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.id):
            raise ValueError(f"그림 id는 영문 소문자·숫자·밑줄만: {self.id!r}")
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.id}: 분류는 {CATEGORIES} 중 하나: {self.category!r}")

    def render(self) -> str:
        return self.draw(self.recipe)

    def catalog_row(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "keywords": list(self.keywords),
            "category": self.category,
            "recipe": self.recipe,
        }


# ── 글자 만들기 ────────────────────────────────────────────────────────────────


def num(value: float) -> str:
    """좌표 한 개. 소수 한 자리까지만 써서 파일을 작게 둔다."""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def d(*parts: str | float) -> str:
    """경로 명령. 숫자는 num()으로 줄인다: d("M", 10, 20.25, "L", 30, 40) → "M 10 20.2 L 30 40"."""
    return " ".join(part if isinstance(part, str) else num(part) for part in parts)


def paint(name: str) -> str:
    return PALETTE[name]


def shade(name: str) -> str:
    """같은 색의 그늘 면. 팔레트에 <이름>_shade가 있으면 그것, 없으면 조금 어둡게."""
    if f"{name}_shade" in PALETTE:
        return PALETTE[f"{name}_shade"]
    value = PALETTE[name].lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{round(c * 0.86):02X}" for c in (r, g, b))


def _style(fill: str, opacity: float | None, transform: str | None) -> str:
    out = f' fill="{fill}"'
    if opacity is not None:
        out += f' opacity="{num(opacity)}"'
    if transform:
        out += f' transform="{transform}"'
    return out


def _fill(name: str, shaded: bool) -> str:
    return shade(name) if shaded else paint(name)


def ellipse(cx, cy, rx, ry, fill, *, opacity=None, transform=None, shaded=False) -> str:
    style = _style(_fill(fill, shaded), opacity, transform)
    return f'<ellipse cx="{num(cx)}" cy="{num(cy)}" rx="{num(rx)}" ry="{num(ry)}"{style}/>'


def circle(cx, cy, r, fill, *, opacity=None, shaded=False) -> str:
    style = _style(_fill(fill, shaded), opacity, None)
    return f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(r)}"{style}/>'


def rect(x, y, w, h, fill, *, rx=0, opacity=None, transform=None, shaded=False) -> str:
    corner = f' rx="{num(rx)}"' if rx else ""
    style = _style(_fill(fill, shaded), opacity, transform)
    return f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}"{corner}{style}/>'


def polygon(points: Sequence[tuple[float, float]], fill, *, opacity=None, shaded=False) -> str:
    coords = " ".join(f"{num(x)},{num(y)}" for x, y in points)
    return f'<polygon points="{coords}"{_style(_fill(fill, shaded), opacity, None)}/>'


def path(d: str, fill, *, opacity=None, transform=None, shaded=False) -> str:
    return f'<path d="{d}"{_style(_fill(fill, shaded), opacity, transform)}/>'


def line(d: str, color, width, *, opacity=None) -> str:
    """둥근 끝 선(김, 빨대, 손잡이). 도형의 외곽선으로 쓰지 않는다."""
    extra = f' opacity="{num(opacity)}"' if opacity is not None else ""
    return (
        f'<path d="{d}" fill="none" stroke="{paint(color)}" stroke-width="{num(width)}"'
        f' stroke-linecap="round" stroke-linejoin="round"{extra}/>'
    )


def group(parts: Sequence[str], transform: str) -> str:
    return f'<g transform="{transform}">{"".join(parts)}</g>'


def shadow(cx, cy, rx, ry=None) -> str:
    """바닥 그림자: 카드 색을 살짝 어둡게(검정 7%)."""
    ry = rx * TILT * 0.5 if ry is None else ry
    return (
        f'<ellipse cx="{num(cx)}" cy="{num(cy)}" rx="{num(rx)}" ry="{num(ry)}"'
        ' fill="#000000" opacity="0.07"/>'
    )


def svg(card: str, *parts: str) -> str:
    """정사각형 문서 + 연한 단색 배경 카드."""
    body = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}"'
        f' width="{SIZE}" height="{SIZE}">'
        f'<rect width="{SIZE}" height="{SIZE}" rx="{CARD_RADIUS}" fill="{paint(card)}"/>'
        f"{body}</svg>\n"
    )


# ── 45도 도형 ─────────────────────────────────────────────────────────────────


def plate(cx, cy, rx, fill="porcelain") -> str:
    """접시: 테두리 그늘 + 안쪽 면."""
    ry = rx * TILT
    return (
        shadow(cx, cy + ry * 0.35, rx * 1.02, ry * 0.95)
        + ellipse(cx, cy + ry * 0.12, rx, ry, fill, shaded=True)
        + ellipse(cx, cy, rx, ry, fill)
        + ellipse(cx, cy + ry * 0.04, rx * 0.72, ry * 0.72, fill, shaded=True, opacity=0.45)
    )


def cylinder(cx, top, rx, height, side, top_fill=None, *, bottom_rx=None) -> str:
    """원기둥(아래가 좁으면 컵 모양): 옆면 + 윗면 타원. top: 윗면 중심의 y."""
    ry = rx * TILT
    brx = rx if bottom_rx is None else bottom_rx
    bry = brx * TILT
    bottom = top + height
    d = (
        f"M{num(cx - rx)} {num(top)}"
        f"L{num(cx - brx)} {num(bottom)}"
        f"A{num(brx)} {num(bry)} 0 0 0 {num(cx + brx)} {num(bottom)}"
        f"L{num(cx + rx)} {num(top)}Z"
    )
    return path(d, side, shaded=True) + ellipse(cx, top, rx, ry, top_fill or side)


def citrus_wheel(cx, cy, r, rind, pith, *, segments: int = 8) -> str:
    """레몬·자몽 같은 과일 단면: 껍질 원 + 흰 속 + 조각들."""
    parts = [circle(cx, cy, r, rind), circle(cx, cy, r * 0.84, pith)]
    step = 2 * math.pi / segments
    for i in range(segments):
        start, end = i * step + 0.09, (i + 1) * step - 0.09
        middle = (start + end) / 2
        tip = (cx + r * 0.1 * math.cos(middle), cy + r * 0.1 * math.sin(middle))
        arc = [
            (cx + r * 0.72 * math.cos(a), cy + r * 0.72 * math.sin(a))
            for a in (start, middle, end)
        ]
        parts.append(polygon([tip, *arc], rind))
    return "".join(parts)


def cake_slice(
    cx: float,
    apex_y: float,
    radius: float,
    height: float,
    layers: Sequence[tuple[str, float]],
    top: str,
    *,
    span: float = 56,
) -> str:
    """삼각 조각 케이크: 뾰족한 끝이 앞을 보고 양쪽 자른 면에 층이 보인다.

    apex_y: 윗면 뾰족한 끝의 y. layers: 위에서부터 (색 이름, 두께 비율). 비율 합이 1이 아니면
    비율대로 나눈다. top: 윗면 색. 왼쪽 면은 그늘색으로 칠한다.
    """
    left = math.radians(270 - span / 2)
    right = math.radians(270 + span / 2)

    def at(angle: float, depth: float) -> tuple[float, float]:
        return cx + radius * math.cos(angle), apex_y + radius * math.sin(angle) * TILT + depth

    apex = lambda depth: (cx, apex_y + depth)  # noqa: E731
    total = sum(weight for _, weight in layers) or 1
    parts = []
    depth = 0.0
    for color, weight in layers:
        lower = depth + height * weight / total
        for angle, shaded in ((left, True), (right, False)):
            parts.append(
                polygon(
                    [apex(depth), at(angle, depth), at(angle, lower), apex(lower)],
                    color,
                    shaded=shaded,
                )
            )
        depth = lower
    arc = [at(math.radians(270 - span / 2 + span * i / 12), 0) for i in range(13)]
    parts.append(polygon([apex(0), *arc], top))
    return "".join(parts)


# ── 러시아 요리 색(티켓 04) ──────────────────────────────────────────────────────
# 보르시의 비트 빛 수프, 사모바르의 놋쇠, 콤포트의 과일 빛 음료, 살구.
PALETTE.update(
    {
        "beet": "#B2334F",
        "beet_light": "#D0617A",
        "brass": "#DDA640",
        "brass_shade": "#B07E22",
        "compote": "#C65A5E",
        "compote_light": "#E89482",
        "apricot": "#F2A54A",
    }
)


# ── 음료 색(티켓 03) ──────────────────────────────────────────────────────────
# 음료끼리 한눈에 구분되도록 음료 색·잔 색을 더한다. 거품·라떼아트는 cream·white를 쓴다.
PALETTE.update(
    {
        "latte": "#C99B6A",
        "earl_grey": "#8F3B22",
        "milk_tea": "#D3A679",
        "buckthorn": "#F39A26",
        "berry_tea": "#8C2F4E",
        "blackcurrant": "#4A2340",
        "grapefruit": "#F2846F",
        "grapefruit_light": "#FBC3B3",
        "raspberry_ade": "#EC6F8A",
        "omija": "#C42F4A",
        "mors": "#7A1D38",
        "cup_blue": "#9DB7D5",
        "cup_clay": "#D98C6A",
        "kraft": "#C9A57A",
    }
)


# ── 케이크·디저트 색(티켓 02) ──────────────────────────────────────────────────

PALETTE.update(
    {
        "cocoa": "#A06A43",  # 코코아 시트(초코보다 밝다)
        "caramel": "#C27A33",
        "berry_cream": "#F4B9C4",  # 베리 크림·사워베리 크림
        "custard": "#F8D985",
        "cheese": "#FCEBB6",
        "cheese_top": "#E8B45E",  # 구운 치즈케이크 윗면
        "carrot": "#EE8636",
        "carrot_sponge": "#D6843F",
        "nut": "#B27A47",
        "nut_shade": "#8C5A30",
        "almond": "#EBCB9B",
        "blueberry": "#4F5E9E",
        "meringue": "#FFFCF4",
        "meringue_shade": "#EEE3CD",
    }
)


# ── 가까운 메뉴 색(티켓 07) ────────────────────────────────────────────────────
# 말차(녹차 케이크·녹차 라떼), 레드벨벳 시트, 망고, 허브차(카모마일), 민트차, 구운 자국.
PALETTE.update(
    {
        "matcha": "#8DB255",
        "matcha_light": "#C6DC9C",
        "red_velvet": "#B8323F",
        "mango": "#F6B13C",
        "herbal": "#EAC461",
        "mint_tea": "#B5D88A",
        "burnt": "#5C3A21",
    }
)
