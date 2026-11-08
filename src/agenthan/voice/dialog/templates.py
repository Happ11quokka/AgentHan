"""손님에게 하는 말은 전부 여기서 만든다 (spec §7).

규칙: 해요체, 짧게. 한 턴에 질문은 하나. 괄호·이모지·마크다운 금지(TTS가 읽지 않음).
한 문장은 150자를 넘지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

# ---------------------------------------------------------------- 수량·단위

# 잔으로 세는 카테고리. 나머지는 개.
_CUP_CATEGORIES = frozenset({"커피", "음료", "차"})

# 1~10 고유어 수사. (단독형, 관형형) 예: 하나/한 잔, 둘/두 잔
_NATIVE_NUMBERS = {
    1: ("하나", "한"),
    2: ("둘", "두"),
    3: ("셋", "세"),
    4: ("넷", "네"),
    5: ("다섯", "다섯"),
    6: ("여섯", "여섯"),
    7: ("일곱", "일곱"),
    8: ("여덟", "여덟"),
    9: ("아홉", "아홉"),
    10: ("열", "열"),
}


def counter_for(category: str) -> str:
    """메뉴 카테고리에 맞는 단위."""
    return "잔" if category in _CUP_CATEGORIES else "개"


def qty_phrase(qty: int, unit: str = "개") -> str:
    """수량 읽기. 1개는 "하나", 2개는 "두 개", 1잔은 "한 잔". 11 이상은 숫자."""
    if qty not in _NATIVE_NUMBERS:
        return f"{qty}{unit}"
    alone, attributive = _NATIVE_NUMBERS[qty]
    if unit == "개" and qty == 1:
        return alone
    return f"{attributive} {unit}"


def line_phrase(name: str, qty: int, unit: str, options: Sequence[str] = ()) -> str:
    """장바구니 한 줄. 예: "아메리카노 디카페인 변경 두 잔"."""
    parts = [name, *options, qty_phrase(qty, unit)]
    return " ".join(parts)


def won(amount: int) -> str:
    return f"{amount:,}원"


def spoken_time(dt: datetime) -> str:
    """픽업 시각 읽기. 18:30 → "6시 반", 18:15 → "6시 15분", 18:00 → "6시"."""
    hour = dt.hour % 12 or 12
    if dt.minute == 0:
        return f"{hour}시"
    if dt.minute == 30:
        return f"{hour}시 반"
    return f"{hour}시 {dt.minute}분"


def _join(phrases: Iterable[str]) -> str:
    return ", ".join(phrases)


def _has_batchim(word: str) -> bool:
    last = word[-1:] if word else ""
    return "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0


def i_ga(word: str) -> str:
    """주격 조사를 붙인다. 블린 → 블린이, 아메리카노 → 아메리카노가."""
    return f"{word}{'이' if _has_batchim(word) else '가'}"


# ---------------------------------------------------------------- 대사

def greeting(store_name: str) -> str:
    return f"네, {store_name}입니다. 주문 도와드릴게요."


def ask_order() -> str:
    return "어떤 걸로 드릴까요?"


def items_noted(phrases: Sequence[str]) -> str:
    """항목을 받았다고 되받기만 한다. 예: "메도빅 하나요."."""
    return f"{_join(phrases)}요."


def item_accepted(phrases: Sequence[str]) -> str:
    """항목 수락 + 다음 질문. 예: "메도빅 하나요. 더 필요하신 거 있으세요?"."""
    return f"{items_noted(phrases)} 더 필요하신 거 있으세요?"


def not_on_menu(menu_names: Sequence[str]) -> str:
    return f"죄송해요, 그 메뉴는 없어요. 저희는 {i_ga(_join(menu_names))} 있어요."


def ask_pickup() -> str:
    return "몇 시쯤 오세요?"


def pickup_not_understood() -> str:
    return "죄송해요, 몇 시쯤 오실지 한 번 더 말씀해 주세요."


def final_readback(cart_summary: str, pickup: datetime, total: int) -> str:
    """최종 복창. 장바구니 객체에서 만든다 (D6)."""
    return f"{cart_summary}, {spoken_time(pickup)} 픽업, 총 {won(total)}이에요. 맞으세요?"


def ask_what_to_fix() -> str:
    return "어떤 걸 고쳐 드릴까요?"


def closing(pickup: datetime) -> str:
    return f"{spoken_time(pickup)}에 준비해 둘게요. 감사합니다."


def not_understood() -> str:
    return "죄송해요, 잘 못 알아들었어요."


# ---------------------------------------------------------------- 픽업 규칙 (티켓 04)


def meridiem_time(dt: datetime) -> str:
    """오전·오후를 붙여 읽기. 11:00 → "오전 11시", 14:00 → "오후 2시", 12:30 → "낮 12시 반"."""
    prefix = "오전" if dt.hour < 12 else "낮" if dt.hour == 12 else "오후"
    return f"{prefix} {spoken_time(dt)}"


def pickup_too_early(prep_minutes: int, offer: datetime) -> str:
    return f"준비에 {prep_minutes}분 정도 걸려요. {spoken_time(offer)} 괜찮으세요?"


def _euro(word: str) -> str:
    """'으로/로'를 붙인다. 5시 25분 → 5시 25분으로, 6시 → 6시로."""
    rieul = word[-1:] and (ord(word[-1]) - ord("가")) % 28 == 8
    return f"{word}{'으로' if _has_batchim(word) and not rieul else '로'}"


def pickup_asap(prep_minutes: int, at: datetime) -> str:
    return f"준비에 {prep_minutes}분 정도 걸려서 {_euro(spoken_time(at))} 잡아 둘게요."


def pickup_after_close(close: datetime) -> str:
    return f"죄송해요, 저희가 {spoken_time(close)}에 문을 닫아요. 그 전에 몇 시쯤 오세요?"


def pickup_before_open(open_at: datetime) -> str:
    return f"죄송해요, 저희는 {spoken_time(open_at)}에 문을 열어요. 몇 시쯤 오세요?"


def confirm_pickup(at: datetime) -> str:
    return f"{meridiem_time(at)}에 오시는 거죠?"


def ask_pickup_again() -> str:
    return "그럼 몇 시쯤 오세요?"
