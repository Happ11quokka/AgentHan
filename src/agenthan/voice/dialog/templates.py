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


def _josa(word: str, after_batchim: str, after_vowel: str) -> str:
    return f"{word}{after_batchim if _has_batchim(word) else after_vowel}"


def eun_neun(word: str) -> str:
    """메도빅 → 메도빅은, 아메리카노 → 아메리카노는."""
    return _josa(word, "은", "는")


def i_rang(word: str) -> str:
    """메도빅 → 메도빅이랑, 아메리카노 → 아메리카노랑."""
    return _josa(word, "이랑", "랑")


def i_na(word: str) -> str:
    """블린 → 블린이나, 아메리카노 → 아메리카노나."""
    return _josa(word, "이나", "나")


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
    return f"{items_noted(phrases)} {anything_else()}"


def anything_else() -> str:
    return "더 필요하신 거 있으세요?"


def not_on_menu(menu_names: Sequence[str]) -> str:
    return f"죄송해요, 그 메뉴는 없어요. 저희는 {i_ga(_join(menu_names))} 있어요."


# ---------------------------------------------------------------- 옵션·변경·삭제·요청 (티켓 03)


def ask_required_option(menu_name: str, choices: Sequence[str]) -> str:
    """필수 옵션 하나를 묻는다. 예: "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"."""
    if len(choices) == 2:
        first, second = choices
        return f"{eun_neun(menu_name)} {first} 드릴까요, {second} 드릴까요?"
    return f"{eun_neun(menu_name)} {_join(choices)} 중에 어떤 걸로 드릴까요?"


def option_not_available(menu_name: str, options: Sequence[str]) -> str:
    """그 메뉴에 없는 옵션을 말했을 때. 되는 옵션이 있으면 알려 준다."""
    if not options:
        return f"죄송해요, {eun_neun(menu_name)} 따로 고를 수 있는 옵션이 없어요."
    can = f"{eun_neun(_join(options))} 할 수 있어요."
    return f"죄송해요, {eun_neun(menu_name)} 그 옵션이 없어요. {can}"


def option_unavailable(option_name: str) -> str:
    """옵션은 있는데 오늘 안 될 때 (`MenuOption.available=False`)."""
    return f"죄송해요, 오늘은 {i_ga(option_name)} 안 돼요."


def which_item() -> str:
    """대상 메뉴를 모르는 옵션·요청. 예: 장바구니가 빈 채로 "디카페인으로요"."""
    return "어떤 메뉴에 해 드릴까요?"


def removed(menu_name: str) -> str:
    return f"{menu_name} 뺐어요."


def not_in_cart(menu_name: str) -> str:
    return f"주문하신 것 중에 {eun_neun(menu_name)} 없어요."


def request_noted(menu_name: str, request: str) -> str:
    return f"{menu_name}에 {request}라고 적어 둘게요."


# ---------------------------------------------------------------- 픽업·확인·마무리


def ask_pickup() -> str:
    return "몇 시쯤 오세요?"


def pickup_not_understood() -> str:
    return "죄송해요, 몇 시쯤 오실지 한 번 더 말씀해 주세요."


def final_readback(
    cart_summary: str,
    pickup: datetime,
    total: int,
    *,
    requests: Sequence[tuple[str, str]] = (),
    after_fix: bool = False,
) -> str:
    """최종 복창. 장바구니 객체에서 만든다 (D6).

    `requests`는 (메뉴 이름, 요청 원문). 정정한 뒤 다시 복창할 때는 앞에 "그럼"을 붙인다.
    """
    head = "그럼 " if after_fix else ""
    parts = [f"{head}{cart_summary}, {spoken_time(pickup)} 픽업, 총 {won(total)}이에요."]
    parts += [f"{name}에 {request}라고 적어 뒀어요." for name, request in requests]
    return " ".join([*parts, "맞으세요?"])


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


