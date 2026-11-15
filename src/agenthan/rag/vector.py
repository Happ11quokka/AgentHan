"""벡터 검색 자리. CLOVA 임베딩 v2(bge-m3, 1024차원, core.embedding)로 붙인다.

- CLOVASTUDIO_API_KEY가 없으면 꺼진 상태로 아무것도 돌려주지 않는다(검색은 나머지 단계로 동작).
- 지금은 메뉴 임베딩을 메모리에만 둔다. 나중에 pgvector나 SQLite 캐시(내용 해시 → 벡터)로 옮긴다.
- 호출이 실패하면 그 뒤로는 끄고 경고만 남긴다(전화 응답이 멈추지 않게).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable

from agenthan.rag import rules

log = logging.getLogger(__name__)
Embed = Callable[[str], list[float]]


def _default_embed() -> Embed | None:
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        return None
    from agenthan.core.embedding import embed_text

    return embed_text


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class VectorIndex:
    def __init__(self, docs: dict[str, str], embed: Embed | None | bool = None) -> None:
        """embed=None이면 키가 있을 때만 CLOVA 임베딩을 쓴다. False면 끈다.
        함수를 넘기면 그걸 쓴다(테스트용)."""
        self.docs = docs
        if embed is False:
            self.embed = None
        else:
            self.embed = embed if callable(embed) else _default_embed()
        self._vectors: dict[str, list[float]] | None = None

    @property
    def enabled(self) -> bool:
        return self.embed is not None

    def _build(self) -> None:
        self._vectors = {
            i: _unit(self.embed(text)) for i, text in self.docs.items() if text.strip()
        }

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """→ [(메뉴 id, 코사인 유사도)] 기준(VECTOR_MIN_COSINE) 이상만."""
        if not self.enabled or not query.strip():
            return []
        try:
            if self._vectors is None:
                self._build()
            q = _unit(self.embed(query))
        except Exception as error:  # 키·네트워크 문제 → 끄고 나머지 단계로
            log.warning("벡터 검색을 끕니다: %s", type(error).__name__)
            self.embed = None
            return []
        scored = [
            (i, sum(a * b for a, b in zip(q, v, strict=True))) for i, v in self._vectors.items()
        ]
        scored = [(i, s) for i, s in scored if s >= rules.VECTOR_MIN_COSINE]
        return sorted(scored, key=lambda t: t[1], reverse=True)[:k]
