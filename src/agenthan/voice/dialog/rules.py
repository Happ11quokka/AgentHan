"""NLU를 부르기 전 규칙 단계 (spec D3, 리서치 R-Q2).

예/아니오, 주문 취소, 사장님 연결, 다시 말해 주세요는 정규식으로 바로 act로 바꾸고 NLU를 건너뛴다.
걸리지 않으면 None을 돌려주고 엔진이 NLU를 부른다.

- 예/아니오는 발화 전체가 그 말일 때만 건다. "아니요, 두 개요"처럼 뒤에 내용이 붙으면 NLU가 읽는다
- 취소는 "주문"이 들어가거나 발화가 "취소할게요" 꼴뿐일 때만 건다. "블린 취소할게요"는
  메뉴 빼기라서 NLU로 넘긴다. 앞에 붙은 "아니요,"·"네," 머리말은 떼고 본다
  ("아니요, 취소할게요"는 주문 취소다)
- 사장님 연결은 "사장님/사람/직원 + 바꿔/연결/통화"가 있을 때만 건다. "사장님이 직접
  만드세요?"는 질문이다. "사람이랑 얘기할게요"·"사장님 계세요?"·"기계 말고 사람이요"도 같다
  (6단계 품질, 사람 요청)
- 짜증 표현(`frustrated`)은 act가 아니다. 엔진이 통화에서 이미 한 번 되물은 뒤에만 보고 사장님께
  넘긴다. 오탐이 적은 말만 둔다("몇 번을 말해요"·"말했잖아요"·"왜 못 알아들어요"·"답답하네"·
  욕설·"그냥 끊을게요"). "됐어요"·"아 진짜"처럼 다른 뜻으로도 쓰는 말은 넣지 않는다
- "아니요 + 끝 표현"("아니요, 그게 다예요"·"아뇨 됐어요")과 "없어요"는 [no, done_ordering]이다
  (6단계 티켓 02). 발화 전체가 그 꼴일 때만 건다. 머리말 없는 "그게 다예요"·"괜찮아요"는 다른
  질문의 답일 수 있어 NLU가 읽는다. 엔진은 예/아니요를 묻는 중(메뉴 확인·시각 제안·취소 확인)에는
  이 규칙을 쓰지 않는다(`no_and_done`, 그때는 "아니요"가 답이고 뒷말은 군말일 수 있다)
- 쿠폰·할인 물음("쿠폰 돼요?"·"할인 되나요"·"쿠폰 쓸게요")은 `ask_coupon`이다 (쿠폰 티켓 11).
  발화가 쿠폰·할인 말과 묻는·쓰겠다는 말뿐일 때만 건다. "할인 메뉴 주세요"처럼 다른 말(메뉴 등)이
  섞이면 NLU가 읽는다(주문이 먼저다). NLU가 그 말을 질문으로 내면 엔진이 쿠폰·할인 낱말을 보고
  쿠폰 답으로 돌린다(`mentions_coupon`). `ask_coupon`은 규칙만 내는 act라 HCX 도구 스키마에는 없다.
  쿠폰 권유의 답이 쿠폰 말이면("네, 쿠폰 쓸게요"·"아니요, 쿠폰 안 써도 돼요") 엔진이
  `coupon_offer_answer`로 그 예/아니요를 읽는다. 질문에 섞인 쿠폰 마디는 `without_coupon_clauses`로
  떼고 남은 질문도 답한다
"""

from __future__ import annotations

import re
from typing import Literal

from agenthan.voice.nlu.base import Act

_TRAILING = re.compile(r"[\s.,!?~]+$")
_SEP = r"[\s,.!~]+"

