"""NLU를 부르기 전 규칙 단계 (spec D3, 리서치 R-Q2).

예/아니오, 주문 취소, 사장님 연결, 다시 말해 주세요는 정규식으로 바로 act로 바꾸고 NLU를 건너뛴다.
걸리지 않으면 None을 돌려주고 엔진이 NLU를 부른다.

- 예/아니오는 발화 전체가 그 말일 때만 건다. "아니요, 두 개요"처럼 뒤에 내용이 붙으면 NLU가 읽는다
- 취소는 "주문"이 들어가거나 발화가 "취소할게요" 꼴뿐일 때만 건다. "블린 취소할게요"는
  메뉴 빼기라서 NLU로 넘긴다. 앞에 붙은 "아니요,"·"네," 머리말은 떼고 본다
  ("아니요, 취소할게요"는 주문 취소다)
- 사장님 연결은 "사장님/사람/직원 + 바꿔/연결/통화"가 있을 때만 건다. "사장님이 직접
  만드세요?"는 질문이다
"""

from __future__ import annotations

import re

from agenthan.voice.nlu.base import Act

_TRAILING = re.compile(r"[\s.,!?~]+$")
_SEP = r"[\s,.!~]+"

_YES_WORD = r"(?:네|예|응|맞아요|맞아|맞습니다|좋아요|그래요|그렇게\s*해\s*주세요)"
_NO_WORD = r"(?:아니요|아뇨|아니|아니에요|아닌데요|틀려요|틀렸어요)"
_YES = re.compile(rf"{_YES_WORD}(?:(?:{_SEP})?{_YES_WORD})*")
_NO = re.compile(rf"{_NO_WORD}(?:(?:{_SEP})?{_NO_WORD})*")

# 내용 앞의 예/아니요 머리말. "네 개요"의 "네"(넷)는 뒤에 쉼표나 띄어쓰기가 있어야 떼므로 남는다
_LEAD = re.compile(rf"^(?:{_YES_WORD}|{_NO_WORD})(?:{_SEP})")

_CANCEL_TAIL =r"(?:할게요|할래요|해\s*주세요|해\s*줘요|해요|하고\s*싶어요|합니다|요)?"
_CANCEL = re.compile(
    rf"(?:그냥\s*)?(?:주문\s*(?:을|은|도)?\s*)?(?:다\s*|전부\s*|그냥\s*)?취소{_CANCEL_TAIL}"
    r"|(?:그냥\s*)?주문\s*(?:을|은)?\s*안\s*(?:할게요|할래요|해요)"
)
# 발화 어디에 있어도 된다: "죄송한데 주문 취소해 주세요"
_CANCEL_ANYWHERE = re.compile(r"주문\s*(?:을|은|도)?\s*(?:다\s*|전부\s*)?취소")

_WHO = r"(?:사장님|사람|직원(?:분)?|상담원|점원)"
_OWNER = re.compile(
    rf"{_WHO}\s*(?:이랑|하고|과|와|을|를|좀|한테)?\s*(?:좀\s*)?(?:바꿔|연결|통화|대\s*주|불러)"
)

_REPEAT = re.compile(
    r"다시\s*(?:한\s*번\s*)?(?:말해|말씀해)\s*(?:주세요|주실래요|줄래요)?|뭐라고요|뭐라고\s*하셨어요"
)


def apply_rules(text: str) -> list[Act] | None:
    """규칙에 걸리면 act 목록, 아니면 None."""
    t = _TRAILING.sub("", text.strip())
    if not t:
        return None
    if _YES.fullmatch(t):
        return [Act("yes")]
    if _NO.fullmatch(t):
        return [Act("no")]
    rest = _LEAD.sub("", t)  # "아니요, 취소할게요"의 머리말은 떼고 본다 (리뷰 C6)
    if _CANCEL.fullmatch(rest) or _CANCEL_ANYWHERE.search(t):
        return [Act("cancel_order", free_text=text.strip())]
    if _OWNER.search(t):
        return [Act("want_owner", free_text=text.strip())]
    if _REPEAT.fullmatch(t):
        return [Act("repeat_please")]
    return None
