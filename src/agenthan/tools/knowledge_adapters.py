"""StoreKnowledge(Part B 계약) → 리뷰 서브에이전트 어댑터.

Part A는 Part B 내부(`agenthan.rag.*`)를 import하지 않고 `core.contracts.StoreKnowledge`만 받는다.
진짜 구현 전에는 `core.fakes.FakeStoreKnowledge.from_fixture()`를 넣는다.

- KnowledgeReviewRepo: StoreKnowledge.recent_reviews() → agent.review의 ReviewRepo 행
- KnowledgeMenuCatalog: StoreKnowledge.list_menu() → agent.review의 MenuCatalog

리뷰 행 형식(agent.review 스냅샷과 같음):
    {review_id, store, category, visit_date, rating, text, features, has_owner_reply}
계약의 Review에는 [특징] 분석 결과와 답글 여부가 없다. features는 `features_for`(예: 분류기)로
채우고, 없으면 빈 리스트다. has_owner_reply는 None(모름)으로 두고 답글 저장소가 판단한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time

from agenthan.agent.review.repo import ReviewRepo
from agenthan.agent.review.topic import MenuCatalog
from agenthan.core.contracts import Review, StoreKnowledge

# recent_reviews(limit)는 최신순 상한이 필요하다. 데모 가게 규모(리뷰 수백 건)를 넉넉히 덮는다.
REVIEW_LIMIT = 1000


def review_row(
    review: Review,
    *,
    store: str,
    category: str = "",
    features_for: Callable[[str], list[dict]] | None = None,
) -> dict:
    return {
        "review_id": review.id,
        "store": store,
        "category": category,
        "visit_date": review.created_at.date().isoformat(),
        "rating": review.rating,
        "text": review.text,
        "features": features_for(review.text) if features_for else [],
        "has_owner_reply": None,
        "menu_ids": list(review.menu_ids),
    }


class KnowledgeReviewRepo(ReviewRepo):
    """가게 하나(StoreKnowledge 하나)의 리뷰. store_id는 행의 store 이름으로만 쓴다."""

    def __init__(
        self,
        knowledge: StoreKnowledge,
        *,
        store_name: str = "",
        category: str = "",
        features_for: Callable[[str], list[dict]] | None = None,
    ):
        self.knowledge = knowledge
        self.store_name = store_name
        self.category = category
        self.features_for = features_for

    def get_reviews(self, store_id, start: date | None = None, end: date | None = None):
        since = datetime.combine(start, time.min) if start else None
        rows = [
            review_row(
                r,
                store=self.store_name or store_id,
                category=self.category,
                features_for=self.features_for,
            )
            for r in self.knowledge.recent_reviews(limit=REVIEW_LIMIT, since=since)
        ]
        if end:
            rows = [r for r in rows if r["visit_date"] <= end.isoformat()]
        return sorted(rows, key=lambda r: (r["visit_date"], r["review_id"]), reverse=True)


class KnowledgeMenuCatalog(MenuCatalog):
    """메뉴 이름은 name_ko, 별칭은 aliases + 원어 이름(name_native)."""

    def __init__(self, knowledge: StoreKnowledge):
        super().__init__({})
        self.knowledge = knowledge

    def menus(self, store: str) -> list[dict]:
        out = []
        for m in self.knowledge.list_menu():
            aliases = list(m.aliases)
            if m.name_native and m.name_native not in aliases:
                aliases.append(m.name_native)
            out.append({"name": m.name_ko, "aliases": aliases})
        return out