# 짧은 예 말. 예/아니요 판정과 맞장구 목록(`BACKCHANNELS`)이 같이 쓴다 (리뷰 S10)
YES_SHORT = ("네", "예", "응", "그래요")
_YES_WORD = rf"(?:{'|'.join(YES_SHORT)}|맞아요|맞아|맞습니다|좋아요|그렇게\s*해\s*주세요)"
_NO_WORD = r"(?:아니요|아뇨|아니|아니에요|아닌데요|틀려요|틀렸어요)"
_YES = re.compile(rf"{_YES_WORD}(?:(?:{_SEP})?{_YES_WORD})*")
_NO = re.compile(rf"{_NO_WORD}(?:(?:{_SEP})?{_NO_WORD})*")

# "아니요, 그게 다예요" (6단계 티켓 02). 손님이 "아니요" 뒤에 쉬었다 덧붙인 말을 세션이 한 턴으로
# 합치면 이 꼴이 된다. NLU(HCX 약 1초)를 건너뛰려고 규칙으로 둔다. NLU가 내던 것과 같은
# [no, done_ordering]을 내서 상태별 뜻은 엔진이 그대로 정한다: "더 필요하신 거?" 뒤면 주문 끝
# (`_no_means_done`), 그 밖에는 앞의 no를 떼고 done_ordering만 본다. 그래서 복창 중에는 확정도
# 확정 거부도 아니다(못 알아들었다고 하고 복창을 다시 한다).
# STT 표기 흔들림: 다예요·다에요·다 예요·다요·답니다, 끝이에요·끝이예요, 됐어요·됬어요·됐습니다
_CLOSE_HEAD = r"(?:아니요|아니오|아뇨|아니에요|아니)"
_NONE_LEFT = (
    r"(?:(?:더\s*는?\s*)?(?:필요\s*(?:한\s*(?:거|건|것)\s*(?:은|는)?\s*)?)?"
    r"(?:없어요|없어|없습니다))"
)
_THIS = r"(?:그게|그거|이게|이거|그걸로|이걸로|그거면|이거면)"
_THATS_ALL = (
    rf"(?:(?:(?:이제|그럼)\s*)?(?:{_THIS}\s*)?"
    r"(?:(?:다|전부|끝)\s*(?:예요|에요|이에요|이예요|이요|요|입니다|야|이야)|답니다"
    r"|(?:다\s*)?(?:됐어요|됬어요|됐습니다|됐어))"
    # "그거면 돼요"는 가리키는 말이 있을 때만. 맨 "돼요"는 무엇이 된다는 말인지 모른다
    rf"|(?:(?:이제|그럼)\s*)?{_THIS}\s*(?:돼요|되요|됩니다))"
)
_CLOSE_TAIL = rf"(?:{_THATS_ALL}|{_NONE_LEFT}|괜찮아요|괜찮습니다|괜찮아)"
_NO_AND_DONE = re.compile(
    rf"{_CLOSE_HEAD}(?:{_SEP}{_CLOSE_HEAD})*(?:{_SEP}{_CLOSE_TAIL})+"
    rf"|{_NONE_LEFT}(?:{_SEP}{_CLOSE_TAIL})*"
)

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
    # 사람 요청 (6단계 품질). "사람이랑 얘기할게요"·"직원한테 말할래요".
    # 지난 일("얘기했는데요")은 아니다
    rf"|{_WHO}\s*(?:이랑|하고|과|와|한테)\s*(?:직접\s*)?(?:얘기|이야기|말|말씀)\s*"
    r"(?:할게|할래|하고\s*싶|하게\s*해|하겠|드릴게)"
    # "사장님 계세요?"·"사장님 안 계세요?" (주어 조사가 붙은 "사장님이 계세요?"는 NLU가 읽는다)
    r"|사장님\s*(?:지금\s*)?(?:안\s*)?(?:계세요|계신가요|계십니까)"
    # "기계 말고 사람이요"·"AI 말고 사람"
    rf"|(?:기계|로봇|에이아이|AI|ai|인공지능|자동\s*응답)\s*말고\s*{_WHO}"
)

