"""손님 요청 배지 (4단계 spec "번역", 리서치 TR Q3 보호 장치 3). 한국어 원문 규칙으로만 붙인다.

번역이 부정을 놓치거나 뒤집어도("빼 주세요" → "넣어 주세요") 사장님이 위험한 요청을 놓치지 않게
번역 결과는 보지 않는다. 배지 이름은 코드(`remove`·`allergy`)이고 화면이 사장님 언어 고정 문구로
보인다.

- `remove`("빼기"): 빼·없이·말고·넣지·안 넣·제외·필요 없·없어도·들어가면 안·넣으면 안·
  안 주셔도·안 줘도.
  "덜"(덜 달게)은 빼기가 아니라 안 붙인다
- `allergy`("알레르기"): 알레르기·알러지·못 먹·먹으면 안 같은 말이 있거나, 알레르기 성분 이름과
  빼기가 같이 있을 때("땅콩 빼 주세요"). 성분 이름만 있으면("우유 많이") 안 붙인다
"""

from __future__ import annotations

# 띄어쓰기를 지운 원문에서 찾는다("고수빼주세요", "못먹어요")
_REMOVE = ("빼", "없이", "말고", "넣지", "안넣", "제외", "필요없", "없어도",
           # 티켓 12: 04 실측에서 배지가 빠진 모양("땅콩 들어가면 안 돼요", "초는 안 주셔도 돼요")
           "들어가면안", "넣으면안", "안주셔도", "안줘도")
_ALLERGY = ("알레르기", "알러지", "알레르겐", "못먹", "먹으면안", "먹으면큰일")
_ALLERGENS = ("견과", "땅콩", "호두", "아몬드", "잣", "우유", "유제품", "치즈", "계란", "달걀",
              "밀가루", "글루텐", "대두", "메밀", "새우", "갑각류", "조개", "복숭아", "참깨")


def request_badges(text: str) -> list[str]:
    """한국어 요청 원문 → 배지 코드 목록(`remove`, `allergy` 순)."""
    flat = "".join(text.split())
    badges = []
    remove = any(w in flat for w in _REMOVE)
    if remove:
        badges.append("remove")
    if any(w in flat for w in _ALLERGY) or (remove and any(w in flat for w in _ALLERGENS)):
        badges.append("allergy")
    return badges
