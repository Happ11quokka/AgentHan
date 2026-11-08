"""규칙 기반 가짜 NLU. HCX 키가 나오기 전 개발·테스트용 (spec D2).

인식기(recognizer)를 차례로 돌려 act를 모은다. 표현을 넓힐 때는 인식기를 추가하거나 고친다.
- `_EXCLUSIVE`: 발화 전체가 그 뜻일 때만 걸리고, 걸리면 다른 인식기는 돌리지 않는다 (네/아니요)
- `_RECOGNIZERS`: 한 발화에서 여러 개가 같이 걸릴 수 있다 (메뉴 + 픽업 시간 등)

티켓 01: "{메뉴명} 하나/두 개", "그게 다예요", "네/아니요", "{N}시".
티켓 03: 여러 항목, 수량(숫자·고유어), 옵션, 빼기, 수량 바꾸기, 요청사항.
티켓 05: 메뉴 질문(`ask_menu_info`), "아니요, ~" 머리말(no + 나머지), 장바구니가 있을 때
메뉴 없는 수량("두 개요") → change_qty. 취소·사장님 연결은 엔진의 규칙 단계(`dialog/rules.py`)가
NLU보다 먼저 잡는다.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.timeparse import ASAP_PATTERN

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


# ---------------------------------------------------------------- 메뉴 질문 (티켓 05)

# 묻는 말. "하나요"의 "나요"는 수량이라 뺀다.
_ASKING = re.compile(r"얼마|무슨|어떤|어때|뭐예요|뭐에요|뭔가요|뭐가|(?<!하)나요$")
# 메뉴 이름 없이 물어도 메뉴 질문으로 보는 말
_MENU_TOPIC = re.compile(
    r"알레르기|알러지|글루텐|비건|칼로리|얼마|가격|옵션|맛|달아|들어가|인분|디카페인"
)
# 물음표가 없어도 질문으로 보는 말 ("메도빅 알레르기 있어요")
_ALWAYS_ASKING = re.compile(r"알레르기|알러지|글루텐|비건|칼로리")


def _menu_question(text: str, ctx: NluContext) -> list[Act]:
    asking = "?" in text or _ASKING.search(_TRAILING.sub("", text))
    if not (asking or _ALWAYS_ASKING.search(text)):
        return []
    named = [n for n in ctx.menu_names if n in text]
    if named:
        name = min(named, key=text.index)
        return [Act("ask_menu_info", menu_text=name, menu_guess=name, free_text=text)]
    return [Act("ask_menu_info", free_text=text)] if _MENU_TOPIC.search(text) else []


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
_TIME = re.compile(rf"{_RELATIVE}|(?:{_LATER}\s*)?(?:{_CLOCK})|{ASAP_PATTERN}|{_LATER}")


def _pickup_time(text: str, ctx: NluContext) -> list[Act]:
    m = _TIME.search(text)
    return [Act("set_pickup_time", time_text=m.group(0))] if m else []


# ---------------------------------------------------------------- 주문 끝

_DONE = re.compile(r"(그게|이게|그거|이거)\s*(다|전부)|그거면\s*돼|없어요|됐어요|끝이에요")


def _done_ordering(text: str, ctx: NluContext) -> list[Act]:
    return [Act("done_ordering")] if _DONE.search(text) else []


# ---------------------------------------------------------------- 메뉴 + 수량·옵션·변경·삭제·요청
#
# 발화에서 메뉴 이름이 나온 자리마다 한 act를 만든다. 메뉴 i에 딸린 말은
# "메뉴 i 앞에서 직전 연결어 뒤까지(prefix)" + "메뉴 i 뒤에서 첫 연결어 앞까지(head)"다.
#   "아이스 아메리카노 하나랑 따뜻한 아메리카노 하나요"
#    prefix=아이스 head=하나 | prefix=따뜻한 head=하나요
# head로 종류를 정한다: 옵션 → (장바구니에 있으면) set_option, 아니면 add_item
#                      빼/취소 → remove_item · "N개로" → change_qty · 따로/많이… → set_request
# 메뉴 이름이 없으면 발화 전체로 같은 순서로 본다 (대상은 엔진이 직전 항목으로 정한다).

_NATIVE_QTY = {
    "하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}
_QTY_PAT = (
    r"\d+\s*(?:개|잔)?"
    r"|(?:하나|둘|셋|넷|다섯|여섯|일곱|여덟|아홉|열)(?:\s*(?:개|잔))?"
    r"|(?:한|두|세|네)\s*(?:개|잔)"
)
_QTY = re.compile(_QTY_PAT)
_UNIT = re.compile(r"\s*(?:개|잔)$")
# 메뉴 목록에 없는 이름 + 수량. 예: "짜장면 하나요" → menu_text="짜장면" (판정은 엔진의 매처)
_UNLISTED_ITEM = re.compile(
    r"(?P<menu>[가-힣A-Za-z][가-힣A-Za-z ]*?)\s*"
    rf"(?P<qty>{_QTY_PAT})"
    r"\s*(?:요|이요|주세요|만)?$"
)
# 손님이 옵션을 부르는 말 (가짜 NLU용 어휘. 어느 옵션인지는 엔진의 options.py가 판정)
_OPTION = re.compile(
    r"따뜻하게|따뜻한|따듯하게|따듯한|뜨겁게|뜨거운|차갑게|차가운|차게|아이스|시원하게|시원한"
    r"|디카페인|샷\s*추가|사워크림\s*(?:빼|없이)|홀\s*케이크"
)
_CONNECTOR = re.compile(r"이랑|랑|하고|그리고|[,.]")
_PARTICLE = re.compile(r"^(?:은|는|을|를|도|에는|에|이|가)?\s*")
_REMOVE = re.compile(r"빼|취소|없애")
_CHANGE = re.compile(rf"({_QTY_PAT})\s*으?로")
_REQUEST = re.compile(r"따로|많이|적게|덜|넉넉히|조금만|잘라|썰어|넣어|담아|데워|챙겨|포장")
_REMOVE_WHAT = re.compile(
    r"(?P<what>[가-힣A-Za-z ]*?)\s*(?:은|는|을|를|도)?\s*(?P<verb>빼|취소|없애)"
)
_THAT_ONE = frozenset(
    {"", "그거", "그것", "이거", "이것", "저거", "그건", "방금 거", "방금거", "마지막 거"}
)


def _parse_qty(text: str) -> int | None:
    m = _QTY.search(text)
    if m is None:
        return None
    word = _UNIT.sub("", m.group(0)).strip()
    return int(word) if word.isdigit() else _NATIVE_QTY[word]


def _option_text(text: str) -> str:
    return " ".join(re.sub(r"\s+", " ", m.group(0)) for m in _OPTION.finditer(text))


def _clean(text: str) -> str:
    return _TRAILING.sub("", _PARTICLE.sub("", text.strip(), count=1)).strip()


def _menu_act(name: str, prefix: str, head: str, ctx: NluContext) -> Act:
    menu = {"menu_text": name, "menu_guess": name}
    qty = _parse_qty(head)
    if option := _option_text(prefix + " " + head):
        if qty is None and name in ctx.cart_summary:
            return Act("set_option", **menu, option_text=option)
        return Act("add_item", **menu, qty=qty, option_text=option)
    if _REMOVE.match(_PARTICLE.sub("", head.strip(), count=1)):
        return Act("remove_item", **menu)
    if m := _CHANGE.search(head):
        return Act("change_qty", **menu, qty=_parse_qty(m.group(1)))
    if _REQUEST.search(head):
        return Act("set_request", **menu, free_text=_clean(head))
    return Act("add_item", **menu, qty=qty)


def _split_head(segment: str) -> tuple[str, str]:
    """메뉴 뒤 구간 → (이 메뉴에 딸린 말, 다음 메뉴 앞에 붙는 말)."""
    m = _CONNECTOR.search(segment)
    return (segment, "") if m is None else (segment[: m.start()], segment[m.end() :])


_BARE_QTY = re.compile(rf"(?:{_QTY_PAT})\s*(?:이요|요|만요|만|으로요|로요)?")


def _no_menu_act(text: str, ctx: NluContext) -> list[Act]:
    t = _TRAILING.sub("", text).strip()
    if ctx.cart_summary and _BARE_QTY.fullmatch(t):  # "두 개요": 방금 다룬 줄의 수량
        return [Act("change_qty", qty=_parse_qty(t))]
    if option := _option_text(t):
        return [Act("set_option", option_text=option)]
    if (m := _REMOVE_WHAT.match(t)) and "주문" not in m.group("what"):
        what = m.group("what").strip()
        if not what and m.group("verb") == "취소":  # 무엇을 말하지 않은 "취소할게요" = 주문 취소
            return [Act("cancel_order", free_text=t)]
        return [Act("remove_item", menu_text="" if what in _THAT_ONE else what)]
    if m := _CHANGE.search(t):
        return [Act("change_qty", qty=_parse_qty(m.group(1)))]
    if _REQUEST.search(t):
        return [Act("set_request", free_text=t)]
    m = _UNLISTED_ITEM.search(t)
    if m is None:
        return []
    return [Act("add_item", menu_text=m.group("menu").strip(), qty=_parse_qty(m.group("qty")))]


def _items(text: str, ctx: NluContext) -> list[Act]:
    text = _TIME.sub(" ", text)  # "6시"의 6을 수량으로 읽지 않게
    found = sorted(
        (pos, name)
        for name in ctx.menu_names
        for pos in (m.start() for m in re.finditer(re.escape(name), text))
    )
    if not found:
        return _no_menu_act(text, ctx)
    acts = []
    prefix = text[: found[0][0]]
    for i, (pos, name) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        head, tail = _split_head(text[pos + len(name) : end])
        acts.append(_menu_act(name, prefix, head, ctx))
        prefix = tail
    return acts


# ---------------------------------------------------------------- 조립

_EXCLUSIVE: tuple[Recognizer, ...] = (_yes_no, _menu_question)
_RECOGNIZERS: tuple[Recognizer, ...] = (_items, _done_ordering, _pickup_time)

# "아니요, 두 개요" → no + "두 개요". "네 개요"·"네 시"(넷)의 "네"는 머리말이 아니다.
_LEAD = re.compile(
    r"(?P<lead>아니요|아뇨|아니|네|예)(?:\s*[,.!]\s*|\s+(?![개잔시]))(?P<rest>\S.*)"
)


# 말 앞의 군말. STT 결과에 그대로 붙어 온다 ("아 그거 빼 주세요"). 뒤에 띄어쓰기가 있을 때만 뗀다
_FILLER = re.compile(r"^(?:(?:아|어|음|엄|저기|그러니까)\s*[,.]?\s+)+")


class FakeNlu:
    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        text = _FILLER.sub("", text.strip()) or text.strip()
        if m := _LEAD.fullmatch(text):
            lead = Act("yes") if m.group("lead") in ("네", "예") else Act("no")
            rest = self.parse(m.group("rest"), ctx)
            return [lead] + [a for a in rest if a.type != "unknown"] or [lead]
        for recognize in _EXCLUSIVE:
            if acts := recognize(text, ctx):
                return acts
        acts = [act for recognize in _RECOGNIZERS for act in recognize(text, ctx)]
        return acts or [Act("unknown", free_text=text)]
