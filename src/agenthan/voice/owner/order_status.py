"""손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04, spec "주문 현황: 통화 중 대기를 없앤다").

사장님 허브의 주문 카드 하나 → 손님 화면 주문 현황 응답(`GET /api/phone/orders/{id}`, `http.py`).
사장님 칸(원어 이름·번역·뱃지·카운트다운·문자)은 싣지 않는다. 자동 취소까지 남은 시간도 손님에게
보이지 않는다(spec). 모양:

    {"order_id": 주문 번호(= 통화 id),
     "stage": "checking" | "confirmed" | "cancelled",
     "placed_at": 주문서를 만든 가게 시각 ISO,
     "lines": [{"menu_id", "name", "quantity", "options", "request", "subtotal"}],
     "amount": {"subtotal": 할인 전 금액, "total": 낼 금액,
                "discount": 할인액, "coupon_name": 쿠폰 이름},  # 할인 두 칸은 쿠폰을 쓴 주문만
     "pickup": {"time": ISO, "label": "18:30",
                "original_time": "", "original_label": ""},
     "outcome": 결과 | null, "reason": {"code", "text"} | null,
     "sold_out": [떨어진 메뉴 이름], "can_cancel": 손님이 화면에서 취소할 수 있다}

단계(`stage`, 닫힌 목록. 웹 `orderStatus.ts` `OrderStage`가 같은 값을 든다):
- checking: 가게 확인 중. 확인 요청이 결정을 기다린다(통화 중에 손님이 고치는 중도 포함).
  통화 중에 받았지만 통화가 아직 손님에게 말하지 않은 결정(허브 `_unspoken`)과 늦춘 시각을 손님이
  통화에서 듣는 중인 수락도 아직 확인 중이다(통화가 말해야 굳는다)
- confirmed: 확정. 사장님 수락(늦춘 수락이면 `pickup.time`이 바뀐 시각, `original_*`이 원래 시각)과
  확인 요청 없이 온 자동 확정(사장님 화면 꺼짐·승인 꺼짐)
- cancelled: 주문 취소. 거절·자동 취소·손님이 통화에서 취소한 요청·늦춘 시각을 거절함·손님이 주문
  현황에서 취소함(카드 처리 상태 customer_cancelled, 티켓 07)

결과(`outcome`, 티켓 05. 웹 `orderStatus.ts` `OrderOutcome`이 같은 값을 든다): 확인 중이면 null.
확정은 accepted(제시간 수락) · delayed(늦춘 수락) · auto(확인 요청 없이 자동 확정), 취소는
rejected(거절) · out_of_stock(재료 소진 거절) · expired(5분 무응답 자동 취소) · customer_cancelled
(손님이 통화에서 거둔 요청·늦춘 시각을 거절함·주문 현황에서 취소함). 사유(`reason`)는 거절일
때만: 사장님이 고른 사유 코드와 한 줄. 한 줄은 손님 문자 문장(`sms.py`)을 그대로 쓴다. 직접
쓰기(free)는 사장님 글의 한국어 번역(번역 중이면 "", 번역하지 못했으면 원문). `sold_out`은 재료
소진 거절의 떨어진 메뉴 이름들.

손님 취소(`can_cancel`, 티켓 07): 손님이 주문 현황의 [취소하기]로 취소할 수 있는 두 때만 참이다.
1) 가게 확인 중인 통화 뒤 확인 요청(통화가 끝나 손님 말을 받을 통화가 없다), 2) 늦춘 수락으로
확정(손님이 바뀐 시각에 묶이지 않게). 제시간 수락·자동 확정은 가게가 이미 만들기 시작했을 수 있어
거짓이다(손님 화면은 "취소하려면 가게로 전화 주세요"). 통화 중 확인 요청·통화가 아직 말하지 않은
결정·늦춘 시각을 손님이 통화에서 듣는 중이면 통화가 손님 말을 받는다(거짓). 취소 API는 허브 카드만
받는다: 허브 카드로 만든 현황만 `cancellable=True`로 부른다. DB에서 읽은 주문(티켓 06)은 늘 거짓.
거부 사유(`cancel_refusal`)는 지금 결과(`outcome`) 그대로, 통화가 받는 때는 in_call이다. 손님에게
보일 한 줄은 `CANCEL_REFUSED`

금액 (쿠폰 티켓 09): `subtotal`은 줄 합계, `total` = `subtotal` - 할인액(낼 금액, 사장님 카드
`total`과 같다). 쿠폰을 쓴 주문만 `discount`·`coupon_name`(주문서에 적힌 쿠폰, 카드 칸)이 있다.
쿠폰이 없으면 응답이 쿠폰 전과 똑같다(할인 칸 없음). 카드에 쿠폰 칸이 없으면(쿠폰 전 카드) 할인
없음으로 읽는다.
"""

