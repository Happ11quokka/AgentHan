"""주문 현황 다시 열기 (전화 메뉴·주문 현황·쿠폰 티켓 06, spec "주문 현황: 통화 중 대기를 없앤다").

손님 주문 현황 조회는 사장님 허브(메모리)의 주문 카드를 먼저 본다. 서버가 다시 떴거나 카드가
상한으로 지워졌으면 전화 주문 기록(DB `phone_orders`)의 마지막 상태로 같은 모양의 응답을
만든다. 둘 다 없으면 "주문을 찾을 수 없어요"(허브 `lookup_order_status`, 404).

    hub = OwnerHub(..., archive=archive)  # 없으면 허브만 (DB 없는 개발 서버)

- `OrderArchive.load`는 동기다(DB 읽기). 허브가 이벤트 루프 밖 스레드에서 부른다. 실패는 구현
  안에서 삼키고 None을 돌려준다
- voice와 db는 서로를 import하지 않는다(PRD §7). db 행 → `ArchivedOrder`로 옮기는 일은
  `agenthan.serve`가 한다
- 기록 한 줄을 허브 주문 카드 모양으로 되살린 뒤(`archived_card`) 허브와 같은 `order_status`로
  응답을 만든다. 그래서 응답에 칸을 더하는 티켓(05 결과·사유, 07 손님 취소, 09 할인)은 카드
  칸만 맞추면 DB 쪽도 같은 값을 낸다. 기록에 없는 카드 칸은 기본값이다(통화 중 칸·번역·문자)
- 단계: 결정 전 기록(`owner_status` '')은 가게 확인 중이다. 다만 허브의 자동 취소 타이머는 서버와
  같이 사라졌으므로, 확인 요청을 연 시각(= 주문서 `created_at`, 손님이 "네"라고 한 턴) + 자동
  취소 마감이 지났으면 자동 취소(expired)로 읽는다. 주문서 시각이 지금(가게 시계)보다 뒤인
  기록도 자동 취소다: `PHONE_NOW` 시계가 다시 뜬 서버에서 같은 시각부터 다시 가서 생긴다(리뷰).
  기록은 고치지 않는다(읽기만 한다)
- 손님 취소(`can_cancel`)는 늘 거짓이다: 허브에 카드가 없는 주문은 취소를 받을 곳이 없다
- 넘김(handoff) 기록은 주문 현황이 없다(통화 결과로 보인다)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from agenthan.core.contracts import Order
from agenthan.voice.owner.order_status import order_status


@dataclass(frozen=True)
class ArchivedOrder:
    """전화 주문 기록 한 줄의 주문 현황 몫 (voice 모양). 칸 뜻은 `DecisionRecord`와 같다."""

    # 주문서: id(= 통화 id)·줄(주문할 때의 이름·단가)·픽업 시각·통화 결과(confirmed·draft·handoff)·
    # 주문서를 만든 시각·쓴 쿠폰(`coupon_id`·`coupon_name`·`discount`, 쿠폰 티켓 09)
    order: Order
    owner_status: str = ""  # accepted · auto_accepted · rejected · expired … ('' = 아직)
    reject_reason: str = ""
    reject_menu_ids: tuple[str, ...] = ()
    reject_text: str = ""
    reject_text_ko: str = ""
    decided_at: str = ""
    owner_pickup_time: str = ""


class OrderArchive(Protocol):
    """전화 주문 기록을 주문 번호로 읽는 곳(운영은 DB)."""

    def load(self, order_id: str) -> ArchivedOrder | None:
        """동기. 이벤트 루프 밖에서 불린다. 없거나 읽지 못하면 None(예외를 올리지 않는다)."""


def archived_order_status(
    record: ArchivedOrder, *, now: datetime, expire: timedelta
) -> dict[str, Any] | None:
    """기록 한 줄 → 손님 주문 현황 응답(허브와 같은 모양). 주문 현황이 없는 기록이면 None."""
    card = archived_card(record, now=now, expire=expire)
    if card is None:
        return None
    status = order_status(card)
    status["can_cancel"] = False
    return status


def archived_card(
    record: ArchivedOrder, *, now: datetime, expire: timedelta
) -> dict[str, Any] | None:
    """기록 한 줄 → 허브 주문 카드 모양(손님 주문 현황이 읽는 칸만). 넘김·결정 없는 초안은 None."""
    order = record.order
    if order.status == "handoff":
        return None
    status = record.owner_status or "new"
    if status == "new" and order.status != "confirmed":
        return None  # 결정도 확정 주문서도 없는 줄은 주문이 아니다
    decided_at = record.decided_at
    if status == "new":
        deadline = _deadline(order.created_at, expire)
        # 허브 타이머 대신 마감으로 자동 취소를 읽는다. 주문서 시각이 지금보다 뒤면(PHONE_NOW
        # 시계는 서버가 뜰 때마다 같은 시각에서 다시 간다) 지난 실행의 주문이다: 그 허브 타이머는
        # 서버와 같이 사라졌고 결정할 곳도 없으므로 자동 취소로 읽는다 (리뷰)
        if deadline is None or deadline <= now or deadline - expire > now:
            status, decided_at = "expired", deadline.isoformat() if deadline else ""
    auto = status == "auto_accepted"
    lines = [
        {"menu_id": line.menu_id, "name_ko": line.name or line.menu_id,
         "quantity": line.quantity, "options": list(line.options), "request": line.request,
         "subtotal": line.unit_price * line.quantity}
        for line in order.lines
    ]
    names = {line["menu_id"]: line["name_ko"] for line in lines}
    subtotal = sum(line["subtotal"] for line in lines)
    moved = record.owner_pickup_time if status in ("accepted", "auto_accepted") else ""
    stock_out = record.reject_reason == "out_of_stock"
    return {
        "id": order.id,
        "status": "accepted" if auto else status,
        "auto_accepted": auto,
        "received_at": order.created_at,
        "pickup_time": order.pickup_time,
        "pickup_label": _clock_label(order.pickup_time),
        "lines": lines,
        # 금액은 허브 카드와 같다 (쿠폰 티켓 09): 할인 전·쿠폰·낼 금액. 쿠폰 전 기록은 할인 0
        "subtotal": subtotal,
        "coupon_id": order.coupon_id,
        "coupon_name": order.coupon_name,
        "discount": order.discount,
        "total": subtotal - order.discount,
        "stage": "after_call" if status == "new" else "",
        "requested_at": order.created_at if not auto else "",
        "decided_at": decided_at,
        "delay_min": _minutes_between(order.pickup_time, moved),
        "owner_pickup_time": moved,
        "owner_pickup_label": _clock_label(moved),
        "delay_reply": "",
        "withdrawn": "",
        "reject_reason": record.reject_reason,
        "reject_menu_ids": list(record.reject_menu_ids),
        "reject_text": record.reject_text,
        "reject_text_ko": record.reject_text_ko,
        # 직접 쓰기 거절 문자는 나갔다(기록을 읽을 때 번역은 이미 끝났다). 그래서 번역이 없으면
        # 허브의 번역 실패와 같이 원문이 사유 한 줄이다(`order_status._reason`)
        "sms": [{"kind": "rejected"}] if status == "rejected" and record.reject_reason == "free"
        else [],
        "sold_out": [
            {"menu_id": menu_id, "name_ko": names.get(menu_id, menu_id),
             "name_owner": names.get(menu_id, menu_id)}
            for menu_id in (record.reject_menu_ids if stock_out else ())
        ],
    }


def _deadline(created_at: str, expire: timedelta) -> datetime | None:
    try:
        return datetime.fromisoformat(created_at) + expire
    except ValueError:  # 시각 없는 옛 기록: 기다리는 중으로 둘 수 없다
        return None


def _minutes_between(start: str, end: str) -> int:
    if not start or not end:
        return 0
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60)


def _clock_label(iso: str) -> str:
    """`hub.clock_label`과 같다(허브가 이 모듈을 import해서 거꾸로는 못 쓴다)."""
    return datetime.fromisoformat(iso).strftime("%H:%M") if iso else ""
