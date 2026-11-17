"""Owner screen backend: the review and customer-stats tabs read their own data (#72)."""

from agenthan.tools.dummy_store import DummyStore
from test_photo_api import client, start


def reviews(api, session_id):
    return api.get(f"/assistant/sessions/{session_id}/reviews")


def report(api, session_id, period=None):
    query = f"?period={period}" if period else ""
    return api.get(f"/assistant/sessions/{session_id}/report{query}")


def test_reviews_tab_lists_the_session_reviews_newest_first_with_a_summary(tmp_path):
    api = client(tmp_path)
    session_id = start(api)

    body = reviews(api, session_id).json()
    assert body["summary"] == {
        "count": 3,
        "average_rating": 4.0,
        "unanswered": 2,
        "waiting": 0,
        "preparing": 0,
    }
    assert [r["id"] for r in body["reviews"]] == ["r03", "r02", "r01"]
    newest = body["reviews"][0]
    assert newest == {
        "id": "r03",
        "visit_date": "2026-11-05",
        "rating": 4,
        "text": "블린 양이 생각보다 적었지만 맛은 좋아요.",
        "softened": False,
        "original": None,
        "has_owner_reply": False,
        "draft": None,
    }


def test_harsh_words_are_softened_and_the_original_is_kept_aside(tmp_path):
    api = client(tmp_path)
    session_id = start(api)
    store = DummyStore(tmp_path / session_id / "store.json")
    data = store.read()
    data["reviews"].append(
        {
            "review_id": "r04",
            "visit_date": "2026-11-06",
            "rating": 1,
            "text": "빵이 존나 짜요. 사장 인성 쓰레기.",
            "features": [],
            "has_owner_reply": False,
        }
    )
    store.write(data)

    [harsh] = [r for r in reviews(api, session_id).json()["reviews"] if r["id"] == "r04"]
    assert harsh["softened"] is True
    assert "존나" not in harsh["text"] and "쓰레기" not in harsh["text"] and "짜요" in harsh["text"]
    assert harsh["original"] == "빵이 존나 짜요. 사장 인성 쓰레기."


def test_stats_tab_gives_this_weeks_numbers_and_one_thing_to_try(tmp_path):
    api = client(tmp_path)
    session_id = start(api)

    body = report(api, session_id).json()
    assert body["period"] == "week" and body["end"] == "2026-11-08"
    cards = {c["key"]: c for c in body["cards"]}
    assert list(cards) == ["place_views", "booking_order_requests", "smartcall_calls"]
    assert cards["place_views"]["value"] == 1260
    assert {"change", "change_percent"} <= set(cards["place_views"])
    assert len(body["hourly"]) == 24 and sum(body["hourly"]) == 1260
    assert [d["day"] for d in body["weekday"]] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert sum(d["views"] for d in body["weekday"]) == 1260
    assert round(sum(c["share_percent"] for c in body["channels"]), 1) == 100.0
    assert body["keywords"] and all({"name", "views", "new"} == set(k) for k in body["keywords"])
    assert body["tip"] is None or isinstance(body["tip"], str)


def test_stats_tab_switches_to_a_day_and_rejects_other_periods(tmp_path):
    api = client(tmp_path)
    session_id = start(api)

    day = report(api, session_id, "day").json()
    assert day["period"] == "day" and len(day["hourly"]) == 24
    assert report(api, session_id, "month").status_code == 422


def test_unknown_sessions_have_no_reviews_or_stats(tmp_path):
    api = client(tmp_path)
    assert reviews(api, "nope").status_code == 404
    assert report(api, "nope").status_code == 404
