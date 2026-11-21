"""주문 확인 요청과 사장님 결정 (주문 승인 spec "엔진", 티켓 02).

주문은 가게가 수락해야 성립한다. 복창에 손님이 "네"라고 하면 엔진은 바로 마무리하지 않고 확인
요청(`ApprovalRequest`)을 턴 결과에 싣고 "사장님께 주문 확인 중이에요. 잠시만요."로 끝낸다.
세션은 그 요청을 `OwnerChannel.open_approval`로 사장님 화면에 보내고, 사장님 결정
(`OwnerDecision`)을 엔진의 `owner_decided`로 넣는다. 질문 카드(`owner_question.py`)와 같은 모양이다.

요청 번호는 통화 안에서 1부터 센다. 손님이 기다리는 중에 주문을 고치면 요청을 거두고, 다시
복창한 뒤 새 번호로 연다. 거둔 번호에 온 결정은 버린다(낡은 결정).

결정 종류: 수락(accept, 늦춘 분 `delay_min`은 티켓 04가 0이 아닌 값을 쓴다)과 시간 초과(timeout,
세션이 통화 중 대기 시간이 지나면 넣는다). 늦춘 수락·거절(티켓 04~06)은 이 목록과 필드를 넓힌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agenthan.core.contracts import Order

# 사장님 결정 종류. 사장님 화면 수락(accept)과 세션이 넣는 시간 초과(timeout)
DecisionKind = Literal["accept", "timeout"]
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
    delay_min: int = 0  # 늦춘 수락의 분 (티켓 04). 지금은 0


@dataclass(frozen=True)
class ApprovalWithdrawn:
    """이번 턴에 엔진이 열린 확인 요청을 거뒀다. 세션이 사장님 쪽에 알린다."""

    number: int
    reason: WithdrawReason
