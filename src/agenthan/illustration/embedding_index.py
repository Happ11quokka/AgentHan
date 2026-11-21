"""대표 그림 카탈로그의 CLOVA 임베딩 인덱스(티켓 06). 장소 기능 인덱스(place_matcher)와 같은 방식.

그림마다 이름·별칭을 한 글로 묶어 미리 임베딩해 catalog_embeddings.json에 둔다(저장소에 커밋).
매처는 메뉴 이름 하나만 실행 중에 임베딩하고 이 파일과 코사인으로 비교한다.

그림을 더하거나 이름·별칭을 고친 뒤(병합 뒤에도) 다시 만든다. 글이 그대로인 그림은 저장된
벡터를 다시 쓰고, 새로 바뀐 글만 임베딩하며, 임베딩한 그림 id를 출력한다:

    uv run --env-file .env python -m agenthan.illustration.build_index

인덱스에 없거나 글이 바뀐 그림은 매처가 다시 만들 때까지 임베딩 후보에서 빠진다(키워드·분류
기본 그림은 그대로). tests/test_illustration_library.py가 인덱스가 카탈로그와 맞는지 확인한다.
"""

import functools
import json
from collections.abc import Callable
from pathlib import Path

from agenthan.core.embedding import ClovaEmbeddingError, embed_text, validate_embedding
from agenthan.illustration.library import load_catalog

INDEX_PATH = Path(__file__).with_name("catalog_embeddings.json")
MODEL = "CLOVA embedding v2"


def embedding_text(row: dict) -> str:
    """그림 하나를 임베딩할 글: 이름과 별칭을 한 줄씩(겹치면 한 번)."""
    return "\n".join(dict.fromkeys([row["name"], *row["aliases"]]))


@functools.cache
def stored() -> dict[str, dict]:
    """인덱스 파일 그대로: {그림 id: {"text", "vector"}}. 파일이 없거나 깨졌으면 빈 dict.

    파일을 바꾼 뒤에는 stored.cache_clear()."""
    try:
        items = json.loads(INDEX_PATH.read_text(encoding="utf-8"))["items"]
        return {
            key: {"text": item["text"], "vector": validate_embedding(item["vector"])}
            for key, item in items.items()
            if isinstance(item.get("text"), str)
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ClovaEmbeddingError):
        return {}


def load_index() -> dict[str, list[float]]:
    """지금 카탈로그와 글이 같은 벡터만: {그림 id: 벡터}. 낡은 줄과 없는 그림은 뺀다."""
    items = stored()
    return {
        row["id"]: items[row["id"]]["vector"]
        for row in load_catalog()
        if row["id"] in items and items[row["id"]]["text"] == embedding_text(row)
    }


def _write(items: dict[str, dict]) -> None:
    """id 순으로 그림 하나에 한 줄(병합 diff가 그림 단위로 보이게). 임시 파일 뒤 교체."""
    lines = [
        f"    {json.dumps(key)}: "
        + json.dumps(items[key], ensure_ascii=False, separators=(",", ":"))
        for key in sorted(items)
    ]
    text = (
        f'{{\n  "model": {json.dumps(MODEL)},\n  "items": {{\n' + ",\n".join(lines) + "\n  }\n}\n"
    )
    temporary = INDEX_PATH.with_suffix(".json.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(INDEX_PATH)
    finally:
        temporary.unlink(missing_ok=True)
        stored.cache_clear()


def build_index(embed: Callable[[str], list[float]] | None = None) -> list[str]:
    """카탈로그 전체 id의 벡터를 쓴다. 저장된 글과 같으면 그 벡터를 다시 쓰고 새 글만 임베딩.

    임베딩한 그림 id를 돌려준다. 중간에 실패해도 그때까지 임베딩한 것은 저장하고 예외를 다시
    던진다(다시 돌리면 남은 것만 임베딩). 카탈로그에서 빠진 그림의 줄은 지운다."""
    embed = embed or embed_text  # 기본은 CLOVA 임베딩 v2(CLOVASTUDIO_API_KEY)
    rows = sorted(load_catalog(), key=lambda row: row["id"])
    old = stored()
    reuse = {item["text"]: item["vector"] for item in old.values()}
    ids = {row["id"] for row in rows}
    items = {key: item for key, item in old.items() if key in ids}
    embedded: list[str] = []
    try:
        for row in rows:
            text = embedding_text(row)
            if text not in reuse:
                reuse[text] = validate_embedding(embed(text))
                embedded.append(row["id"])
            items[row["id"]] = {"text": text, "vector": reuse[text]}
    finally:
        if items != old or not INDEX_PATH.is_file():
            _write(items)
    return embedded
