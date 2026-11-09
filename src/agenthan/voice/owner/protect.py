"""자유 문장 번역 보호 (4단계 spec "번역", 리서치 TR Q3). 번역 전에 가리고, 번역 뒤 확인하고 채운다.

    masked = mask("메도빅은 초 2개 넣어 주세요", names={"메도빅": "Медовик"}, terms=RU.terms)
    masked.text   # "[1] [2] 2개 넣어 주세요"  ← 번역기에는 이것만 간다
    restore("[1] [2] 2 штуки", masked, numerals=RU.numerals)  # "Медовик свечи 2 штуки"

- 가리는 것: 가게 메뉴명·별칭(→ 사장님 원어 이름, 비면 한국어)과 사전의 헷갈리는 재료 낱말
  (`lexicon.Lexicon.terms`). 자리표시자는 `[1]`, `[2]` … (2026-11-10 Papago ko→ru 실측:
  `[1]`은 그대로 나오고, `⟦1⟧`은 `⟦1 ⟧`, `{{1}}`은 `{{1}`로 깨졌다)
- 낱말 경계: 앞 글자가 한글이면 낱말 중간으로 보고 안 가린다("양파"의 "파"). 재료 낱말은 뒤도 본다:
  끝·한글 아닌 글자·조사("고수는", "밤이")·자주 붙는 말("빼", "없이", "많이" …)만.
  "파이"·"초코"·"밤에"(시간)는 안 가린다
- 가린 낱말 바로 뒤 격조사(은/는/이/가/을/를)는 뗀다. 남겨 두면 Papago가 조사를 낱말로 옮긴다
  ("[1]은 빼 주세요" → "Снимите серебро", 은 = 銀)
- 가린 낱말 바로 뒤 "(이)랑"은 "하고"로 바꿔 보낸다. "[1]이랑 [2]"는 Papago가 자리표시자 하나를
  지웠고(티켓 04 실측 2/2), "[1]하고 [2]"는 둘 다 남았다(티켓 12 재확인 3/3). 원문(`source`)은 그대로
- 확인(`restore`): 자리표시자가 하나라도 사라지거나 늘거나 번호가 틀리면 버린다(None).
  원문 숫자(아라비아 숫자)가 번역문에 숫자나 사전의 수사(`numerals`, "2" → "два·две…")로 없거나,
  번역문에 원문에 없는 숫자가 있으면 버린다. 한글 수사("두 개")는 아직 안 본다
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agenthan.voice.hangul import has_batchim

PLACEHOLDER = re.compile(r"\[(\d+)\]")
_NUMBER = re.compile(r"\d+")
# 받침 있는 낱말 / 없는 낱말 뒤 격조사 (뗄 것)
_CASE_AFTER_BATCHIM = ("은", "이", "을")
_CASE_AFTER_VOWEL = ("는", "가", "를")
# 재료 낱말 뒤에 와도 되는 말 (격조사 말고). 이 밖의 한글이 바로 붙으면 다른 낱말로 본다
_TERM_FOLLOWERS = ("과", "와", "이랑", "랑", "하고", "도", "만", "좀", "빼", "없", "넣", "많", "적",
                   "추가", "대신", "말고", "알레르기", "알러지", "꽂", "안")


@dataclass(frozen=True)
class Masked:
    text: str  # 번역기에 보낼 글
    fills: tuple[str, ...]  # `[1]`, `[2]` … 자리에 넣을 사장님 언어 글
    source: str  # 가리기 전 원문


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def _case_particle(word: str, rest: str) -> int:
    """`rest`가 `word`에 맞는 격조사 하나로 시작하고 그 뒤가 낱말 끝이면 조사 길이, 아니면 0."""
    for particle in (_CASE_AFTER_BATCHIM if has_batchim(word) else _CASE_AFTER_VOWEL):
        if rest.startswith(particle) and not _is_hangul(rest[len(particle):][:1]):
            return len(particle)
    return 0


def _rang(word: str, rest: str) -> int:
    """`rest`가 `word`에 맞는 "(이)랑" 하나로 시작하고 그 뒤가 낱말 끝이면 그 길이, 아니면 0."""
    joiner = "이랑" if has_batchim(word) else "랑"
    if rest.startswith(joiner) and not _is_hangul(rest[len(joiner):][:1]):
        return len(joiner)
    return 0


def _term_ends_here(word: str, rest: str) -> bool:
    if not rest or not _is_hangul(rest[0]):
        return True
    return bool(_case_particle(word, rest)) or rest.startswith(_TERM_FOLLOWERS)


def mask(text: str, *, names: Mapping[str, str], terms: Mapping[str, str]) -> Masked:
    """메뉴명(`names`: 한국어 → 원어)과 재료 낱말(`terms`)을 자리표시자로 바꾼다. 긴 말이 먼저다."""
    words = sorted(
        [(ko, owner, False) for ko, owner in names.items() if ko]
        + [(ko, owner, True) for ko, owner in terms.items() if ko],
        key=lambda w: -len(w[0]),
    )
    out: list[str] = []
    fills: list[str] = []
    i = 0
    while i < len(text):
        found = None
        if i == 0 or not _is_hangul(text[i - 1]):
            for ko, owner, is_term in words:
                if text.startswith(ko, i):
                    rest = text[i + len(ko):]
                    if not is_term or _term_ends_here(ko, rest):
                        found = (ko, owner)
                        break
        if found is None:
            out.append(text[i])
            i += 1
            continue
        ko, owner = found
        fills.append(owner)
        out.append(f"[{len(fills)}]")
        i += len(ko)
        i += _case_particle(ko, text[i:])
        if joiner := _rang(ko, text[i:]):
            out.append("하고")
            i += joiner
    return Masked(text="".join(out), fills=tuple(fills), source=text)


def restore(
    translated: str, masked: Masked, *, numerals: Mapping[int, tuple[str, ...]] = {}  # noqa: B006
) -> str | None:
    """번역문의 자리표시자를 채운다. 자리표시자·숫자가 원문과 안 맞으면 None(번역을 버린다)."""
    found = sorted(int(n) for n in PLACEHOLDER.findall(translated))
    if found != list(range(1, len(masked.fills) + 1)):
        return None
    without = PLACEHOLDER.sub(" ", translated)
    if not _numbers_kept(masked.source, without, numerals):
        return None

    def fill(m: re.Match[str]) -> str:
        value = masked.fills[int(m.group(1)) - 1]
        before = translated[m.start() - 1:m.start()]
        after = translated[m.end():m.end() + 1]
        # 붙어 나온 자리표시자(`[1][2]`)는 뒤 낱말 앞에 빈칸 하나("Блины лосось")
        return (" " if before.isalnum() else "") + value + (
            " " if after.isalnum() or after == "[" else "")

    return PLACEHOLDER.sub(fill, translated)


def _numbers_kept(source: str, translated: str, numerals: Mapping[int, tuple[str, ...]]) -> bool:
    wanted = set(_NUMBER.findall(source))
    got = set(_NUMBER.findall(translated))
    if not got <= wanted:
        return False
    words = set(re.findall(r"\w+", translated.lower()))
    return all(n in got or words & set(numerals.get(int(n), ())) for n in wanted)
