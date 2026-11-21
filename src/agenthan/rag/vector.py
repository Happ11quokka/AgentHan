"""벡터 검색: CLOVA 임베딩 v2(bge-m3, 1024차원). 호출은 팀 공통 클라이언트 core.embedding을 쓴다
(sdk.md 3장 계약: 429·5xx 최대 2회 재시도, 1024차원 검사).

- CLOVASTUDIO_API_KEY가 없으면 꺼진 상태로 아무것도 돌려주지 않는다(검색은 나머지 단계로 동작).
- 임베딩은 '내용 해시 → 벡터'로 캐시 파일에 저장한다. 메뉴 글이 안 바뀌면 다시 부르지 않는다.
  질문 임베딩도 같은 파일에 캐시한다(같은 질문을 다시 물으면 호출 없음).
- 호출이 실패하면(키 오류·네트워크·429) FAIL_COOLDOWN초 동안 끄고 경고만 남긴다.
  그동안은 별칭·BM25만으로 답한다(전화 응답이 멈추지 않게). 시간이 지나면 다시 시도한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from collections.abc import Callable
from pathlib import Path

from agenthan.rag import rules
from agenthan.rag.repository import REPO_ROOT

log = logging.getLogger(__name__)
Embed = Callable[[str], list[float]]

MODEL_ID = "clova-embedding-v2/bge-m3"  # 캐시 열쇠에 넣어서 모델이 바뀌면 캐시가 자동으로 무효
CACHE_DIR = REPO_ROOT / "data" / "cache"  # data/는 gitignore 대상
FAIL_COOLDOWN = 60.0  # 호출 실패 후 벡터를 쉬는 시간(초). CLOVA 분당 호출 제한(429)을 고려


def default_cache_path(data_dir: Path) -> Path:
    """data/clean → data/cache/clean_embeddings.json
    fixtures/dummy_store → data/cache/dummy_store_embeddings.json"""
    return CACHE_DIR / f"{Path(data_dir).name}_embeddings.json"


def _default_embed() -> Embed | None:
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        return None
    from agenthan.core.embedding import embed_text

    return embed_text


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def content_hash(text: str) -> str:
    return hashlib.sha256(f"{MODEL_ID}\n{text}".encode()).hexdigest()


class EmbeddingCache:
    """{"model": ..., "vectors": {내용 해시: [1024개 실수]}} JSON 파일."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.vectors: dict[str, list[float]] = {}
        self.dirty = False
        if path and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("model") == MODEL_ID:
                    self.vectors = raw.get("vectors", {})
            except (OSError, ValueError):
                log.warning("임베딩 캐시를 읽지 못해 새로 만듭니다")

    def get(self, text: str) -> list[float] | None:
        return self.vectors.get(content_hash(text))

    def put(self, text: str, vector: list[float]) -> None:
        self.vectors[content_hash(text)] = [round(x, 6) for x in vector]
        self.dirty = True

    def save(self) -> None:
        if not self.path or not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"model": MODEL_ID, "vectors": self.vectors}), encoding="utf-8")
        tmp.replace(self.path)  # 다 쓴 뒤 한 번에 바꾼다(중간에 실패해도 이전 캐시 유지)
        self.dirty = False


class VectorIndex:
    def __init__(
        self,
        docs: dict[str, str],
        embed: Embed | None | bool = None,
        cache_path: Path | None = None,
    ) -> None:
        """docs: {메뉴 id: 임베딩할 글}.
        embed=None이면 키가 있을 때만 CLOVA 임베딩을 쓴다. False면 끈다.
        함수를 넘기면 그걸 쓴다(테스트용).
        cache_path가 있으면 그 파일에 임베딩을 캐시한다."""
        self.docs = docs
        if embed is False:
            self.embed = None
        else:
            self.embed = embed if callable(embed) else _default_embed()
        self.cache = EmbeddingCache(cache_path)
        self.calls = 0  # 이 객체가 실제로 임베딩 API를 부른 횟수
        self._vectors: dict[str, list[float]] | None = None
        self._paused_until = 0.0

    @property
    def enabled(self) -> bool:
        return self.embed is not None

    @property
    def paused(self) -> bool:
        return time.monotonic() < self._paused_until

    def _vector(self, text: str) -> list[float]:
        cached = self.cache.get(text)
        if cached is not None:
            return cached
        v = self.embed(text)
        self.calls += 1
        self.cache.put(text, v)
        return v

    def warm(self) -> int:
        """메뉴 임베딩을 모두 준비(캐시에 없으면 호출)하고 캐시 파일에 저장. → 이번에 부른 횟수."""
        if not self.enabled:
            return 0
        before = self.calls
        try:
            self._vectors = {
                i: _unit(self._vector(text)) for i, text in self.docs.items() if text.strip()
            }
        finally:
            self.cache.save()
        return self.calls - before

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """→ [(메뉴 id, 코사인 유사도)] 기준(VECTOR_MIN_COSINE) 이상만."""
        if not self.enabled or self.paused or not query.strip():
            return []
        try:
            if self._vectors is None:
                self.warm()
            q = _unit(self._vector(query))
            self.cache.save()
        except Exception as error:  # 키·네트워크·429 → 잠시 끄고 나머지 단계로
            log.warning("벡터 검색을 %d초 쉽니다: %s", FAIL_COOLDOWN, type(error).__name__)
            self.cache.save()
            self._paused_until = time.monotonic() + FAIL_COOLDOWN
            return []
        scored = [
            (i, sum(a * b for a, b in zip(q, v, strict=True))) for i, v in self._vectors.items()
        ]
        scored = [(i, s) for i, s in scored if s >= rules.VECTOR_MIN_COSINE]
        return sorted(scored, key=lambda t: t[1], reverse=True)[:k]


