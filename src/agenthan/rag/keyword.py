"""키워드 검색: Kiwi 형태소 분석(메뉴명·별칭 사용자 사전) + BM25(bm25s).

- 문서 = 메뉴 1개(이름·원어명·별칭·설명·태그·묶음 이름).
- 낱말 = Kiwi 명사·외국어 + 그 낱말의 두 글자 조각.
  사용자 사전 때문에 '치즈케이크'가 한 낱말이 되어도 '치즈'로 물으면 조각('치즈')으로 찾는다.
  (오타는 여기가 아니라 knowledge.py의 '오타 허용 일치' 단계에서 잡는다)
- 근거 기준: 질문의 뜻 있는 말 중 MIN_COVERAGE 이상이 그 메뉴에 나와야 결과에 넣는다.
"""

from __future__ import annotations

import re
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


class KeywordIndex:
    def __init__(self, docs: dict[str, str], dictionary: frozenset[str]) -> None:
        """docs: {메뉴 id: 검색할 글}. dictionary: Kiwi 사용자 사전 낱말."""
        self.kiwi = _kiwi(dictionary)
        self.stop = {key(w) for w in rules.STOPWORDS}
        self.ids = list(docs)
        self.doc_tokens = [self.tokens(docs[i]) for i in self.ids]
        self.doc_sets = [set(t) for t in self.doc_tokens]
        self.bm25 = bm25s.BM25()
        self.bm25.index([t or ["<빈문서>"] for t in self.doc_tokens], show_progress=False)

    def words(self, text: str) -> list[str]:
        """뜻 있는 낱말(명사·외국어)만. 불용어는 뺀다."""
        out = []
        for tok in self.kiwi.tokenize(normalize(text)):
            if tok.tag in rules.KEYWORD_TAGS or (tok.tag.startswith("NN") and len(tok.form) > 1):
                w = key(tok.form)
                if w and w not in self.stop and (len(w) > 1 or not _HANGUL.search(w)):
                    out.append(w)
        return out

    def tokens(self, text: str) -> list[str]:
        """BM25 낱말 = 뜻 있는 낱말 + 두 글자 조각."""
        out = []
        for w in self.words(text):
            out.append(w)
            out += _bigrams(w)
        return out

    def _covered(self, word: str, doc: set[str]) -> bool:
        if word in doc:
            return True
        grams = _bigrams(word)  # '마카롱'의 '마카'만 겹치는 정도(1/2)로는 근거로 치지 않는다
        return bool(grams) and sum(g in doc for g in grams) / len(grams) >= 0.67

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """→ [(메뉴 id, 근거 비율 0~1)] 점수 높은 순. 근거 기준 미달이면 넣지 않는다."""
        words = self.words(query)
        if not words:
            return []
        q_tokens = self.tokens(query)
        scores = self.bm25.get_scores(q_tokens)
        found = []
        for idx, doc_id in enumerate(self.ids):
            if scores[idx] <= 0:
                continue
            coverage = sum(self._covered(w, self.doc_sets[idx]) for w in words) / len(words)
            if coverage >= rules.MIN_COVERAGE:
                found.append((doc_id, coverage, float(scores[idx])))
        found.sort(key=lambda t: (t[1], t[2]), reverse=True)
        return [(doc_id, cov) for doc_id, cov, _ in found[:k]]