from __future__ import annotations

from typing import Any, Literal

from agenthan.voice.owner import sms

OrderStage = Literal["checking", "confirmed", "cancelled"]
OrderOutcome = Literal["accepted", "delayed", "auto", "rejected", "out_of_stock", "expired",
                       "customer_cancelled"]


def order_stage(card: dict[str, Any], *, unspoken: bool = False) -> OrderStage:
    """주문 카드의 손님 단계. `unspoken`: 통화가 아직 손님에게 말하지 않은 통화 중 결정이 있다."""
    status = card["status"]
    if unspoken:
        return "checking"
    if status == "new":
        return "cancelled" if card.get("withdrawn") == "cancelled" else "checking"
    if status == "accepted":
        return "checking" if card.get("delay_reply") == "asking" else "confirmed"
    # rejected · expired · cancelled(늦춘 시각 거절) · customer_cancelled(손님 취소)
    return "cancelled"


def order_outcome(card: dict[str, Any], stage: OrderStage) -> OrderOutcome | None:
    """단계 안의 결과 (모듈 설명). 확인 중이면 None."""
    status = card["status"]
    if stage == "checking":
        return None
    if stage == "confirmed":
        if card.get("auto_accepted"):
            return "auto"
        return "delayed" if card.get("delay_min") else "accepted"
    if status == "rejected":
        return "out_of_stock" if card.get("reject_reason") == "out_of_stock" else "rejected"
    if status == "expired":
        return "expired"
    # 통화에서 거둔 요청(withdrawn)·늦춘 시각 거절(cancelled)·주문 현황에서 취소(customer_cancelled)
    return "customer_cancelled"


def _menu_names(card: dict[str, Any], menu_ids: list[str]) -> list[str]:
    names = {line["menu_id"]: line["name_ko"] for line in card["lines"]}
    return [names.get(menu_id, menu_id) for menu_id in menu_ids]


def _reason(card: dict[str, Any], outcome: OrderOutcome | None) -> dict[str, str] | None:
    """거절 사유 코드와 한 줄 (모듈 설명). 거절이 아니면 None."""
    if outcome not in ("rejected", "out_of_stock"):
        return None
    code = card["reject_reason"]
    if code == "out_of_stock":
        text = sms.rejected_text(code, _menu_names(card, card["reject_menu_ids"]))
    elif code == "free":
        # 번역이 끝났으면 번역문. 번역하지 못했으면(취소 문자가 원문으로 나감) 원문. 번역 중이면 ""
        texted = any(t["kind"] == "rejected" for t in card.get("sms", ()))
        text = card.get("reject_text_ko") or (card["reject_text"] if texted else "")
    else:
        text = sms.rejected_text(code)
    return {"code": code, "text": text}


# 손님 취소 거부 사유 코드 (티켓 07): 지금 결과(`OrderOutcome`)이거나 in_call(통화가 손님 말을
# 받는다)
CancelRefusal = Literal["accepted", "auto", "rejected", "out_of_stock", "expired",
                        "customer_cancelled", "in_call"]

