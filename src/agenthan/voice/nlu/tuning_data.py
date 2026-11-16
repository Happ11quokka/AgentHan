"""HCX 튜닝 학습 데이터 생성기 (5단계 티켓 08).

가상 가게 여러 곳(`fixtures/phone/tuning_stores.json`) × 손님 말 틀로
(문맥, 손님 말, 정답 act)를 만든다.
정답은 틀에서 바로 나오므로 사람이 라벨을 달 필요가 없다. 평가 가게(샘플 베이커리·쌀국수집)는
학습에 넣지 않는다 → 튜닝 모델이 처음 보는 가게에도 통하는지 잰다(티켓 05 범용 원칙).

- STT식 변형: 메뉴명 자모 흔들기(ㅔ↔ㅐ, ㄱ↔ㅋ, 받침 빠짐 등)·띄어쓰기. 들린 그대로가 menu_text,
  진짜 메뉴명이 menu_guess
- 문맥 짧은 대답: "두 개요"(수량), "차갑게요"(꼭 고를 것), "네/아니요"(되묻기·복창)
- 한 행 = 대화 하나(C_ID), 턴 하나(T_ID 0). 지시문은 `hcx.tuned_system`(추론과 같은 함수)

    uv run python -m agenthan.voice.nlu.tuning_data --rows 1000 --out private/tuning/train.jsonl

CLOVA 형식: JSONL, 열 순서 System_Prompt·C_ID·T_ID·Text·Completion, UTF-8(BOM 없음), 한 행
8,000자 이하. API 문서는 "UTF8-sig"라고 하지만 BOM을 붙이면 1행 JSON 오류로 학습이 실패했다
(2026-11-11, 작업 4ceh2sgp).
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agenthan.core.contracts import MenuItem, MenuOption
from agenthan.voice.dialog import templates
from agenthan.voice.hangul import decompose, has_batchim
from agenthan.voice.nlu.base import NluContext
from agenthan.voice.nlu.catalog import menu_catalog
from agenthan.voice.nlu.hcx import tuned_system
from agenthan.voice.store_config import RequiredOption, StoreConfig

STORES = Path(__file__).parents[4] / "fixtures" / "phone" / "tuning_stores.json"
MAX_ROW_CHARS = 8_000
ANYTHING_ELSE = templates.anything_else()

# 가게에 없는 음식 (없는 메뉴 주문). 그 가게 메뉴와 겹치면 고르지 않는다
OTHER_FOODS = (
    "피자",
    "돈까스",
    "비빔밥",
    "냉면",
    "초밥",
    "햄버거",
    "제육볶음",
    "파스타",
    "짜장면",
    "후라이드치킨",
    "김치찌개",
    "샐러드",
)
TEMPERATURE_WORDS = {
    "따뜻하게": ("따뜻한", "뜨거운", "따뜻하게", "핫"),
    "차갑게": ("차가운", "아이스", "차갑게", "시원한"),
}


@dataclass(frozen=True)
class Store:
    name: str
    menu: tuple[MenuItem, ...]
    config: StoreConfig
    catalog: tuple[str, ...]

    def required(self, item: MenuItem) -> tuple[RequiredOption, ...]:
        return tuple(self.config.required_options.get(item.id, ()))


@dataclass
class Sample:
    text: str
    acts: list[dict[str, Any]]
    cart: str = ""
    question: str = ""
    state: str = "ordering"
    kind: str = ""
    store: Store | None = None


def load_stores(path: Path = STORES) -> list[Store]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    stores = []
    for s in raw["stores"]:
        menu = tuple(
            MenuItem(
                id=m["id"],
                name_ko=m["name_ko"],
                price=m["price"],
                category=m.get("category", ""),
                aliases=tuple(m.get("aliases", ())),
                options=tuple(
                    MenuOption(o["name"], extra_price=o.get("extra_price", 0))
                    for o in m.get("options", ())
                ),
            )
            for m in s["menu"]
        )
        config = StoreConfig.from_store_json(s)
        stores.append(
            Store(s["store"]["name"], menu, config, menu_catalog(menu, config.required_options))
        )
    return stores


# ---------------------------------------------------------------- 한국어 조각


def _josa(word: str, batchim: str, vowel: str) -> str:
    return word + (batchim if has_batchim(word) else vowel)


def _ro(word: str) -> str:
    """방향 조사: 순살 → 순살로, 매운맛 → 매운맛으로, 아이스 → 아이스로 (ㄹ 받침은 "로")."""
    if not has_batchim(word):
        return word + "로"
    j = decompose(word[-1])
    return word + ("로" if j and j[2] == "ㄹ" else "으로")


def _yo(word: str) -> str:
    """끝맺음: 하나 → 하나요, 한 잔 → 한 잔이요."""
    return word + ("이요" if has_batchim(word) else "요")


def _unit(item: MenuItem) -> str:
    return templates.counter_for(item.category)


def _qty(rng: random.Random, item: MenuItem) -> tuple[int, str]:
    n = rng.choices((1, 2, 3, 4), weights=(5, 3, 1.5, 0.5))[0]
    return n, templates.qty_phrase(n, _unit(item))


def _end(rng: random.Random, qty_phrase: str) -> str:
    """ "하나" → "하나요" / "하나 주세요" / "하나만 주세요" …"""
    tail = rng.choice(
        ("요", " 주세요", "만 주세요", " 부탁해요", " 주실래요?", "요.", " 할게요", " 주세요.")
    )
    return _yo(qty_phrase) + tail[1:] if tail.startswith("요") else qty_phrase + tail


def _filler(rng: random.Random) -> str:
    return rng.choices(("", "음 ", "어 ", "저기요 ", "음… "), weights=(10, 2, 1, 1, 0.5))[0]


# ---------------------------------------------------------------- STT식 변형

_SWAPS = {
    "ㅔ": "ㅐ",
    "ㅐ": "ㅔ",
    "ㅗ": "ㅓ",
    "ㅓ": "ㅗ",
    "ㅜ": "ㅡ",
    "ㅡ": "ㅜ",
    "ㅕ": "ㅓ",
    "ㄱ": "ㅋ",
    "ㅋ": "ㄱ",
    "ㄷ": "ㅌ",
    "ㅌ": "ㄷ",
    "ㅂ": "ㅍ",
    "ㅍ": "ㅂ",
    "ㅈ": "ㅊ",
    "ㅊ": "ㅈ",
    "ㅅ": "ㅆ",
    "ㄲ": "ㄱ",
    "ㄸ": "ㄷ",
    "ㅃ": "ㅂ",
    "ㅉ": "ㅈ",
}
_I, _M, _F = (
    "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ",
    "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ",
    " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ",
)


def _compose(i: str, m: str, f: str) -> str:
    return chr(0xAC00 + (_I.index(i) * 21 + _M.index(m)) * 28 + _F.index(f))


def misheard(name: str, rng: random.Random) -> str:
    """메뉴명을 STT가 잘못 받아 적은 것처럼 한두 군데 바꾼다. 못 바꾸면 그대로."""
    chars = list(name)
    spots = [i for i, c in enumerate(chars) if decompose(c) and decompose(c)[1] != " "]
    if not spots:
        return name
    for _ in range(rng.choice((1, 1, 2))):
        i = rng.choice(spots)
        ini, med, fin = decompose(chars[i])  # type: ignore[misc]
        roll = rng.random()
        if roll < 0.4 and med in _SWAPS and _SWAPS[med] in _M:
            med = _SWAPS[med]
        elif roll < 0.75 and ini in _SWAPS:
            ini = _SWAPS[ini]
        elif fin != " ":
            fin = " "  # 받침 빠짐 (메도빅 → 메도비)
        elif ini in _SWAPS:
            ini = _SWAPS[ini]
        chars[i] = _compose(ini, med, fin)
    heard = "".join(chars)
    if rng.random() < 0.3:
        heard = heard.replace(" ", "") if " " in heard else heard
    return heard


# ---------------------------------------------------------------- 틀

Template = Callable[["Gen"], Sample | None]


@dataclass
class Gen:
    store: Store
    rng: random.Random
    noise: float = 0.25
    _items: list[MenuItem] = field(init=False)

    def __post_init__(self) -> None:
        self._items = list(self.store.menu)

    def item(self, cond: Callable[[MenuItem], bool] = lambda _: True) -> MenuItem | None:
        pool = [m for m in self._items if cond(m)]
        return self.rng.choice(pool) if pool else None

    def said(self, item: MenuItem) -> str:
        """손님이 부른 이름: 메뉴명·다른 이름, 가끔 STT 변형."""
        name = self.rng.choice((item.name_ko,) * 3 + item.aliases) if item.aliases else item.name_ko
        if self.rng.random() < self.noise:
            heard = misheard(name, self.rng)
            if all(heard != m.name_ko for m in self._items):
                return heard
        return name

    def cart_of(self, *items: MenuItem) -> str:
        return ", ".join(templates.line_phrase(i.name_ko, 1, _unit(i)) for i in items)


def _act(type_: str, **fields: Any) -> dict[str, Any]:
    return {"type": type_, **{k: v for k, v in fields.items() if v not in ("", None)}}


def _add(said: str, item: MenuItem | None, qty: int | None, option: str = "") -> dict[str, Any]:
    return _act(
        "add_item",
        menu_text=said,
        menu_guess=item.name_ko if item else "",
        qty=qty,
        option_text=option,
    )


def t_add_one(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    said = g.said(item)
    n, qp = _qty(g.rng, item)
    return Sample(f"{_filler(g.rng)}{said} {_end(g.rng, qp)}", [_add(said, item, n)], kind="add")


def t_add_two(g: Gen) -> Sample | None:
    a = g.item()
    b = g.item(lambda m: m is not a)
    if a is None or b is None:
        return None
    sa, sb = g.said(a), g.said(b)
    na, qa = _qty(g.rng, a)
    nb, qb = _qty(g.rng, b)
    conj = g.rng.choice(
        (
            lambda q: _josa(q, "이랑", "랑"),
            lambda q: q + " 하고",
            lambda q: q + "에",
            lambda q: q + " 그리고",
        )
    )
    text = f"{_filler(g.rng)}{sa} {conj(qa)} {sb} {_end(g.rng, qb)}"
    return Sample(text, [_add(sa, a, na), _add(sb, b, nb)], kind="add2")


def _option_phrase(g: Gen, item: MenuItem) -> tuple[str, str] | None:
    """(손님 말 조각, option_text). 옵션이나 꼭 고를 것 하나."""
    choices: list[tuple[str, str]] = []
    for o in item.options:
        name = o.name
        if name.endswith(" 빼기"):
            t = name[:-3]
            choices += [
                (f"{t} 빼 주세요", f"{t} 빼"),
                (f"{_josa(t, '은', '는')} 빼고요", f"{_josa(t, '은', '는')} 빼"),
                (f"{t} 없이", f"{t} 없이"),
            ]
        elif name.endswith(" 추가"):
            t = name[:-3]
            choices += [(f"{t} 추가해서", f"{t} 추가"), (f"{t} 추가로", f"{t} 추가")]
        elif name.endswith(" 변경"):
            t = name[:-3]
            choices += [(f"{_ro(t)} 바꿔서", t), (f"{_ro(t)}요", t)]
        else:
            choices.append((name, name))
    for group in g.store.required(item):
        for c in group.choices:
            for word in TEMPERATURE_WORDS.get(c.name, (c.name,)):
                choices.append((f"{_ro(word)}", word))
    return g.rng.choice(choices) if choices else None


def t_add_option(g: Gen) -> Sample | None:
    item = g.item(lambda m: bool(m.options) or bool(g.store.required(m)))
    if item is None:
        return None
    picked = _option_phrase(g, item)
    if picked is None:
        return None
    phrase, option = picked
    said = g.said(item)
    n, qp = _qty(g.rng, item)
    if g.rng.random() < 0.5:
        text = f"{said} {g.rng.choice((_yo(qp) + ',', qp, qp + ' 주시고'))} {phrase}"
        text += (
            "" if phrase.endswith(("요", "세요")) else g.rng.choice((" 주세요", "요", " 해 주세요"))
        )
    else:
        text = f"{said} {phrase} {_end(g.rng, qp)}"
    return Sample(text, [_add(said, item, n, option)], kind="add_option")


def t_attached_temperature(g: Gen) -> Sample | None:
    item = g.item(lambda m: any(gr.name == "온도" for gr in g.store.required(m)))
    if item is None:
        return None
    word = g.rng.choice(("아이스", "아이스", "핫", "따뜻한", "차가운"))
    n, qp = _qty(g.rng, item)
    sep = "" if word in ("아이스", "핫") and g.rng.random() < 0.6 else " "
    text = f"{word}{sep}{item.name_ko} {_end(g.rng, qp)}"
    return Sample(text, [_add(item.name_ko, item, n, word)], kind="attached")


def t_set_option_in_cart(g: Gen) -> Sample | None:
    item = g.item(lambda m: bool(m.options))
    if item is None:
        return None
    picked = _option_phrase(g, item)
    if picked is None:
        return None
    phrase, option = picked
    said = g.said(item)
    if g.rng.random() < 0.5:
        text = f"{_josa(said, '에', '에')} {phrase}" + ("" if phrase.endswith("요") else " 주세요")
        act = _act("set_option", menu_text=said, menu_guess=item.name_ko, option_text=option)
    else:
        text = phrase + ("" if phrase.endswith("요") else g.rng.choice((" 주세요", "요")))
        act = _act("set_option", option_text=option)
    return Sample(text, [act], cart=g.cart_of(item), question=ANYTHING_ELSE, kind="set_option")


def t_required_answer(g: Gen) -> Sample | None:
    item = g.item(lambda m: bool(g.store.required(m)))
    if item is None:
        return None
    group = g.rng.choice(g.store.required(item))
    choice = g.rng.choice(group.choices).name
    word = g.rng.choice(TEMPERATURE_WORDS.get(choice, (choice,)))
    text = g.rng.choice(
        (_yo(word), f"{_ro(word)} 주세요", f"{_ro(word)}요", f"음 {word}", f"{_ro(word)} 할게요")
    )
    question = templates.ask_required_option(item.name_ko, [c.name for c in group.choices])
    return Sample(
        text,
        [_act("set_option", option_text=word)],
        cart=g.cart_of(item),
        question=question,
        state="clarify",
        kind="required",
    )


def t_change_qty(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    n = g.rng.choice((2, 3, 4))
    qp = templates.qty_phrase(n, _unit(item))
    if g.rng.random() < 0.55:
        said = g.said(item)
        text = g.rng.choice(
            (
                f"{said} {_ro(qp)} 해 주세요",
                f"{said} {_ro(qp)} 바꿔 주세요",
                f"{_josa(said, '은', '는')} {_yo(_ro(qp))}",
            )
        )
        act = _act("change_qty", menu_text=said, menu_guess=item.name_ko, qty=n)
    else:
        text = g.rng.choice((_yo(qp), f"{_ro(qp)} 해 주세요", f"아 {_yo(qp)}"))
        act = _act("change_qty", qty=n)
    return Sample(text, [act], cart=g.cart_of(item), question=ANYTHING_ELSE, kind="change_qty")


def t_remove(g: Gen) -> Sample | None:
    a = g.item()
    b = g.item(lambda m: m is not a)
    if a is None or b is None:
        return None
    said = g.said(a)
    text = g.rng.choice(
        (
            f"{_josa(said, '은', '는')} 빼 주세요",
            f"{said} 취소해 주세요",
            f"{_josa(said, '은', '는')} 안 할게요",
            f"{said} 빼고요",
        )
    )
    return Sample(
        text,
        [_act("remove_item", menu_text=said, menu_guess=a.name_ko)],
        cart=g.cart_of(a, b),
        question=ANYTHING_ELSE,
        kind="remove",
    )


def t_remove_and_add(g: Gen) -> Sample | None:
    a = g.item()
    b = g.item(lambda m: m is not a)
    if a is None or b is None:
        return None
    sa, sb = g.said(a), g.said(b)
    text = f"{_josa(sa, '은', '는')} 빼고 {sb} {templates.qty_phrase(1, _unit(b))} 추가해 주세요"
    return Sample(
        text,
        [_act("remove_item", menu_text=sa, menu_guess=a.name_ko), _add(sb, b, 1)],
        cart=g.cart_of(a),
        question=ANYTHING_ELSE,
        kind="swap",
    )


TIMES = (
    "여섯 시 반",
    "6시 반",
    "일곱 시",
    "7시",
    "삼십 분 뒤",
    "20분 후",
    "저녁 일곱 시",
    "다섯 시 십오 분",
    "한 시간 뒤",
    "8시쯤",
    "지금 바로",
    "열두 시 반",
    "6시 40분",
)


def t_pickup(g: Gen) -> Sample | None:
    t = g.rng.choice(TIMES)
    end = "에 갈게요" if not t.endswith(("뒤", "후", "바로", "쯤")) else " 갈게요"
    text = g.rng.choice(
        (
            f"{t}{end}",
            f"{t}쯤 찾으러 갈게요" if not t.endswith("쯤") else f"{t} 찾으러 갈게요",
            _yo(t),
        )
    )
    item = g.item()
    assert item is not None
    return Sample(
        text,
        [_act("set_pickup_time", time_text=t)],
        cart=g.cart_of(item),
        question=templates.ask_pickup(),
        state="ask_pickup",
        kind="pickup",
    )


def t_add_and_pickup(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    said = g.said(item)
    n, qp = _qty(g.rng, item)
    t = g.rng.choice(("다섯 시", "6시 반", "일곱 시", "삼십 분 뒤", "4시"))
    tail = "에 찾으러 갈게요" if not t.endswith("뒤") else "에 갈게요"
    return Sample(
        f"{said} {qp} 주시고 {t}{tail}",
        [_add(said, item, n), _act("set_pickup_time", time_text=t)],
        kind="add_pickup",
    )


def t_done(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    text = g.rng.choice(
        (
            "그게 다예요",
            "이렇게만 주세요",
            "됐어요",
            "아니요 그게 다예요",
            "그거면 돼요",
            "없어요 그거면 충분해요",
            "아니요 됐어요",
            "그렇게만 할게요",
        )
    )
    return Sample(
        text, [_act("done_ordering")], cart=g.cart_of(item), question=ANYTHING_ELSE, kind="done"
    )


def t_done_and_pickup(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    t = g.rng.choice(("6시", "일곱 시", "여섯 시 반", "30분 뒤"))
    text = g.rng.choice((f"그게 다고요 {t}에 갈게요", f"이게 다예요 {t}에 찾으러 갈게요"))
    return Sample(
        text,
        [_act("done_ordering"), _act("set_pickup_time", time_text=t)],
        cart=g.cart_of(item),
        question=ANYTHING_ELSE,
        kind="done_pickup",
    )


def t_yes_no(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    yes = g.rng.random() < 0.6
    text = (
        g.rng.choice(("네", "네 맞아요", "응 맞아", "맞아요", "네네"))
        if yes
        else g.rng.choice(("아니요", "아뇨", "아니요 틀려요"))
    )
    if g.rng.random() < 0.5:
        question = f"혹시 {item.name_ko} 말씀하신 거 맞으세요?"
        state = "clarify"
    else:
        question = (
            f"{templates.line_phrase(item.name_ko, 1, _unit(item))}, 6시 반 픽업, "
            f"총 {templates.won(item.price)}이에요. 맞으세요?"
        )
        state = "confirm"
    return Sample(
        text,
        [_act("yes" if yes else "no")],
        cart=g.cart_of(item),
        question=question,
        state=state,
        kind="yes_no",
    )


def t_confirm_fix(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    n = g.rng.choice((2, 3))
    said = g.said(item)
    text = f"아니요, {said} {_yo(templates.qty_phrase(n, _unit(item)))}"
    question = (
        f"{templates.line_phrase(item.name_ko, 1, _unit(item))}, 6시 반 픽업, "
        f"총 {templates.won(item.price)}이에요. 맞으세요?"
    )
    return Sample(
        text,
        [_act("change_qty", menu_text=said, menu_guess=item.name_ko, qty=n)],
        cart=g.cart_of(item),
        question=question,
        state="confirm",
        kind="confirm_fix",
    )


def t_ask_menu(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    said = g.said(item)
    text = g.rng.choice(
        (
            f"{said} 많이 {g.rng.choice(('달아요', '매워요', '짜요', '셔요'))}?",
            f"{_josa(said, '은', '는')} 얼마예요?",
            f"{_josa(said, '에', '에')} 뭐 들어가요?",
            f"{_josa(said, '은', '는')} 어떤 맛이에요?",
            f"{said} 양 많아요?",
            f"{_josa(said, '에', '에')} 견과류 들어가요?",
        )
    )
    return Sample(
        text,
        [_act("ask_menu_info", menu_text=said, menu_guess=item.name_ko, free_text=text)],
        kind="ask_menu",
    )


def t_ask_other(g: Gen) -> Sample | None:
    text = g.rng.choice(
        (
            "주차 돼요?",
            "몇 시까지 해요?",
            "가게가 어디에 있어요?",
            "카드 돼요?",
            "화장실 있어요?",
            "배달도 돼요?",
            "예약할 수 있어요?",
        )
    )
    return Sample(text, [_act("ask_other", free_text=text)], kind="ask_other")


def t_request(g: Gen) -> Sample | None:
    item = g.item()
    assert item is not None
    if g.rng.random() < 0.5:
        said = g.said(item)
        req = g.rng.choice(("따로 담아 주세요", "잘라 주세요", "포장 꼼꼼히 해 주세요"))
        text = f"{_josa(said, '은', '는')} {req}"
        act = _act("set_request", menu_text=said, menu_guess=item.name_ko, free_text=req)
    else:
        text = g.rng.choice(
            (
                "포크 두 개 넣어 주세요",
                "젓가락 챙겨 주세요",
                "물티슈 좀 주세요",
                "숟가락 넣어 주세요",
            )
        )
        act = _act("set_request", free_text=text)
    return Sample(text, [act], cart=g.cart_of(item), question=ANYTHING_ELSE, kind="request")


def t_unknown(g: Gen) -> Sample | None:
    text = g.rng.choice(
        (
            "어 그러니까",
            "음 잠깐만요",
            "여보세요?",
            "아 네네",
            "어디 보자",
            "오늘 날씨 좋네요",
            "음…",
            "그 뭐더라",
        )
    )
    return Sample(text, [_act("unknown")], kind="unknown")


def t_not_on_menu(g: Gen) -> Sample | None:
    names = {m.name_ko for m in g.store.menu} | {a for m in g.store.menu for a in m.aliases}
    food = g.rng.choice([f for f in OTHER_FOODS if f not in names])
    n = g.rng.choice((1, 2))
    return Sample(
        f"{food} {_end(g.rng, templates.qty_phrase(n))}", [_add(food, None, n)], kind="not_on_menu"
    )


def t_misc(g: Gen) -> Sample | None:
    kind, text = g.rng.choice(
        (
            ("want_owner", "사장님 좀 바꿔 주세요"),
            ("want_owner", "사장님이랑 통화할 수 있어요?"),
            ("cancel_order", "주문 취소할게요"),
            ("cancel_order", "그냥 안 시킬게요"),
            ("repeat_please", "네? 다시 말씀해 주세요"),
            ("repeat_please", "잘 못 들었어요"),
        )
    )
    act = _act(kind, free_text=text) if kind == "want_owner" else _act(kind)
    return Sample(text, [act], kind=kind)


TEMPLATES: tuple[tuple[Template, float], ...] = (
    (t_add_one, 14),
    (t_add_two, 8),
    (t_add_option, 10),
    (t_attached_temperature, 3),
    (t_set_option_in_cart, 6),
    (t_required_answer, 6),
    (t_change_qty, 5),
    (t_remove, 4),
    (t_remove_and_add, 2),
    (t_pickup, 6),
    (t_add_and_pickup, 3),
    (t_done, 5),
    (t_done_and_pickup, 2),
    (t_yes_no, 5),
    (t_confirm_fix, 2),
    (t_ask_menu, 7),
    (t_ask_other, 3),
    (t_request, 3),
    (t_unknown, 3),
    (t_not_on_menu, 3),
    (t_misc, 3),
)


def generate(rows: int, *, seed: int = 11, stores: Sequence[Store] | None = None) -> list[Sample]:
    rng = random.Random(seed)
    stores = list(stores or load_stores())
    funcs, weights = zip(*TEMPLATES, strict=True)
    out: list[Sample] = []
    seen: set[tuple[str, str, str, str]] = set()
    tries = 0
    while len(out) < rows and tries < rows * 20:
        tries += 1
        store = rng.choice(stores)
        sample = rng.choices(funcs, weights=weights)[0](Gen(store, rng))
        if sample is None:
            continue
        key = (store.name, sample.text, sample.cart, sample.question)
        if key in seen:
            continue
        seen.add(key)
        sample.store = store
        out.append(sample)
    return out


def context(sample: Sample) -> NluContext:
    store = sample.store
    assert store is not None
    return NluContext(
        menu_names=tuple(m.name_ko for m in store.menu),
        cart_summary=sample.cart,
        last_question=sample.question,
        state=sample.state,
        menu_catalog=store.catalog,
    )


def rows(samples: Sequence[Sample]) -> list[dict[str, Any]]:
    out = []
    for i, s in enumerate(samples):
        completion = json.dumps({"acts": s.acts}, ensure_ascii=False, separators=(",", ":"))
        row = {
            "System_Prompt": tuned_system(context(s)),
            "C_ID": i,
            "T_ID": 0,
            "Text": s.text,
            "Completion": completion,
        }
        if (
            sum(len(str(v)) for k, v in row.items() if k in ("System_Prompt", "Text", "Completion"))
            > MAX_ROW_CHARS
        ):
            raise ValueError(f"한 행이 {MAX_ROW_CHARS}자를 넘어요: {s.text!r}")
        out.append(row)
    return out


def write_jsonl(path: Path, data: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HCX 튜닝 학습 데이터 만들기")
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--out", type=Path, default=Path("private/tuning/train.jsonl"))
    args = parser.parse_args(argv)
    samples = generate(args.rows, seed=args.seed)
    data = rows(samples)
    write_jsonl(args.out, data)
    kinds: dict[str, int] = {}
    for s in samples:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    chars = sum(len(r["System_Prompt"]) + len(r["Text"]) + len(r["Completion"]) for r in data)
    print(f"{len(data)}행 → {args.out} (글자 {chars:,}, 행 평균 {chars // max(len(data), 1):,})")
    print("종류:", ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
