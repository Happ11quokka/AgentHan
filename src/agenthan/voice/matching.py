"""메뉴 매처. 엔진은 `MenuMatcher.match()`의 판정만 본다 (spec §6, D5, 리서치 R-Q3).

순서:
1. 정규화: 공백·문장부호를 빼고, 끝에 붙은 조사·수량 표현("~요", "~하나", "~두 개", "~랑")을 뗀다
2. 이름·별칭과 같으면 1.0
3. 자모 유사도 `1 - jamo_levenshtein / max_len`. 발화가 더 길면 메뉴명 길이 구간으로 부분 일치
4. `menu_guess`(NLU 추정)가 같은 메뉴를 가리키면 점수를 올린다. 보조 신호일 뿐이다
5. 판정: accept / confirm(후보 1개) / choose(1·2등이 가까움) / reject. 품절은 sold_out으로 따로

수락에는 신호 두 개가 필요하다: 이름·별칭과 정확히 같거나, 자모 유사도가 높고 `menu_guess`도
같은 메뉴를 가리킬 때. 유사도 하나만 높으면 되묻는다(잘못 수락하는 비용이 더 크다, R-Q3).

결과 타입은 voice 내부 타입이다. 계약의 `MenuHit.matched_by`에 "fuzzy"가 없어서다
(티켓 08에서 제안).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agenthan.core.contracts import MenuItem
from agenthan.voice.hangul import has_batchim, jamo_levenshtein

MatchDecision = Literal["accept", "confirm", "choose", "reject", "sold_out"]

# 판정 경계값 (시작값. 5단계에서 오류 셋으로 조정한다)
ACCEPT_AT = 0.85  # 이 점수 이상이고 2등과 MARGIN 이상 차이 나면 수락
REJECT_BELOW = 0.6  # 이 점수 미만이면 없는 메뉴
MARGIN = 0.1  # 1·2등 차이가 이보다 작으면 둘 중 하나를 묻는다
GUESS_BONUS = 0.1  # menu_guess가 같은 메뉴를 가리킬 때 더하는 점수

# ---------------------------------------------------------------- 정규화

_NOISE = re.compile(r"[\s.,!?~'\"…·]+")

# 끝에서 한 번에 하나씩 뗀다. 위에서부터 먼저 본다.
_TAIL_POLITE = re.compile(r"(?:주세요|주실래요|줄래요|할게요|요)$")
_TAIL_QTY = re.compile(
    r"(?:\d+(?:개|잔)?|하나|둘|셋|넷|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)(?:개|잔))$"
)
_TAIL_PARTICLE = re.compile(r"(?:랑|하고|도)$")
# "이요"·"이랑"의 "이"는 앞 글자에 받침이 있을 때만 조사다 (블린이요 / 파이요)
_TAIL_I = re.compile(r"이(?:요|랑)$")


def _clean(text: str) -> str:
    return _NOISE.sub("", text).lower()


def _strip_once(q: str) -> str:
    m = _TAIL_I.search(q)
    if m and m.start() > 0 and has_batchim(q[m.start() - 1]):
        return q[: m.start()]
    for pattern in (_TAIL_POLITE, _TAIL_QTY, _TAIL_PARTICLE):
        m = pattern.search(q)
        if m and m.start() > 0:
            return q[: m.start()]
    return q


def query_forms(text: str) -> tuple[str, ...]:
    """발화 → 꼬리를 하나씩 뗀 형태들. 덜 뗀 것부터. 예: "메도빅하나요" → (…, "메도빅")."""
    forms: list[str] = []
    q = _clean(text)
    while q and q not in forms:
        forms.append(q)
        q = _strip_once(q)
    return tuple(forms)


def normalize(text: str) -> str:
    """꼬리를 다 뗀 핵심 메뉴 표현."""
    forms = query_forms(text)
    return forms[-1] if forms else ""


# ---------------------------------------------------------------- 유사도


def _ratio(a: str, b: str) -> float:
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return max(0.0, 1 - jamo_levenshtein(a, b) / longest)


def similarity(query: str, name: str) -> float:
    """0~1. 발화가 메뉴명보다 길면 메뉴명 길이의 구간들과도 비교한다(부분 일치).

    두 글자 메뉴명은 구간이 정확히 같을 때만 부분 일치로 친다.
    "블루베리"의 "블루"가 "블린"과 가깝게 나오는 식의 잡음을 막는다.
    """
    if not query or not name:
        return 0.0
    if query == name:
        return 1.0
    score = _ratio(query, name)
    n = len(name)
    if len(query) > n >= 2:
        windows = (query[i : i + n] for i in range(len(query) - n + 1))
        if n == 2:
            score = max(score, 1.0 if name in query else 0.0)
        else:
            score = max(score, *(_ratio(w, name) for w in windows))
    return score


# ---------------------------------------------------------------- 매처


@dataclass(frozen=True)
class MatchResult:
    decision: MatchDecision
    item: MenuItem | None = None  # accept·confirm·sold_out일 때
    candidates: tuple[MenuItem, ...] = ()  # choose일 때 후보들 (점수 순)
    score: float = 0.0  # 1등 점수


class MenuMatcher:
    def __init__(
        self,
        menu: list[MenuItem],
        *,
        accept_at: float = ACCEPT_AT,
        reject_below: float = REJECT_BELOW,
        margin: float = MARGIN,
        guess_bonus: float = GUESS_BONUS,
        fuzzy_accept_needs_guess: bool = True,
    ) -> None:
        self._menu = list(menu)
        self._names = {m.id: {_clean(n) for n in (m.name_ko, *m.aliases)} - {""} for m in menu}
        self._accept_at = accept_at
        self._reject_below = reject_below
        self._margin = margin
        self._guess_bonus = guess_bonus
        self._fuzzy_accept_needs_guess = fuzzy_accept_needs_guess

    # ---------------------------------------------------------------- 판정

    def match(self, menu_text: str, menu_guess: str = "") -> MatchResult:
        ranked = self._rank(menu_text or menu_guess, menu_guess)
        if not ranked or ranked[0][0] < self._reject_below:
            return MatchResult("reject", score=ranked[0][0] if ranked else 0.0)

        top, item, corroborated = ranked[0]
        close = [m for s, m, _ in ranked[1:2] if s >= self._reject_below and top - s < self._margin]
        if close:
            return self._choose((item, *close), top)
        if top >= self._accept_at and (corroborated or not self._fuzzy_accept_needs_guess):
            return self._single("accept", item, top)
        return self._single("confirm", item, top)

    def _rank(self, text: str, guess: str) -> list[tuple[float, MenuItem, bool]]:
        """(점수, 메뉴, 신호가 둘인지) 목록. 점수 높은 순, 같은 점수는 메뉴 순서."""
        forms = query_forms(text)
        if not forms:
            return []
        guessed = set(query_forms(guess)) if guess else set()
        scored = []
        for item in self._menu:
            names = self._names[item.id]
            score = max((similarity(q, n) for q in forms for n in names), default=0.0)
            exact = score == 1.0
            agreed = bool(guessed & names)
            if agreed:
                score = min(1.0, score + self._guess_bonus)
            scored.append((score, item, exact or agreed))
        return sorted(scored, key=lambda row: row[0], reverse=True)

    @staticmethod
    def _single(decision: MatchDecision, item: MenuItem, score: float) -> MatchResult:
        if item.sold_out:
            return MatchResult("sold_out", item, score=score)
        return MatchResult(decision, item, score=score)

    @staticmethod
    def _choose(candidates: tuple[MenuItem, ...], score: float) -> MatchResult:
        """1·2등이 가까울 때. 품절 후보는 빼고, 하나만 남으면 그것만 확인한다."""
        available = tuple(m for m in candidates if not m.sold_out)
        if not available:
            return MatchResult("sold_out", candidates[0], score=score)
        if len(available) == 1:
            return MatchResult("confirm", available[0], score=score)
        return MatchResult("choose", candidates=available, score=score)

    # ---------------------------------------------------------------- 안내용

    def available(self) -> list[MenuItem]:
        """품절이 아닌 메뉴. 메뉴 순서 그대로."""
        return [m for m in self._menu if not m.sold_out]

    def alternatives(self, item: MenuItem, k: int = 2) -> list[MenuItem]:
        """품절 메뉴 대신 권할 메뉴. 같은 카테고리를 먼저."""
        others = [m for m in self.available() if m.id != item.id]
        others.sort(key=lambda m: m.category != item.category)  # 안정 정렬
        return others[:k]
