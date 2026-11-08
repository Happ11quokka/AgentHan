"""한글 자모 분해와 자모 편집거리 (spec §6 3번, 리서치 R-Q3).

출처: soynlp (https://github.com/lovit/soynlp)
  - `soynlp/hangle/_hangle.py`의 `decompose`와 자모 표
  - `soynlp/hangle/_distance.py`의 `levenshtein`, `jamo_levenshtein`
  Copyright (C) 2019 lovit <soy.lovit@gmail.com>
  라이선스: GNU Lesser General Public License v3.0 (LGPL-3.0)
  전문: https://github.com/lovit/soynlp/blob/master/LICENSE

soynlp는 numpy·scipy·sklearn에 의존하므로 패키지를 쓰지 않고 위 함수만 이 파일로 옮겼다.
바꾼 점: 타입 힌트, `to_base`를 `ord`로 대체, `levenshtein`의 기본 인자 `cost={}`를 `None`으로,
`decompose` 중복 호출 제거, 파이썬 2 호환 코드 삭제. 알고리즘은 그대로다.

LGPL-3.0 조건 때문에 이 파일은 다른 voice 코드와 섞지 않고 따로 둔다.
이 파일을 고친 내용도 LGPL-3.0으로 공개되어야 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

kor_begin = 44032  # "가"
kor_end = 55203  # "힣"
chosung_base = 588
jungsung_base = 28
jaum_begin = 12593  # "ㄱ"
jaum_end = 12622  # "ㅎ"
moum_begin = 12623  # "ㅏ"
moum_end = 12643  # "ㅣ"

chosung_list = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ",
    "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]  # fmt: skip

jungsung_list = [
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ",
    "ㅕ", "ㅖ", "ㅗ", "ㅘ", "ㅙ", "ㅚ",
    "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ",
    "ㅡ", "ㅢ", "ㅣ",
]  # fmt: skip

jongsung_list = [
    " ", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ",
    "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ",
    "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ",
    "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]  # fmt: skip


def character_is_korean(c: str) -> bool:
    i = ord(c)
    return (kor_begin <= i <= kor_end) or (jaum_begin <= i <= jaum_end) or (
        moum_begin <= i <= moum_end
    )


def decompose(c: str) -> tuple[str, str, str] | None:
    """한 글자 → (초성, 중성, 종성). 빈 자리는 " ". 한글이 아니면 None."""
    if len(c) != 1 or not character_is_korean(c):
        return None
    i = ord(c)
    if jaum_begin <= i <= jaum_end:
        return (c, " ", " ")
    if moum_begin <= i <= moum_end:
        return (" ", c, " ")
    i -= kor_begin
    cho = i // chosung_base
    jung = (i - cho * chosung_base) // jungsung_base
    jong = i - cho * chosung_base - jung * jungsung_base
    return (chosung_list[cho], jungsung_list[jung], jongsung_list[jong])


def levenshtein(
    s1: Sequence[str], s2: Sequence[str], cost: Mapping[tuple[str, str], float] | None = None
) -> float:
    # based on Wikipedia/Levenshtein_distance#Python
    if len(s1) < len(s2):
        return levenshtein(s2, s1, cost)

    if len(s2) == 0:
        return len(s1)

    costs = cost or {}

    def get_cost(c1: str, c2: str) -> float:
        return 0 if (c1 == c2) else costs.get((c1, c2), 1)

    previous_row: list[float] = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row: list[float] = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + get_cost(c1, c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def jamo_levenshtein(s1: str, s2: str) -> float:
    """음절 편집거리. 다른 음절끼리의 대체 비용은 `초·중·종성 편집거리 / 3`이다.

    예: "메도빅" ↔ "메도빗" = 1/3.
    """
    if len(s1) < len(s2):
        return jamo_levenshtein(s2, s1)

    if len(s2) == 0:
        return len(s1)

    def get_jamo_cost(c1: str, c2: str) -> float:
        if c1 == c2:
            return 0
        jamo1 = decompose(c1)
        jamo2 = decompose(c2)
        if jamo1 is None or jamo2 is None:
            return 1
        return levenshtein(jamo1, jamo2) / 3

    previous_row: list[float] = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row: list[float] = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + get_jamo_cost(c1, c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]
