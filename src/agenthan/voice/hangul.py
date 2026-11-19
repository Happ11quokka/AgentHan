"""한글 자모 분해와 자모 단위 편집거리. 메뉴 매처(`matching.py`)가 쓴다.

- 음절 분해는 유니코드 표준의 한글 음절 공식을 따른다.
  음절 번호 = (초성 × 21 + 중성) × 28 + 종성, 음절 = U+AC00 + 음절 번호
- 편집거리는 보통의 두 줄 동적 계획법(Wagner–Fischer)이다. 넣기·빼기는 1이고, 음절 바꾸기는
  초·중·종성 세 자리의 편집거리를 3으로 나눈 값이다. "빅" → "빗"은 1/3.
- `confusable_distance`는 같은 틀에 "STT가 자주 헷갈리는 자모는 싸게" 비용 표를 얹은 것이다
  (티켓 10, `research/s2-stt-misrecognition.md` Q2). 메뉴 매처가 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

_T = TypeVar("_T", bound=Hashable)

BLANK = " "  # 빈 자리 (받침 없음, 낱자음의 중성 등)

_SYLLABLE_FIRST = 0xAC00  # 가
_SYLLABLE_LAST = 0xD7A3  # 힣
_MEDIALS_X_FINALS = 21 * 28

# 호환용 자모(U+3131~)로 적는다. 손님 말과 테스트가 이 글자를 쓴다.
_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_MEDIALS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_FINALS = (BLANK, *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")

# 낱자 (호환용 자모의 현대 글자 구간)
_LONE_CONSONANTS = range(0x3131, 0x314F)  # ㄱ ~ ㅎ
_LONE_VOWELS = range(0x314F, 0x3164)  # ㅏ ~ ㅣ

Jamo = tuple[str, str, str]


def decompose(c: str) -> Jamo | None:
    """글자 하나 → (초성, 중성, 종성). 빈 자리는 `" "`. 한글이 아니거나 한 글자가 아니면 None.

    낱자음은 `(c, " ", " ")`, 낱모음은 `(" ", c, " ")`이다.
    """
    if len(c) != 1:
        return None
    code = ord(c)
    if _SYLLABLE_FIRST <= code <= _SYLLABLE_LAST:
        index = code - _SYLLABLE_FIRST
        initial, rest = divmod(index, _MEDIALS_X_FINALS)
        medial, final = divmod(rest, 28)
        return _INITIALS[initial], _MEDIALS[medial], _FINALS[final]
    if code in _LONE_CONSONANTS:
        return c, BLANK, BLANK
    if code in _LONE_VOWELS:
        return BLANK, c, BLANK
    return None


def final_consonant(word: str) -> str:
    """마지막 글자의 받침. 받침이 없거나 마지막 글자가 한글 음절이 아니면 ""."""
    last = word[-1:]
    if not last or not _SYLLABLE_FIRST <= ord(last) <= _SYLLABLE_LAST:
        return ""
    jamo = decompose(last)
    assert jamo is not None
    return "" if jamo[2] == BLANK else jamo[2]


def has_batchim(word: str) -> bool:
    """마지막 음절에 받침이 있나. 조사 고르기(이/가, 은/는)와 조사 떼기에 쓴다."""
    return final_consonant(word) != ""


def _edit_distance(
    a: Sequence[_T], b: Sequence[_T], substitute: Callable[[_T, _T], float]
) -> float:
    """넣기·빼기 1, 바꾸기 `substitute(x, y)`인 편집거리. 같은 원소는 0."""
    previous = [float(j) for j in range(len(b) + 1)]
    for i, x in enumerate(a, start=1):
        current = [float(i)]
        for j, y in enumerate(b, start=1):
            change = 0.0 if x == y else substitute(x, y)
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + change))
        previous = current
    return previous[-1]


def levenshtein(a: Sequence[Hashable], b: Sequence[Hashable]) -> int:
    """보통의 편집거리 (모든 연산 1). 문자열이나 튜플을 받는다."""
    return int(_edit_distance(a, b, lambda x, y: 1.0))


def _syllable_cost(x: str, y: str) -> float:
    jx, jy = decompose(x), decompose(y)
    if jx is None or jy is None:
        return 1.0
    return levenshtein(jx, jy) / 3


def jamo_levenshtein(a: str, b: str) -> float:
    """음절 넣기·빼기 1, 음절 바꾸기는 자모 차이 / 3. 순서를 바꿔도 값이 같다."""
    return _edit_distance(a, b, _syllable_cost)


# ---------------------------------------------------------------- STT 혼동 비용 (티켓 10)
# 자리별로 "소리가 가까워 STT가 서로 바꿔 적는" 자모 쌍의 바꾸기 비용. 표에 없는 쌍은 1이다.
# 값은 07 실측 변형(메도빅→맥도빅·맥도비, 나폴레옹→나폴내용, 사워크림→샤워크림, 블린→볼링)이
# 매처 수락 구간(0.85 이상)에 들고, 무관 단어(맥도날드·블루베리·메뉴·멕시칸)는 거절 경계 아래에
# 남도록 맞춘 시작값이다. 한국어 STT 자모 혼동 통계를 낸 공개 자료는 없어서 근거는 실측뿐이다.


def _pairs(cost: float, *groups: str) -> dict[frozenset[str], float]:
    """`"ㅔㅐ"`처럼 적은 묶음 안의 모든 두 자모 쌍 → 비용."""
    return {
        frozenset((x, y)): cost
        for group in groups
        for i, x in enumerate(group)
        for y in group[i + 1 :]
    }


_MEDIAL_COST = {
    **_pairs(0.15, "ㅔㅐ", "ㅖㅒ"),  # 서울말에서 사실상 같은 소리
    **_pairs(0.2, "ㅚㅙㅞ"),
    **_pairs(0.35, "ㅏㅑ", "ㅓㅕ", "ㅗㅛ", "ㅜㅠ"),  # 반모음 ㅣ 넣기·빼기 (사워 → 샤워)
    **_pairs(0.4, "ㅢㅣ"),
    **_pairs(0.5, "ㅗㅜ", "ㅓㅗ", "ㅡㅜ", "ㅡㅗ"),  # 입 모양이 가까운 것 (블린 → 볼링)
}
_INITIAL_COST = {
    **_pairs(0.3, "ㄱㄲ", "ㄷㄸ", "ㅂㅃ", "ㅅㅆ", "ㅈㅉ"),  # 평음/경음
    **_pairs(0.5, "ㄱㅋ", "ㄷㅌ", "ㅂㅍ", "ㅈㅊ", "ㄴㄹ"),  # 평음/격음, ㄴ/ㄹ (레옹 → 내용)
}
_FINAL_COST = {
    **_pairs(0.5, "ㄴㅇㅁ"),  # 콧소리 받침끼리 (린 → 링)
    **_pairs(0.3, "ㄱㄲㅋ"),  # 받침에서는 같은 소리
}
# 받침 넣기·빼기. 다음 음절 초성에 묻히기 쉬운 ㄱ·ㅇ·ㄴ만 싸다 (메도빅 → 맥도비)
_FINAL_INSERT_COST = {"ㄱ": 0.4, "ㅇ": 0.4, "ㄴ": 0.4}
_SEAT_COST = (_INITIAL_COST, _MEDIAL_COST, _FINAL_COST)


def _jamo_cost(seat: int, x: str, y: str) -> float:
    if x == y:
        return 0.0
    if seat == 2 and BLANK in (x, y):
        return _FINAL_INSERT_COST.get(y if x == BLANK else x, 1.0)
    return _SEAT_COST[seat].get(frozenset((x, y)), 1.0)


def _confusable_syllable_cost(x: str, y: str) -> float:
    jx, jy = decompose(x), decompose(y)
    if jx is None or jy is None:
        return 1.0
    seats = enumerate(zip(jx, jy, strict=True))
    return sum(_jamo_cost(seat, a, b) for seat, (a, b) in seats) / 3


def confusable_distance(a: str, b: str) -> float:
    """`jamo_levenshtein`과 같은 틀(음절 넣기·빼기 1, 바꾸기는 자리 비용 합 / 3)에
    자리별 혼동 비용 표를 얹은 거리. 표에 없는 쌍은 1이다.

    자모는 자리끼리(초성은 초성끼리) 견준다. 한글 음절끼리는 이것이 세 자리 편집거리와 같아서,
    음절끼리라면 `jamo_levenshtein`보다 커지지 않는다.
    """
    return _edit_distance(a, b, _confusable_syllable_cost)
