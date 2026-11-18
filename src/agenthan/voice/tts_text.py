"""TTS로 보내기 전 숫자를 말로 바꾼다 (5단계 티켓 10).

CLOVA Voice의 숫자 읽기 규칙은 문서에 없다. "6시"를 "육 시"로 읽을 수 있어 코드로 고정한다
(`docs/research/03-phone-voice.md` 2-3 ②). 자막(`bot.say`)은 숫자 그대로 두고 소리만 바꾼다.

- 시: 고유어("여섯 시", "열두 시"). 분: 한자어("이십 분")
- 원·그 밖 숫자: 한자어("만 구천오백 원"). 쉼표는 뺀다
- 개·잔·명·번: 고유어("열 개"). 1~99만, 그 밖은 한자어
"""

from __future__ import annotations

import re

_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ((1000, "천"), (100, "백"), (10, "십"))
_NATIVE_ONES = ("", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉")
_NATIVE_TENS = ("", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔")
_NATIVE_COUNTERS = ("개", "잔", "명", "번", "그릇", "판", "병", "조각")


def _below_10000(n: int) -> str:
    out = []
    for unit, word in _SMALL_UNITS:
        d, n = divmod(n, unit)
        if d:
            out.append(("" if d == 1 else _DIGITS[d]) + word)
    if n:
        out.append(_DIGITS[n])
    return "".join(out)


def sino(n: int) -> str:
    """한자어 수. 10000 → "만", 19500 → "만 구천오백"."""
    if n == 0:
        return "영"
    parts = []
    for unit, word in ((10**8, "억"), (10**4, "만")):
        d, n = divmod(n, unit)
        if d:
            parts.append(("" if d == 1 and word == "만" else _below_10000(d)) + word)
    if n:
        parts.append(_below_10000(n))
    return " ".join(parts)


def native(n: int) -> str:
    """고유어 수(관형형). 1 → "한", 20 → "스무", 25 → "스물다섯". 100 이상은 한자어."""
    if not 0 < n < 100:
        return sino(n)
    tens, ones = divmod(n, 10)
    if tens == 2 and ones == 0:
        return "스무"
    return _NATIVE_TENS[tens] + _NATIVE_ONES[ones]


def _num(s: str) -> int:
    return int(s.replace(",", ""))


_TIME = re.compile(r"(\d{1,2})시(?:\s*(\d{1,2})분|(\s*반))?")
_COUNT = re.compile(r"(\d[\d,]*)\s*(" + "|".join(_NATIVE_COUNTERS) + r")")
_NUMBER = re.compile(r"\d[\d,]*")


def _time(m: re.Match[str]) -> str:
    hour = native(int(m.group(1))) + " 시"
    if m.group(2):
        return f"{hour} {sino(int(m.group(2)))} 분"
    return hour + (" 반" if m.group(3) else "")


def to_spoken(text: str) -> str:
    text = _TIME.sub(_time, text)
    text = _COUNT.sub(lambda m: f"{native(_num(m.group(1)))} {m.group(2)}", text)
    text = re.sub(r"(\d[\d,]*)\s*원", lambda m: f"{sino(_num(m.group(1)))} 원", text)
    text = re.sub(r"(\d[\d,]*)\s*분", lambda m: f"{sino(_num(m.group(1)))} 분", text)
    return _NUMBER.sub(lambda m: sino(_num(m.group(0))), text)
