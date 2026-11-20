"""비서 가게(DummyStore)를 Part B 검색으로: 대화마다 그 가게의 메뉴·리뷰로 검색한다.

    from agenthan.rag import LiveShopKnowledge
    knowledge = LiveShopKnowledge(store.read)   # store = DummyStore

- 가게 파일이 바뀌면(메뉴 추가·수정·리뷰 추가) 다음 호출 때 색인을 다시 만든다.
  처음 쓸 때까지는 안 만든다
  (Kiwi를 처음 띄우는 데 몇 초 걸려서 대화 시작을 늦추지 않게).
- 벡터는 CLOVASTUDIO_API_KEY가 있을 때만. AGENTHAN_DB_URL이 있으면 임베딩 캐시를 DB 표에 둔다
  (시드에서 이미 만든 pilot 메뉴는 다시 부르지 않는다).
- 메뉴가 하나도 없으면 검색 결과는 늘 빈 목록.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from datetime import datetime

from agenthan.core.contracts import MenuHit, MenuItem, Review
from agenthan.rag.knowledge import RagStoreKnowledge
from agenthan.rag.repository import ShopDataRepository
from agenthan.rag.vector import Embed, PgEmbeddingCache


def shop_knowledge(data: dict, embed: Embed | None | bool = None) -> RagStoreKnowledge:
    """가게 파일 한 벌 → 검색. 메뉴가 있어야 한다."""
    kb = RagStoreKnowledge(ShopDataRepository(data), embed=embed)
    from agenthan.db.conn import available

    if kb.vector.enabled and available():
        kb.vector.cache = PgEmbeddingCache()
    return kb


class LiveShopKnowledge:
    """`StoreKnowledge` 계약. `read()`가 돌려주는 가게 파일이 바뀌면 색인을 새로 만든다."""

    def __init__(self, read: Callable[[], dict], embed: Embed | None | bool = None) -> None:
        self._read = read
        self._embed = embed
        self._lock = threading.Lock()
        self._sig = ""
        self._kb: RagStoreKnowledge | None = None

    def _current(self) -> RagStoreKnowledge | None:
        data = self._read()
        sig = hashlib.sha1(
            json.dumps(
                [data.get("menus"), data.get("reviews")],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        with self._lock:
            if sig != self._sig:
                self._kb = shop_knowledge(data, self._embed) if data.get("menus") else None
                self._sig = sig
            return self._kb

    def search_menu(self, query: str, k: int = 5) -> list[MenuHit]:
        kb = self._current()
        return kb.search_menu(query, k) if kb else []

    def get_menu(self, menu_id: str) -> MenuItem | None:
        kb = self._current()
        return kb.get_menu(menu_id) if kb else None

    def list_menu(self) -> list[MenuItem]:
        kb = self._current()
        return kb.list_menu() if kb else []

    def recent_reviews(self, limit: int = 10, since: datetime | None = None) -> list[Review]:
        kb = self._current()
        return kb.recent_reviews(limit, since) if kb else []
