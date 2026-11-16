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
    # 주문할 때의 값(스냅숏). 통화 뒤 메뉴 이름·가격이 바뀌어도 이미 받은 주문은 그대로다.
    # 비어 있으면(""·0) 받는 쪽이 `menu_id`로 메뉴를 찾는다. 사장님 언어 이름은 넣지 않는다.
    name: str = ""  # 한국어 메뉴 이름 (`MenuItem.name_ko` 복사)
    unit_price: int = 0  # 한 개 가격(원) = 메뉴 가격 + 고른 옵션 `extra_price` 합


@dataclass
class Order:
    """전화 주문 결과. 사장님 화면에서 사장님 언어로 보여 준다.

    `id`는 통화 id와 같다(전화 주문). 통화 중 사장님 질문·자막도 같은 id로 묶는다.
    """

    id: str
    lines: list[OrderLine]
    pickup_time: str = ""
    customer_name: str = ""
    customer_phone: str = ""  # 화면 표시 때 마스킹
    # 통화가 어떻게 끝났나. confirmed = 손님이 복창을 듣고 확정, handoff = 사장님께 넘김
    # (메모·미완성 주문). 사장님이 접수·거절·준비 완료로 바꾸는 처리 상태는 여기 없다. 계약 밖
    # 사장님 화면이 따로 들고 Part A 어휘(new·accepted·rejected·ready·completed)를 쓴다.
    # A 예약 상태의 `confirmed`와도 다른 뜻이다.
    status: Literal["draft", "confirmed", "handoff"] = "draft"
    transcript: list[str] = field(default_factory=list)
    # 주문서를 만든 시각. pickup_time과 같은 ISO 8601, 시간대 포함. 예: "2026-11-10T14:03:12+09:00".
    # 모르면 "".
    created_at: str = ""


class OrderSink(Protocol):
    """주문을 받는 쪽. 데모에서는 메모리 저장 + 사장님 화면 표시."""

    def submit(self, order: Order) -> None:
        """주문 확정(`confirmed`)·넘김(`handoff`) 때만, 통화 하나에 많아야 한 번 부른다.

        손님이 끊음·취소·무응답 종료·오류로 끝난 통화는 부르지 않는다. 서버 이벤트 루프에서
        불리므로 바로 돌아와야 한다. 네트워크·긴 디스크 쓰기는 구현 안에서 스레드나 큐로 넘긴다.
        """
        ...
