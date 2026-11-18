"""대사 → TTS 문장 (spec TTS). 문장 단위, `limit`자 이하.

첫 소리를 앞당기려고 자른다.
CLOVA Voice 첫 바이트는 글자 수에 비례한다(10자 0.37초, 120자 0.72초).
문장 끝(. ? !) 뒤 공백에서 먼저 자른다.
그래도 길면 쉼표 뒤, 그다음 공백, 마지막으로 글자 수로 자른다.
"""

from __future__ import annotations

import re

TTS_LIMIT = 150

_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_COMMA = re.compile(r"(?<=,)\s+")


def split_sentences(text: str, limit: int = TTS_LIMIT) -> list[str]:
    out: list[str] = []
    for sentence in _SENTENCE_END.split(text.strip()):
        out += _fit(sentence.strip(), limit)
    return [s for s in out if s]


def _fit(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    for sep in (_COMMA, re.compile(r"\s+")):
        parts = sep.split(text)
        if len(parts) > 1:
            return _pack(parts, limit)
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _pack(parts: list[str], limit: int) -> list[str]:
    """조각을 `limit`을 넘지 않게 앞에서부터 이어 붙인다. 한 조각이 넘으면 다시 자른다."""
    out: list[str] = []
    cur = ""
    for part in parts:
        joined = f"{cur} {part}" if cur else part
        if len(joined) <= limit:
            cur = joined
            continue
        if cur:
            out.append(cur)
        if len(part) <= limit:
            cur = part
        else:
            pieces = _fit(part, limit)
            out += pieces[:-1]
            cur = pieces[-1]
    if cur:
        out.append(cur)
    return out
