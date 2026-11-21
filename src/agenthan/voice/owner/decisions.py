"""사장님 결정 기록 (주문 승인 티켓 10). 허브가 주문 카드의 처리 상태가 바뀔 때마다 내보낸다.

    hub = OwnerHub(..., decision_sink=sink)  # 없으면 메모리(카드)만

- 허브는 `order.update`를 보낼 때마다 카드를 `decision_of`로 읽어, 결정이 지난번과 달라졌을 때만
  `DecisionSink.record`를 부른다(통화가 아직 손님에게 말하지 않은 통화 중 결정은 굳기 전이라 뺀다,
  `hub.py` 모듈 설명). 그래서 새 결정 종류(거절·늦춘 수락·자동 취소 …)는 카드 칸만
  채우면 따로 잇지 않아도 같은 길로 간다. 카드 칸 약속:
  - `status`: new(아직, 기록 안 함) 말고는 그대로 `owner_status`. 자동 확정 카드(`auto_accepted`)는
    `auto_accepted`
  - `reject_reason`: 거절 사유(out_of_stock·busy·closed·free …). 없으면 ""
  - `decided_at`: 결정 시각 ISO. 비어 있으면 허브가 기록하는 시각을 넣는다(자동 취소 등)
  - `owner_pickup_time`: 늦춘 수락의 바뀐 픽업 시각 ISO. 없으면 ""
  - `reject_menu_ids`·`reject_text`·`reject_text_ko`: 재료 소진의 떨어진 메뉴 id들, 직접 쓰기 글
    원문과 한국어 번역(전화 메뉴·주문 현황·쿠폰 티켓 06). 서버가 다시 떠 허브에 카드가 없을 때
    손님 주문 현황(거절 사유·품절 메뉴)을 DB 행에서 다시 만든다. 번역이 늦게 오면 기록이 한 번 더
    간다(칸이 달라졌다)
- 상태·사유는 일반 글이다. DB 쪽도 enum 없이 그대로 남긴다(`agenthan.serve`가 DB 행
  `agenthan.db.orders.OwnerDecisionRow`로 옮긴다. voice와 db는 서로를 import하지 않는다)
- `record`는 허브의 이벤트 루프에서 불린다. 바로 돌아와야 한다(DB 쓰기는 구현이 큐·스레드로)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agenthan.core.contracts import Order


@dataclass(frozen=True)
class DecisionRecord:
    """주문 하나의 사장님 결정. 같은 주문의 결정이 바뀌면 다시 온다. 마지막 것이 맞다."""

    order_id: str  # = 통화 id = `Order.id`
    owner_status: str  # accepted · auto_accepted · rejected · expired …
    reject_reason: str = ""
    decided_at: str = ""  # ISO 8601, 시간대 포함
    owner_pickup_time: str = ""  # 늦춘 수락의 바뀐 픽업 시각 ISO, 없으면 ""
    # 거절 내용 (티켓 06): 재료 소진의 떨어진 메뉴 id들, 직접 쓰기 글 원문과 한국어 번역
    reject_menu_ids: tuple[str, ...] = ()
    reject_text: str = ""
    reject_text_ko: str = ""
    # 주문서(확인 요청 또는 확정 주문). 통화가 취소로 끝나 주문 기록이 없을 때 행을 채운다.
    # 결정이 같은지 볼 때는 보지 않는다
    order: Order | None = field(default=None, compare=False)


class DecisionSink(Protocol):
    """사장님 결정을 받는 쪽(DB 기록 등)."""

    def record(self, decision: DecisionRecord) -> None:
        """허브 루프에서 불린다. 바로 돌아온다. 실패는 구현 안에서 삼킨다(화면·통화는 계속)."""


def decision_of(card: Mapping[str, Any], order: Order | None = None) -> DecisionRecord | None:
    """주문 카드 → 결정 기록. 아직 결정 전(new)이면 None."""
    status = str(card.get("status") or "")
    if not status or status == "new":
        return None
    if card.get("auto_accepted"):
        status = "auto_accepted"
    return DecisionRecord(
        order_id=str(card["id"]),
        owner_status=status,
        reject_reason=str(card.get("reject_reason") or ""),
        decided_at=str(card.get("decided_at") or ""),
        owner_pickup_time=str(card.get("owner_pickup_time") or ""),
        reject_menu_ids=tuple(str(m) for m in card.get("reject_menu_ids") or ()),
        reject_text=str(card.get("reject_text") or ""),
        reject_text_ko=str(card.get("reject_text_ko") or ""),
        order=order,
    )
