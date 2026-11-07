"""NLU 인터페이스 (spec §4).

NLU는 발화에서 슬롯 조각만 뽑는다. 메뉴 판정·가격·시각 계산·다음 질문은 엔진 코드가 한다 (D1, D5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ActType = Literal[
    "add_item",
    "remove_item",
    "change_qty",
    "set_option",
    "set_pickup_time",
    "set_name",
    "set_request",
    "yes",
    "no",
    "done_ordering",
    "ask_menu_info",
    "ask_other",
    "repeat_please",
    "cancel_order",
    "want_owner",
    "unknown",
]


@dataclass(frozen=True)
class Act:
    type: ActType
    menu_text: str = ""  # 손님이 말한 그대로 ("메도비크")
    menu_guess: str = ""  # NLU 추정 메뉴명 또는 "" (보조 신호)
    qty: int | None = None
    option_text: str = ""
    time_text: str = ""  # "여섯 시 반" 그대로. 계산은 코드가 한다
    free_text: str = ""  # 요청사항·질문 원문


@dataclass(frozen=True)
class NluContext:
    menu_names: tuple[str, ...]
    cart_summary: str
    last_question: str
    state: str


class Nlu(Protocol):
    def parse(self, text: str, ctx: NluContext) -> list[Act]: ...
