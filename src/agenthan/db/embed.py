"""벡터 채우기: 가게마다 메뉴·리뷰·가게 정보 조각을 CLOVA 임베딩 v2(bge-m3)로 embeddings 표에.

    uv run --env-file .env python -m agenthan.db.embed                 # 전부
    uv run --env-file .env python -m agenthan.db.embed --store real    # 한 가게만

- 같은 글은 embedding_cache 표에서 꺼내 쓴다(다시 안 부름). 글이 안 바뀐 행은 건너뛴다.
- 메뉴 글은 검색과 같은 글(RagStoreKnowledge._embed_text). 리뷰는 본문, 조각은 "제목: 본문".
- CLOVA 키 하나에 분당 약 60회 제한이라 새로 부를 때는 PACE_S초씩 쉰다.
"""

from __future__ import annotations

import argparse
import sys
import time

from agenthan.db.conn import connect
from agenthan.rag.vector import MODEL_ID, PgEmbeddingCache, _literal, content_hash

PACE_S = 1.05


def _targets(conn, store_id: str, url: str | None) -> dict[str, dict[str, str]]:
    from agenthan.rag import RagStoreKnowledge

    has_menu = conn.execute("SELECT 1 FROM menus WHERE store_id = %s LIMIT 1", (store_id,))
    menus: dict[str, str] = {}
    if has_menu.fetchone():  # 메뉴 없는 가게(플레이스 리포트)는 검색 색인을 만들 수 없다
        kb = RagStoreKnowledge.from_db(store_id, embed=False, url=url)
        menus = {i: kb._embed_text(i) for i in kb.records}
    reviews = dict(
        conn.execute(
            "SELECT id, text FROM reviews WHERE store_id = %s AND length(trim(text)) > 0",
            (store_id,),
        ).fetchall()
    )
    docs = {
        i: f"{title}: {body}" if title else body
        for i, title, body in conn.execute(
            "SELECT id, title, body FROM store_docs WHERE store_id = %s", (store_id,)
        ).fetchall()
    }
    return {"menu": menus, "review": reviews, "doc": docs}


def embed_store(
    conn, store_id: str, embed, cache: PgEmbeddingCache, pace: float = PACE_S
) -> tuple[int, int]:
    """→ (이번에 넣은 행 수, CLOVA를 부른 횟수). 캐시와 같은 DB(cache.url)를 쓴다."""
    written = calls = 0
    for kind, texts in _targets(conn, store_id, cache.url).items():
        have = dict(
            conn.execute(
                "SELECT ref_id, content_hash FROM embeddings WHERE store_id = %s AND kind = %s",
                (store_id, kind),
            ).fetchall()
        )
        for ref_id, text in texts.items():
            h = content_hash(text)
            if have.get(ref_id) == h:
                continue
            vector = cache.get(text)
            if vector is None:
                vector = embed(text)
                cache.put(text, vector)
                calls += 1
                time.sleep(pace)
            conn.execute(
                "INSERT INTO embeddings (store_id, kind, ref_id, model, content_hash, content,"
                " embedding) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (store_id, kind, ref_id) DO UPDATE SET model = EXCLUDED.model,"
                " content_hash = EXCLUDED.content_hash, content = EXCLUDED.content,"
                " embedding = EXCLUDED.embedding, updated_at = now()",
                (store_id, kind, ref_id, MODEL_ID, h, text, _literal(vector)),
            )
            written += 1
        conn.execute(
            "DELETE FROM embeddings WHERE store_id = %s AND kind = %s AND NOT (ref_id = ANY(%s))",
            (store_id, kind, list(texts)),
        )
    return written, calls


def main(argv: list[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", action="append", help="가게 id (여러 번 가능, 기본 전부)")
    args = parser.parse_args(argv)
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        print("CLOVASTUDIO_API_KEY가 없어 임베딩을 만들 수 없습니다")
        return 2
    from agenthan.core.embedding import embed_text

    cache = PgEmbeddingCache()
    code = 0
    with connect() as conn:
        stores = args.store or [r[0] for r in conn.execute("SELECT id FROM stores ORDER BY id")]
        for store_id in stores:
            try:
                written, calls = embed_store(conn, store_id, embed_text, cache)
            except Exception as error:  # 오류 내용에 키가 섞이지 않게 종류만
                print(f"  {store_id}: 실패 ({type(error).__name__})")
                code = 1
                continue
            print(f"  {store_id}: {written}행 넣음, CLOVA {calls}회")
        total = conn.execute(
            "SELECT kind, count(*) FROM embeddings GROUP BY kind ORDER BY kind"
        ).fetchall()
    print("embeddings:", ", ".join(f"{k} {n}" for k, n in total))
    return code


if __name__ == "__main__":
    sys.exit(main())
