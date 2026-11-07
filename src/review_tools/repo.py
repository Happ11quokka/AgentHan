"""리뷰 저장소 인터페이스. 지금은 스냅샷 파일, 나중에는 팀 DB 구현으로 바꾼다."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path


class ReviewRepo(ABC):
    @abstractmethod
    def get_reviews(
        self, store_id: str, start: date | None = None, end: date | None = None
    ) -> list[dict]:
        """store_id 매장의 리뷰를 최신순으로. start/end는 포함 범위, None이면 제한 없음."""


class SnapshotRepo(ReviewRepo):
    """data/snapshots/*.jsonl 을 읽는다. store_id는 매장명 앞부분만 줘도 된다 (예: '홍만당')."""

    def __init__(self, path: str | Path):
        with Path(path).open(encoding="utf-8") as f:
            self.rows = [json.loads(line) for line in f]

    def _match_store(self, store_id: str) -> str:
        names = sorted({r["store"] for r in self.rows})
        hits = [n for n in names if n == store_id] or [n for n in names if n.startswith(store_id)]
        if len(hits) != 1:
            raise KeyError(f"매장을 하나로 특정할 수 없음: {store_id} → {hits or names}")
        return hits[0]

    def get_reviews(self, store_id, start=None, end=None):
        store = self._match_store(store_id)
        out = []
        for r in self.rows:
            if r["store"] != store or not r.get("visit_date"):
                continue
            d = date.fromisoformat(r["visit_date"])
            if (start and d < start) or (end and d > end):
                continue
            out.append(r)
        return sorted(out, key=lambda r: (r["visit_date"], r["review_id"]), reverse=True)
