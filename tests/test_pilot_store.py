"""Pilot store: the owner screen starts every chat in one made-up shop on a fixed day (#73)."""

from datetime import date

from fastapi.testclient import TestClient

from agenthan.app import pilot
from agenthan.app.server import create_app
from agenthan.tools.dummy_store import DummyStore
from test_review_delegate import FakeWriter

PILOT_DAY = date(2026, 11, 9)


def test_a_new_chat_opens_the_pilot_shop(tmp_path):
    api = TestClient(create_app(make_assistant=pilot.assistant, root=tmp_path))
    session_id = api.post("/assistant/sessions").json()["session_id"]

    place = api.get(f"/assistant/sessions/{session_id}/place").json()["place"]
    assert {"메도빅", "피로시키", "보르시"} <= {m["name"] for m in place["menu"]}
    assert len(place["menu"]) >= 12
    assert place["coupon"]

    summary = api.get(f"/assistant/sessions/{session_id}/reviews").json()["summary"]
    assert summary["count"] >= 18
    assert 0 < summary["unanswered"] < summary["count"]


def test_the_evaluation_seed_is_left_alone(tmp_path):
    pilot.assistant(tmp_path / "store.json")

    evaluation = DummyStore(tmp_path / "other.json").read()
    assert len(evaluation["menus"]) == 4
    assert len(evaluation["reviews"]) == 3


def test_the_assistant_and_its_review_helper_share_the_pilot_day(tmp_path):
    assistant = pilot.assistant(tmp_path / "store.json")
    assert assistant.today == PILOT_DAY

    review = pilot.review_session(assistant.store, owner_lang="ko", writer=FakeWriter())
    assert review.today == PILOT_DAY


def test_pilot_records_happen_on_or_before_the_pilot_day(tmp_path):
    data = pilot.assistant(tmp_path / "store.json").store.read()

    for review in data["reviews"]:
        assert date.fromisoformat(review["visit_date"]) <= PILOT_DAY
    for call in data["calls"]:
        assert date.fromisoformat(call["time"][:10]) <= PILOT_DAY
    assert data["coupons"], "a coupon to change"
    assert any(t["status"] == "unanswered" for t in data["talktalk"])
    assert any(o["status"] == "new" for o in data["orders"])
    assert any(r["status"] == "requested" for r in data["reservations"])


DUMMY_DATA = "fixtures/dummy_store"  # same format as data/clean, safe to commit


def test_a_store_data_folder_replaces_the_made_up_shop(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHAN_DATA_DIR", DUMMY_DATA)
    api = TestClient(create_app(make_assistant=pilot.assistant, root=tmp_path))
    session_id = api.post("/assistant/sessions").json()["session_id"]

    place = api.get(f"/assistant/sessions/{session_id}/place").json()["place"]
    names = {m["name"] for m in place["menu"]}
    assert "피로시키" not in names, "the made-up pilot menu is gone"
    assert len(place["menu"]) >= 20

    data = pilot.assistant(tmp_path / "check.json").store.read()
    menu_names = {m["name"] for m in data["menus"]}
    assert all(any(n in o["items"] for n in menu_names) for o in data["orders"])
    assert any(n in data["coupons"][0]["title"] for n in menu_names)
    for review in data["reviews"]:
        assert date.fromisoformat(review["visit_date"]) <= PILOT_DAY
    assert data["statistics"] and data["reservations"], "missing records stay as dummies"


def test_unrated_reviews_do_not_break_the_average(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHAN_DATA_DIR", DUMMY_DATA)
    data = pilot.assistant(tmp_path / "store.json").store.read()
    data["reviews"][0]["rating"] = None

    from agenthan.app.screens import reviews

    summary = reviews(data)["summary"]
    assert summary["average_rating"] is not None
    assert summary["count"] == len(data["reviews"])