# 거부 사유 → 손님 화면 한 줄 (티켓 07). 진 쪽 화면은 이 줄과 함께 이긴 결과(현황)를 보인다
CANCEL_REFUSED: dict[str, str] = {
    "accepted": "가게가 이미 주문을 확정했어요. 취소하려면 가게로 전화 주세요.",
    "auto": "가게가 이미 주문을 확정했어요. 취소하려면 가게로 전화 주세요.",
    "rejected": "가게에서 이미 주문을 취소했어요.",
    "out_of_stock": "가게에서 이미 주문을 취소했어요.",
    "expired": "가게에서 확인하지 못해 이미 취소된 주문이에요.",
    "customer_cancelled": "이미 취소한 주문이에요.",
    "in_call": "통화 중인 주문은 통화로 말씀해 주세요.",
}


def can_cancel(card: dict[str, Any], stage: OrderStage, outcome: OrderOutcome | None) -> bool:
    """손님이 주문 현황에서 취소할 수 있다 (모듈 설명 "손님 취소")."""
    if stage == "checking":
        # 통화 뒤 확인 요청이 결정을 기다린다(통화 중 결정·늦춘 시각을 듣는 중은 status가 new가
        # 아니다)
        return (card["status"] == "new" and not card.get("withdrawn")
                and card.get("stage") == "after_call")
    return outcome == "delayed"


def cancel_refusal(status: dict[str, Any]) -> CancelRefusal | None:
    """주문 현황(`order_status` 응답)이 손님 취소를 거부하는 사유 코드. 받으면 None."""
    if status["can_cancel"]:
        return None
    return status["outcome"] or "in_call"  # 확인 중인데 못 받으면 통화가 받는 중이다


def order_status(card: dict[str, Any], *, unspoken: bool = False,
                 cancellable: bool = False) -> dict[str, Any]:
    """허브 주문 카드 → 손님 주문 현황 응답 (모듈 설명의 모양).

    `cancellable`: 손님 취소 API가 받는 주문(허브 카드)이다. 거짓이면(DB에서 읽은 주문)
    `can_cancel`도 거짓이다.
    """
    moved = card["status"] == "accepted" and bool(card.get("owner_pickup_time"))
    if moved:  # 늦춘 수락: 바뀐 시각과 원래 시각
        pickup = {"time": card["owner_pickup_time"], "label": card["owner_pickup_label"],
                  "original_time": card["pickup_time"], "original_label": card["pickup_label"]}
    else:
        pickup = {"time": card["pickup_time"], "label": card["pickup_label"],
                  "original_time": "", "original_label": ""}
    subtotal = sum(line["subtotal"] for line in card["lines"])
    discount = card.get("discount", 0)
    stage = order_stage(card, unspoken=unspoken)
    outcome = order_outcome(card, stage)
    return {
        "order_id": card["id"],
        "stage": stage,
        "placed_at": card["received_at"],
        "lines": [
            {"menu_id": line["menu_id"], "name": line["name_ko"], "quantity": line["quantity"],
             "options": list(line["options"]), "request": line["request"],
             "subtotal": line["subtotal"]}
            for line in card["lines"]
        ],
        "amount": _amount(subtotal, discount, card.get("coupon_name", "")),
        "pickup": pickup,
        "outcome": outcome,
        "reason": _reason(card, outcome),
        "sold_out": (_menu_names(card, card["reject_menu_ids"])
                     if outcome == "out_of_stock" else []),
        "can_cancel": cancellable and can_cancel(card, stage, outcome),  # 손님 취소 (티켓 07)
    }


def _amount(subtotal: int, discount: int, coupon_name: str) -> dict[str, Any]:
    """주문 현황 금액 칸. 할인 칸은 쿠폰을 쓴 주문만 (쿠폰 티켓 09)."""
    amount: dict[str, Any] = {"subtotal": subtotal, "total": subtotal - discount}
    if discount:
        amount |= {"discount": discount, "coupon_name": coupon_name}
    return amount
