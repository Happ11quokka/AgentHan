"""사장님 주문 카드의 주문서 몫과 카드 표기 (PR #44 리뷰: 허브와 기록 읽기가 같이 쓴다).

허브(`hub.py`)는 주문서가 올 때, 기록 읽기(`order_archive.py`)는 서버가 다시 떠 카드가 없을 때
같은 함수로 카드의 주문서 칸(줄·금액·픽업 시각)을 만든다. 허브가 기록 읽기를 import하므로 둘이
같이 쓰는 것은 이 잎 모듈에 둔다(이 모듈은 허브·기록 읽기를 import하지 않는다).

금액 (쿠폰 티켓 09): `subtotal`은 줄 합계(할인 전), `total`은 낼 금액(`coupons.payable`),
쿠폰이 없으면 할인 0·이름 "" (카드에 할인 줄이 없다).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from agenthan.core.contracts import Order
from agenthan.voice.dialog.coupons import payable


def clock_label(iso: str) -> str:
    """ISO 8601 시각 → `18:30` (가게 시각 그대로, 24시간제). 비면 빈 글."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).strftime("%H:%M")


def won_label(amount: int) -> str:
    """원 금액 → `25,000₩` (언어와 상관없는 고정 표기)."""
    return f"{amount:,}₩"


def order_card(order: Order, lines: Sequence[dict[str, Any]], received_at: str) -> dict[str, Any]:
    """주문서 → 확인 대기(new) 주문 카드의 주문서 칸. `lines`는 카드 줄(`subtotal` 칸이 있다)."""
    subtotal = sum(line["subtotal"] for line in lines)
    total = payable(subtotal, order.discount)  # 통화가 복창한 쿠폰 할인
    return {
        "id": order.id,
        "status": "new",
        "received_at": received_at,
        "received_label": clock_label(received_at),  # 주문 시각 `16:05` (이야기 25, 리뷰 P1)
        # 손님 이름(성). 지금 통화 엔진은 이름을 받지 않아 비어 있다. 차 있으면 카드에 보인다
        "customer_name": order.customer_name,
        "pickup_time": order.pickup_time,
        "pickup_label": clock_label(order.pickup_time),
        "lines": list(lines),
        "total": total,
        "total_label": won_label(total),
        "subtotal": subtotal,
        "subtotal_label": won_label(subtotal),
        "coupon_id": order.coupon_id,
        "coupon_name": order.coupon_name,
        "discount": order.discount,
        "discount_label": won_label(order.discount),
    }
