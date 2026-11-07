"""계약의 가짜 구현. 진짜 구현이 나오기 전까지 각 파트가 이걸로 개발·테스트한다.

검색은 글자 포함 여부만 본다. 성능 비교 대상이 아니다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from agenthan.core.contracts import MenuHit, MenuItem, MenuOption, Order, Review

SAMPLE_STORE = Path(__file__).resolve().parents[3] / "fixtures" / "sample_store.json"


class FakeStoreKnowledge:
    """`fixtures/sample_store.json`을 읽는 메모리 구현."""

    def __init__(self, menu: list[MenuItem], reviews: list[Review] | None = None) -> None:
        self._menu = {m.id: m for m in menu}
        self._reviews = sorted(reviews or [], key=lambda r: r.created_at, reverse=True)

    @classmethod
    def from_fixture(cls, path: Path = SAMPLE_STORE) -> FakeStoreKnowledge:
        raw = json.loads(path.read_text(encoding="utf-8"))
        menu = [
            MenuItem(
                **{k: v for k, v in m.items() if k not in ("aliases", "options", "allergens")},
                aliases=tuple(m.get("aliases", ())),
                options=tuple(MenuOption(**o) for o in m.get("options", ())),
                allergens=tuple(m.get("allergens", ())),
            )
            for m in raw["menu"]
        ]
        reviews = [
            Review(
                **{k: v for k, v in r.items() if k not in ("created_at", "menu_ids")},
                created_at=datetime.fromisoformat(r["created_at"]),
                menu_ids=tuple(r.get("menu_ids", ())),
            )
            for r in raw.get("reviews", [])
        ]
        return cls(menu, reviews)

    def search_menu(self, query: str, k: int = 5) -> list[MenuHit]:
        q = query.replace(" ", "")
        hits: list[MenuHit] = []
        for item in self._menu.values():
            if item.name_ko.replace(" ", "") == q:
                hits.append(MenuHit(item, 1.0, "exact"))
            elif any(a.replace(" ", "") in q or q in a.replace(" ", "") for a in item.aliases):
                hits.append(MenuHit(item, 0.8, "alias"))
            elif q and (q in item.name_ko.replace(" ", "") or item.name_ko.replace(" ", "") in q):
                hits.append(MenuHit(item, 0.6, "keyword"))
        return sorted(hits, key=lambda h: h.score, reverse=True)[:k]

    def get_menu(self, menu_id: str) -> MenuItem | None:
        return self._menu.get(menu_id)

    def list_menu(self) -> list[MenuItem]:
        return list(self._menu.values())

    def recent_reviews(self, limit: int = 10, since: datetime | None = None) -> list[Review]:
        rows = [r for r in self._reviews if since is None or r.created_at >= since]
        return rows[:limit]


class MemoryOrderSink:
    """받은 주문을 리스트에 쌓는다."""

    def __init__(self) -> None:
        self.orders: list[Order] = []

    def submit(self, order: Order) -> None:
        self.orders.append(order)
