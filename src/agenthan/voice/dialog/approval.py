"""주문 확인 요청과 사장님 결정 (주문 승인 spec "엔진", 티켓 02).

주문은 가게가 수락해야 성립한다. 복창에 손님이 "네"라고 하면 엔진은 바로 마무리하지 않고 확인
요청(`ApprovalRequest`)을 턴 결과에 싣고 "사장님께 주문 확인 중이에요. 잠시만요."로 끝낸다.
세션은 그 요청을 `OwnerChannel.open_approval`로 사장님 화면에 보내고, 사장님 결정
(`OwnerDecision`)을 엔진의 `owner_decided`로 넣는다. 질문 카드(`owner_question.py`)와 같은 모양이다.

통화 중 대기가 0이면(기본, 전화 메뉴·주문 현황 티켓 04) 엔진은 "네"에서 통화 뒤 확인 요청
(`in_call` 거짓)을 열고 "주문 넣어 드렸어요. 가게에서 확인하면 화면으로 알려 드릴게요."로 바로
끝낸다. 주문서는 그 턴에 넘어가고, 사장님 결정은 통화 뒤 주문 현황으로 손님에게 보인다. 아래의
통화 중 결정(늦춘 시각 묻기, 재료 소진 뒤 빼기·바꾸기)은 통화 중 대기를 켠 설정에서만 탄다.

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

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, get_args

from agenthan.core.contracts import Order

# 사장님 결정 종류. 사장님 화면 수락(accept)·늦춘 수락(delay)·거절(reject)과 세션이 넣는
# 시간 초과(timeout)
DecisionKind = Literal["accept", "delay", "timeout", "reject"]
# 사장님이 고를 수 있는 늦춘 분 (사장님 화면 [+15분] [+30분], 티켓 04). 0은 그냥 수락. HTTP 본문
# 검사(`AcceptBody`)도 이 타입을 쓴다. 웹은 `ownerStream.ts` `ACCEPT_DELAYS`가 같은 값을 든다
AcceptDelay = Literal[0, 15, 30]
ACCEPT_DELAYS: tuple[AcceptDelay, ...] = get_args(AcceptDelay)
# 거절 사유 (사장님 화면 사유 시트, 티켓 05·06). 허브 카드·DB의 `reject_reason`과 같은 글
RejectReason = Literal["out_of_stock", "busy", "closed", "free"]
REJECT_REASONS: tuple[RejectReason, ...] = get_args(RejectReason)
# 엔진이 확인 요청을 거둔 이유: 손님이 주문을 고침(changed) · 손님이 주문을 취소함(cancelled)
WithdrawReason = Literal["changed", "cancelled"]


@dataclass(frozen=True)
class ApprovalRequest:
    """사장님께 보내는 확인 요청 하나."""

    number: int  # 통화 안 요청 번호(1부터). 결정에 붙어 와서 낡은 결정을 가른다
    # 수락되면 넘어갈 주문서(상태 confirmed). 사장님 카드가 이것으로 메뉴·합계·픽업 시각을 보인다
    order: Order
    # 통화가 결정을 기다린다(통화 중 대기가 켜짐). 거짓이면 통화 뒤 확인 요청이다(전화 메뉴·주문
    # 현황 티켓 04): 엔진은 이 요청을 열면서 통화를 끝내고, 결정은 통화로 가지 않고 주문 현황으로
    # 간다
    in_call: bool = True


@dataclass(frozen=True)
class OwnerDecision:
    """사장님 화면(또는 세션의 시간 초과) → 통화 세션. 요청 번호와 결정."""

    request: int  # `ApprovalRequest.number`
    kind: DecisionKind
    delay_min: int = 0  # 늦춘 수락(delay)의 분 15·30 (티켓 04). 그 밖의 결정은 0
    reason: RejectReason | Literal[""] = ""  # 거절 사유 (kind reject만, 티켓 05)
    menu_ids: tuple[str, ...] = ()  # 재료 소진 거절의 떨어진 메뉴 id들 (티켓 06)


def only_the_rest(asked: Iterable[str], sold_out: Iterable[str],
                  confirmed: Iterable[str]) -> bool:
    """재료 소진 거절 뒤 확정된 주문이 떨어진 메뉴만 뺀 나머지다(새 메뉴 없음, 티켓 06). 그러면
    재승인 없이 그 요청의 수락이다. 메뉴 id로 본다: 요청한 메뉴 − 떨어진 메뉴 안에서만 골랐다.

    허브(사장님 카드 accepted)와 세션(손님 화면 accepted)이 같은 규칙을 쓴다(리뷰 3번).
    """
    rest = set(asked) - set(sold_out)
    kept = list(confirmed)
    return bool(kept) and all(menu_id in rest for menu_id in kept)


@dataclass(frozen=True)
class ApprovalWithdrawn:
    """이번 턴에 엔진이 열린 확인 요청을 거뒀다. 세션이 사장님 쪽에 알린다."""

    number: int
    reason: WithdrawReason
