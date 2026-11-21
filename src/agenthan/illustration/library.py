"""대표 그림 카탈로그(실행 중에 읽는 쪽).

생성기(`uv run python -m agenthan.illustration.art`)가 그림 가족마다 catalog/<가족>.json을 쓰고
SVG를 web/public/illustrations/<id>.svg로 낸다. 여기서는 그 JSON을 모두 읽어 한 목록으로 본다.
"""

import functools
import json
from pathlib import Path

CATALOG_DIR = Path(__file__).with_name("catalog")
# 웹 정적 파일 경로(web/public/illustrations → 빌드 뒤 /illustrations/<id>.svg)
URL_PREFIX = "/illustrations/"

# 분류 기본 그림 8개(spec). 카탈로그 줄의 category는 이 중 하나.
CATEGORIES = ("cake", "bakery", "savory", "soup", "coffee", "tea", "ade", "other")
DEFAULT_IDS = {category: f"default_{category}" for category in CATEGORIES}


@functools.cache
def load_catalog() -> tuple[dict, ...]:
    """카탈로그 전체: {id, name, aliases, keywords, category, recipe}. 고치지 말고 읽기만."""
    rows: list[dict] = []
    for path in sorted(CATALOG_DIR.glob("*.json")):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    return tuple(rows)


def catalog_ids() -> frozenset[str]:
    return frozenset(row["id"] for row in load_catalog())


def illustration_url(illustration_id: object) -> str | None:
    """화면에 보낼 그림 주소. 카탈로그에 없는 id(그림을 지운 경우 등)면 None."""
    if not isinstance(illustration_id, str) or illustration_id not in catalog_ids():
        return None
    return f"{URL_PREFIX}{illustration_id}.svg"
