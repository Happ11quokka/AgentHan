"""규칙 기반 가짜 NLU. HCX 키가 나오기 전 개발·테스트용 (spec D2).

인식기(recognizer)를 차례로 돌려 act를 모은다. 표현을 넓힐 때는 인식기를 추가하거나 고친다.
- `_EXCLUSIVE`: 발화 전체가 그 뜻일 때만 걸리고, 걸리면 다른 인식기는 돌리지 않는다 (네/아니요)
- `_RECOGNIZERS`: 한 발화에서 여러 개가 같이 걸릴 수 있다 (메뉴 + 픽업 시간 등)

티켓 01 범위: "{메뉴명} 하나/두 개", "그게 다예요", "네/아니요", "{N}시".
"""

from __future__ import annotations

import re
from collections.abc import Callable

from agenthan.voice.nlu.base import Act, NluContext

Recognizer = Callable[[str, NluContext], list[Act]]

_TRAILING = re.compile(r"[\s.,!?~]+$")

# ---------------------------------------------------------------- 네 / 아니요

_YES = re.compile(r"(네|예|응|맞아요|맞아|맞습니다|좋아요|그래요)")
_NO = re.compile(r"(아니요|아뇨|아니|아니에요|틀려요)")


def _yes_no(text: str, ctx: NluContext) -> list[Act]:
    t = _TRAILING.sub("", text)
    if _YES.fullmatch(t):
        return [Act("yes")]
    if _NO.fullmatch(t):
        return [Act("no")]
    return []


# ---------------------------------------------------------------- 픽업 시간

# 시각 조각만 그대로 뽑는다. 계산은 `timeparse.parse_pickup`이 한다 (티켓 04).
_HOUR_NUM = r"(?:\d{1,2}|열한|열두|열|한|두|세|네|다섯|여섯|일곱|여덟|아홉)"
_SINO_NUM = r"(?:[이삼사오]?십[일이삼사오육칠팔구]?|[일이삼사오육칠팔구])"
_MINUTES = rf"(?:\d{{1,2}}|{_SINO_NUM})\s*분"
_LATER = r"(?:좀\s*)?(?:이따가?|있다가)"
_RELATIVE = (
    rf"(?:{_MINUTES}|{_HOUR_NUM}\s*시간(?:\s*반)?)\s*(?:뒤|후|있다가?|이따가?)"
)
_CLOCK = (
    rf"(?:(?:오전|오후|아침|낮|저녁|밤)\s*)?{_HOUR_NUM}\s*시(?!간)(?:\s*반|\s*{_MINUTES})?"
    r"|\d{1,2}:\d{2}"
)
_ASAP = (
    r"(?:지금(?:\s*바로)?|바로|곧|금방|잠시\s*(?:후|뒤))"
    r"(?=\s*(?:에\s*)?(?:갈|가|출발|도착|들|방문|이요|요|$))"
)
_TIME = re.compile(rf"{_RELATIVE}|(?:{_LATER}\s*)?(?:{_CLOCK})|{_ASAP}|{_LATER}")


def _pickup_time(text: str, ctx: NluContext) -> list[Act]:
    m = _TIME.search(text)
    return [Act("set_pickup_time", time_text=m.group(0))] if m else []


# ---------------------------------------------------------------- 주문 끝

_DONE = re.compile(r"(그게|이게|그거|이거)\s*(다|전부)|그거면\s*돼|없어요|됐어요|끝이에요")


def _done_ordering(text: str, ctx: NluContext) -> list[Act]:
    return [Act("done_ordering")] if _DONE.search(text) else []


# ---------------------------------------------------------------- 메뉴 + 수량

_NATIVE_QTY = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4}
_QTY = re.compile(r"(\d+)\s*(?:개|잔)?|(하나|둘|셋|넷)|(한|두|세|네)\s*(?:개|잔)")
# 메뉴 목록에 없는 이름 + 수량. 예: "짜장면 하나요" → menu_text="짜장면" (판정은 엔진의 매처)
_UNLISTED_ITEM = re.compile(
    r"(?P<menu>[가-힣A-Za-z][가-힣A-Za-z ]*?)\s*"
    r"(?P<qty>\d+\s*(?:개|잔)?|하나|둘|셋|넷|(?:한|두|세|네)\s*(?:개|잔))"
    r"\s*(?:요|이요|주세요|만)?$"
)


def _parse_qty(text: str) -> int | None:
    m = _QTY.search(text)
    if m is None:
        return None
    if m.group(1):
        return int(m.group(1))
    return _NATIVE_QTY[m.group(2) or m.group(3)]


def _items(text: str, ctx: NluContext) -> list[Act]:
    text = _TIME.sub(" ", text)  # "6시"의 6을 수량으로 읽지 않게
    found = sorted(
        (pos, name)
        for name in ctx.menu_names
        for pos in (m.start() for m in re.finditer(re.escape(name), text))
    )
    acts = []
    for i, (pos, name) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        qty = _parse_qty(text[pos + len(name) : end])
        acts.append(Act("add_item", menu_text=name, menu_guess=name, qty=qty))
    if acts:
        return acts
    m = _UNLISTED_ITEM.search(_TRAILING.sub("", text))
    if m is None:
        return []
    return [Act("add_item", menu_text=m.group("menu").strip(), qty=_parse_qty(m.group("qty")))]


# ---------------------------------------------------------------- 조립

_EXCLUSIVE: tuple[Recognizer, ...] = (_yes_no,)
_RECOGNIZERS: tuple[Recognizer, ...] = (_items, _done_ordering, _pickup_time)


class FakeNlu:
    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        text = text.strip()
        for recognize in _EXCLUSIVE:
            if acts := recognize(text, ctx):
                return acts
        acts = [act for recognize in _RECOGNIZERS for act in recognize(text, ctx)]
        return acts or [Act("unknown", free_text=text)]
