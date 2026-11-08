"""옵션 말 → `MenuOption` 찾기. 메뉴처럼 코드가 최종 판정한다 (spec D5).

NLU는 손님이 말한 옵션 조각(`Act.option_text`)만 넘긴다. 여기서 그 메뉴가 받을 수 있는
옵션(필수 선택지 + 선택 옵션) 가운데 어느 것인지 고른다. 후보가 그 메뉴 옵션뿐이라(보통 1~3개)
퍼지로 찾아도 엉뚱한 옵션이 걸릴 일이 드물다.

옵션 이름은 "대상어 + 동작"으로 본다 (티켓 10, `research/s2-stt-misrecognition.md` Q3).
- "~빼기": 대상어("사워크림") 뒤에 빼는 말(빼·없이·말고·제외·안 넣·넣지 말)이 와야 한다.
  대상어와 빼는 말 사이의 조사 하나(은·는·을·를…)와 짧은 말("좀")은 건너뛴다.
  "빼지 마세요"처럼 빼는 말을 다시 부정하면 받지 않는다. 대상어만 있으면 받지 않는다
- "~변경": 대상어("디카페인", "홀케이크")만 말해도 받는다. 뒤에 빼는 말("디카페인 말고")이
  오면 받지 않는다
- 그 밖("샷 추가", "차갑게"): 이름 그대로, 또는 `_SYNONYMS`의 다른 말("아이스" → 차갑게)
- 대상어가 3음절 이상이면 STT 오인식("샤워크림")도 받는다: 정규화한 발화에서 대상어 길이의
  창을 밀며 혼동 가중 유사도(`hangul.confusable_distance`)가 `FUZZY_AT` 이상인 곳을 찾는다.
  2음절 이하("샷")는 한 자모만 달라도 다른 말이 되기 쉬워 정확히 같을 때만 받는다
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from agenthan.core.contracts import MenuOption
from agenthan.voice.hangul import confusable_distance

# 문장부호·따옴표·말줄임표는 STT·NLU 조각에 섞여 온다 (07 실측: 끝 ".", 리서치 2-4)
_NOISE = re.compile(r"[\s.,!?~'\"…·“”‘’]+")

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "차갑게": ("차가운", "차게", "아이스", "시원하게", "시원한"),
    "따뜻하게": ("따뜻한", "따듯하게", "따듯한", "뜨겁게", "뜨거운", "따뜻이", "핫"),
}

# 대상어 퍼지 창 경계. 실측 "샤워크림"↔"사워크림"이 0.97, "디까페인"↔"디카페인"이 0.98이다.
# 메뉴 매처 수락 경계(0.85)와 같게 둔다
FUZZY_AT = 0.85
FUZZY_MIN_LEN = 3  # 이보다 짧은 대상어는 정확 일치만

_PARTICLE = re.compile(r"(?:은|는|을|를|이|가|도|만)")
# 대상어(+조사) 뒤 두 글자 안에서 시작하는 빼는 말 ("사워크림은 좀 빼 주세요")
_REMOVE_CUE = re.compile(r".{0,2}?(?:빼|없이|말고|제외|안넣|넣지말)")
# 빼는 말 바로 뒤의 부정 ("빼지 마세요", "빼지 말고", "빼면 안 돼요")
_NEGATED = re.compile(r"(?:지마|지말|지않|면안)")


def _normalize(text: str) -> str:
    return _NOISE.sub("", text)


def _change_target(name: str) -> str:
    """"디카페인 변경" → "디카페인". 변경 옵션이 아니면 ""."""
    base = _normalize(name)
    return base[: -len("변경")] if base.endswith("변경") and len(base) > 2 else ""


def _removal_target(name: str) -> str:
    """"사워크림 빼기" → "사워크림". 빼기 옵션이 아니면 ""."""
    base = _normalize(name)
    return base[: -len("빼기")] if base.endswith("빼기") and len(base) > 2 else ""


def option_target(name: str) -> str:
    """옵션 이름의 대상어. "사워크림 빼기" → "사워크림", "디카페인 변경" → "디카페인",
    "샷 추가" → "샷". 대상어로 나눌 수 없는 이름("차갑게")은 "". STT 부스팅 단어에 쓴다."""
    if target := _removal_target(name) or _change_target(name):
        return target
    base = _normalize(name)
    return base[: -len("추가")] if base.endswith("추가") and len(base) > 2 else ""


def option_keys(name: str) -> tuple[str, ...]:
    """옵션 이름 → 정확히 찾을 말(정규화한 것). 긴 말부터.

    "~빼기"는 대상어와 빼는 말을 따로 찾으므로(`_removal_follows`) 여기에는 이름만 있다.
    """
    keys = {_normalize(name), *(_normalize(s) for s in _SYNONYMS.get(name, ()))}
    if target := _change_target(name):
        keys.add(target)
    return tuple(sorted(keys, key=len, reverse=True))


def _target_spans(said: str, target: str) -> Iterator[tuple[int, int]]:
    """발화에서 대상어가 나온 구간 (시작, 끝). 앞에서부터."""
    n = len(target)
    if n < FUZZY_MIN_LEN:
        start = said.find(target)
        while start >= 0:
            yield start, start + n
            start = said.find(target, start + 1)
        return
    for start in range(len(said) - n + 1):
        window = said[start : start + n]
        if window == target or 1 - confusable_distance(window, target) / n >= FUZZY_AT:
            yield start, start + n


def _removal_follows(said: str, end: int) -> bool:
    """대상어 끝(`end`) 뒤에 빼는 말이 오고, 그 말을 다시 부정하지 않았나."""
    rest = said[end:]
    if (particle := _PARTICLE.match(rest)) is not None:
        rest = rest[particle.end() :]
    cue = _REMOVE_CUE.match(rest)
    return cue is not None and _NEGATED.match(rest, cue.end()) is None


def _position(option: MenuOption, said: str) -> int | None:
    """발화에서 이 옵션을 말한 자리. 말하지 않았으면 None."""
    if target := _removal_target(option.name):
        starts = (s for s, e in _target_spans(said, target) if _removal_follows(said, e))
        return next(starts, None)
    found = [said.find(k) for k in option_keys(option.name) if k and k in said]
    if target := _change_target(option.name):
        # 대상어("디카페인")는 오인식도 받는다. 뒤에 빼는 말이 오면 고른 것이 아니다
        spans = list(_target_spans(said, target))
        if any(_removal_follows(said, end) for _, end in spans):
            return None
        found += [start for start, _ in spans]
    return min(found, default=None)


def find_options(candidates: Iterable[MenuOption], text: str) -> list[MenuOption]:
    """`text`에 나온 옵션을 말한 순서대로. 하나도 없으면 빈 목록."""
    said = _normalize(text)
    if not said:
        return []
    found = [(at, option) for option in candidates if (at := _position(option, said)) is not None]
    found.sort(key=lambda pair: pair[0])
    return [option for _, option in found]
