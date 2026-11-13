"""Match owner requests against the local SmartPlace feature catalog.

Only build-index embeds the catalog. Matching embeds just the query and reads the
precomputed index; unavailable credentials/API/index fall back to keywords.
"""

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agenthan.core.embedding import (
    ClovaEmbeddingError,
    embed_text,
    validate_embedding,
)

CATALOG_PATH = Path(__file__).with_name("place_features.json")
INDEX_PATH = Path(__file__).with_name("place_feature_embeddings.json")
KEYWORD_WEIGHT = 0.3
EMBEDDING_WEIGHT = 0.7
RELEVANCE_THRESHOLD = 0.48


@dataclass(frozen=True)
class FeatureMatch:
    feature_id: str
    score: float
    keyword_score: float
    embedding_score: float | None
    matched_terms: list[str]
    keyword_only: bool = False


def load_features() -> list[dict]:
    """Load the catalog as independent dictionaries for downstream input flows."""
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def get_feature(feature_id: str) -> dict | None:
    return next((f for f in load_features() if f["id"] == feature_id), None)


def catalog_sha256() -> str:
    """Hash the exact catalog bytes, including whitespace and all input fields."""
    return hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()


def feature_embedding_text(feature: dict) -> str:
    return "\n".join(
        [feature["name_ko"], feature["summary_ko"]]
        + feature["keywords_ko"]
        + feature.get("keywords_en", [])  # Korean-only catalog; review entries still carry English
    )


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _keyword_score(text: str, feature: dict) -> tuple[float, list[str]]:
    normalized = _normalize(text)
    compact = re.sub(r"[^\w]", "", normalized)
    matched = []
    for term in dict.fromkeys(feature["keywords_ko"] + feature.get("keywords_en", [])):
        normalized_term = _normalize(term)
        if re.search(r"[가-힣]", normalized_term):
            # Korean particles stay attached; spacing and punctuation may vary.
            found = re.sub(r"[^\w]", "", normalized_term) in compact
        else:
            words = re.findall(r"\w+\+?", normalized_term)
            # ASCII boundaries allow Korean particles but reject English substrings.
            pattern = r"(?<![a-z0-9])" + r"[\W_]*".join(map(re.escape, words)) + r"(?![a-z0-9])"
            found = bool(words) and re.search(pattern, normalized) is not None
        if found:
            matched.append(term)
    return (min(1.0, 0.7 + 0.1 * (len(matched) - 1)) if matched else 0.0), matched


def _cosine(left: list[float], right: list[float]) -> float:
    left_norm, right_norm = math.hypot(*left), math.hypot(*right)
    value = math.fsum((a / left_norm) * (b / right_norm) for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, value))


def _load_index(features: list[dict]) -> dict[str, list[float]]:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    if index["catalog_sha256"] != catalog_sha256():
        raise ValueError("Embedding index does not match the catalog")
    embeddings = index["embeddings"]
    if not isinstance(embeddings, dict):
        raise ValueError("Embedding index must map feature IDs to vectors")
    if set(embeddings) != {feature["id"] for feature in features}:
        raise ValueError("Embedding index does not cover the catalog")
    return {key: validate_embedding(value) for key, value in embeddings.items()}


def rank_features(
    text: str, embed: Callable[[str], list[float]] | None = None
) -> list[FeatureMatch]:
    """Every feature ranked by score, no threshold (candidates for select_feature).

    Scores are the weighted sum of keyword evidence and cosine similarity. With
    no usable embedding, the full keyword score is used (keyword_only=True).
    Invalid or stale indexes also use fallback rather than unrelated embeddings.
    """
    if not text.strip():
        return []
    features = load_features()
    query = None
    index = {}
    try:
        index = _load_index(features)
        query = validate_embedding((embed if embed is not None else embed_text)(text))
    except (ClovaEmbeddingError, OSError, ValueError, KeyError, TypeError):
        pass
    matches = []
    for feature in features:
        keyword_score, terms = _keyword_score(text, feature)
        embedding_score = _cosine(query, index[feature["id"]]) if query is not None else None
        score = (
            KEYWORD_WEIGHT * keyword_score + EMBEDDING_WEIGHT * embedding_score
            if embedding_score is not None
            else keyword_score
        )
        matches.append(
            FeatureMatch(feature["id"], score, keyword_score, embedding_score, terms, query is None)
        )
    return sorted(matches, key=lambda match: (-match.score, match.feature_id))


def match_features(
    text: str, top_k: int = 3, embed: Callable[[str], list[float]] | None = None
) -> list[FeatureMatch]:
    """Features above RELEVANCE_THRESHOLD; embedding_score=None marks keyword fallback."""
    if top_k <= 0:
        return []
    ranked = rank_features(text, embed)
    return [m for m in ranked if m.score > RELEVANCE_THRESHOLD][:top_k]


def build_index() -> None:
    """Generate all vectors via CLOVA, then atomically replace the local index."""
    digest = catalog_sha256()
    features = load_features()
    embeddings = {
        feature["id"]: validate_embedding(embed_text(feature_embedding_text(feature)))
        for feature in features
    }
    if catalog_sha256() != digest:
        raise ValueError("Catalog changed during index generation; run build-index again")
    data = {"catalog_sha256": digest, "embeddings": embeddings}
    temporary = INDEX_PATH.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(INDEX_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build-index"])
    parser.parse_args()
    try:
        build_index()
    except (ClovaEmbeddingError, OSError, ValueError):
        print(
            "Index generation failed; check API access and catalog. Existing index kept.",
            file=sys.stderr,
        )
        return 1
    print("Built CLOVA embedding index for the feature catalog.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
