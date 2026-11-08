"""한글 자모 분해와 자모 단위 편집거리. 메뉴 매처(`matching.py`)가 쓴다.

- 음절 분해는 유니코드 표준의 한글 음절 공식을 따른다.
  음절 번호 = (초성 × 21 + 중성) × 28 + 종성, 음절 = U+AC00 + 음절 번호
- 편집거리는 보통의 두 줄 동적 계획법(Wagner–Fischer)이다. 넣기·빼기는 1이고, 음절 바꾸기는
  초·중·종성 세 자리의 편집거리를 3으로 나눈 값이다. "빅" → "빗"은 1/3.
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
