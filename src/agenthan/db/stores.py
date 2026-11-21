"""가게 원본 문서 읽기·쓰기: 전화 가게(phone 형식), 비서 가게 파일(DummyStore) 사본,
게시된 전화용 가게."""

from __future__ import annotations

import logging
from collections.abc import Collection
from pathlib import Path

from psycopg.types.json import Jsonb

from agenthan.db.conn import guarded

log = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[3]

# 게시된 전화용 가게(전화 메뉴 티켓 03): stores의 한 줄. raw = 게시 값(serve.phone_store_doc).
# 시드(db.seed)는 이 id를 쓰지 않으므로 덮어쓰지 않는다.
PUBLISHED_STORE_ID = "phone_published"
PUBLISHED_FORMAT = "published"
# 게시 메뉴 한 벌의 벡터 자리(리뷰): 메뉴 한 벌마다 stores 한 줄("phone_published@<메뉴 해시>",
# 형식 published_vectors)을 두고 메뉴 벡터(embeddings)가 그 id를 가리킨다. 한 자리를 여러 벌이
# 같이 쓰면 진행 중인 통화의 옛 메뉴가 첫 검색 때 새 메뉴 벡터를 덮어쓰고 지웠다. 서버가 들고
# 있지 않은 벌의 줄은 게시할 때 지운다(벡터도 같이 지워진다, ON DELETE CASCADE).
PUBLISHED_VECTORS_FORMAT = "published_vectors"


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


def load_published_phone_store(url: str | None = None) -> dict | None:
    """마지막으로 게시된 전화용 가게(게시 값). 아직 게시가 없거나 DB가 꺼지면 None."""

    def work(conn):
        row = conn.execute(
            "SELECT raw FROM stores WHERE id = %s", (PUBLISHED_STORE_ID,)
        ).fetchone()
        return row[0] if row and row[0] else None

    return guarded(work, None, url)


def published_vector_store_id(menus_key: str) -> str:
    """게시 메뉴 한 벌(메뉴 내용 해시)의 벡터 자리 id."""
    return f"{PUBLISHED_STORE_ID}@{menus_key[:16]}"


def ensure_published_vector_store(store_id: str, url: str | None = None) -> bool:
    """메뉴 한 벌의 벡터 자리(stores 한 줄)를 만들어 둔다. 실패하면 False(벡터 검색이 쉰다)."""

    def work(conn):
        conn.execute(
            "INSERT INTO stores (id, name, kind, source_format)"
            " VALUES (%s, '전화용 가게 메뉴 벡터', 'real', %s) ON CONFLICT (id) DO NOTHING",
            (store_id, PUBLISHED_VECTORS_FORMAT),
        )
        return True

    return guarded(work, False, url)


def prune_published_vector_stores(keep: Collection[str], url: str | None = None) -> bool:
    """`keep` 말고 남은 메뉴 벡터 자리를 지운다(그 벡터도 같이). 실패하면 False(다음 게시 때)."""

    def work(conn):
        conn.execute(
            "DELETE FROM stores WHERE source_format = %s AND NOT (id = ANY(%s))",
            (PUBLISHED_VECTORS_FORMAT, list(keep)),
        )
        return True

    return guarded(work, False, url)


def save_published_phone_store(doc: dict, url: str | None = None) -> bool:
    """게시 값을 남긴다(서버가 다시 떠도 이 메뉴로 시작).
    실패하면 False(전화는 메모리의 게시 값으로 계속)."""

    def work(conn):
        conn.execute(
            "INSERT INTO stores (id, name, kind, source_format, raw)"
            " VALUES (%s, '전화용 가게', 'real', %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET raw = EXCLUDED.raw, loaded_at = now()",
            (PUBLISHED_STORE_ID, PUBLISHED_FORMAT, Jsonb(doc)),
        )
        return True

    return guarded(work, False, url)