# 짜증 표현 (6단계 품질). 발화 어디에 있어도 본다. 오탐이 적은 것만: 되풀이를 탓하는 말,
# 못 알아듣는다는 말, 답답·짜증, 욕설. 끊겠다는 말은 발화 전체가 그 말일 때만
_FRUSTRATED = re.compile(
    r"몇\s*번(?:을|이나|씩|째)?\s*(?:을\s*)?(?:말|얘기|이야기|말씀)"
    r"|(?:말했|얘기했|이야기했|말씀드렸|말씀\s*드렸)잖아"
    r"|(?:왜|자꾸|계속|진짜|정말|하나도)\s*(?:이렇게\s*)?(?:말을\s*)?못\s*알아\s*(?:들어|듣|먹)"
    r"|말귀|답답하|답답해|짜증\s*(?:나|이\s*나|납니다)"
    r"|씨발|시발|씨바|젠장|에이\s*씨"
)
_HANG_UP = re.compile(
    r"(?:(?:아\s*)?(?:됐어요|됐어|됬어요|됐습니다)\s*)?(?:그냥\s*)?(?:전화\s*)?"
    r"(?:끊을게요|끊을래요|끊겠습니다|끊어요|끊습니다)"
)

# 쿠폰·할인 물음 (쿠폰 티켓 11). 머리말 + 쿠폰 말 + 조사 + 묻는·쓰겠다는 말(되다·있다·쓰다·적용 …
# 줄기와 짧은 끝). 메뉴 이름 같은 다른 낱말이 끼면 걸리지 않는다
_COUPON_WORD = r"(?:쿠폰|할인)"
_COUPON_HEAD = (
    r"(?:(?:혹시|근데|그런데|그리고|그럼|저기|저|그|음|어|아|참|여기|지금|오늘|이번에|제가"
    r"|전화\s*주문(?:은|도|에|으로|이면|하면)?|포장(?:은|도|하면)?)(?:[\s,.!~]+))*"
)
_COUPON_PARTICLE = (
    r"(?:\s*(?:같은|이런|그런)\s*(?:거|건|것))?"
    r"(?:은요|는요|이요|은|는|이|가|도|을|를|이나|이라도|요)?"
)
_COUPON_LINK = r"(?:안|못|수|거|건|게|좀|따로|같이)"
_COUPON_VERB = r"(?:되|돼|됩|될|있|없|쓰|쓸|써|사용|적용|받|해|하|할|주|가능)[가-힣]{0,5}"
_COUPON = re.compile(
    rf"{_COUPON_HEAD}{_COUPON_WORD}(?:\s*{_COUPON_WORD})*{_COUPON_PARTICLE}"
    rf"(?:\s*(?:{_COUPON_LINK}\s*)*{_COUPON_VERB})*"
)
_COUPON_MENTION = re.compile(_COUPON_WORD)
# 쿠폰 권유("… 더 담으실까요?")의 답이 쿠폰 말로 올 때 (리뷰). "쿠폰 안 써도 돼요"는 거절,
# "쿠폰 쓸게요"는 받겠다는 말이다. 둘 다 아니면 머리말 예/아니요를 본다
_COUPON_DECLINE = re.compile(
    r"(?:안|못)\s*(?:써|쓸|쓰|해|할|받)|없어도|필요\s*없|괜찮|됐어|됬어|말고")
_COUPON_ACCEPT = re.compile(
    r"쓸게|쓸래|쓰고\s*싶|써\s*주|사용할게|사용해\s*주|적용할게|적용해\s*주|받을게|받고\s*싶")
_YES_LEAD = re.compile(rf"^{_YES_WORD}(?:{_SEP})")
_NO_LEAD = re.compile(rf"^{_NO_WORD}(?:{_SEP})")
# 한 말 안의 질문 마디 경계: 문장 부호, 또는 "…요" 뒤 띄어쓰기("들어가요 할인도 되나요")
_CLAUSE_END = re.compile(r"[?.!]+\s*|(?<=요)\s+")

