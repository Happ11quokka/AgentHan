"""DummyStore → review_tools adapters: one shop data source for both sides.

- DummyStoreReviewRepo: review_tools.ReviewRepo over the store's reviews
- DummyStoreMenuCatalog: review_tools.MenuCatalog over the store's menus (read live, so a
  menu registered with upsert_menu is matched in review questions right away)
- DummyStoreReplyStore: review_tools MockReplyStore whose post() saves into the store
  (status saved_not_sent; nothing is sent to Naver)

Store review row (same as review_tools snapshots, also the target schema for crawled data):
    {review_id, visit_date, rating, text, features, has_owner_reply[, owner_reply]}
The adapter adds "store" (shop name) and "category" (shop category).
"""

from datetime import date, datetime

from agenthan.tools.dummy_store import DummyStore
from review_tools.replies import MockReplyStore
from review_tools.repo import ReviewRepo
from review_tools.topic import MenuCatalog


class DummyStoreReviewRepo(ReviewRepo):
    def __init__(self, store: DummyStore):
        self.store = store

    def get_reviews(self, store_id, start: date | None = None, end: date | None = None):
        data = self.store.read()
        shop = data["shop"]
        out = []
        for r in data["reviews"]:
            day = r.get("visit_date")
            if not day:
                continue
            if (start and day < start.isoformat()) or (end and day > end.isoformat()):
                continue
            out.append(
                {
                    **r,
                    "store": shop.get("name", store_id),
                    "category": shop.get("category", ""),
                }
            )
        return sorted(out, key=lambda r: (r["visit_date"], r["review_id"]), reverse=True)


class DummyStoreMenuCatalog(MenuCatalog):
    def __init__(self, store: DummyStore):
        super().__init__({})
        self.store = store

    def menus(self, store: str) -> list[dict]:
        return [
            {"name": m["name"], "aliases": list(m.get("aliases", []))}
            for m in self.store.read()["menus"]
        ]


class DummyStoreReplyStore(MockReplyStore):
    """Posted replies live in the store review (has_owner_reply + owner_reply)."""

    def __init__(self, store: DummyStore, today: date | None = None):
        super().__init__(None, today=today)
        self.store = store
        for r in store.read()["reviews"]:
            reply = r.get("owner_reply")
            if r.get("has_owner_reply") and isinstance(reply, dict) and reply.get("reply"):
                self.posted[r["review_id"]] = {
                    "reply": reply["reply"],
                    "posted_at": reply.get("posted_at", ""),
                }

    def has_reply(self, review: dict) -> bool:
        # The store always has an explicit has_owner_reply: no seed_days guessing.
        return review["review_id"] in self.posted or bool(review.get("has_owner_reply"))

    def post(self, review_id: str, reply: str) -> dict:
        data = self.store.read()
        record = next((r for r in data["reviews"] if r["review_id"] == review_id), None)
        if record is None:
            raise KeyError(review_id)
        rec = {"reply": reply, "posted_at": datetime.now().isoformat(timespec="seconds")}
        record.update(has_owner_reply=True, owner_reply={**rec, "status": "saved_not_sent"})
        self.store.write(data)
        self.posted[review_id] = rec
        return rec
