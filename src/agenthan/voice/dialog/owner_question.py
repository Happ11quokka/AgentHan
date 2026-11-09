"""사장님께 묻는 질문과 답 (4단계 spec "ASK_OWNER: 엔진", 티켓 05).

엔진은 근거 없는 메뉴 질문을 사장님 질문으로 열어 턴 결과에 싣고(`TurnResult.owner_question`),
세션은 그 질문을 `OwnerChannel`로 사장님 화면에 보낸다. 사장님 답은 세션이 엔진의
`owner_answered`로 넣는다. 질문·답은 계약(`Order`) 밖이다. 허브가 통화 id로 묶는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 사장님 버튼 답: 돼요 · 안 돼요 · 모르겠어요 (grilling Q6). 시간 초과(06)·자유 입력(07)은 그 티켓이
# 더한다. 엔진은 답 종류마다 고정 존댓말 문장을 말한다(`templates.owner_answer`)
OwnerAnswer = Literal["yes", "no", "unsure"]
OWNER_ANSWERS: tuple[OwnerAnswer, ...] = ("yes", "no", "unsure")


@dataclass(frozen=True)
class OwnerQuestion:
    """사장님께 여쭤볼 메뉴 질문 하나."""

    number: int  # 통화 안 질문 번호(1부터). 답에 붙어 와서 낡은 답을 가른다
    menu_id: str
    menu_name: str  # 한국어 메뉴명
    text: str  # 손님 질문 원문(STT 글)


@dataclass(frozen=True)
class OwnerReply:
    """사장님 화면 → 통화 세션. 질문 번호와 답 종류."""

    question: int  # `OwnerQuestion.number`
    answer: OwnerAnswer