_REPEAT = re.compile(
    r"다시\s*(?:한\s*번\s*)?(?:말해|말씀해)\s*(?:주세요|주실래요|줄래요)?|뭐라고요|뭐라고\s*하셨어요"
)


# 기다림 요청("잠깐만요"). 말 끝에 있으면 앞에 항목이 있어도 기다림 요청이다
# ("아메리카노 한 잔이랑, 잠시만요"). 끝말 규칙(`call/turn_end.py`)도 이 식을 쓴다
WAIT_REQUEST = re.compile(
    r"(?:잠깐|잠시)\s*만?\s*(?:요|기다려\s*(?:주세요|줘요|주실래요|줘)?)?$"
)
# 글을 토큰으로 나눌 때 지우는 문장부호. 끝말 규칙(`call/turn_end.py`)도 이것을 쓴다 (리뷰 S8)
PUNCT = re.compile(r"[.,!?~…·]+")
# 필러. 끝말 규칙의 "필러만 있는 말"·"끝 필러"와 기다림 요청 앞 머리말이 같이 쓴다 (리뷰 S9).
# 띄어쓰기로 본다(STT가 "음맞는"처럼 붙여 쓰면 필러가 아니다)
FILLERS = frozenset({
    "음", "으음", "음음", "어", "어어", "으", "아", "그", "저", "저기", "뭐더라", "그러니까",
})
# 맞장구 목록 (spec: 하나로 둔다). 짧은 예 말 + 짧은 필러. 듣는 중이면 답, 봇이 말하는 중이면
# 맞장구로 본다
BACKCHANNELS = frozenset({*YES_SHORT, "네네", "어", "아", "음"})


def wait_request_rest(text: str) -> str | None:
    """기다림 요청으로 끝나는 말이면 그 앞의 내용(없으면 ""), 아니면 None (3단계 티켓 04).

    앞부분이 예/아니요·필러·"잠깐"뿐이면 내용이 없다고 본다. 복창 중 "네, 잠시만요"가 확정이
    되지 않게.
    """
    t = " ".join(PUNCT.sub(" ", text).split())
    found = WAIT_REQUEST.search(t)
    if found is None:
        return None
    rest = t[: found.start()].strip()
    if yes_or_no(rest) is not None or all(_no_content(w) for w in rest.split()):
        return ""
    return rest


# 하던 말을 거두는 혼잣말("한 여섯 시… 아 아니다"). 앞에 내용이 있을 때만 본다
_RETRACTION = re.compile(r"(?:아\s*)?아니다$|아\s+아니(?:야|에요|예요)?$")


def retracted(text: str) -> bool:
    """내용을 말하다가 "아 아니다"로 거둔 말이다 (5단계 티켓 11, 실기기 통화).

    "아니다"만 있거나 앞이 예/아니요·필러뿐이면 그냥 "아니요"다(규칙·NLU가 본다).
    """
    t = " ".join(PUNCT.sub(" ", text).split())
    found = _RETRACTION.search(t)
    if found is None:
        return False
    rest = t[: found.start()].strip()
    return bool(rest) and yes_or_no(rest) is None and not all(_no_content(w) for w in rest.split())


def _no_content(word: str) -> bool:
    return word in FILLERS or word in ("잠깐", "잠시") or yes_or_no(word) is not None


def yes_or_no(text: str) -> str | None:
    """발화 전체가 예/아니요 말이면 "yes"·"no", 아니면 None.

    끝말 규칙(`call/turn_end.py`)도 같은 목록을 쓴다.
    """
    t = _TRAILING.sub("", text.strip())
    if _YES.fullmatch(t):
        return "yes"
    if _NO.fullmatch(t):
        return "no"
    return None


def frustrated(text: str) -> bool:
    """손님이 짜증을 냈다 (6단계 품질). 엔진이 이미 되물은 통화에서만 보고 사장님께 넘긴다."""
    t = _TRAILING.sub("", " ".join(PUNCT.sub(" ", text).split()))
    return bool(t) and bool(_FRUSTRATED.search(t) or _HANG_UP.fullmatch(t))


