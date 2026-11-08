"""메뉴 질문 답하기 (티켓 05, spec §2).

`MenuItem`의 필드(설명·가격·옵션·알레르기)로만 답한다. 필드에 근거가 없으면 지어내지 않고
"그건 제가 정확히 모르겠어요"라고 한다.

질문 종류는 순서대로 본다.
1. 알레르기 성분 하나("우유 들어가요?"): 목록에 있으면 "네, 들어가요". 없으면 모른다
   (목록에 없다고 안 들어간다는 근거는 아니다)
2. 알레르기 전체("알레르기 있어요?"): 목록을 읽는다. 비어 있으면 모른다
3. 가격("얼마예요?")
4. 옵션 하나("디카페인 돼요?") 또는 옵션 전체("옵션 뭐 있어요?")
5. 맛·설명("달아요?", "뭐예요?"): 설명을 읽는다. 비어 있으면 모른다
6. 그 밖("글루텐 프리예요?", "몇 인분이에요?"): 모른다

"글루텐"을 밀로 바꿔 읽는 것처럼 필드 밖 지식으로 추론하지 않는다.
"""

from __future__ import annotations

import re

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
_PRICE = re.compile(r"얼마|가격|몇\s*원|비싸|비싼")
_OPTION = re.compile(r"옵션|변경|추가|바꿀\s*수|고를\s*수|선택")
_DESCRIBE = re.compile(
    r"달아|달까|단가|달달|단맛|맛|어떤|뭐예요|뭐에요|뭔가요|뭐가\s*들어|무슨|어때|설명|재료"
    r"|부드러|촉촉|바삭|느끼|매워|매운|짜요|짜나|시어|셔요"
)


def answer_menu_question(item: MenuItem, question: str) -> str:
    """메뉴 질문 하나에 한두 문장으로 답한다."""
    if (asked := _asked_allergen(question)) is not None:
        if asked in item.allergens:
            return templates.allergen_present(item.name_ko, asked)
        return templates.dont_know()
    if _ALLERGY.search(question):
        if not item.allergens:
            return templates.dont_know()
        return templates.allergen_list(item.name_ko, item.allergens)
    if _PRICE.search(question):
        return templates.menu_price(item.name_ko, item.price)
    if found := find_options(item.options, question):
        option = found[0]
        if not option.available:
            return templates.option_unavailable(option.name)
        return templates.option_possible(item.name_ko, option.name, option.extra_price)
    if _OPTION.search(question):
        names = [o.name for o in item.options if o.available]
        return templates.menu_options(item.name_ko, names)
    if _DESCRIBE.search(question):
        if not item.description:
            return templates.dont_know()
        return templates.menu_description(item.name_ko, item.description)
    return templates.dont_know()


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
