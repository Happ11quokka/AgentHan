"""가게 원본 문서 읽기·쓰기: 전화 가게(phone 형식)와 비서 가게 파일(DummyStore) 사본."""

from __future__ import annotations

import logging
from pathlib import Path

from psycopg.types.json import Jsonb

from agenthan.db.conn import guarded

log = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _source_key(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def phone_store_raw(fixture: Path, url: str | None = None) -> dict | None:
    """전화 가게 JSON(fixtures/sample_store.json 등)과 같은 원본을 DB에서. 없거나 꺼지면 None."""
    key = _source_key(fixture)

    def work(conn):
        row = conn.execute(
            "SELECT raw FROM stores WHERE source_path = %s AND source_format = 'phone'"
            " AND raw IS NOT NULL ORDER BY id LIMIT 1",
            (key,),
        ).fetchone()
        return row[0] if row else None

    return guarded(work, None, url)


def save_assistant_doc(doc_key: str, data: dict, url: str | None = None) -> bool:
    """비서 대화의 가게 파일 한 벌을 assistant_store_docs에 (실패해도 파일은 이미 있음)."""
    shop_id = str((data.get("shop") or {}).get("id") or "")

    def work(conn):
        conn.execute(
            "INSERT INTO assistant_store_docs (doc_key, shop_id, data) VALUES (%s, %s, %s)"
            " ON CONFLICT (doc_key) DO UPDATE SET shop_id = EXCLUDED.shop_id,"
            " data = EXCLUDED.data, updated_at = now()",
            (doc_key, shop_id, Jsonb(data)),
        )
        return True

    return guarded(work, False, url)


def load_assistant_doc(doc_key: str, url: str | None = None) -> dict | None:
    def work(conn):
        row = conn.execute(
            "SELECT data FROM assistant_store_docs WHERE doc_key = %s", (doc_key,)
        ).fetchone()
        return row[0] if row else None

    return guarded(work, None, url)
