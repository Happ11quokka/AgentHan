"""손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04, spec "주문 현황: 통화 중 대기를 없앤다").

사장님 허브의 주문 카드 하나 → 손님 화면 주문 현황 응답(`GET /api/phone/orders/{id}`, `http.py`).
사장님 칸(원어 이름·번역·뱃지·카운트다운·문자)은 싣지 않는다. 자동 취소까지 남은 시간도 손님에게
보이지 않는다(spec). 모양:

    {"order_id": 주문 번호(= 통화 id),
     "stage": "checking" | "confirmed" | "cancelled",
     "placed_at": 주문서를 만든 가게 시각 ISO,
     "lines": [{"menu_id", "name", "quantity", "options", "request", "subtotal"}],
     "amount": {"subtotal": 할인 전 금액, "total": 낼 금액},
     "pickup": {"time": ISO, "label": "18:30",
                "original_time": "", "original_label": ""}}

단계(`stage`, 닫힌 목록. 웹 `orderStatus.ts` `OrderStage`가 같은 값을 든다):
- checking: 가게 확인 중. 확인 요청이 결정을 기다린다(통화 중에 손님이 고치는 중도 포함).
  통화 중에 받았지만 통화가 아직 손님에게 말하지 않은 결정(허브 `_unspoken`)과 늦춘 시각을 손님이
  통화에서 듣는 중인 수락도 아직 확인 중이다(통화가 말해야 굳는다)
- confirmed: 확정. 사장님 수락(늦춘 수락이면 `pickup.time`이 바뀐 시각, `original_*`이 원래 시각)과
  확인 요청 없이 온 자동 확정(사장님 화면 꺼짐·승인 꺼짐)
- cancelled: 주문 취소. 거절·자동 취소·손님이 통화에서 취소한 요청·늦춘 시각을 거절함.
  사유와 다시 주문하기는 다음 티켓(05)이 더한다

금액: 지금은 할인이 없어 `subtotal` = `total`(줄 합계). 쿠폰(티켓 09)이 할인 칸을 이 묶음에 더한다.
"""

from __future__ import annotations

from typing import Any, Literal

OrderStage = Literal["checking", "confirmed", "cancelled"]


def order_stage(card: dict[str, Any], *, unspoken: bool = False) -> OrderStage:
    """주문 카드의 손님 단계. `unspoken`: 통화가 아직 손님에게 말하지 않은 통화 중 결정이 있다."""
    status = card["status"]
    if unspoken:
        return "checking"
    if status == "new":
        return "cancelled" if card.get("withdrawn") == "cancelled" else "checking"
    if status == "accepted":
        return "checking" if card.get("delay_reply") == "asking" else "confirmed"
    return "cancelled"  # rejected · expired · cancelled(늦춘 시각 거절)


def order_status(card: dict[str, Any], *, unspoken: bool = False) -> dict[str, Any]:
    """허브 주문 카드 → 손님 주문 현황 응답 (모듈 설명의 모양)."""
    moved = card["status"] == "accepted" and bool(card.get("owner_pickup_time"))
    if moved:  # 늦춘 수락: 바뀐 시각과 원래 시각
        pickup = {"time": card["owner_pickup_time"], "label": card["owner_pickup_label"],
                  "original_time": card["pickup_time"], "original_label": card["pickup_label"]}
    else:
        pickup = {"time": card["pickup_time"], "label": card["pickup_label"],
                  "original_time": "", "original_label": ""}
    total = sum(line["subtotal"] for line in card["lines"])
    return {
        "order_id": card["id"],
        "stage": order_stage(card, unspoken=unspoken),
        "placed_at": card["received_at"],
        "lines": [
            {"menu_id": line["menu_id"], "name": line["name_ko"], "quantity": line["quantity"],
             "options": list(line["options"]), "request": line["request"],
             "subtotal": line["subtotal"]}
            for line in card["lines"]
        ],
        "amount": {"subtotal": total, "total": total},
        "pickup": pickup,
    }
