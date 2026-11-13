"""플레이스 시드와 StoreKnowledge 공통 fixture는 같은 더미 가게를 나타낸다."""

import json
from pathlib import Path

import pytest

from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.review_clf.schema import FEATURES

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sample_store.json"


@pytest.fixture
def sample_store():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def seed(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    data = store.read()
    assert not store.path.exists()
    return data


def test_shop_name_matches_fixture(seed, sample_store):
    assert seed["shop"]["name"] == sample_store["store"]["name"]


def test_menus_match_fixture(seed, sample_store):
    actual = [
        {
            "id": menu["id"],
            "name": menu["name"],
            "price": menu["price"],
            "aliases": menu["aliases"],
            "name_native": menu.get("name_native"),
            "description": menu.get("description"),
        }
        for menu in seed["menus"]
    ]
    expected = [
        {
            "id": menu["id"],
            "name": menu["name_ko"],
            "price": menu["price"],
            "aliases": menu.get("aliases", []),
            "name_native": menu.get("name_native"),
            "description": menu.get("description"),
        }
        for menu in sample_store["menu"]
    ]
    assert sorted(actual, key=lambda menu: menu["id"]) == sorted(
        expected, key=lambda menu: menu["id"]
    )


def test_reviews_match_fixture(seed, sample_store):
    actual = [
        {key: review[key] for key in ("review_id", "text", "rating", "visit_date")}
        for review in seed["reviews"]
    ]
    expected = [
        {
            "review_id": review["id"],
            "text": review["text"],
            "rating": review["rating"],
            "visit_date": review["created_at"].split("T")[0],
        }
        for review in sample_store["reviews"]
    ]
    assert sorted(actual, key=lambda review: review["review_id"]) == sorted(
        expected, key=lambda review: review["review_id"]
    )


def test_reviews_support_reply_demo(seed):
    assert sum(review["has_owner_reply"] is False for review in seed["reviews"]) >= 2
    for review in seed["reviews"]:
        assert isinstance(review["has_owner_reply"], bool)
        assert isinstance(review["features"], list)
        for feature in review["features"]:
            assert feature["feature"] in FEATURES
            for highlight in feature["highlights"]:
                assert 0 <= highlight["start"] < highlight["end"] <= len(review["text"])
                assert review["text"][highlight["start"] : highlight["end"]] == highlight["text"]
