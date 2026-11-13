import http.client
import io
import json
import math
import os
import re
import time
import traceback
import urllib.error
import uuid
from collections import Counter
from pathlib import Path

import pytest

from agenthan.agent import place_matcher as matcher
from agenthan.core import embedding as clova

CASES_PATH = Path(__file__).parent / "data" / "eval" / "route.jsonl"
SUPPORTED: set[str] = set()  # Tool inputs live in tools/feature_tools.py
FEATURE_IDS = {
    "business_info",
    "menu",
    "business_hours",
    "talktalk",
    "order",
    "reservation",
    "smartcall",
    "search_keywords",
    "search_diagnosis",
    "coupon",
    "new_opening",
    "review",
    "naverpay_signup",
    "onsite_payment",
    "settlement",
    "place_plus",
    "statistics",
    "learning_support",
}


def cases():
    """The M0 feature-matching sentences (source m0) in the shared route file (42, Korean only)."""
    rows = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row["source"] == "m0"]


def vector(first=1.0, second=0.0):
    return [first, second] + [0.0] * 1022


@pytest.fixture(autouse=True)
def offline_by_default(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        return
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        pytest.fail("Offline tests must not use the network")

    monkeypatch.setattr(clova.urllib.request, "urlopen", no_network)


def test_catalog():
    features = matcher.load_features()
    assert {f["id"] for f in features} == FEATURE_IDS
    assert len(features) == len(FEATURE_IDS)
    assert {f["id"] for f in features if f["input_supported"]} == SUPPORTED
    stages = {
        "시작하기",
        "매일 운영",
        "손님 끌기",
        "평판 관리",
        "돈 관리",
        "성과 확인",
        "학습·지원",
    }
    for feature in features:
        assert re.fullmatch(r"[a-z]+(?:_[a-z]+)*", feature["id"])
        for field in ("name_ko", "summary_ko"):
            assert isinstance(feature[field], str) and feature[field].strip()
        assert feature["stage"] in stages
        assert type(feature["input_supported"]) is bool
        assert isinstance(feature["keywords_ko"], list) and feature["keywords_ko"]
        assert all(isinstance(term, str) and term.strip() for term in feature["keywords_ko"])
        # Korean-only core (docs/eval-baseline.md §0).
        assert "name_en" not in feature and "keywords_en" not in feature
        if feature["input_supported"]:
            fields = feature["input_fields"]
            assert fields and len({f["id"] for f in fields}) == len(fields)
            assert all(f["name_ko"] and type(f["required"]) is bool for f in fields)
        else:
            assert not feature.get("input_fields")
    assert matcher.get_feature("unknown") is None
    names = [f["name_ko"] for f in features]
    assert len(set(names)) == len(names)
    # A keyword shared by two features pulls both toward the same request.
    owners = Counter(term for f in features for term in set(f["keywords_ko"]))
    assert [term for term, n in owners.items() if n > 1] == []
    features[0]["name_ko"] = "mutated"
    assert matcher.get_feature(features[0]["id"])["name_ko"] != "mutated"


def test_dataset():
    data = cases()
    assert len(data) >= 40
    counts = Counter(row["lang"] for row in data)
    assert counts["ko"] >= 20 and counts["ko_simple"] >= 10
    assert set(counts) == {"ko", "ko_simple"}  # Korean only (docs/eval-baseline.md §0)
    expected_counts = Counter(row["expected"] for row in data)
    assert expected_counts[None] >= 4  # 5 before the English case was removed
    assert all(expected_counts[feature] >= 5 for feature in SUPPORTED)
    assert len({row["text"] for row in data}) == len(data)
    for row in data:
        assert set(row) == {"id", "split", "source", "text", "lang", "expected"}
        assert isinstance(row["text"], str) and row["text"].strip()
        assert row["expected"] is None or row["expected"] in FEATURE_IDS


def test_committed_index():
    data = json.loads(matcher.INDEX_PATH.read_text(encoding="utf-8"))
    assert data["catalog_sha256"] == matcher.catalog_sha256()
    embeddings = matcher._load_index(matcher.load_features())
    assert set(embeddings) == FEATURE_IDS
    assert all(len(v) == 1024 and math.hypot(*v) > 0 for v in embeddings.values())
    # All entries must contain actual distinct feature vectors.
    assert len({tuple(v) for v in embeddings.values()}) == len(FEATURE_IDS)


@pytest.fixture
def fake_index(monkeypatch, tmp_path):
    embeddings = {feature_id: vector(-1.0) for feature_id in FEATURE_IDS}
    embeddings["menu"] = vector(0.8, 0.6)
    path = tmp_path / "index.json"

    def write():
        path.write_text(
            json.dumps({"catalog_sha256": matcher.catalog_sha256(), "embeddings": embeddings}),
            encoding="utf-8",
        )

    write()
    monkeypatch.setattr(matcher, "INDEX_PATH", path)
    return embeddings, write


def test_weighted_sum_and_threshold(fake_index):
    embedded = []

    def embed(text):
        embedded.append(text)
        return vector()

    matches = matcher.match_features("메뉴를 고쳐요", embed=embed)
    assert embedded == ["메뉴를 고쳐요"]
    assert [m.feature_id for m in matches] == ["menu"]
    match = matches[0]
    assert not match.keyword_only
    assert match.embedding_score == pytest.approx(0.8)
    assert match.keyword_score == pytest.approx(0.7)
    assert match.score == pytest.approx(
        0.7 * matcher.KEYWORD_WEIGHT + 0.8 * matcher.EMBEDDING_WEIGHT
    )
    assert match.matched_terms == ["메뉴"]
    assert matcher.match_features("unrecognized text", embed=lambda _: vector(0, 1)) == []
    assert matcher.match_features("unrecognized text", embed=embed)[0].feature_id == "menu"


def test_threshold_is_strict_and_weights_are_constants(fake_index, monkeypatch):
    monkeypatch.setattr(matcher, "EMBEDDING_WEIGHT", 0.0)
    monkeypatch.setattr(matcher, "KEYWORD_WEIGHT", 1.0)
    monkeypatch.setattr(matcher, "RELEVANCE_THRESHOLD", 0.7)
    assert matcher.match_features("메뉴", embed=lambda _: vector()) == []
    monkeypatch.setattr(matcher, "RELEVANCE_THRESHOLD", 0.69)
    assert matcher.match_features("메뉴", embed=lambda _: vector())[0].feature_id == "menu"


@pytest.mark.parametrize("text", ["", " ", "\n\t", "　"])
def test_empty_input_never_embeds(text):
    def embed(_):
        pytest.fail("Empty input must not call embed")

    assert matcher.match_features(text, embed=embed) == []


def test_top_k_and_normalization():
    assert matcher.match_features("메뉴와 사진", top_k=0) == []
    assert matcher.match_features("메뉴", top_k=-1) == []
    assert len(matcher.match_features("가게 사진이랑 음식 사진", top_k=1)) == 1
    assert matcher.match_features("영 업 시 간을 바꿔요")[0].feature_id == "business_hours"
    assert matcher.match_features("ＰＯＳ 연동")[0].feature_id == "place_plus"
    assert matcher.match_features("chair airport repair") == []  # no substring hit for AI


@pytest.mark.parametrize(
    ("text", "feature_id", "term"),
    [
        ("QR로 결제하고 싶어요", "onsite_payment", "QR"),
        ("AI가 답글 써줘요", "review", "AI"),
        ("POS를 연결해요", "place_plus", "POS"),
        ("place+를 연결해요", "place_plus", "place+"),
    ],
)
def test_ascii_keywords_with_korean_particles(text, feature_id, term):
    matches = matcher.match_features(text)
    match = next((match for match in matches if match.feature_id == feature_id), None)
    assert match is not None
    assert term in match.matched_terms
    assert match.keyword_only and match.embedding_score is None


def test_missing_key_and_keyword_recall():
    # Korean only (docs/eval-baseline.md §0): no English cases or keywords.
    data = [row for row in cases() if row["expected"] is not None]
    misses = []
    for row in data:
        matches = matcher.match_features(row["text"])
        assert all(m.keyword_only and m.embedding_score is None for m in matches)
        if row["expected"] not in [m.feature_id for m in matches]:
            misses.append(row["text"])
    assert 1 - len(misses) / len(data) >= 0.8, misses
    assert matcher.match_features("메뉴")[0].keyword_only


def test_unrelated_keyword_requests():
    for row in cases():
        if row["expected"] is None:
            assert matcher.match_features(row["text"]) == [], row["text"]


def test_api_failure_falls_back(fake_index, monkeypatch):
    def fail(_):
        raise clova.ClovaEmbeddingError("API unavailable")

    monkeypatch.setattr(matcher, "embed_text", fail)
    result = matcher.match_features("메뉴")
    assert result[0].feature_id == "menu"
    assert result[0].keyword_only and result[0].embedding_score is None
    assert result[0].score == result[0].keyword_score
    assert matcher.match_features("unrelated words") == []


@pytest.mark.parametrize(
    "damage", ["missing", "hash", "dimension", "nonfinite", "coverage", "shape"]
)
def test_invalid_index_falls_back(fake_index, damage):
    path = matcher.INDEX_PATH
    data = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    else:
        if damage == "hash":
            data["catalog_sha256"] = "stale"
        elif damage == "dimension":
            data["embeddings"]["menu"] = [1]
        elif damage == "nonfinite":
            data["embeddings"]["menu"][0] = float("nan")
        elif damage == "shape":
            data["embeddings"] = sorted(FEATURE_IDS)
        else:
            del data["embeddings"]["menu"]
        path.write_text(json.dumps(data))

    def embed(_):
        pytest.fail("Invalid indexes must not call the API")

    assert matcher.match_features("메뉴", embed=embed)[0].keyword_only


@pytest.mark.parametrize(
    "bad",
    [
        [0.0] * 1024,
        [1.0],
        [True] * 1024,
        [float("nan")] * 1024,
        [float("inf")] * 1024,
        [10**1000] * 1024,
    ],
)
def test_invalid_query_vector_falls_back(fake_index, bad):
    assert matcher.match_features("메뉴", embed=lambda _: bad)[0].keyword_only


def test_build_index_is_atomic_and_uses_catalog(fake_index, monkeypatch):
    texts = []

    def embed(text):
        texts.append(text)
        return vector()

    monkeypatch.setattr(matcher, "embed_text", embed)
    matcher.build_index()
    assert texts == [matcher.feature_embedding_text(f) for f in matcher.load_features()]
    assert len(matcher._load_index(matcher.load_features())) == len(FEATURE_IDS)
    original = matcher.INDEX_PATH.read_bytes()

    def fail(_):
        raise clova.ClovaEmbeddingError("unavailable")

    monkeypatch.setattr(matcher, "embed_text", fail)
    with pytest.raises(clova.ClovaEmbeddingError):
        matcher.build_index()
    assert matcher.INDEX_PATH.read_bytes() == original
    assert not matcher.INDEX_PATH.with_suffix(".json.tmp").exists()


def response(code="20000", embedding=None):
    return io.BytesIO(
        json.dumps(
            {
                "status": {"code": code},
                "result": {"embedding": vector() if embedding is None else embedding},
            }
        ).encode()
    )


def test_client_request(monkeypatch):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-test-key")
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == clova.REQUEST_TIMEOUT
        return response()

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    assert clova.embed_text("메뉴 바꾸기") == vector()
    assert clova.embed_text("hello") == vector()
    request = requests[0]
    assert request.full_url == clova.ENDPOINT
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"text": "메뉴 바꾸기"}
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["authorization"] == "Bearer fake-test-key"
    assert headers["content-type"] == "application/json"
    assert uuid.UUID(headers["x-ncp-clovastudio-request-id"]).version == 4
    assert requests[0].headers != requests[1].headers


