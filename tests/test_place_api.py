"""Backend for the owner screen: chat turns, the place snapshot and pending changes."""

import json

from fastapi.testclient import TestClient

import test_owner_assistant as base
from agenthan.agent import owner_assistant as owner
from agenthan.app import place
from agenthan.app.server import create_app
from agenthan.tools.dummy_store import SEED_PATH

FakeChat, answer, call = base.FakeChat, base.answer, base.call
mock_index, offline_by_default = base.mock_index, base.offline_by_default  # fixtures

SEED = json.loads(SEED_PATH.read_text(encoding="utf-8"))


def client(tmp_path, index, *replies, feature="menu"):
    fake = FakeChat(*replies)

    def make(path):
        return owner.OwnerAssistant(path, chat=fake, embed=lambda _: index[feature])

    return TestClient(create_app(make_assistant=make, root=tmp_path))


def test_snapshot_has_the_three_sections():
    view = place.snapshot(SEED)
    assert set(view) == {"menu", "business_hours", "coupon"}
    assert view["menu"][0] == {
        "id": "m01",
        "name": "메도빅",
        "price": 7500,
        "description": SEED["menus"][0]["description"],
    }
    assert list(view["business_hours"]) == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert view["business_hours"]["sun"] == {"closed": True}
    assert view["coupon"] == []


def test_preview_of_hours_lists_changed_days_only():
    actions = [
        {
            "name": "update_business_hours",
            "arguments": {
                "days": ["sun"],
                "closed": False,
                "open_time": "10:00",
                "close_time": "18:00",
            },
        }
    ]
    result = place.preview(SEED, "business_hours", actions)
    assert result["changes"] == {"business_hours": {"added": [], "updated": ["sun"], "removed": []}}
    assert result["after"]["business_hours"]["sun"]["open_time"] == "10:00"
    assert SEED["shop"]["hours"]["sun"] == {"closed": True}  # The seed is not touched.


def test_preview_of_a_coupon_lists_the_new_coupon():
    args = {
        "title": "오픈 기념",
        "discount_type": "percent",
        "discount_value": 10,
        "start_date": "2026-11-20",
        "end_date": "2026-11-30",
    }
    result = place.preview(SEED, "coupon", [{"name": "create_coupon", "arguments": args}])
    assert result["changes"]["coupon"] == {"added": ["coupon-1"], "updated": [], "removed": []}
    assert result["after"]["coupon"][0]["title"] == "오픈 기념"


def test_preview_that_fails_validation_has_no_after():
    args = {
        "title": "이상한 쿠폰",
        "discount_type": "percent",
        "discount_value": 150,
        "start_date": "2026-11-20",
        "end_date": "2026-11-30",
    }
    result = place.preview(SEED, "coupon", [{"name": "create_coupon", "arguments": args}])
    assert result["after"] is None and result["changes"] == {}
    assert result["error"]


def test_menu_change_is_previewed_then_saved_on_yes(tmp_path, mock_index):
    api = client(tmp_path, mock_index, call("upsert_menu", {"name": "연습 차", "price": 5000}))
    session = api.post("/assistant/sessions").json()
    sid = session["session_id"]
    assert not any(m["name"] == "연습 차" for m in session["place"]["menu"])

    first = api.post(
        f"/assistant/sessions/{sid}/messages", json={"text": "연습 차 5000원으로 등록"}
    )
    body = first.json()
    assert first.status_code == 200
    assert "바꿀까요" in body["reply"]
    assert not any(m["name"] == "연습 차" for m in body["place"]["menu"])  # Not saved yet.
    [pending] = body["pending"]
    assert pending["feature"] == "menu"
    assert len(pending["changes"]["menu"]["added"]) == 1
    assert any(m["name"] == "연습 차" for m in pending["after"]["menu"])

    second = api.post(f"/assistant/sessions/{sid}/messages", json={"text": "네"}).json()
    assert second["pending"] == []
    assert any(m["name"] == "연습 차" and m["price"] == 5000 for m in second["place"]["menu"])
    again = api.get(f"/assistant/sessions/{sid}/place").json()
    assert again["place"] == second["place"] and again["pending"] == []


def test_sessions_have_their_own_store(tmp_path, mock_index):
    api = client(tmp_path, mock_index, call("upsert_menu", {"name": "연습 차", "price": 5000}))
    one = api.post("/assistant/sessions").json()["session_id"]
    two = api.post("/assistant/sessions").json()["session_id"]
    api.post(f"/assistant/sessions/{one}/messages", json={"text": "연습 차 5000원으로 등록"})
    api.post(f"/assistant/sessions/{one}/messages", json={"text": "네"})
    menu = api.get(f"/assistant/sessions/{two}/place").json()["place"]["menu"]
    assert not any(m["name"] == "연습 차" for m in menu)


def test_unknown_session_and_empty_text(tmp_path, mock_index):
    api = client(tmp_path, mock_index)
    assert api.get("/assistant/sessions/nope/place").status_code == 404
    assert api.post("/assistant/sessions/nope/messages", json={"text": "네"}).status_code == 404
    sid = api.post("/assistant/sessions").json()["session_id"]
    assert api.post(f"/assistant/sessions/{sid}/messages", json={"text": "  "}).status_code == 422
