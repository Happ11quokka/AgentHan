"""파트 사이의 약속(계약). 초안 v0 — 첫 주에 세 파트가 함께 확정한다.

세 파트는 서로의 내부 코드를 import하지 않고, 이 파일의 타입과 Protocol만 쓴다.
- Part B(가게 지식)는 `StoreKnowledge`를 구현한다.
- Part A(사장님 비서)와 Part C(전화 주문)는 `StoreKnowledge`를 받아서 쓴다.
- Part C는 주문이 끝나면 `Order`를 만들어 `OrderSink`에 넘긴다. 사장님 화면(Part A·web)이 받는다.

진짜 구현이 나오기 전에는 `agenthan.core.fakes`의 가짜 구현으로 개발한다.
이 파일을 고칠 때는 카톡에 한 줄 공지한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

# ---------------------------------------------------------------- 가게 지식 (Part B 제공)


@dataclass(frozen=True)
class MenuOption:
    """메뉴에 붙는 선택 사항. 예: 디카페인 변경, 고수 빼기."""

    name: str
    available: bool = True
    extra_price: int = 0
    note: str = ""


@dataclass(frozen=True)
class MenuItem:
    """메뉴 1개 = 문서 1개."""

    id: str
    name_ko: str
    price: int
    description: str = ""
    category: str = ""
    name_native: str = ""  # 사장님이 실제로 쓰는 원어 이름
    aliases: tuple[str, ...] = ()  # 손님이 부르는 다른 이름·표기 변형
    options: tuple[MenuOption, ...] = ()
    allergens: tuple[str, ...] = ()
    sold_out: bool = False


@dataclass(frozen=True)
class MenuHit:
    """메뉴 검색 결과 한 건."""

    item: MenuItem
    score: float  # 0~1, 높을수록 가깝다
    matched_by: Literal["exact", "alias", "keyword", "vector", "hybrid"] = "hybrid"


@dataclass(frozen=True)
class Review:
    """손님 리뷰. 작성자 정보는 Part B가 마스킹해서 넘긴다."""

    id: str
    text: str
    created_at: datetime
    rating: float | None = None
    author: str = ""  # 마스킹된 닉네임
    menu_ids: tuple[str, ...] = ()


class StoreKnowledge(Protocol):
    """사장님 RAG. 비서와 전화 주문이 함께 쓰는 가게 지식."""

    def search_menu(self, query: str, k: int = 5) -> list[MenuHit]:
        """손님·사장님 말에서 메뉴를 찾는다. 근거가 없으면 빈 리스트를 돌려준다."""
        ...

    def get_menu(self, menu_id: str) -> MenuItem | None: ...

    def list_menu(self) -> list[MenuItem]: ...

    def recent_reviews(self, limit: int = 10, since: datetime | None = None) -> list[Review]: ...


# ---------------------------------------------------------------- 주문 (Part C → 사장님 화면)


@dataclass(frozen=True)
class OrderLine:
    menu_id: str
    quantity: int
    options: tuple[str, ...] = ()
    request: str = ""  # 손님 요청 원문. 예: "고수 빼 주세요"


@dataclass
class Order:
    """전화 주문 결과. 사장님 화면에서 사장님 언어로 보여 준다."""

    id: str
    lines: list[OrderLine]
    pickup_time: str = ""
    customer_name: str = ""
    customer_phone: str = ""  # 화면 표시 때 마스킹
    status: Literal["draft", "confirmed", "handoff"] = "draft"
    transcript: list[str] = field(default_factory=list)


class OrderSink(Protocol):
    """주문을 받는 쪽. 데모에서는 메모리 저장 + 사장님 화면 표시."""

    def submit(self, order: Order) -> None: ...
