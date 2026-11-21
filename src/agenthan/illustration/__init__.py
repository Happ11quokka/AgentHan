"""메뉴 대표 그림: 사진이 없는 메뉴 자리에 보여 줄 미리 만든 SVG 일러스트(spec: menu-illustration).

- library: 카탈로그(catalog/*.json)와 화면 주소(/illustrations/<id>.svg)
- matcher: 메뉴 이름(+분류) → 그림 id (정확한 이름 → 키워드 → CLOVA 임베딩 → 분류 기본 그림)
- embedding_index: 그림 이름·별칭의 미리 계산한 임베딩(catalog_embeddings.json).
  uv run --env-file .env python -m agenthan.illustration.build_index
- art: 그림·카탈로그·갤러리 생성기(빌드 때만). uv run python -m agenthan.illustration.art
"""

from agenthan.illustration.library import illustration_url
from agenthan.illustration.matcher import assign_missing, pick_illustration

__all__ = ["assign_missing", "illustration_url", "pick_illustration"]