# ------------------------------------------------------------------ PostgreSQL + pgvector
def _parse_vector(value) -> list[float]:
    """pgvector 값 → 실수 목록 (psycopg는 '[1,2,...]' 글로 준다)."""
    return json.loads(value) if isinstance(value, str) else [float(x) for x in value]


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


class PgEmbeddingCache:
    """embedding_cache 표를 쓰는 캐시. EmbeddingCache와 같은 get·put·save."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url

    def get(self, text: str) -> list[float] | None:
        from agenthan.db.conn import connect

        with connect(self.url) as conn:
            row = conn.execute(
                "SELECT embedding FROM embedding_cache WHERE content_hash = %s",
                (content_hash(text),),
            ).fetchone()
        return _parse_vector(row[0]) if row else None

    def put(self, text: str, vector: list[float]) -> None:
        from agenthan.db.conn import connect

        with connect(self.url) as conn:
            conn.execute(
                "INSERT INTO embedding_cache (content_hash, model, embedding) VALUES (%s, %s, %s)"
                " ON CONFLICT (content_hash) DO NOTHING",
                (content_hash(text), MODEL_ID, _literal(vector)),
            )

    def save(self) -> None:  # 표에 바로 쓰므로 따로 저장할 것이 없다
        return


class PgVectorIndex(VectorIndex):
    """벡터를 embeddings 표(pgvector, HNSW cosine)에 두고 유사도도 DB에서 계산한다.

    - warm(): 메뉴 글이 바뀐 것만 임베딩해서(캐시 표 우선) embeddings에 넣고, 없어진 메뉴는 지운다.
    - search(): 질문 하나만 임베딩(캐시 표 우선) → `ORDER BY embedding <=> 질문` 상위 k개.
    - search_kind(): 같은 방식으로 리뷰(review)·가게 정보 조각(doc)도 찾는다.
    실패하면 VectorIndex와 똑같이 FAIL_COOLDOWN초 쉬고 [](나머지 검색 단계로)."""

    def __init__(
        self,
        docs: dict[str, str],
        store_id: str,
        embed: Embed | None | bool = None,
        url: str | None = None,
    ) -> None:
        super().__init__(docs, embed, cache_path=None)
        self.store_id = store_id
        self.url = url
        self.cache = PgEmbeddingCache(url)

    def warm(self) -> int:
        if not self.enabled:
            return 0
        from agenthan.db.conn import connect

        before = self.calls
        with connect(self.url) as conn:
            have = dict(
                conn.execute(
                    "SELECT ref_id, content_hash FROM embeddings"
                    " WHERE store_id = %s AND kind = 'menu'",
                    (self.store_id,),
                ).fetchall()
            )
        rows = []
        for ref_id, text in self.docs.items():
            if not text.strip() or have.get(ref_id) == content_hash(text):
                continue
            rows.append((ref_id, text, self._vector(text)))
        with connect(self.url) as conn, conn.transaction():
            for ref_id, text, vector in rows:
                conn.execute(
                    "INSERT INTO embeddings (store_id, kind, ref_id, model, content_hash, content,"
                    " embedding) VALUES (%s, 'menu', %s, %s, %s, %s, %s)"
                    " ON CONFLICT (store_id, kind, ref_id) DO UPDATE SET model = EXCLUDED.model,"
                    " content_hash = EXCLUDED.content_hash, content = EXCLUDED.content,"
                    " embedding = EXCLUDED.embedding, updated_at = now()",
                    (self.store_id, ref_id, MODEL_ID, content_hash(text), text, _literal(vector)),
                )
            conn.execute(
                "DELETE FROM embeddings WHERE store_id = %s AND kind = 'menu'"
                " AND NOT (ref_id = ANY(%s))",
                (self.store_id, list(self.docs)),
            )
        self._vectors = {}  # 준비 끝 표시 (벡터는 DB에 있다)
        return self.calls - before

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        if self._vectors is None and self.enabled and not self.paused:
            try:
                self.warm()
            except Exception as error:
                self._pause(error)
                return []
        return self.search_kind("menu", query, k, rules.VECTOR_MIN_COSINE)

    def search_kind(
        self, kind: str, query: str, k: int = 10, min_cosine: float = 0.0
    ) -> list[tuple[str, float]]:
        """→ [(ref_id, 코사인 유사도)] 높은 순."""
        if not self.enabled or self.paused or not query.strip():
            return []
        from agenthan.db.conn import connect

        try:
            q = _literal(self._vector(query))
            with connect(self.url) as conn:
                rows = conn.execute(
                    "SELECT ref_id, 1 - (embedding <=> %s::vector) AS score FROM embeddings"
                    " WHERE store_id = %s AND kind = %s"
                    " ORDER BY embedding <=> %s::vector LIMIT %s",
                    (q, self.store_id, kind, q, k),
                ).fetchall()
        except Exception as error:
            self._pause(error)
            return []
        return [(ref_id, float(score)) for ref_id, score in rows if score >= min_cosine]

    def _pause(self, error: Exception) -> None:
        log.warning("벡터 검색(DB)을 %d초 쉽니다: %s", FAIL_COOLDOWN, type(error).__name__)
        self._paused_until = time.monotonic() + FAIL_COOLDOWN
