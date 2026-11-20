"""The store knowledge RAG over a session's own menus (ADR-0008).

`index_factory(folder)` gives tools/menu_index a builder: menu rows from the shop copy go in,
a RagStoreKnowledge over exactly those menus comes out. Search-only fields the rows do not
carry (family, tags, popularity, category, options) come from the data folder by menu id;
the copy's name, price, aliases and sold-out state win.

Two searches, one per risk (ADR-0008):
- resolve/mentions (they pick what a write changes): names, aliases, typos and family names
  only. Vector search is never used and the embedding API is never called.
- lookup (read-only questions): the same names first; only when none match, the full RAG
  search (intent rules, BM25 and, with CLOVASTUDIO_API_KEY, vectors). That index is built
  on first use, and its embeddings are cached by content, so a menu change re-embeds only the
  menus whose text changed.
"""

from __future__ import annotations

import re
from dataclasses import replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from agenthan.app.store_data import resolve
from agenthan.core.contracts import MenuItem
from agenthan.rag.knowledge import RagStoreKnowledge, jamo
from agenthan.rag.normalize import key, normalize
from agenthan.rag.repository import DUMMY_DIR, Family, JsonDirRepository, MenuRecord, StoreData
from agenthan.rag.vector import Embed, default_cache_path
from agenthan.tools.menu_index import Factory, Lookup, Resolution

DEFAULT_FOLDER = DUMMY_DIR  # no data folder set: the committed dummy store
WHOLE_RATIO = 0.8  # a name this close to a menu/alias/family name is that name (typos allowed)
CONFIDENT = ("exact", "alias")  # name, alias, family and typo matches; not BM25 or vectors
LOOKUP_LIMIT = 3  # related menus for a question that names none
_HANGUL = re.compile(r"[가-힣]")


@lru_cache(maxsize=4)
def _source(folder: str) -> StoreData:
    return JsonDirRepository(resolve(folder)).load()


class SessionRepository:
    """rag MenuRepository over menu rows from a shop copy."""

    def __init__(self, menus: list[dict], source: StoreData):
        self.menus = menus
        self.source = source

    def load(self) -> StoreData:
        known = {r.item.id: r for r in self.source.menus}
        records = []
        for row in self.menus:
            base = known.get(row["id"])
            src = base.item if base else None
            item = MenuItem(
                id=row["id"],
                name_ko=row["name"],
                price=row["price"],
                description=row.get("description") or (src.description if src else ""),
                category=src.category if src else "",
                name_native=row.get("name_native") or (src.name_native if src else ""),
                aliases=tuple(row.get("aliases") or ()),
                options=src.options if src else (),
                allergens=src.allergens if src else (),
                sold_out=bool(row.get("sold_out", False)),
            )
            records.append(replace(base, item=item) if base else MenuRecord(item=item))
        ids = [r.item.id for r in records]
        families = []
        for f in self.source.families:
            members = tuple(m for m in f.members if m in ids)
            if members:
                lead = f.representative if f.representative in members else members[0]
                families.append(replace(f, members=members, representative=lead))
        return StoreData(records, families, [], self.source.store)


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
        menus: list[dict],
        source: StoreData,
        embed: Embed | None | bool = None,
        cache_path: Path | None = None,
    ):
        self.repo = SessionRepository(menus, source)
        self.knowledge = RagStoreKnowledge(self.repo, embed=False)  # names only, for writes
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
        self.terms = terms
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
            self._open = RagStoreKnowledge(self.repo, self._embed, self._cache_path)
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


def index_factory(folder: str | Path, embed: Embed | None | bool = None) -> Factory:
    """embed: None = CLOVA embeddings when CLOVASTUDIO_API_KEY is set (cached in
    data/cache/<folder>_embeddings.json, shared with Part B), False = off, a function = tests."""
    source = _source(str(folder))
    cache = default_cache_path(resolve(folder)) if embed is None else None
    return lambda menus: RagMenuIndex(menus, source, embed, cache)
