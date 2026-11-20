"""StoreKnowledge 진짜 구현 (Part B).

search_menu 순서 (자세한 키워드·기준값은 rules.py). 한 단계에서 못 찾으면 다음 단계로 넘어간다.
  0. 전처리: 표기 정규화 → 말 바꾸기면 정정 뒤쪽만 → 여러 메뉴면 조각으로 나누기
  1. 정확 일치: 메뉴명·원어명·별칭 → 그 메뉴만.
     남은 내용어가 그 메뉴에 없고 같은 종류의 다른 메뉴에 있으면 그 메뉴를 앞에 둔다
     ("아아 카페인 없이" → 디카페인 메뉴가 있으면 디카페인 먼저)
  2. 묶음 일치: 묶음 별칭 → 묶음 전체(대표 메뉴 1등). '초코' 같은 구분 말이 있으면 그 메뉴만,
     비교 질문('차이')이면 대표 메뉴만
     → 1·2로 찾았으면 여기서 끝 (빠르게)
  3. 빈 목록이 정답인 질문: 데이터에 없는 정보·서비스(견과류·초·레터링…)·가게 정보(몇 시·주차…)
  4. 의도 규칙: 덜 단 것 / 단 것 / 한 판 / 선물 / 요즘 인기 / 인기 / 가격.
     내용어가 같이 있으면 내용어로 먼저 거르고 그 안에서 순위 ("꿀 들어간 유명한 거")
  5. 태그: "러시아 전통 케이크", "홍차"
  6. 종류(category): 종류 자체를 물을 때만 ("에이드 뭐 있어요?"). 다른 내용어가 있으면 건너뜀
  7. 오타 허용 일치: 글자·자모 단위, 띄어 쓴 두 낱말은 붙여서도 ("프라크 케이크" → 프라하)
  8. 키워드(BM25)·벡터 검색 → RRF로 합치기. 근거 기준 미달이면 빈 목록

matched_by는 계약 값만 쓴다: 메뉴명·원어명 = exact, 별칭·묶음 별칭·오타 = alias,
규칙·태그·종류·BM25 = keyword, 벡터만 = vector, BM25와 벡터가 같이 찾은 것 = hybrid.
품절·옵션은 거르지 않고 MenuItem에 그대로 담는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from agenthan.core.contracts import MenuHit, MenuItem, Review
from agenthan.rag import rules
from agenthan.rag.keyword import Analysis, KeywordIndex, user_words
from agenthan.rag.normalize import contains_any, drop_correction, key, normalize, split_items
from agenthan.rag.repository import (
    DUMMY_DIR,
    JsonDirRepository,
    MenuRepository,
    PgRepository,
    StoreData,
)
from agenthan.rag.vector import Embed, PgVectorIndex, VectorIndex, default_cache_path

log = logging.getLogger(__name__)
_HANGUL = re.compile(r"[가-힣]")
_LETTER = r"a-z0-9Ѐ-ӿ"  # 라틴·숫자·키릴 글자
_MASK = "\x00"

Found = list[tuple[str, float, str]]  # (메뉴 id, 점수, matched_by)


@dataclass(frozen=True)
class _Term:
    key: str
    target: str  # 메뉴 id 또는 'family:묶음id'
    kind: str  # exact | alias | family
    pattern: re.Pattern
    jamo: str = ""


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


_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def jamo(text: str) -> str:
    """한글을 자모로 푼다: '프라크' → 'ㅍㅡㄹㅏㅋㅡ'."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [_CHO[code // 588], _JUNG[(code % 588) // 28], _JONG[code % 28].strip()]
        else:
            out.append(ch)
    return "".join(out)