# ---------------------------------------------------------------- 메뉴 되묻기·대안 (티켓 02)


def confirm_menu(name: str) -> str:
    """매칭이 애매할 때 후보 하나를 확인한다."""
    return f"혹시 {name} 말씀하신 거 맞으세요?"


def choose_menu(names: Sequence[str]) -> str:
    """1·2등이 가까울 때. 예: "메도빅이랑 나폴레옹 중에 어떤 거요?"."""
    if len(names) == 2:
        return f"{i_rang(names[0])} {names[1]} 중에 어떤 거요?"
    return f"{_join(names)} 중에 어떤 거요?"


def menu_declined(menu_names: Sequence[str]) -> str:
    """되묻기에 "아니요"라고 했을 때. 메뉴 목록을 다시 알려 준다."""
    return f"죄송해요. 저희는 {i_ga(_join(menu_names))} 있어요."


def sold_out(name: str) -> str:
    return f"죄송해요, {eun_neun(name)} 오늘 다 떨어졌어요."


def suggest_menu(names: Sequence[str]) -> str:
    """품절 대신 권하기. 하나면 "나폴레옹은 어떠세요?", 둘이면 "나폴레옹이나 블린은 어떠세요?"."""
    if len(names) == 1:
        return f"{eun_neun(names[0])} 어떠세요?"
    head = ", ".join([*names[:-2], i_na(names[-2])])
    return f"{head} {eun_neun(names[-1])} 어떠세요?"


# ---------------------------------------------------------------- 메뉴 질문 (티켓 05)


def _ieyo(word: str) -> str:
    """케이크 → 케이크예요, 토핑 → 토핑이에요."""
    return _josa(word, "이에요", "예요")


def dont_know() -> str:
    """필드에 근거가 없는 질문. 지어내지 않는다."""
    return "그건 제가 정확히 모르겠어요."


def menu_description(name: str, description: str) -> str:
    return f"{eun_neun(name)} {_ieyo(description.rstrip('. '))}."


def menu_price(name: str, price: int) -> str:
    return f"{eun_neun(name)} {won(price)}이에요."


def menu_options(name: str, options: Sequence[str]) -> str:
    if not options:
        return f"{eun_neun(name)} 따로 고를 수 있는 옵션이 없어요."
    return f"{eun_neun(name)} {_join(options)} 할 수 있어요."


def option_possible(name: str, option: str, extra_price: int) -> str:
    said = f"네, {eun_neun(name)} {option} 할 수 있어요."
    return f"{said} {won(extra_price)} 추가예요." if extra_price else said


def allergen_list(name: str, allergens: Sequence[str]) -> str:
    return f"{name}에는 {i_ga(_join(allergens))} 들어가요."


def allergen_present(name: str, allergen: str) -> str:
    return f"네, {name}에는 {i_ga(allergen)} 들어가요."


# ---------------------------------------------------------------- 정정·취소·사장님 연결 (티켓 05)


def changed_to(phrases: Sequence[str]) -> str:
    """정정한 항목만 되받는다. 예: "아메리카노 두 잔으로 바꿨어요."."""
    return f"{_euro(_join(phrases))} 바꿨어요."


def pickup_changed(at: datetime) -> str:
    return f"픽업 시간 {_euro(spoken_time(at))} 바꿨어요."


def pickup_noted(at: datetime) -> str:
    """주문 중에 시각만 말했을 때. 예: "6시 픽업으로 알아 둘게요."."""
    return f"{spoken_time(at)} 픽업으로 알아 둘게요."


def confirm_cancel() -> str:
    return "주문 취소할까요?"


def cancelled() -> str:
    return "네, 주문 취소했어요. 감사합니다."


def cancel_declined() -> str:
    return "네, 계속 도와드릴게요."


def too_many_failures() -> str:
    return "죄송해요, 제가 잘 못 알아듣고 있어요."


def handoff() -> str:
    return "사장님께 메모 남겨 드릴게요."
