"""통화 세션이 사장님 답을 기다리는 대기 항목 (주문 승인 티켓 01, 사전 정리).

세션은 사장님 쪽 일을 기다리는 동안 열린 대기 항목 하나를 든다(동시에 하나). 항목은 종류를
가진다. 종류는 질문 카드(`question`, 4단계 ASK_OWNER)와 확인 요청(`approval`, 주문 승인 티켓
02)이다.

종류와 상관없이 세션이 하는 일: 항목 열기(번호·연 시각), 대기 시간 초과 타이머, 받은 알림 큐,
손님이 말하는 중이면 미뤘다가 봇 턴(`bot_turn`)으로 말하기, 낡은 번호의 알림 버리기, 통화 끝
정리. 종류마다 다른 일(사장님 쪽에 무엇을 여나, 시간 초과에 무엇을 넣나, 엔진의 어느 행동을
부르나, 턴 기록에 무엇을 남기나)은 세션이 종류로 가른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agenthan.voice.dialog.approval import OwnerDecision
from agenthan.voice.dialog.owner_question import OwnerNote, OwnerReply

# 대기 항목 종류. 질문 카드(4단계 ASK_OWNER) · 확인 요청(주문 승인)
WaitKind = Literal["question", "approval"]
# 사장님 쪽에서 세션의 받는 곳으로 오는 알림: 질문 답·열람, 확인 요청 결정
WaitNote = OwnerNote | OwnerDecision


@dataclass
class OwnerWait:
    """열린 대기 항목 하나. 번호는 종류마다 따로 센다(엔진이 매긴다)."""

    kind: WaitKind
    number: int
    opened_at: float  # 사장님 쪽에 카드를 보낸 때(세션 시계). 시간 초과와 대기 시간의 기준
    seen_at: float | None = None  # 사장님이 그 카드를 처음 본 때
    seen_said: bool = False  # "사장님이 보고 계세요"를 이 항목에 이미 말했다

    @property
    def key(self) -> tuple[WaitKind, int]:
        return self.kind, self.number


def note_key(note: WaitNote) -> tuple[WaitKind, int]:
    """사장님 쪽 알림이 어느 대기 항목의 것인가(종류, 번호). 열린 항목과 다르면 낡은 알림이다."""
    if isinstance(note, OwnerDecision):
        return "approval", note.request
    return "question", note.question


def closes_wait(note: WaitNote) -> bool:
    """대기를 끝내는 답인가. 아니면 중간 알림(카드 열람)이라 항목마다 한 번만 말한다."""
    return isinstance(note, OwnerReply | OwnerDecision)