class RagStoreKnowledge:
    """가게 지식 RAG. 기본은 fixtures/dummy_store, AGENTHAN_DATA_DIR로 실제 데이터(data/clean)."""

    def __init__(
        self,
        repository: MenuRepository,
        embed: Embed | None | bool = None,
        cache_path: Path | None = None,
    ) -> None:
        """embed: None=키가 있으면 CLOVA 임베딩, False=벡터 끔, 함수=그 함수(테스트용).
        cache_path: 임베딩 캐시 파일 (None이면 캐시 없이 메모리만)."""
        data: StoreData = repository.load()
        self.records = {r.item.id: r for r in data.menus}
        self.families = {f.id: f for f in data.families}
        self.store = data.store
        self._reviews = sorted(data.reviews, key=lambda r: r.created_at, reverse=True)
        self._terms = self._build_terms()
        self._fuzzy_terms = [t for t in self._terms if _HANGUL.search(t.key) and len(t.key) >= 3]
        self._family_words = self._build_family_words()
        self._rule_phrases = self._build_rule_phrases()
        docs = {i: self._doc_text(i) for i in self.records}
        names = {i: self._name_text(i) for i in self.records}
        dictionary = user_words(
            [r.item.name_ko for r in data.menus]
            + [a for r in data.menus for a in r.item.aliases]
            + [a for f in data.families for a in f.aliases]
        )
        self.keyword = KeywordIndex(docs, dictionary, names)
        embed_docs = {i: self._embed_text(i) for i in self.records}
        self.vector = VectorIndex(embed_docs, embed, cache_path)

    # ---------------------------------------------------------------- 만들기
    @classmethod
    def from_dir(
        cls,
        path: str | Path | None = None,
        embed: Embed | None | bool = None,
        cache_path: Path | None = None,
    ):
        """path → 환경변수 AGENTHAN_DATA_DIR → 더미 순서. 벡터는 CLOVASTUDIO_API_KEY가 있을 때만.
        CLOVA 임베딩을 쓸 때 캐시는 기본 data/cache/<폴더 이름>_embeddings.json.
        가짜 임베딩 함수를 넘기면 캐시를 쓰지 않는다(진짜 캐시가 섞이지 않게)."""
        repo = JsonDirRepository(path)
        if cache_path is None and embed is None:
            cache_path = default_cache_path(repo.path)
        return cls(repo, embed, cache_path)

    @classmethod
    def from_db(
        cls,
        store_id: str | None = None,
        embed: Embed | None | bool = None,
        url: str | None = None,
    ) -> RagStoreKnowledge:
        """PostgreSQL(AGENTHAN_DB_URL)의 가게 한 곳. store_id → AGENTHAN_STORE_ID → dummy_store.
        벡터는 pgvector(embeddings 표)에서 찾고, 임베딩 캐시도 DB 표(embedding_cache)를 쓴다.
        리뷰·가게 정보 의미 검색(search_reviews·search_store_info)은 DB일 때만 된다."""
        from agenthan.db.conn import connect, default_store_id

        sid = store_id or default_store_id()
        kb = cls(PgRepository(sid, url), embed=False)
        kb.vector = PgVectorIndex(kb.vector.docs, sid, embed, url)
        kb.store_id = sid
        with connect(url) as conn:
            kb.store_docs = {
                i: (title, body)
                for i, title, body in conn.execute(
                    "SELECT id, title, body FROM store_docs WHERE store_id = %s ORDER BY id", (sid,)
                ).fetchall()
            }
        return kb

    @classmethod
    def from_env(cls, embed: Embed | None | bool = None) -> RagStoreKnowledge:
        """AGENTHAN_DB_URL이 있으면 DB, 없거나 DB를 못 읽으면 JSON 폴더(from_dir)."""
        from agenthan.db.conn import enabled, pause

        if enabled():
            try:
                return cls.from_db(embed=embed)
            except Exception as error:
                pause(error)
                log.warning("DB에서 가게 지식을 못 읽어 JSON으로 엽니다: %s", type(error).__name__)
        return cls.from_dir(embed=embed)

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
            terms.append(_Term(k, target, kind, pat, jamo(k)))

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

    def _build_rule_phrases(self) -> list[str]:
        """규칙을 고르는 말(의도·비교·가격·한 판·종류·태그). 이 말은 '내용어'로 치지 않는다."""
        phrases = [w for words in rules.INTENT_WORDS.values() for w in words]
        phrases += rules.COMPARE_WORDS + rules.PRICE_HIGH_WORDS + rules.PRICE_LIMIT_WORDS
        phrases += rules.WHOLE_MARKERS + [w for ws in rules.CATEGORY_WORDS.values() for w in ws]
        phrases += [w for ws in rules.TAG_WORDS.values() for w in ws]
        phrases += [t for r in self.records.values() for t in r.tags]
        return sorted({key(p) for p in phrases if key(p)})

    def _name_text(self, menu_id: str) -> str:
        r = self.records[menu_id]
        fam = self.families.get(r.family)
        parts = [r.item.name_ko, r.item.name_native, *r.item.aliases]
        if fam:
            parts += [fam.name_ko, *fam.aliases]
        return " ".join(p for p in parts if p)

    def _embed_text(self, menu_id: str) -> str:
        """임베딩할 글 = 한국어 이름 + 원어명 + 별칭 + 설명 + 옵션(되는 것과 메모)."""
        r = self.records[menu_id]
        options = [o.name for o in r.item.options if o.available]
        options += [o.note for o in r.item.options if o.note]
        parts = [r.item.name_ko, r.item.name_native, ", ".join(r.item.aliases), r.item.description]
        if options:
            parts.append("옵션: " + ", ".join(options))
        return " / ".join(p for p in parts if p)

    def _doc_text(self, menu_id: str) -> str:
        """검색 문서 = 이름·별칭(+묶음) + 설명 + 옵션(되는 것과 그 메모) + 태그."""
        r = self.records[menu_id]
        options = [o.name for o in r.item.options if o.available]
        options += [o.note for o in r.item.options if o.note]
        parts = [self._name_text(menu_id), r.item.description, *options, *r.tags]
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

    # ---------------------------------------------------------------- DB 의미 검색 (계약 밖)
    store_id: str = ""
    store_docs: dict[str, tuple[str, str]] = {}  # {조각 id: (제목, 본문)}

    def search_reviews(self, query: str, k: int = 5) -> list[tuple[Review, float]]:
        """리뷰 의미 검색 ("양 많다는 얘기 있어?"). DB·벡터가 켜져 있을 때만, 아니면 []."""
        if not isinstance(self.vector, PgVectorIndex):
            return []
        by_id = {r.id: r for r in self._reviews}
        hits = self.vector.search_kind("review", query, k, rules.VECTOR_MIN_COSINE)
        return [(by_id[i], s) for i, s in hits if i in by_id]

    def search_store_info(self, query: str, k: int = 3) -> list[tuple[str, str, float]]:
        """가게 정보 조각 의미 검색 ("주차 돼요?" → 편의·결제 조각). → [(제목, 본문, 점수)]."""
        if not isinstance(self.vector, PgVectorIndex):
            return []
        hits = self.vector.search_kind("doc", query, k, rules.VECTOR_MIN_COSINE)
        return [(*self.store_docs[i], s) for i, s in hits if i in self.store_docs]

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
        compare = contains_any(text_key, rules.COMPARE_WORDS)
        per_segment = [self._match_terms(seg, compare=compare) for seg in segments]
        if any(per_segment):
            if len(segments) > 1:
                # 메뉴 이름이 없는 조각은 키워드 검색으로만 보충한다 ("메도빅 하나랑 오미자차")
                per_segment = [
                    found or self._keyword_vector(seg)[:1]
                    for seg, found in zip(segments, per_segment, strict=True)
                ]
                return self._ordered_hits(per_segment)
            return self._hits(per_segment[0])

        analysis = self.keyword.analyze(text)

        # 3. 메뉴 데이터로 답할 수 없는 질문
        if self._unanswerable(text_key, analysis):
            return []

        # 4. 의도 규칙 (덜 단 것은 근거가 없으면 빈 목록이 정답이라 여기서 끝)
        ruled = self._intent(text, text_key, analysis)
        if ruled is not None:
            ids, final = ruled
            if ids or final:
                return self._hits(self._rank_rule_list(ids, text, keep_order=True))

        # 5. 태그 → 6. 종류 자체를 묻는 질문
        for ids in (self._tagged(text_key), self._pure_category(text_key, analysis)):
            if ids:
                return self._hits(self._rank_rule_list(ids, text))

        # 7. 오타 허용
        fuzzy = self._fuzzy(text)
        if fuzzy:
            return self._hits(fuzzy)

        # 8. 키워드 + 벡터
        return self._hits(self._keyword_vector(text))

    def _hits(self, found: Found) -> list[MenuHit]:
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

    def _ordered_hits(self, per_segment: list[Found]) -> list[MenuHit]:
        """여러 메뉴 주문: 조각마다 첫 메뉴를 말한 순서대로 먼저, 나머지 메뉴는 그 뒤에.
        계약상 점수는 내림차순이어야 해서, 앞 결과보다 높은 점수는 앞 점수로 맞춘다."""
        firsts = [seg[0] for seg in per_segment if seg]
        rest = [hit for seg in per_segment for hit in seg[1:]]
        out: list[MenuHit] = []
        seen: set[str] = set()
        ceiling = 1.0
        for mid, score, how in firsts + rest:
            if mid in seen or mid not in self.records:
                continue
            seen.add(mid)
            ceiling = min(ceiling, score)
            out.append(MenuHit(self.records[mid].item, round(max(ceiling, 0.0), 4), how))
        return out

    # ---- 내용어
    def _is_rule_word(self, w: str) -> bool:
        return any(w == p or w in p or (len(p) >= 2 and p in w) for p in self._rule_phrases)

    def _content(self, a: Analysis) -> Analysis:
        """규칙을 고르는 말과 일반어를 뺀 '내용어'만 남긴다. 예: '꿀 들어간 유명한 거' → ['꿀']"""
        words = tuple(w for w in a.distinct if not self._is_rule_word(w))
        return Analysis(words, a.name_only)

    # ---- 1·2
    def _match_terms(self, seg: str, compare: bool) -> Found:
        hay = seg
        matches: list[tuple[int, _Term]] = []
        for term in self._terms:
            m = term.pattern.search(hay)
            if not m:
                continue
            matches.append((m.start(), term))
            # 찾은 자리는 지워서 더 짧은 말이 그 안에서 또 걸리지 않게 한다
            # ('초코 메도빅' 안의 '메도빅')
            hay = hay[: m.start()] + _MASK * (m.end() - m.start()) + hay[m.end() :]
        matches.sort(key=lambda t: t[0])
        rest = hay.replace(_MASK, " ")
        rest_key = rest.replace(" ", "")
        out: Found = []
        for _, term in matches:
            if term.kind == "exact":
                out.append((term.target, 1.0, "exact"))
            elif term.kind == "alias":
                out.append((term.target, 0.95, "alias"))
            else:
                out += self._expand_family(term.target.split(":", 1)[1], rest_key, compare)
        if len(matches) == 1 and matches[0][1].kind != "family":
            # 찾은 말에 붙어 있는 말('크림'+'치즈케이크')은 빼고, 따로 떨어진 낱말만 본다
            apart = " ".join(w for w in hay.split() if _MASK not in w)
            out = self._better_with_rest(matches[0][1].target, apart) + out
        return out

    def _better_with_rest(self, matched: str, rest: str) -> Found:
        """정확히 찾은 메뉴 말고 남은 내용어가 있을 때, 그 말이 찾은 메뉴엔 없고 같은 종류의
        다른 메뉴 이름·별칭엔 다 있으면 그 메뉴를 앞에 둔다. 예: '아아 카페인 없이' → 디카페인 메뉴
        설명에만 있는 말로는 바꾸지 않는다."""
        content = self._content(self.keyword.analyze(rest))
        if not content.words:
            return []
        if self.keyword.coverage(matched, list(content.words), content.name_only) >= 1.0:
            return []  # 찾은 메뉴가 이미 그 말을 덮는다 (예: 디카페인 변경 옵션이 있는 아메리카노)
        cat = self.records[matched].item.category
        in_names = Analysis(
            content.words, frozenset(content.words)
        )  # 다른 메뉴는 이름·별칭에 있어야
        return [
            (mid, 0.96, "keyword")
            for mid, cov in self.keyword.search(analysis=in_names, k=len(self.records))
            if cov >= 1.0 and mid != matched and self.records[mid].item.category == cat
        ]

    def _expand_family(self, fid: str, rest_key: str, compare: bool) -> Found:
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

    # ---- 3
    @staticmethod
    def _unanswerable(text_key: str, a: Analysis) -> bool:
        """데이터에 없는 정보·서비스·가게 정보 질문. 한 글자 말('초')은 낱말이 그대로 있을 때만."""
        for w in rules.NO_INFO_WORDS + rules.STORE_INFO_WORDS:
            k = key(w)
            if (len(k) == 1 and k in a.words) or (len(k) > 1 and k in text_key):
                return True
        return False

    # ---- 4
    def _intent(self, text: str, text_key: str, a: Analysis) -> tuple[list[str], bool] | None:
        """의도 규칙에 걸리면 (메뉴 id 목록, 이 결과로 끝낼지), 안 걸리면 None.
        내용어가 있으면 그 말로 먼저 거른 후보 안에서 순위를 매긴다. 후보가 없으면 거르지 않는다."""
        intent = next(
            (n for n in rules.INTENT_ORDER if contains_any(text_key, rules.INTENT_WORDS[n])), None
        )
        amount = _amount_limit(text) if contains_any(text_key, rules.PRICE_LIMIT_WORDS) else None
        if intent is None and amount is not None:
            intent = "price"
        if intent is None:
            return None
        content = self._content(a)
        cand = (
            [i for i, _ in self.keyword.search(analysis=content, k=len(self.records))]
            if content.words
            else []
        )
        cats = self._categories(text_key)
        recs = list(self.records.values())

        if intent == "less_sweet":
            ids = [r.item.id for r in recs if r.taste and "덜" in (r.taste.get("label") or "")]
            return ([i for i in ids if i in cand] if cand else ids), True
        if intent == "sweet":
            sweet = [r for r in recs if r.taste and "덜" not in (r.taste.get("label") or "")]
            sweet.sort(key=lambda r: r.taste.get("sweet_mentions", 0), reverse=True)
            ids = [r.item.id for r in sweet]
            if cand:
                inside = [i for i in ids if i in cand]
                return (inside, False) if inside else None
            return ids, True
        if intent == "whole":
            ids = self._whole_ids()
            inside = [i for i in ids if i in cand]
            return (inside or ids), False
        if intent == "gift":
            if cand:
                return sorted(cand, key=self._gift_score, reverse=True), False
            return self._gift_ids(), False
        if intent in ("recent", "popular"):
            field = "recent_mentions" if intent == "recent" else "review_mentions"
            if cand:
                return sorted(cand, key=lambda m: self._pop_score(m, field), reverse=True), False
            return self._popular_ids(field, cats, text_key), False
        # price
        high = contains_any(text_key, rules.PRICE_HIGH_WORDS)
        pool = [self.records[i] for i in cand] if cand else recs
        pool = [r for r in pool if isinstance(r.item.price, int)]
        if cats and not cand:
            pool = [r for r in pool if r.item.category in cats]
        if amount is not None:
            pool = [r for r in pool if r.item.price <= amount]
        if cand or amount is not None:
            return [
                r.item.id for r in sorted(pool, key=lambda r: r.item.price, reverse=high)
            ], False
        if not pool:
            return [], False
        edge = max(r.item.price for r in pool) if high else min(r.item.price for r in pool)
        return [r.item.id for r in pool if r.item.price == edge], False

    def _pop_score(self, mid: str, field: str) -> tuple[int, bool, bool]:
        """묶음 메뉴는 묶음 전체 언급 수, 나머지는 메뉴 언급 수. 같으면 Best 표시, 대표 메뉴 순."""
        r = self.records[mid]
        fam = self.families.get(r.family)
        src = fam.popularity if fam and fam.popularity else r.popularity
        is_rep = bool(fam and fam.representative == mid)
        if not src:
            return (0, False, is_rep)
        return (src.get(field, 0), bool(src.get("best_mark")), is_rep)

    def _gift_score(self, mid: str) -> tuple[int, bool]:
        r = self.records[mid]
        own = (r.gift or {}).get("gift_mentions", 0)
        fam = self.families.get(r.family)
        fam_gift = (fam.gift or {}).get("gift_mentions", 0) if fam else 0
        return (
            own or (fam_gift if fam and fam.representative == mid else 0),
            mid in self._whole_ids(),
        )

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
                pop = f.popularity
                units.append((pop.get(field, 0), pop.get("best_mark", False), f.representative))
        for r in self.records.values():
            if r.family in self.families or not r.popularity:
                continue
            pop = r.popularity
            units.append((pop.get(field, 0), pop.get("best_mark", False), r.item.id))
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

    # ---- 5·6
    def _categories(self, text_key: str) -> set[str]:
        present = {r.item.category for r in self.records.values()}
        return {
            c
            for c, words in rules.CATEGORY_WORDS.items()
            if c in present and contains_any(text_key, words)
        }

    def _tagged(self, text_key: str) -> list[str]:
        all_tags = {t for r in self.records.values() for t in r.tags}
        tags = {t for t in all_tags if contains_any(text_key, [t, *rules.TAG_WORDS.get(t, [])])}
        if not tags:
            return []
        tagged = [r.item.id for r in self.records.values() if tags & set(r.tags)]
        cats = self._categories(text_key)
        both = [m for m in tagged if self.records[m].item.category in cats]
        return both or tagged

    def _pure_category(self, text_key: str, a: Analysis) -> list[str]:
        """종류 자체를 묻는 질문일 때만 그 종류 전부. 다른 내용어가 있으면 종류 질문이 아니다."""
        cats = self._categories(text_key)
        if not cats or self._content(a).words:
            return []
        return [r.item.id for r in self.records.values() if r.item.category in cats]

    def _rank_rule_list(self, ids: list[str], text: str, keep_order: bool = False) -> Found:
        """규칙이 고른 목록 안에서만 순서를 다듬는다(키워드 검색과 RRF). 목록 밖 메뉴는 넣지 않는다.
        keep_order=True(의도 규칙: 인기순·가격순 등)면 규칙이 정한 순서를 그대로 쓴다."""
        if not ids:
            return []
        if keep_order:
            return [(m, max(0.85 - 0.01 * n, 0.5), "keyword") for n, m in enumerate(ids)]
        allowed = set(ids)
        kw = [i for i, _ in self.keyword.search(text, k=len(self.records)) if i in allowed]
        fused = (
            _rrf([ids, kw]) if kw else {m: 1.0 / (rules.RRF_K + n) for n, m in enumerate(ids, 1)}
        )
        ordered = sorted(ids, key=lambda m: fused.get(m, 0.0), reverse=True)
        return [(m, max(0.85 - 0.01 * n, 0.5), "keyword") for n, m in enumerate(ordered)]

    # ---- 7
    def _fuzzy(self, text: str) -> Found:
        """오타 허용. 낱말 하나, 그리고 다음 낱말과 붙인 것을 메뉴 말과 비교한다.
        글자 단위(FUZZY_MIN_RATIO)나 자모 단위(FUZZY_MIN_JAMO_RATIO) 중 하나만 넘으면 된다.
        - 메뉴 데이터에 그대로 있는 낱말('마스카포네')은 오타가 아니라서 비교하지 않는다.
        - 첫 자음(초성)은 같아야 한다: 오타는 보통 첫 글자보다 뒤에서 난다."""
        skip = {key(w) for w in rules.STOPWORDS + rules.GENERIC_WORDS}
        known = set(self.keyword.words(text)) & self.keyword.vocab
        words = text.split()
        out: Found = []
        for i, word in enumerate(words):
            if not _HANGUL.search(word) or key(word) in skip or self._is_rule_word(key(word)):
                continue
            if any(w in known for w in self.keyword.words(word)):
                continue
            pieces = [word] + ([word + words[i + 1]] if i + 1 < len(words) else [])
            best: tuple[float, _Term] | None = None
            for piece in pieces:
                if len(piece) < rules.FUZZY_MIN_LEN:
                    continue
                for term in self._fuzzy_terms:
                    for length in (len(term.key) - 1, len(term.key), len(term.key) + 1):
                        if length < rules.FUZZY_MIN_LEN or len(piece) < length:
                            continue
                        part = piece[:length]
                        if jamo(part[:1])[:1] != term.jamo[:1]:
                            continue
                        syl = SequenceMatcher(None, part, term.key).ratio()
                        jam = SequenceMatcher(None, jamo(part), term.jamo).ratio()
                        ok = syl >= rules.FUZZY_MIN_RATIO or jam >= rules.FUZZY_MIN_JAMO_RATIO
                        score = max(syl, jam)
                        if ok and (best is None or score > best[0]):
                            best = (score, term)
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

    # ---- 8
    def _keyword_vector(self, text: str) -> Found:
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