@pytest.mark.parametrize("code", [429, 500, 503, 599, 400, 401, 403, 404])
@pytest.mark.parametrize("api_status", [False, True])
def test_client_retry_policy(monkeypatch, code, api_status):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-test-key")
    calls, sleeps = [], []

    def urlopen(request, timeout):
        calls.append(request)
        if api_status:
            return response(str(code) + "01")
        raise urllib.error.HTTPError(clova.ENDPOINT, code, "hidden body", {}, io.BytesIO())

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(clova.time, "sleep", sleeps.append)
    with pytest.raises(clova.ClovaEmbeddingError):
        clova.embed_text("hello")
    retryable = code == 429 or code >= 500
    assert len(calls) == (3 if retryable else 1)
    assert sleeps == ([0.25, 0.5] if retryable else [])


@pytest.mark.parametrize("error", [http.client.IncompleteRead(b""), ConnectionResetError()])
def test_truncated_read_is_retried_until_success(monkeypatch, error):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-test-key")
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        if len(calls) < 5:
            raise error
        return response()

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(clova.time, "sleep", lambda _: None)
    assert clova.embed_text("hello") == vector()
    assert len(calls) == 5


def test_client_retry_can_succeed(monkeypatch):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-test-key")
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        if len(calls) < 3:
            raise urllib.error.HTTPError(clova.ENDPOINT, 429, "rate limit", {}, io.BytesIO())
        return response()

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(clova.time, "sleep", lambda _: None)
    assert clova.embed_text("hello") == vector()
    assert len(calls) == 3


