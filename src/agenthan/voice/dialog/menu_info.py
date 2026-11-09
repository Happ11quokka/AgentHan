"""메뉴 질문 답하기 (티켓 05, spec §2).

`MenuItem`의 필드(설명·가격·옵션·알레르기)로만 답한다. 필드에 근거가 없으면 지어내지 않고
"그건 제가 정확히 모르겠어요"라고 한다.

질문 종류는 순서대로 본다.
1. 알레르기 성분 하나("우유 들어가요?"): 목록에 있으면 "네, 들어가요". 없으면 모른다
   (목록에 없다고 안 들어간다는 근거는 아니다)
2. 알레르기 전체("알레르기 있어요?"): 목록을 읽는다. 비어 있으면 모른다
3. 가격("얼마예요?")
4. 옵션 하나("디카페인 돼요?") 또는 옵션 전체("옵션 뭐 있어요?")
5. 재료("재료가 뭐예요?", "뭐가 들어가요?"): 설명을 읽는다. 비어 있으면 모른다
6. 맛·설명("달아요?", "뭐예요?"): 설명을 읽는다. 비어 있으면 모른다
7. 그 밖("글루텐 프리예요?", "몇 인분이에요?"): 모른다

5 앞에서 이름 붙은 재료 하나("시나몬 들어가요?")를 본다: 재료 종류, 대상은 그 낱말, 필드로는 늘
모른다(설명에 나와도 추론하지 않는다).

보충 지식(티켓 13)을 찾을 대상(`MenuAnswer.subject`)도 같이 돌려준다: 알레르기 성분 하나, 이름 붙은
재료, 그 밖 질문은 메뉴명·띄어쓰기·부호를 뺀 원문. "들어가요?"처럼 들어가는지만 묻는 질문은
`yes_no`가 참이다(사장님 "돼요/안 돼요"를 들어간다/안 들어간다는 사실로 저장할 수 있다, Q20).

답과 함께 질문 종류(`MenuAnswer.kind`)를 돌려준다. 엔진은 근거 없는 답 가운데 알레르기·재료·가능
여부(그 밖) 질문만 사장님께 묻는다(4단계 spec "ASK_OWNER: 엔진", 티켓 06). 맛·설명은 버튼 셋
(돼요/안 돼요/모르겠어요)으로 답하기 어색하고, 가격·옵션은 늘 필드로 답한다.

"글루텐"을 밀로 바꿔 읽는 것처럼 필드 밖 지식으로 추론하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agenthan.core.contracts import MenuItem
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.options import find_options

# 손님이 부르는 말 → 알레르기 목록에 적힌 이름
_ALLERGEN_SYNONYMS = {"밀가루": "밀", "달걀": "계란", "유제품": "우유"}
# 알레르기 표시 대상 22종(식약처)에서 손님이 자주 말하는 것. 목록 밖 성분 질문을 알아보려고 쓴다.
_ALLERGEN_WORDS = (
    "밀가루", "메밀", "밀", "우유", "유제품", "계란", "달걀", "땅콩", "호두", "견과", "대두", "콩",
    "새우", "꽃게", "오징어", "조개", "고등어", "복숭아", "토마토", "돼지고기", "쇠고기", "닭고기",
)  # fmt: skip
# "게"·"잣"은 "어떤 게 있어요?"처럼 다른 말과 겹쳐서 뺐다.
_ALLERGY = re.compile(r"알레르기|알러지")
# "얼마나 달아요/커요"는 정도를 묻는 말이라 뺀다 (리뷰 P2)
_PRICE = re.compile(r"얼마(?!나)|가격|몇\s*원|비싸|비싼")
_OPTION = re.compile(r"옵션|변경|추가|바꿀\s*수|고를\s*수|선택")
# 재료 질문. 맛·설명(`_DESCRIBE`)보다 먼저 본다("어떤 재료"의 "어떤")
_INGREDIENT = re.compile(r"재료|성분|뭐가\s*들어|뭐\s*들어")
# 들어가는지 묻는 말("들어가요?", "들었어요?", "있어요?")과 그 뜻을 흐리는 말(빼기·없음·괜찮음)
_PRESENCE = re.compile(
    r"들어\s*가|들어\s*있|들었|넣어|넣나|넣었|함유|있어요|있나요|있죠|있어\?|있니")
_NOT_PRESENCE = re.compile(r"빼|없|안\s*들|안\s*넣|프리|괜찮|먹어도|돼")
# 이름 붙은 재료 하나: "시나몬 들어가요?", "고수가 들어있어요?" (낱말 바로 뒤에 조사·띄어쓰기)
_NAMED = re.compile(r"(?<![가-힣A-Za-z])([가-힣A-Za-z]+)\s*(?:들어\s*가|들어\s*있|들었)")
# 재료 이름이 아닌 말 ("뭐가 들어가요?"는 재료 전체 질문)
_NOT_NAMED = frozenset({
    "뭐", "뭐가", "뭔가", "무엇", "무엇이", "무슨", "어떤", "재료", "재료가", "성분", "성분이",
    "거", "게", "것", "것이", "다", "많이", "조금", "같이", "안",
})
_PARTICLE = re.compile(r"(?:이|가|은|는|도|를|을)$")
_DESCRIBE = re.compile(
    r"달아|달까|단가|달달|단맛|맛|어떤|뭐예요|뭐에요|뭔가요|뭐가\s*들어|무슨|어때|설명|재료"
    r"|부드러|촉촉|바삭|느끼|매워|매운|짜요|짜나|시어|셔요"
)


# 메뉴 질문 종류: 알레르기 성분 하나 · 알레르기 전체 · 가격 · 옵션 · 재료 · 맛·설명 · 그 밖
QuestionKind = Literal["allergen", "allergy", "price", "option", "ingredient", "taste", "other"]
# 근거가 없을 때 사장님께 물을 수 있는 종류 (spec "알레르기 성분이 목록에 없음, 재료, 가능 여부")
_OWNER_KINDS: frozenset[QuestionKind] = frozenset({"allergen", "allergy", "ingredient", "other"})


@dataclass(frozen=True)
class MenuAnswer:
    """메뉴 질문 하나의 답. `text`가 None이면 필드에 근거가 없다."""

    kind: QuestionKind
    text: str | None
    subject: str = ""  # 보충 지식 대상 (티켓 13, 모듈 설명)
    yes_no: bool = False  # 대상이 들어가는지만 묻는다 (사장님 돼요/안 돼요 = 들어간다/안 들어간다)

    @property
    def for_owner(self) -> bool:
        """근거가 없고, 사장님 버튼 답(돼요/안 돼요/모르겠어요)으로 답할 만한 질문이다."""
        return self.text is None and self.kind in _OWNER_KINDS


def answer_menu_question(item: MenuItem, question: str) -> str:
    """메뉴 질문 하나에 한두 문장으로 답한다. 근거가 없으면 "그건 제가 정확히 모르겠어요"."""
    return menu_answer(item, question).text or templates.dont_know()


def menu_answer(item: MenuItem, question: str) -> MenuAnswer:
    """메뉴 필드에 근거가 있는 답과 질문 종류. 근거가 없으면 `text`가 None(4단계 티켓 05·06).

    근거 없음: 목록에 없는 알레르기 성분, 빈 알레르기 목록, 빈 설명(재료·맛), 그 밖의 질문.
    """
    presence = bool(_PRESENCE.search(question)) and not _NOT_PRESENCE.search(question)
    if (asked := _asked_allergen(question)) is not None:
        if asked in item.allergens:
            return MenuAnswer("allergen", templates.allergen_present(item.name_ko, asked),
                              asked, presence)
        return MenuAnswer("allergen", None, asked, presence)
    if _ALLERGY.search(question):
        if not item.allergens:
            return MenuAnswer("allergy", None)
        return MenuAnswer("allergy", templates.allergen_list(item.name_ko, item.allergens))
    if _PRICE.search(question):
        return MenuAnswer("price", templates.menu_price(item.name_ko, item.price))
    if found := find_options(item.options, question):
        option = found[0]
        if not option.available:
            return MenuAnswer("option", templates.option_unavailable(option.name))
        return MenuAnswer("option", templates.option_possible(item.name_ko, option.name,
                                                              option.extra_price))
    if _OPTION.search(question):
        names = [o.name for o in item.options if o.available]
        return MenuAnswer("option", templates.menu_options(item.name_ko, names))
    if (named := _named_ingredient(question, item)) is not None:
        return MenuAnswer("ingredient", None, named, presence)
    kind: QuestionKind | None = ("ingredient" if _INGREDIENT.search(question)
                                 else "taste" if _DESCRIBE.search(question) else None)
    if kind is not None:
        if not item.description:
            return MenuAnswer(kind, None)
        return MenuAnswer(kind, templates.menu_description(item.name_ko, item.description))
    return MenuAnswer("other", None, _gist(question, item))


def _named_ingredient(question: str, item: MenuItem) -> str | None:
    """질문에 이름이 나온 재료 하나("블린에 시나몬 들어가요?" → 시나몬). 없으면 None."""
    found = _NAMED.search(question)
    if found is None:
        return None
    word = found.group(1)
    if word in _NOT_NAMED or word in (item.name_ko, *item.aliases):
        return None
    bare = _PARTICLE.sub("", word)
    word = bare if len(bare) >= 2 else word  # "오이"의 "이"는 조사가 아니다
    return None if word in _NOT_NAMED else word


def _gist(question: str, item: MenuItem) -> str:
    """그 밖 질문의 보충 지식 대상: 메뉴명(과 바로 뒤 조사)·띄어쓰기·부호를 뺀 원문.

    "메도빅 비건이에요?"와 "메도빅 몇 인분이에요?"가 한 답을 쓰지 않게, 그 밖 질문은 같은 말일
    때만 같은 보충 지식을 쓴다.
    """
    text = re.sub(r"[\s?!.,~]", "", question)
    for name in sorted((item.name_ko, *item.aliases), key=len, reverse=True):
        text = re.sub(rf"{re.escape(name.replace(' ', ''))}(?:은|는|이|가|에|에는|도)?", "", text)
    return text


def _asked_allergen(question: str) -> str | None:
    """질문에 나온 알레르기 성분 하나(목록 표기로). 없으면 None.

    "밀"·"콩"처럼 한 글자는 다른 말 속에 끼기 쉬우니("밀크") 낱말 경계를 본다.
    """
    for word in _ALLERGEN_WORDS:
        pattern = word
        if len(word) == 1:  # 한 글자 성분 뒤에는 조사가 붙을 수 있다: "밀이", "콩은"
            pattern = rf"(?<![가-힣]){word}(?:이|가|은|는|도)?(?![가-힣])"
        if re.search(pattern, question):
            return _ALLERGEN_SYNONYMS.get(word, word)
    return None
