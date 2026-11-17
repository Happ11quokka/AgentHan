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


def test_snapshot_has_the_place_and_record_sections():
    view = place.snapshot(SEED)
    assert set(view) == {
        "menu",
        "business_hours",
        "coupon",
        "keywords",
        *owner.RECORD_TARGETS,
    }
    assert view["keywords"] == SEED["shop"].get("keywords", [])
    assert view["menu"][0] == {
        "id": "m01",
        "name": "메도빅",
        "price": 7500,
        "description": SEED["menus"][0]["description"],
        "photo_url": None,
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


def test_preview_of_keywords_lists_added_and_removed_words():
    data = json.loads(json.dumps(SEED))
    data["shop"]["keywords"] = ["러시안 베이커리", "이태원 케이크"]
    actions = [
        {
            "name": "set_representative_keywords",
            "arguments": {"add": ["메도빅"], "remove": ["이태원 케이크"]},
        }
    ]
    result = place.preview(data, "search_keywords", actions)
    assert result["changes"]["keywords"] == {
        "added": ["메도빅"],
        "updated": [],
        "removed": ["이태원 케이크"],
    }
    assert "메도빅" in result["after"]["keywords"]


def test_snapshot_splits_records_into_label_and_other_fields():
    talk = place.snapshot(SEED)["talktalk"][0]
    record = SEED["talktalk"][0]
    assert talk == {
        "id": record["id"],
        "label": {"message": record["message"], "status": record["status"]},
        "fields": {"reply_text": record["reply_text"]},
    }


def test_preview_of_a_talktalk_reply_shows_the_inquiry_and_the_reply():
    record = SEED["talktalk"][0]
    actions = [
        {
            "name": "save_talktalk_reply",
            "arguments": {"inquiry": record["id"], "reply_text": "네, 하루 전에 말씀해 주세요"},
        }
    ]
    result = place.preview(SEED, "talktalk", actions)
    assert result["changes"] == {
        "talktalk": {"added": [], "updated": [record["id"]], "removed": []}
    }
    [after] = [r for r in result["after"]["talktalk"] if r["id"] == record["id"]]
    assert after["label"]["message"] == record["message"]
    assert after["fields"]["reply_text"] == "네, 하루 전에 말씀해 주세요"
    assert after["label"]["status"] == "saved_not_sent"
    assert SEED["talktalk"][0]["reply_text"] == ""  # The seed is not touched.


def test_preview_of_an_order_status_lists_only_that_order():
    order = SEED["orders"][0]
    actions = [
        {
            "name": "update_order_status",
            "arguments": {"order_id": order["id"], "status": "rejected", "reason": "재료 소진"},
        }
    ]
    result = place.preview(SEED, "order", actions)
    assert result["changes"] == {"order": {"added": [], "updated": [order["id"]], "removed": []}}
    after = result["after"]["order"][0]
    assert after["label"] == {"items": order["items"], "type": order["type"], "status": "rejected"}
    assert after["fields"] == {"reason": "재료 소진"}


def test_preview_of_a_reservation_status_lists_that_reservation():
    resv = SEED["reservations"][1]
    actions = [
        {
            "name": "update_reservation_status",
            "arguments": {"reservation_id": resv["id"], "status": "confirmed"},
        }
    ]
    result = place.preview(SEED, "reservation", actions)
    assert result["changes"] == {
        "reservation": {"added": [], "updated": [resv["id"]], "removed": []}
    }


def test_preview_of_two_features_lists_both_sections():
    actions = [
        {
            "name": "update_business_hours",
            "arguments": {
                "days": ["sun"],
                "closed": False,
                "open_time": "10:00",
                "close_time": "18:00",
            },
        },
        {
            "name": "save_talktalk_reply",
            "arguments": {"inquiry": SEED["talktalk"][0]["id"], "reply_text": "가능해요"},
        },
    ]
    result = place.preview(SEED, "business_hours", actions)
    assert set(result["changes"]) == {"business_hours", "talktalk"}


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