def no_and_done(text: str) -> bool:
    """`apply_rules`가 "아니요 + 끝 표현" 규칙으로 [no, done_ordering]을 내는 말인가.

    엔진이 이 규칙을 쓸지 대화 상태로 정할 때 본다(`DialogEngine._parse`).
    """
    t = _TRAILING.sub("", text.strip())
    return bool(t) and not _NO.fullmatch(t) and bool(_NO_AND_DONE.fullmatch(t))


def mentions_coupon(text: str) -> bool:
    """쿠폰·할인 낱말이 있다 (쿠폰 티켓 11). 엔진이 NLU가 낸 질문 act를 쿠폰 답으로 돌릴 때 본다."""
    return bool(_COUPON_MENTION.search(text))


def coupon_offer_answer(text: str) -> Literal["yes", "no"] | None:
    """쿠폰 권유("… 더 담으실까요?")에 쿠폰 말로 한 답의 뜻 (리뷰). 쿠폰 물음 꼴이 아니면 None.

    "네, 쿠폰 쓸게요"·"쿠폰 쓸게요"는 yes, "아니요, 쿠폰 안 써도 돼요"·"쿠폰은 괜찮아요"는 no다.
    쓰겠다는 말과 머리말 "아니요"가 엇갈리면 None(되묻는다). "쿠폰 돼요?"처럼 묻기만 하면
    머리말이 있을 때만 그 예/아니요다.
    """
    t = _TRAILING.sub("", text.strip())
    rest = _LEAD.sub("", t)
    if not t or not _COUPON.fullmatch(rest):
        return None
    if _COUPON_DECLINE.search(rest):
        return "no"
    if _COUPON_ACCEPT.search(rest):
        return None if _NO_LEAD.match(t) else "yes"
    if _YES_LEAD.match(t):
        return "yes"
    if _NO_LEAD.match(t):
        return "no"
    return None


def without_coupon_clauses(text: str) -> str:
    """질문 한 말에서 쿠폰·할인 말이 든 마디를 뺀 나머지 (리뷰). 엔진은 나머지를 메뉴·가게
    질문으로 답하고 쿠폰 답을 뒤에 붙인다.

    "메도빅에 견과류 들어가요? 할인도 되나요?" → "메도빅에 견과류 들어가요?".
    "메도빅도 할인돼요?"·"쿠폰 받으려면 얼마 더 사야 해요?"처럼 마디가 모두 쿠폰 말이면 "".
    """
    clauses, start = [], 0
    for m in _CLAUSE_END.finditer(text):
        clauses.append(text[start:m.end()])
        start = m.end()
    clauses.append(text[start:])
    return " ".join(c.strip() for c in clauses if c.strip() and not _COUPON_MENTION.search(c))


def apply_rules(text: str) -> list[Act] | None:
    """규칙에 걸리면 act 목록, 아니면 None."""
    t = _TRAILING.sub("", text.strip())
    if not t:
        return None
    if _YES.fullmatch(t):
        return [Act("yes")]
    if _NO.fullmatch(t):
        return [Act("no")]
    if _NO_AND_DONE.fullmatch(t):
        return [Act("no"), Act("done_ordering")]
    rest = _LEAD.sub("", t)  # "아니요, 취소할게요"의 머리말은 떼고 본다 (리뷰 C6)
    if _CANCEL.fullmatch(rest) or _CANCEL_ANYWHERE.search(t):
        return [Act("cancel_order", free_text=text.strip())]
    if _OWNER.search(t):
        return [Act("want_owner", free_text=text.strip())]
    if _REPEAT.fullmatch(t):
        return [Act("repeat_please")]
    if _COUPON.fullmatch(rest):
        return [Act("ask_coupon", free_text=text.strip())]
    return None