@pytest.mark.parametrize(
    "failure", ["http", "http_protocol", "transport", "json", "schema", "vector", "status"]
)
def test_client_errors_never_expose_key(monkeypatch, capsys, failure):
    secret = "fake-sensitive-test-token"
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", secret)
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        if failure == "http":
            raise urllib.error.HTTPError(secret, 401, secret, {}, io.BytesIO(secret.encode()))
        if failure == "http_protocol":
            raise http.client.IncompleteRead(secret.encode())
        if failure == "transport":
            raise urllib.error.URLError(secret)
        if failure == "json":
            return io.BytesIO(secret.encode())
        if failure == "schema":
            return io.BytesIO(json.dumps({secret: secret}).encode())
        if failure == "vector":
            return response(embedding=[secret] * 1024)
        return response(code=secret)

    monkeypatch.setattr(clova.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(clova.time, "sleep", lambda _: None)
    with pytest.raises(clova.ClovaEmbeddingError) as error:
        clova.embed_text("hello")
    # Truncated reads are retried; other failures are not.
    assert len(calls) == (clova.TRANSPORT_RETRIES + 1 if failure == "http_protocol" else 1)
    assert secret not in str(error.value)
    assert secret not in "".join(traceback.format_exception(error.value))
    assert secret not in repr(capsys.readouterr())


def test_client_missing_key():
    with pytest.raises(clova.ClovaEmbeddingError, match="not configured"):
        clova.embed_text("hello")


@pytest.mark.network
def test_live_clova_dataset():
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        pytest.skip("CLOVASTUDIO_API_KEY is not configured")
    totals, correct = Counter(), Counter()
    misses, wrong, unrelated, fallback = [], [], [], []
    for row in cases():
        # Embedding v2 quotas may be per minute. Pace the evaluation itself;
        # the production client still retries at most twice with short delays.
        time.sleep(1.05)
        embedded = []

        def live_embed(text, embedded=embedded):
            result = clova.embed_text(text)
            embedded.append(True)
            return result

        matches = matcher.match_features(row["text"], embed=live_embed)
        ids = [m.feature_id for m in matches]
        detail = f"{row['lang']}: {row['text']!r}; expected={row['expected']}; got={ids}"
        if not embedded or any(m.keyword_only for m in matches):
            fallback.append(detail)
        if row["expected"] is None:
            if matches:
                unrelated.append(detail)
            continue
        totals[row["lang"]] += 1
        if ids and ids[0] == row["expected"]:
            correct[row["lang"]] += 1
        else:
            wrong.append(detail)
        if row["expected"] not in ids:
            misses.append(detail)
    problems = []
    if sum(correct.values()) / sum(totals.values()) < 0.9:
        problems.append(f"Overall top-1 below 90%: {dict(correct)} / {dict(totals)}")
    for lang, total in totals.items():
        if correct[lang] / total < 0.8:
            problems.append(f"{lang} top-1 below 80%: {correct[lang]}/{total}")
    if misses:
        problems.append("Top-3 misses:\n" + "\n".join(misses))
    if unrelated:
        problems.append("Unrelated requests matched:\n" + "\n".join(unrelated))
    if fallback:
        problems.append("Live API fell back to keywords:\n" + "\n".join(fallback))
    print(
        f"Live top-1: {dict(correct)} / {dict(totals)}; top-3 misses: {len(misses)}; "
        f"unrelated false positives: {len(unrelated)}; API fallbacks: {len(fallback)}"
    )
    assert not problems, "\n".join(problems + ["Top-1 errors:"] + wrong)
