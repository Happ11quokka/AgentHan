"""키워드 검색: Kiwi 형태소 분석(메뉴명·별칭 사용자 사전) + BM25(bm25s).

- 문서 = 메뉴 1개(이름·원어명·별칭·설명·옵션·태그·묶음 이름).
- 낱말 = Kiwi 명사·외국어(+ rules.ONE_CHAR_WORDS의 한 글자 말) + 그 낱말의 두 글자 조각.
  '치즈케이크'가 한 낱말이어도 '치즈'로 물으면 조각('치즈')으로 찾는다.
- 일반어(rules.GENERIC_WORDS: 케이크·에이드·음료…)는 점수를 낮게 준다.
  구별하는 말이 안 맞으면 통과 못 한다.
- 붙여 쓴 이름("자몽에이드", "치즈케이크")은 일반어를 떼어 내고, 앞부분(자몽·치즈)은
  메뉴 이름·별칭에서만 찾는다. 설명에 '치즈'가 있다고 '치즈케이크'가 되지는 않는다.
- 근거 기준: 질문의 구별하는 말 중 MIN_COVERAGE 이상이 그 메뉴에 나와야 결과에 넣는다.
(오타는 여기가 아니라 knowledge.py의 '오타 허용 일치' 단계에서 잡는다)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import bm25s
from kiwipiepy import Kiwi

from agenthan.rag import rules
from agenthan.rag.normalize import key, normalize

_HANGUL = re.compile(r"[가-힣]")


@lru_cache(maxsize=4)
def _kiwi(user_words: frozenset[str]) -> Kiwi:
    """Kiwi는 처음 띄울 때 몇 초 걸려서, 같은 사용자 사전이면 한 번만 만든다."""
    kiwi = Kiwi()
    for w in sorted(user_words):
        kiwi.add_user_word(w, "NNP", 0.0)
    return kiwi


def user_words(texts) -> frozenset[str]:
    """메뉴명·별칭 중 한글로만 된 말(공백 없앰) → Kiwi 사용자 사전.
    data의 kiwi_user_dict.txt와 같은 규칙."""
    out = set()
    for t in texts:
        k = key(t)
        if k and re.fullmatch(r"[가-힣]+", k) and len(k) >= 2:
            out.add(k)
    return frozenset(out)


def _bigrams(word: str) -> list[str]:
    if len(word) < 3:
        return []
    return [word[i : i + 2] for i in range(len(word) - 1)]


@dataclass(frozen=True)
class Analysis:
    """질문 분석 결과."""

    words: tuple[str, ...]  # 뜻 있는 낱말 전부 (일반어 포함, 불용어 제외)
    name_only: frozenset[str]  # 붙여 쓴 이름의 앞부분 → 메뉴 이름·별칭에서만 찾는다

    @property
    def distinct(self) -> list[str]:
        generic = {key(g) for g in rules.GENERIC_WORDS}
        return [w for w in self.words if w not in generic]

    @property
    def generic(self) -> list[str]:
        generic = {key(g) for g in rules.GENERIC_WORDS}
        return [w for w in self.words if w in generic]


class KeywordIndex:
    def __init__(
        self, docs: dict[str, str], dictionary: frozenset[str], names: dict[str, str] | None = None
    ) -> None:
        """docs: {메뉴 id: 검색할 글}. names: {메뉴 id: 이름·별칭만}.
        dictionary: Kiwi 사용자 사전."""
        self.kiwi = _kiwi(dictionary)
        self.stop = {key(w) for w in rules.STOPWORDS}
        self.one_char = {key(w) for w in rules.ONE_CHAR_WORDS}
        self.generic_attached = sorted(
            {key(g) for g in rules.GENERIC_WORDS if len(key(g)) >= 2 and _HANGUL.search(key(g))},
            key=len,
            reverse=True,
        )
        self.ids = list(docs)
        self.doc_sets = [set(self.tokens(docs[i])) for i in self.ids]
        names = names or docs
        self.name_sets = [set(self.tokens(names.get(i, ""))) for i in self.ids]
        self.bm25 = bm25s.BM25()
        self.bm25.index([sorted(t) or ["<빈문서>"] for t in self.doc_sets], show_progress=False)
        self.vocab = {t for s in self.doc_sets for t in s}  # 메뉴 데이터에 나오는 낱말 전부

    # ---------------------------------------------------------------- 분석
    def _split_attached(self, text: str) -> tuple[str, set[str]]:
        """'자몽에이드' → '자몽 에이드'. 떼어 낸 앞부분('자몽')을 돌려준다."""
        prefixes: set[str] = set()
        out = []
        for w in normalize(text).split():
            for g in self.generic_attached:
                i = w.find(g, 1)
                if i > 0 and _HANGUL.match(w[i - 1]):
                    prefixes.add(w[:i])
                    w = w[:i] + " " + w[i:]
                    break
            out.append(w)
        return " ".join(out), prefixes

    def analyze(self, text: str) -> Analysis:
        split, prefixes = self._split_attached(text)
        words = []
        for tok in self.kiwi.tokenize(split):
            w = key(tok.form)
            if not w or w in self.stop:
                continue
            if len(w) == 1 and _HANGUL.search(w):
                if w in self.one_char and tok.tag.startswith("N"):
                    words.append(w)
                continue
            if tok.tag in rules.KEYWORD_TAGS or tok.tag.startswith("NN"):
                words.append(w)
        name_only = frozenset(w for w in words if any(w in p for p in prefixes))
        return Analysis(tuple(words), name_only)

    def words(self, text: str) -> list[str]:
        """뜻 있는 낱말(명사·외국어·허용된 한 글자 말). 불용어는 뺀다."""
        return list(self.analyze(text).words)

    def tokens(self, text: str) -> list[str]:
        """BM25 낱말 = 뜻 있는 낱말 + 두 글자 조각."""
        out = []
        for w in self.words(text):
            out.append(w)
            out += _bigrams(w)
        return out

    # ---------------------------------------------------------------- 검색
    @staticmethod
    def _covered(word: str, doc: set[str]) -> bool:
        if word in doc:
            return True
        grams = _bigrams(word)  # '마카롱'의 '마카'만 겹치는 정도(1/2)로는 근거로 치지 않는다
        return bool(grams) and sum(g in doc for g in grams) / len(grams) >= 0.67

    def coverage(self, doc_id: str, words: list[str], name_only=frozenset()) -> float:
        """그 메뉴가 words를 얼마나 덮는지 (0~1)."""
        if not words or doc_id not in self.ids:
            return 0.0
        idx = self.ids.index(doc_id)
        hit = sum(
            self._covered(w, self.name_sets[idx] if w in name_only else self.doc_sets[idx])
            for w in words
        )
        return hit / len(words)

    def search(
        self, query: str = "", k: int = 10, analysis: Analysis | None = None
    ) -> list[tuple[str, float]]:
        """→ [(메뉴 id, 구별하는 말 근거 비율 0~1)] 점수 높은 순. 기준 미달이면 넣지 않는다.
        구별하는 말이 하나도 없으면(일반어만 있으면) 빈 목록."""
        a = analysis or self.analyze(query)
        distinct, generic = a.distinct, a.generic
        if not distinct:
            return []
        q_tokens = [t for w in distinct + generic for t in [w, *_bigrams(w)] if t in self.vocab]
        scores = self.bm25.get_scores(q_tokens) if q_tokens else [0.0] * len(self.ids)
        found = []
        for idx, doc_id in enumerate(self.ids):
            cov = sum(
                self._covered(w, self.name_sets[idx] if w in a.name_only else self.doc_sets[idx])
                for w in distinct
            ) / len(distinct)
            if cov < rules.MIN_COVERAGE:
                continue
            gen = (
                sum(self._covered(w, self.doc_sets[idx]) for w in generic) / len(generic)
                if generic
                else 0.0
            )
            found.append((doc_id, cov, cov + rules.GENERIC_WEIGHT * gen, float(scores[idx])))
        found.sort(key=lambda t: (t[2], t[3]), reverse=True)
        return [(doc_id, cov) for doc_id, cov, _, _ in found[:k]]
