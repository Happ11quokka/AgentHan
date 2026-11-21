"""대표 그림 카탈로그(실행 중에 읽는 쪽).

생성기(`uv run python -m agenthan.illustration.art`)가 그림 가족마다 catalog/<가족>.json을 쓰고
SVG를 web/public/illustrations/<id>.svg로 낸다. 여기서는 그 JSON을 모두 읽어 한 목록으로 본다.

실제 가게 메뉴 이름은 커밋하지 않는다(PRD §0). 그 이름 → 그림 id 짝은 git 밖 파일
data/illustration_aliases.json(PRIVATE_ALIASES_PATH, 환경 변수 AGENTHAN_ILLUSTRATION_ALIASES로
바꿀 수 있음)에서 실행 중에 읽는다. 파일이 없으면 조용히 건너뛴다.
"""

import functools
import json
import os
from collections.abc import Callable
from pathlib import Path

CATALOG_DIR = Path(__file__).with_name("catalog")
# 글 → 임베딩 벡터(운영은 CLOVA 임베딩 v2 core.embedding.embed_text, 테스트는 가짜)
Embed = Callable[[str], list[float]]
# 메뉴 기록에서 대표 그림 id를 담는 칸
ILLUSTRATION_KEY = "illustration"
# 실제 가게 메뉴 이름 → 그림 id. {"메뉴 이름": "그림 id", ...} JSON 객체 하나. git 밖(data/).
PRIVATE_ALIASES_ENV = "AGENTHAN_ILLUSTRATION_ALIASES"
PRIVATE_ALIASES_PATH = Path(__file__).resolve().parents[3] / "data" / "illustration_aliases.json"
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


def private_aliases() -> dict[str, str]:
    """git 밖 별칭 파일의 {메뉴 이름: 그림 id}. 카탈로그에 없는 id는 뺀다. 파일이 없거나 깨졌으면
    빈 dict(예외 없음). 파일을 고치면 다음 호출부터 새 내용을 읽는다."""
    path = Path(os.environ.get(PRIVATE_ALIASES_ENV) or PRIVATE_ALIASES_PATH)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    return dict(_read_private_aliases(str(path), stamp, catalog_ids()))


@functools.cache
def _read_private_aliases(
    path: str, _stamp: int, known: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(raw, dict):
        return ()
    return tuple(
        (name, key)
        for name, key in sorted(raw.items())
        if isinstance(name, str) and isinstance(key, str) and key in known
    )
