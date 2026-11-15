"""StoreKnowledge 진짜 구현 (Part B).

search_menu 순서 (자세한 키워드·기준값은 rules.py):
  0. 전처리: 표기 정규화 → 말 바꾸기면 정정 뒤쪽만 → 여러 메뉴면 조각으로 나누기
  1. 정확 일치: 메뉴명·원어명·별칭 → 그 메뉴만
  2. 묶음 일치: 묶음 별칭 → 묶음 전체(대표 메뉴 1등). '초코' 같은 구분 말이 있으면 그 메뉴만,
     비교 질문('차이')이면 대표 메뉴만
     → 1·2로 찾았으면 여기서 끝 (빠르게)
  3. 빈 목록이 정답인 질문: 데이터에 없는 정보(견과류·비건…)·가게 정보(몇 시·주차…)
  4. 의도 규칙: 덜 단 것 / 단 것 / 한 판 / 선물 / 요즘 인기 / 인기 / 가격
  5. 태그·종류(category): "러시아 전통 케이크", "에이드 뭐 있어요?"
  6. 오타 허용 일치: "나폴래옹" → 나폴레옹
  7. 키워드(BM25)·벡터 검색 → RRF로 합치기. 근거 기준 미달이면 빈 목록

matched_by는 계약 값만 쓴다: 메뉴명·원어명 = exact, 별칭·묶음 별칭·오타 = alias,
규칙·태그·종류·BM25 = keyword, 벡터만 = vector, BM25와 벡터가 같이 찾은 것 = hybrid.
품절·옵션은 거르지 않고 MenuItem에 그대로 담는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from agenthan.core.contracts import MenuHit, MenuItem, Review
from agenthan.rag import rules
from agenthan.rag.keyword import KeywordIndex, user_words
from agenthan.rag.normalize import contains_any, drop_correction, key, normalize, split_items
from agenthan.rag.repository import DUMMY_DIR, JsonDirRepository, MenuRepository, StoreData
from agenthan.rag.vector import Embed, VectorIndex

_HANGUL = re.compile(r"[가-힣]")
_LETTER = r"a-z0-9Ѐ-ӿ"  # 라틴·숫자·키릴 글자
_MASK = "\x00"


@dataclass(frozen=True)
class _Term:
    key: str
    target: str  # 메뉴 id 또는 'family:묶음id'
    kind: str  # exact | alias | family
    pattern: re.Pattern


def _pattern(term_norm: str) -> re.Pattern | None:
    """정규화된 말 → 질문에서 찾을 정규식.
    한글은 글자 사이 공백을 허용하고, 외국어는 낱말 경계를 지킨다."""
    k = term_norm.replace(" ", "")
    if _HANGUL.search(k):
        if len(k) < 2:
            return None
        if len(k) == 2:  # '아아' 같은 두 글자는 붙어 있고 낱말 처음일 때만
            return re.compile(r"(?<![가-힣])" + re.escape(k))
        return re.compile(r"\s*".join(re.escape(c) for c in k))
    if len(k) < 3:
        return None
    body = r"\s*".join(re.escape(w) for w in term_norm.split())
    tail = r"(?:[Ѐ-ӿ]{0,2})?" if re.search(r"[Ѐ-ӿ]", k) else ""  # 러시아어 어미
    return re.compile(rf"(?<![{_LETTER}]){body}{tail}(?![{_LETTER}])")


class RagStoreKnowledge:
    """가게 지식 RAG. 기본은 fixtures/dummy_store, AGENTHAN_DATA_DIR로 실제 데이터(data/clean)."""

    def __init__(self, repository: MenuRepository, embed: Embed | None | bool = None) -> None:
        data: StoreData = repository.load()
        self.records = {r.item.id: r for r in data.menus}
        self.families = {f.id: f for f in data.families}
        self.store = data.store
        self._reviews = sorted(data.reviews, key=lambda r: r.created_at, reverse=True)
        self._terms = self._build_terms()
        self._fuzzy_terms = [t for t in self._terms if _HANGUL.search(t.key) and len(t.key) >= 3]
        self._family_words = self._build_family_words()
        docs = {i: self._doc_text(i) for i in self.records}
        dictionary = user_words(
            [r.item.name_ko for r in data.menus]
            + [a for r in data.menus for a in r.item.aliases]
            + [a for f in data.families for a in f.aliases]
        )
        self.keyword = KeywordIndex(docs, dictionary)
        self.vector = VectorIndex(docs, embed)

    # ---------------------------------------------------------------- 만들기
    @classmethod
    def from_dir(cls, path: str | Path | None = None, embed: Embed | None | bool = None):
        """path → 환경변수 AGENTHAN_DATA_DIR → 더미 순서. 벡터는 키가 있을 때만."""
        return cls(JsonDirRepository(path), embed)

    @classmethod
    def from_fixture(cls) -> RagStoreKnowledge:
        """테스트·데모용: 더미 가게, 벡터 끔(네트워크 없이 항상 같은 결과)."""
        return cls(JsonDirRepository(DUMMY_DIR), embed=False)

    def _build_terms(self) -> list[_Term]:
        seen: dict[str, str] = {}  # 같은 말이 여러 곳에 있으면 묶음이 이긴다(묶음을 먼저 넣음)
        terms: list[_Term] = []

        def add(text: str, target: str, kind: str) -> None:
            norm = normalize(text)
            k = norm.replace(" ", "")
            if not k or k in seen:  # 먼저 넣은 쪽(묶음 → 메뉴명 → 별칭)이 이긴다
                return
            pat = _pattern(norm)
            if pat is None:
                return
            seen[k] = target
            terms.append(_Term(k, target, kind, pat))

        for f in self.families.values():
            for a in (f.name_ko, *f.aliases):
                add(a, f"family:{f.id}", "family")
        for r in self.records.values():
            add(r.item.name_ko, r.item.id, "exact")
            if r.item.name_native:
                add(r.item.name_native, r.item.id, "exact")
            for a in r.item.aliases:
                add(a, r.item.id, "alias")
        return sorted(terms, key=lambda t: len(t.key), reverse=True)

    def _build_family_words(self) -> dict[str, dict[str, list[str]]]:
        """묶음 안에서 메뉴를 고르는 말. {묶음 id: {메뉴 id: ['초코', ...]}}"""
        out: dict[str, dict[str, list[str]]] = {}
        generic = sorted({key(w) for w in rules.FAMILY_GENERIC_WORDS}, key=len, reverse=True)
        for f in self.families.values():
            fam_keys = sorted({key(a) for a in (f.name_ko, *f.aliases)}, key=len, reverse=True)
            words: dict[str, set[str]] = {}
            for mid in f.members:
                r = self.records.get(mid)
                if not r:
                    continue
                chunks: set[str] = set()
                for text in (r.item.name_ko, r.item.name_native, *r.item.aliases):
                    k = key(text)
                    for remove in fam_keys + generic:
                        k = k.replace(remove, " ")
                    chunks |= {c for c in k.split() if c}
                words[mid] = chunks
            # 여러 메뉴에 같이 나오는 말은 구분에 못 쓴다
            out[f.id] = {
                mid: sorted(
                    c for c in cs if all(c not in o for m2, o in words.items() if m2 != mid)
                )
                for mid, cs in words.items()
            }
        return out

    def _doc_text(self, menu_id: str) -> str:
        r = self.records[menu_id]
        fam = self.families.get(r.family)
        parts = [r.item.name_ko, r.item.name_native, *r.item.aliases, r.item.description, *r.tags]
        if fam:
            parts.append(fam.name_ko)
        return " ".join(p for p in parts if p)

    # ---------------------------------------------------------------- 계약
    def get_menu(self, menu_id: str) -> MenuItem | None:
        r = self.records.get(menu_id)
        return r.item if r else None

    def list_menu(self) -> list[MenuItem]:
        return [r.item for r in self.records.values()]

    def recent_reviews(self, limit: int = 10, since: datetime | None = None) -> list[Review]:
        rows = self._reviews
        if since is not None:
            if since.tzinfo is None and rows and rows[0].created_at.tzinfo is not None:
                since = since.replace(tzinfo=rows[0].created_at.tzinfo)
            rows = [r for r in rows if r.created_at >= since]
        return rows[: max(limit, 0)]

    def search_menu(self, query: str, k: int = 5) -> list[MenuHit]:
        hits = self._search(query)
        return hits[: max(k, 0)]

    # ---------------------------------------------------------------- 검색
    def _search(self, query: str) -> list[MenuHit]:
        text = drop_correction(query)
        if not text:
            return []
        text_key = text.replace(" ", "")
        segments = split_items(text) or [text]

        # 1·2. 정확 일치·묶음 일치 (조각마다)
        per_segment: list[list[tuple[str, float, str]]] = []
        compare = contains_any(text_key, rules.COMPARE_WORDS)
        for seg in segments:
            per_segment.append(self._match_terms(seg, compare=compare))
        if any(per_segment):
            if len(segments) > 1:
                # 메뉴 이름이 없는 조각은 키워드 검색으로만 보충한다 ("메도빅 하나랑 오미자차")
                per_segment = [
                    found or self._keyword_vector(seg)[:1]
                    for seg, found in zip(segments, per_segment, strict=True)
                ]
                return self._ordered_hits(per_segment)
            return self._hits(per_segment[0])

        # 3. 메뉴 데이터로 답할 수 없는 질문
        if contains_any(text_key, rules.NO_INFO_WORDS) or contains_any(
            text_key, rules.STORE_INFO_WORDS
        ):
            return []

        # 4·5. 의도 규칙 → 태그·종류
        ruled = self._intent(text, text_key)
        if ruled is None:
            ruled = self._tags_and_categories(text_key, text)
        if ruled is not None:
            return self._hits(self._rank_rule_list(ruled, text))

        # 6. 오타 허용
        fuzzy = self._fuzzy(text)
        if fuzzy:
            return self._hits(fuzzy)

        # 7. 키워드 + 벡터
        return self._hits(self._keyword_vector(text))

    def _hits(self, found: list[tuple[str, float, str]]) -> list[MenuHit]:
        best: dict[str, tuple[float, str]] = {}
        order: list[str] = []
        for mid, score, how in found:
            if mid not in self.records:
                continue
            if mid not in best:
                order.append(mid)
                best[mid] = (score, how)
            elif score > best[mid][0]:
                best[mid] = (score, how)
        ranked = sorted(order, key=lambda m: best[m][0], reverse=True)  # 같은 점수는 찾은 순서 유지
        return [
            MenuHit(self.records[m].item, round(min(max(best[m][0], 0.0), 1.0), 4), best[m][1])
            for m in ranked
        ]

    def _ordered_hits(self, per_segment: list[list[tuple[str, float, str]]]) -> list[MenuHit]:
        """여러 메뉴 주문: 조각마다 첫 메뉴를 말한 순서대로 먼저, 묶음의 나머지 메뉴는 그 뒤에.
        계약상 점수는 내림차순이어야 해서, 앞 결과보다 높은 점수는 앞 점수로 맞춘다."""
        firsts = [seg[0] for seg in per_segment if seg]
        rest = [hit for seg in per_segment for hit in seg[1:]]
        hits = self._hits_in_order(firsts + rest)
        return hits

    def _hits_in_order(self, found: list[tuple[str, float, str]]) -> list[MenuHit]:
        out: list[MenuHit] = []
        seen: set[str] = set()
        ceiling = 1.0
        for mid, score, how in found:
            if mid in seen or mid not in self.records:
                continue
            seen.add(mid)
            ceiling = min(ceiling, score)
            out.append(MenuHit(self.records[mid].item, round(max(ceiling, 0.0), 4), how))
        return out

    # ---- 1·2
    def _match_terms(self, seg: str, compare: bool) -> list[tuple[str, float, str]]:
        hay = seg
        matches: list[tuple[int, _Term, str]] = []
        for term in self._terms:
            m = term.pattern.search(hay)
            if not m:
                continue
            matches.append((m.start(), term, hay[m.start() : m.end()]))
            # 찾은 자리는 지워서 더 짧은 말이 그 안에서 또 걸리지 않게 한다
            # ('초코 메도빅' 안의 '메도빅')
            hay = hay[: m.start()] + _MASK * (m.end() - m.start()) + hay[m.end() :]
        matches.sort(key=lambda t: t[0])
        rest_key = hay.replace(_MASK, " ").replace(" ", "")
        out: list[tuple[str, float, str]] = []
        for _, term, _matched in matches:
            if term.kind == "exact":
                out.append((term.target, 1.0, "exact"))
            elif term.kind == "alias":
                out.append((term.target, 0.95, "alias"))
            else:
                out += self._expand_family(term.target.split(":", 1)[1], rest_key, compare)
        return out

    def _expand_family(
        self, fid: str, rest_key: str, compare: bool
    ) -> list[tuple[str, float, str]]:
        f = self.families[fid]
        if compare:
            return [(f.representative, 0.95, "alias")]
        picked = [
            mid
            for mid, words in self._family_words.get(fid, {}).items()
            if any(self._word_in(w, rest_key) for w in words)
        ]
        if picked:
            return [(mid, 0.95, "alias") for mid in picked]
        members = [f.representative] + [m for m in f.members if m != f.representative]
        return [(members[0], 0.95, "alias")] + [(m, 0.9, "alias") for m in members[1:]]

    @staticmethod
    def _word_in(word: str, hay_key: str) -> bool:
        """구분 말이 질문에 있는지. 긴 한글 말은 앞 두 글자로 본다('사워베리크림' → '사워')."""
        if _HANGUL.search(word) and len(word) >= 3:
            word = word[:2]
        return bool(word) and word in hay_key

    # ---- 4
    def _intent(self, text: str, text_key: str) -> list[str] | None:
        """의도 규칙에 걸리면 메뉴 id 목록(빈 목록일 수 있음), 안 걸리면 None."""
        intent = next(
            (
                name
                for name in rules.INTENT_ORDER
                if contains_any(text_key, rules.INTENT_WORDS[name])
            ),
            None,
        )
        amount = _amount_limit(text) if contains_any(text_key, rules.PRICE_LIMIT_WORDS) else None
        if intent is None and amount is not None:
            intent = "price"
        if intent is None:
            return None
        cats = self._categories(text_key)
        recs = list(self.records.values())

        if intent == "less_sweet":
            return [r.item.id for r in recs if r.taste and "덜" in (r.taste.get("label") or "")]
        if intent == "sweet":
            sweet = [r for r in recs if r.taste and "덜" not in (r.taste.get("label") or "")]
            sweet.sort(key=lambda r: r.taste.get("sweet_mentions", 0), reverse=True)
            return [r.item.id for r in sweet]
        if intent == "whole":
            return self._whole_ids()
        if intent == "gift":
            return self._gift_ids()
        if intent in ("recent", "popular"):
            field = "recent_mentions" if intent == "recent" else "review_mentions"
            return self._popular_ids(field, cats, text_key)
        # price
        pool = [r for r in recs if isinstance(r.item.price, int)]
        if cats:
            pool = [r for r in pool if r.item.category in cats]
        if amount is not None:
            pool = [r for r in pool if r.item.price <= amount]
            return [r.item.id for r in sorted(pool, key=lambda r: r.item.price)]
        if not pool:
            return []
        high = contains_any(text_key, rules.PRICE_HIGH_WORDS)
        edge = max(r.item.price for r in pool) if high else min(r.item.price for r in pool)
        return [r.item.id for r in pool if r.item.price == edge]

    def _whole_ids(self) -> list[str]:
        marks = [key(w) for w in rules.WHOLE_MARKERS]
        out = []
        for r in self.records.values():
            names = [key(x) for x in (r.item.name_ko, r.name_en, *r.item.aliases)]
            if any(m in n for m in marks for n in names):
                out.append(r.item.id)
        return out

    def _gift_ids(self) -> list[str]:
        """선물 언급 있는 메뉴(많은 순). 메뉴엔 없고 묶음에만 있으면 대표 메뉴.
        포장·테이크아웃은 세지 않는다."""
        scored = [
            (r.gift.get("gift_mentions", 0), r.item.id)
            for r in self.records.values()
            if r.gift and r.gift.get("gift_mentions", 0) > 0
        ]
        have = {mid for _, mid in scored}
        for f in self.families.values():
            if f.gift and f.gift.get("gift_mentions", 0) > 0 and not have & set(f.members):
                scored.append((f.gift["gift_mentions"], f.representative))
        ids = [mid for _, mid in sorted(scored, key=lambda t: -t[0])]
        if rules.GIFT_INCLUDES_WHOLE:
            ids += [m for m in self._whole_ids() if m not in ids]
        return ids

    def _popular_ids(self, field: str, cats: set[str], text_key: str) -> list[str]:
        """묶음은 묶음 전체 언급 수로 한 덩어리(대표 메뉴로 돌려줌), 나머지는 메뉴별."""
        drinks_ok = bool(cats & set(rules.DRINK_CATEGORIES)) or contains_any(
            text_key, rules.DRINK_WORDS
        )
        units: list[tuple[int, bool, str]] = []
        for f in self.families.values():
            if f.popularity:
                units.append(
                    (
                        f.popularity.get(field, 0),
                        f.popularity.get("best_mark", False),
                        f.representative,
                    )
                )
        for r in self.records.values():
            if r.family in self.families or not r.popularity:
                continue
            units.append(
                (r.popularity.get(field, 0), r.popularity.get("best_mark", False), r.item.id)
            )
        keep = []
        for count, _best, mid in sorted(units, key=lambda t: (t[0], t[1]), reverse=True):
            cat = self.records[mid].item.category
            if cats and cat not in cats:
                continue
            if not cats and not drinks_ok and cat in rules.DRINK_CATEGORIES:
                continue
            if count > 0:
                keep.append(mid)
        return keep[: rules.POPULAR_TOP]

    # ---- 5
    def _categories(self, text_key: str) -> set[str]:
        present = {r.item.category for r in self.records.values()}
        return {
            c
            for c, words in rules.CATEGORY_WORDS.items()
            if c in present and contains_any(text_key, words)
        }

    def _tags_and_categories(self, text_key: str, text: str) -> list[str] | None:
        tags = set()
        all_tags = {t for r in self.records.values() for t in r.tags}
        for tag in all_tags:
            if contains_any(text_key, [tag, *rules.TAG_WORDS.get(tag, [])]):
                tags.add(tag)
        cats = self._categories(text_key)
        tagged = [r.item.id for r in self.records.values() if tags & set(r.tags)]
        in_cat = [r.item.id for r in self.records.values() if r.item.category in cats]
        if tagged and in_cat:
            both = [m for m in tagged if m in set(in_cat)]
            return both or tagged
        if tagged:
            return tagged
        if in_cat:
            return self._refine_category(in_cat, text)
        return None

    def _refine_category(self, in_cat: list[str], text: str) -> list[str]:
        """종류 말 말고 다른 뜻 있는 말이 있으면 그 말로 좁힌다.
        예: "초코 케이크", "호두 들어간 케이크"
        종류 안에서 → 없으면 전체 메뉴에서 → 그래도 없으면 빈 목록. 다른 말이 없으면 종류 전체."""
        if not self.keyword.words(text):
            return in_cat
        found = [i for i, _ in self.keyword.search(text, k=len(self.records))]
        inside = [i for i in found if i in set(in_cat)]
        return inside or found

    def _rank_rule_list(self, ids: list[str], text: str) -> list[tuple[str, float, str]]:
        """규칙이 고른 목록 안에서만 순서를 다듬는다(키워드 검색과 RRF).
        목록 밖 메뉴는 넣지 않는다."""
        if not ids:
            return []
        allowed = set(ids)
        kw = [i for i, _ in self.keyword.search(text, k=len(self.records)) if i in allowed]
        fused = (
            _rrf([ids, kw]) if kw else {m: 1.0 / (rules.RRF_K + n) for n, m in enumerate(ids, 1)}
        )
        ordered = sorted(ids, key=lambda m: fused.get(m, 0.0), reverse=True)
        return [(m, max(0.85 - 0.01 * n, 0.5), "keyword") for n, m in enumerate(ordered)]

    # ---- 6
    def _fuzzy(self, text: str) -> list[tuple[str, float, str]]:
        stop = {key(w) for w in rules.STOPWORDS}
        out: list[tuple[str, float, str]] = []
        for word in text.split():
            if len(word) < rules.FUZZY_MIN_LEN or not _HANGUL.search(word) or word in stop:
                continue
            best: tuple[float, _Term] | None = None
            for term in self._fuzzy_terms:
                for length in (len(term.key) - 1, len(term.key), len(term.key) + 1):
                    if length < rules.FUZZY_MIN_LEN or len(word) < length:
                        continue
                    ratio = SequenceMatcher(None, word[:length], term.key).ratio()
                    if ratio >= rules.FUZZY_MIN_RATIO and (best is None or ratio > best[0]):
                        best = (ratio, term)
            if best:
                term = best[1]
                if term.target.startswith("family:"):
                    out += [
                        (m, s * 0.8, how)
                        for m, s, how in self._expand_family(term.target[7:], "", False)
                    ]
                else:
                    out.append((term.target, 0.75, "alias"))
        return out

    # ---- 7
    def _keyword_vector(self, text: str) -> list[tuple[str, float, str]]:
        kw = self.keyword.search(text, k=10)
        vec = self.vector.search(text, k=10)
        if not kw and not vec:
            return []
        lists = [[i for i, _ in kw]] + ([[i for i, _ in vec]] if vec else [])
        fused = _rrf(lists)
        top = (len(lists)) / (rules.RRF_K + 1)
        kw_ids, vec_ids = {i for i, _ in kw}, {i for i, _ in vec}
        out = []
        for mid, score in sorted(fused.items(), key=lambda t: t[1], reverse=True):
            how = (
                "hybrid"
                if mid in kw_ids and mid in vec_ids
                else ("keyword" if mid in kw_ids else "vector")
            )
            out.append((mid, 0.3 + 0.4 * score / top, how))
        return out


def _rrf(lists: list[list[str]]) -> dict[str, float]:
    """Reciprocal Rank Fusion: 여러 순위 목록을 1/(K+순위)의 합으로 합친다."""
    scores: dict[str, float] = {}
    for ranked in lists:
        for n, mid in enumerate(ranked, 1):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (rules.RRF_K + n)
    return scores


_NUM = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}


def _amount_limit(text: str) -> int | None:
    """'만 원 이하' → 10000, '8천원' → 8000, '9,000원' → 9000, '1만 5천' → 15000. 없으면 None."""
    t = text.replace(",", "")
    m = re.search(r"(\d+|[일이삼사오육칠팔구])?\s*만\s*(?:(\d+|[일이삼사오육칠팔구])\s*천)?", t)
    if m and ("만 원" in t or "만원" in t or m.group(2)):
        man = m.group(1)
        value = (int(man) if man and man.isdigit() else _NUM.get(man or "", 1)) * 10000
        if m.group(2):
            g = m.group(2)
            value += (int(g) if g.isdigit() else _NUM[g]) * 1000
        return value
    m = re.search(r"(\d+)\s*천\s*원?", t)
    if m:
        return int(m.group(1)) * 1000
    m = re.search(r"(\d{3,})\s*원", t)
    if m:
        return int(m.group(1))
    return None
