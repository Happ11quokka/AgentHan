"""Store-knowledge search over the shop copy a tool is working on (ADR-0008).

Tools never import the RAG (Part B) themselves. The app plugs in a factory that builds a
MenuIndex from a shop's data (its menus and families); without one, tools fall back to exact
name matching.

The index is cached by a fingerprint of the menus and families, so any menu change (add,
rename, price, alias, delete, sold out) builds a new index the next time a tool looks. That
holds for the session store and for the throwaway copies used for previews alike.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

CACHE_SIZE = 8  # a few sessions and preview copies at once


@dataclass(frozen=True)
class Resolution:
    """Menu ids a name points to, best first. `whole` means the name is a menu's own name,
    alias or family name (allowing typos), not a longer phrase that only contains one."""

    ids: tuple[str, ...]
    whole: bool


@dataclass(frozen=True)
class Lookup:
    """Menus a read-only question is about. `named`: the question names them; otherwise they
    are only related (search by meaning, intent rules), so say so instead of asserting."""

    ids: tuple[str, ...]
    named: bool


class MenuIndex(Protocol):
    def resolve(self, name: str) -> Resolution:
        """For writes: names, aliases, typos, family names only. Never by meaning."""
        ...

    def lookup(self, question: str) -> Lookup:
        """For read-only answers: names first, then related menus by meaning."""
        ...

    def mentions(self, text: str) -> tuple[str, ...]:
        """Menu ids a customer text talks about (names, aliases, family names)."""
        ...

    def facts(self, menu_id: str) -> dict:
        """What the shop data says about one menu, for grounding replies."""
        ...


Factory = Callable[[dict], MenuIndex]  # shop data ({"menus", "families"}) → index

_factory: Factory | None = None
_cache: OrderedDict[str, MenuIndex] = OrderedDict()
builds = 0  # how many indexes were built; tests read it


def configure(factory: Factory | None) -> None:
    """Plug in (or remove) the index builder. Clears the cache."""
    global _factory
    _factory = factory
    _cache.clear()


def fingerprint(data: dict) -> str:
    parts = [data.get("menus") or [], data.get("families") or []]
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def index_for(data: dict) -> MenuIndex | None:
    """The index for exactly this shop's menus and families; rebuilt whenever they change."""
    global builds
    if _factory is None or not data.get("menus"):
        return None
    key = fingerprint(data)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    index = _factory(data)
    builds += 1
    _cache[key] = index
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return index
