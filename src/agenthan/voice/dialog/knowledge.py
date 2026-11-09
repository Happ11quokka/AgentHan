"""사장님 보충 지식 (4단계 티켓 13, grilling Q17~Q20). Part C 안쪽 포트, 계약 밖.

AI가 메뉴 필드로 답하지 못한 질문을 사장님이 채우면, 다음 손님부터는 그 내용으로 답한다("진화").

    store = KnowledgeStore()                     # 가게 하나, 메모리. 다시 띄우면 사라진다
    engine = DialogEngine(..., knowledge=store)  # 읽기: 메뉴 답에 필드 근거가 없을 때 먼저
    hub = OwnerHub(..., knowledge=store)         # 쓰기: 사장님 "저장"·질문 카드 버튼 답

- 열쇠(`KnowledgeKey`): 메뉴 id + 질문 종류(`menu_info.QuestionKind`) + 대상. 대상은 알레르기
  성분 하나("견과")·이름 붙은 재료("시나몬")·그 밖 질문의 원문 요약이고, 나머지 종류(맛·설명,
  재료 전체, 알레르기 전체)는 비어 있다. 같은 메뉴의 같은 종류 질문은 같은 답을 쓴다(Q18)
- 값(`Known`): 사장님 설명(`note`, 한국어, 사장님이 "저장"한 글 그대로)과 사장님 확인(`contains`,
  질문 카드 "돼요/안 돼요" → 그 성분이 들어간다/안 들어간다, Q20). 엔진은 설명을 인용 틀로만
  말하고(`templates.owner_said`) 다듬지 않는다. 확인은 고정 문장(`templates.owner_checked`)
- 엔진은 다른 스레드에서 읽을 수 있다. 값은 바꿀 수 없는 객체이고 dict 한 칸을 통째로 바꾼다
  (GIL 아래 읽기·쓰기가 원자적이다)

11/17 통합 때 Part A 메뉴 등록(초안 저장)으로 잇자고 제안했다(grilling Q19). 그때는 이 저장소 대신
메뉴 설명 초안을 쓰는 어댑터를 같은 모양으로 넣으면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol


@dataclass(frozen=True)
class KnowledgeKey:
    menu_id: str
    kind: str  # `menu_info.QuestionKind`
    subject: str = ""  # 알레르기 성분 · 이름 붙은 재료 · 그 밖 질문 요약. 나머지 종류는 ""


@dataclass(frozen=True)
class Known:
    """보충 지식 하나. 둘 다 있을 수 있다."""

    note: str = ""  # 사장님 설명 (한국어, 저장한 글 그대로)
    contains: bool | None = None  # 사장님 확인: 그 대상이 들어간다(True)·안 들어간다(False)


@dataclass(frozen=True)
class KnowledgeRequest:
    """보충 요청: AI가 답하지 못하고 사장님께 전해 둔다고 한 메뉴 질문(턴 결과 → 세션 → 허브)."""

    key: KnowledgeKey
    menu_name: str  # 한국어 메뉴명
    text: str  # 손님 말 원문 (STT 글)


class KnowledgeReader(Protocol):
    """엔진이 보는 읽기 포트. 없으면 None."""

    def lookup(self, key: KnowledgeKey) -> Known | None: ...


class KnowledgeStore:
    """메모리 저장소 하나(가게 하나). 읽기는 엔진, 쓰기는 사장님 허브."""

    def __init__(self) -> None:
        self._known: dict[KnowledgeKey, Known] = {}

    def lookup(self, key: KnowledgeKey) -> Known | None:
        return self._known.get(key)

    def save_note(self, key: KnowledgeKey, note: str) -> None:
        """사장님 설명을 저장한다(다시 저장하면 덮어쓴다). 빈 글이면 `ValueError`."""
        note = note.strip()
        if not note:
            raise ValueError("빈 설명은 저장하지 않아요")
        self._known[key] = replace(self._known.get(key, Known()), note=note)

    def save_check(self, key: KnowledgeKey, contains: bool) -> None:
        """사장님 확인(질문 카드 돼요/안 돼요)을 저장한다. 나중 확인이 앞 확인을 덮어쓴다."""
        self._known[key] = replace(self._known.get(key, Known()), contains=contains)
