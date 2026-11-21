"""주문 확인 요청과 사장님 결정 (주문 승인 spec "엔진", 티켓 02).

주문은 가게가 수락해야 성립한다. 복창에 손님이 "네"라고 하면 엔진은 바로 마무리하지 않고 확인
요청(`ApprovalRequest`)을 턴 결과에 싣고 "사장님께 주문 확인 중이에요. 잠시만요."로 끝낸다.
세션은 그 요청을 `OwnerChannel.open_approval`로 사장님 화면에 보내고, 사장님 결정
(`OwnerDecision`)을 엔진의 `owner_decided`로 넣는다. 질문 카드(`owner_question.py`)와 같은 모양이다.

요청 번호는 통화 안에서 1부터 센다. 손님이 기다리는 중에 주문을 고치면 요청을 거두고, 다시
복창한 뒤 새 번호로 연다. 거둔 번호에 온 결정은 버린다(낡은 결정).

결정 종류: 수락(accept), 늦춘 수락(delay, `delay_min` 15·30분, 티켓 04), 시간 초과(timeout,
세션이 통화 중 대기 시간이 지나면 넣는다), 거절(reject, 티켓 05). 거절은 사유(`reason`)를 단다:
바빠서 못 맞춤(busy) · 영업·가게 사정(closed) · 직접 쓰기(free, 사장님 글은 손님에게 문자로만 가서
결정에 싣지 않는다) · 재료 소진(out_of_stock, 떨어진 메뉴 id `menu_ids`, 티켓 06).

늦춘 수락은 통화 중이면 손님에게 바뀐 시각을 묻는다("사장님이 7시 15분에 준비 가능하시대요.
괜찮으세요?"). "네"면 그 시각으로 확정, "아니요"면 주문 취소다(다른 시각 협상은 없다). 취소면
엔진이 그 요청 번호를 취소(cancelled)로 거둔다(`ApprovalWithdrawn`). 사장님 카드가 취소로 바뀐다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agenthan.core.contracts import Order

# 사장님 결정 종류. 사장님 화면 수락(accept)·늦춘 수락(delay)·거절(reject)과 세션이 넣는
# 시간 초과(timeout)
DecisionKind = Literal["accept", "delay", "timeout", "reject"]
# 사장님이 고를 수 있는 늦춘 분 (사장님 화면 [+15분] [+30분], 티켓 04). 0은 그냥 수락
ACCEPT_DELAYS: tuple[int, ...] = (0, 15, 30)
# 거절 사유 (사장님 화면 사유 시트, 티켓 05·06). 허브 카드·DB의 `reject_reason`과 같은 글
RejectReason = Literal["out_of_stock", "busy", "closed", "free"]
REJECT_REASONS: tuple[RejectReason, ...] = ("out_of_stock", "busy", "closed", "free")
# 엔진이 확인 요청을 거둔 이유: 손님이 주문을 고침(changed) · 손님이 주문을 취소함(cancelled)
WithdrawReason = Literal["changed", "cancelled"]


@dataclass(frozen=True)
class ApprovalRequest:
    """사장님께 보내는 확인 요청 하나."""

    number: int  # 통화 안 요청 번호(1부터). 결정에 붙어 와서 낡은 결정을 가른다
    # 수락되면 넘어갈 주문서(상태 confirmed). 사장님 카드가 이것으로 메뉴·합계·픽업 시각을 보인다
    order: Order


@dataclass(frozen=True)
class OwnerDecision:
    """사장님 화면(또는 세션의 시간 초과) → 통화 세션. 요청 번호와 결정."""

    request: int  # `ApprovalRequest.number`
    kind: DecisionKind
    delay_min: int = 0  # 늦춘 수락(delay)의 분 15·30 (티켓 04). 그 밖의 결정은 0
    reason: RejectReason | Literal[""] = ""  # 거절 사유 (kind reject만, 티켓 05)
    menu_ids: tuple[str, ...] = ()  # 재료 소진 거절의 떨어진 메뉴 id들 (티켓 06)


@dataclass(frozen=True)
class ApprovalWithdrawn:
    """이번 턴에 엔진이 열린 확인 요청을 거뒀다. 세션이 사장님 쪽에 알린다."""

    number: int
    reason: WithdrawReason
