"""Menu lookups for the assistant's tools over the shop copy they work on (ADR-0008).

The search itself is Part B's: `rag.shop_knowledge` over the shop file (`ShopDataRepository`),
the same builder `LiveShopKnowledge` uses. A shop built from a data folder carries the
search-only fields (family, popularity…) and `families` in the file itself (store_data.py).

Two searches, one per risk:
- resolve/mentions (they pick what a write changes): names, aliases, typos and family names
  only, from a vector-free index. The embedding API is never called.
- lookup (read-only questions): the same names first; only when none match, the full RAG
  search (intent rules, BM25 and, with CLOVASTUDIO_API_KEY, vectors). That index is built
  on first use. Embeddings are cached by content (in the DB when there is one, else in
  data/cache/shop_embeddings.json), so a menu change re-embeds only the menus that changed.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from agenthan.rag.knowledge import RagStoreKnowledge, jamo
from agenthan.rag.normalize import key, normalize
from agenthan.rag.repository import Family
from agenthan.rag.shop import shop_knowledge
from agenthan.rag.vector import CACHE_DIR, Embed, EmbeddingCache
from agenthan.tools.menu_index import Factory, Lookup, Resolution

WHOLE_RATIO = 0.8  # a name this close to a menu/alias/family name is that name (typos allowed)
CONFIDENT = ("exact", "alias")  # name, alias, family and typo matches; not BM25 or vectors
LOOKUP_LIMIT = 3  # related menus for a question that names none
FILE_CACHE = CACHE_DIR / "shop_embeddings.json"  # data/ is gitignored
_HANGUL = re.compile(r"[가-힣]")


def _similar(a: str, b: str) -> float:
    """Letter or jamo similarity, whichever is higher ("메도빈" ≈ "메도빅")."""
    return max(SequenceMatcher(None, a, b).ratio(), SequenceMatcher(None, jamo(a), jamo(b)).ratio())


def _flat(text: str) -> str:
    return key(text).replace(" ", "")


def _pattern(term: str) -> re.Pattern | None:
    """Where a name shows up in customer text: spaces between Korean letters allowed,
    two-letter Korean names only at a word start, other scripts on word boundaries."""
    k = _flat(term)
    if _HANGUL.search(k):
        if len(k) < 2:
            return None
        if len(k) == 2:
            return re.compile(r"(?<![가-힣])" + re.escape(k))
        return re.compile(r"\s*".join(re.escape(c) for c in k))
    if len(k) < 3:
        return None
    body = r"\s*".join(re.escape(w) for w in normalize(term).split())
    return re.compile(rf"(?<![a-z0-9а-я]){body}(?![a-z0-9а-я])")


class RagMenuIndex:
    def __init__(
        self,
        data: dict,
        embed: Embed | None | bool = None,
        cache_path: Path | None = FILE_CACHE,
    ):
        """data: the shop file's menus (and families). embed as in rag: None = CLOVA when the
        key is set, False = off, a function = tests. cache_path: file cache when no DB."""
        self.data = {"menus": data.get("menus") or [], "families": data.get("families") or []}
        self.knowledge = shop_knowledge(self.data, embed=False)  # names only, for writes
        self._embed = embed
        self._cache_path = cache_path
        self._open: RagStoreKnowledge | None = None
        self.families: dict[str, Family] = dict(self.knowledge.families)
        self.family_of = {m: f.id for f in self.families.values() for m in f.members}
        terms: list[tuple[str, str]] = []  # (term, menu id or family:<id>)
        for r in self.knowledge.records.values():
            item = r.item
            terms += [(t, item.id) for t in (item.name_ko, item.name_native, *item.aliases) if t]
        for f in self.families.values():
            terms += [(t, f"family:{f.id}") for t in (f.name_ko, *f.aliases) if t]
        # Longest first so "메도빅 초코" wins over "메도빅" in the same place.
        self.patterns = sorted(
            ((p, target, len(_flat(t))) for t, target in terms if (p := _pattern(t))),
            key=lambda x: -x[2],
        )

    def _names_of(self, menu_id: str) -> list[str]:
        item = self.knowledge.records[menu_id].item
        names = [item.name_ko, item.name_native, *item.aliases]
        if fid := self.family_of.get(menu_id):
            names += [self.families[fid].name_ko, *self.families[fid].aliases]
        return [n for n in names if n]

    def resolve(self, name: str) -> Resolution:
        hits = [h for h in self.knowledge.search_menu(name, k=10) if h.matched_by in CONFIDENT]
        ids = tuple(dict.fromkeys(h.item.id for h in hits))
        q = _flat(name)
        whole = any(
            _similar(q, t) >= WHOLE_RATIO
            for menu_id in ids
            for t in map(_flat, self._names_of(menu_id))
        )
        return Resolution(ids, whole)

    def mentions(self, text: str) -> tuple[str, ...]:
        """Menu ids in a text. A family name alone counts for the family's representative."""
        hay = normalize(text)
        taken = [False] * len(hay)
        found: list[str] = []
        for pattern, target, _ in self.patterns:
            for m in pattern.finditer(hay):
                if any(taken[m.start() : m.end()]):
                    continue
                taken[m.start() : m.end()] = [True] * (m.end() - m.start())
                if target.startswith("family:"):
                    target = self.families[target.removeprefix("family:")].representative
                found.append(target)
        return tuple(dict.fromkeys(found))

    @property
    def open_search(self) -> RagStoreKnowledge:
        """The full RAG (rules, BM25, vectors) over the same menus, built on first use."""
        if self._open is None:
            kb = shop_knowledge(self.data, self._embed)
            if kb.vector.enabled and isinstance(kb.vector.cache, EmbeddingCache):
                kb.vector.cache = EmbeddingCache(self._cache_path)  # no DB: keep it on disk
            self._open = kb
        return self._open

    def lookup(self, question: str) -> Lookup:
        """Read-only: menus a question is about. Names first; if it names none, related
        menus from the full search (empty when the evidence is weak)."""
        named = self.mentions(question) or self.resolve(question).ids
        if named:
            return Lookup(tuple(named), named=True)
        hits = self.open_search.search_menu(question, k=LOOKUP_LIMIT)
        return Lookup(tuple(dict.fromkeys(h.item.id for h in hits)), named=False)

    def facts(self, menu_id: str) -> dict:
        item = self.knowledge.records[menu_id].item
        out = {"name": item.name_ko, "price": item.price}
        if item.description:
            out["description"] = item.description
        if item.category:
            out["category"] = item.category
        if item.options:
            out["options"] = [o.name for o in item.options if o.available]
        if item.allergens:
            out["allergens"] = list(item.allergens)
        if item.sold_out:
            out["sold_out"] = True
        return out


def index_factory(embed: Embed | None | bool = None) -> Factory:
    return lambda data: RagMenuIndex(data, embed)
