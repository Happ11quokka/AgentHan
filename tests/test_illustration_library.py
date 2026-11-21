"""대표 그림 라이브러리 무결성: 카탈로그의 모든 그림이 화면에 깨지지 않고 뜬다."""

import re
import xml.etree.ElementTree as ET

import pytest

from agenthan.illustration import embedding_index, library
from agenthan.illustration.art import build
from agenthan.illustration.matcher import normalize

SVG_NS = "{http://www.w3.org/2000/svg}"
CATALOG = library.load_catalog()


def test_the_eight_category_defaults_exist():
    ids = {row["id"] for row in CATALOG}
    assert set(library.DEFAULT_IDS.values()) <= ids
    assert len(library.DEFAULT_IDS) == 8


def test_ids_are_unique_file_names_and_categories_are_known():
    ids = [row["id"] for row in CATALOG]
    assert len(ids) == len(set(ids))
    for row in CATALOG:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", row["id"])
        assert row["category"] in library.CATEGORIES
        assert row["name"].strip()


@pytest.mark.parametrize("row", CATALOG, ids=lambda row: row["id"])
def test_every_illustration_is_a_small_square_svg_without_text(row):
    path = build.SVG_DIR / f"{row['id']}.svg"
    assert path.is_file()
    assert path.stat().st_size <= build.MAX_SVG_BYTES
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg"
    x, y, width, height = (float(v) for v in root.attrib["viewBox"].split())
    assert (x, y) == (0, 0) and width == height > 0
    assert root.find(f".//{SVG_NS}text") is None  # 그림에 글자를 넣지 않는다


def test_each_exact_name_and_keyword_belongs_to_one_illustration():
    """가족끼리 같은 이름·키워드를 쓰면 어느 그림이 붙을지 모호하다."""
    names: dict[str, str] = {}
    keywords: dict[str, str] = {}
    for row in CATALOG:
        for name in {normalize(n) for n in (row["name"], *row["aliases"])}:
            assert names.setdefault(name, row["id"]) == row["id"], name
        for word in {normalize(w) for w in row["keywords"]}:
            assert word, row["id"]
            assert keywords.setdefault(word, row["id"]) == row["id"], word


def test_generated_files_match_the_generator():
    """레시피를 고치고 `uv run python -m agenthan.illustration.art`를 다시 돌렸는지."""
    expected = build.outputs()
    for path, text in expected.items():
        assert path.read_text(encoding="utf-8") == text, f"{path.name}: 생성기를 다시 돌리세요"
    on_disk = set(build.SVG_DIR.glob("*.svg")) | set(library.CATALOG_DIR.glob("*.json"))
    assert on_disk == set(expected), "목록에 없는 생성물이 남아 있어요"


def test_the_embedding_index_matches_the_catalog():
    """그림을 더하거나 이름·별칭을 고쳤으면(병합 뒤에도) 임베딩 인덱스를 다시 만든다:
    uv run --env-file .env python -m agenthan.illustration.build_index"""
    stored = embedding_index.stored()
    assert set(stored) == {row["id"] for row in CATALOG}, "인덱스를 다시 만드세요"
    stale = [
        row["id"]
        for row in CATALOG
        if stored[row["id"]]["text"] != embedding_index.embedding_text(row)
    ]
    assert stale == [], "이름·별칭이 바뀐 그림이 있어요. 인덱스를 다시 만드세요"
