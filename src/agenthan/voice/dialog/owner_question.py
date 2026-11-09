"""사장님께 묻는 질문과 답 (4단계 spec "ASK_OWNER: 엔진", 티켓 05).

엔진은 근거 없는 메뉴 질문을 사장님 질문으로 열어 턴 결과에 싣고(`TurnResult.owner_question`),
세션은 그 질문을 `OwnerChannel`로 사장님 화면에 보낸다. 사장님 답은 세션이 엔진의
`owner_answered`로 넣는다. 질문·답은 계약(`Order`) 밖이다. 허브가 통화 id로 묶는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agenthan.voice.dialog.knowledge import KnowledgeKey

# 사장님 버튼 답: 돼요 · 안 돼요 · 모르겠어요 (grilling Q6). 사장님 화면 POST가 받는 값이다
OwnerButton = Literal["yes", "no", "unsure"]
OWNER_BUTTONS: tuple[OwnerButton, ...] = ("yes", "no", "unsure")
# 사장님 화면이 보내는 답: 버튼 셋 + 자유 입력(free, 티켓 07). 자유 입력 글은 허브가 번역해 손님
# 문자로만 보낸다. 통화에는 종류만 간다
OwnerPost = Literal["yes", "no", "unsure", "free"]
OWNER_POSTS: tuple[OwnerPost, ...] = ("yes", "no", "unsure", "free")
# 엔진이 받는 답 종류: 버튼 셋 + 시간 초과(티켓 06, 세션이 넣는다) + 자유 입력(티켓 07).
# 엔진은 답 종류마다 고정 존댓말 문장을 말한다(`templates.owner_answer`)
OwnerAnswer = Literal["yes", "no", "unsure", "timeout", "free"]


@dataclass(frozen=True)
class OwnerQuestion:
    """사장님께 여쭤볼 메뉴 질문 하나."""

    number: int  # 통화 안 질문 번호(1부터). 답에 붙어 와서 낡은 답을 가른다
    menu_id: str
    menu_name: str  # 한국어 메뉴명
    text: str  # 손님 질문 원문(STT 글)
    # 질문 종류·대상(`menu_info.MenuAnswer`, 티켓 13). 허브가 버튼 답을 보충 지식으로 넣거나
    # (예/아니오로 답할 질문만, `yes_no`) 모르겠어요·시간 초과를 보충 요청 카드로 보낼 때 쓴다
    kind: str = "other"
    subject: str = ""
    yes_no: bool = False  # "견과류 들어가요?"처럼 돼요/안 돼요가 들어간다/안 들어간다는 뜻이다

    @property
    def key(self) -> KnowledgeKey:
        return KnowledgeKey(self.menu_id, self.kind, self.subject)


@dataclass(frozen=True)
class OwnerReply:
    """사장님 화면 → 통화 세션. 질문 번호와 답 종류."""

    question: int  # `OwnerQuestion.number`
    answer: OwnerAnswer


@dataclass(frozen=True)
class OwnerSeen:
    """사장님 화면 → 통화 세션. 사장님이 질문 카드를 봤다(티켓 06). 손님에게 한 번 알린다."""

    question: int  # `OwnerQuestion.number`


# 질문을 연 세션의 받는 곳(`OwnerChannel.open_question`의 `reply`)으로 오는 것
OwnerNote = OwnerReply | OwnerSeen
