import json

import pytest

from agenthan.agent import place_matcher as matcher
from agenthan.eval.cases import load_cases
from agenthan.eval.run import FLOW_CASES
from agenthan.tools.dummy_store import SEED_PATH, DummyStore
from agenthan.tools.feature_tools import (
    DELEGATED_FEATURES,
    TOOL_FEATURES,
    TOOLS,
    all_tool_schemas,
    execute_feature_tool,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def fail(*args, **kwargs):
        pytest.fail("Feature tool tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", fail)


@pytest.fixture
def store(tmp_path):
    return DummyStore(tmp_path / "store.json")


VALID = {
    "update_business_info": {"field": "phone", "value": "02-000-0001"},
    "update_business_hours": {
        "day": "fri",
        "closed": False,
        "open_time": "10:00",
        "close_time": "23:00",
    },
    "save_talktalk_reply": {"inquiry": "talk-1", "reply_text": "메도빅 홀케이크는 예약 가능해요."},
    "update_order_status": {"order_id": "order-1", "status": "accepted"},
    "update_reservation_status": {"reservation_id": "resv-2", "status": "cancelled"},
    "list_call_history": {"missed_only": True},
    "set_representative_keywords": {"keywords": ["디저트", "케이크"]},
    "create_coupon": {
        "title": "첫 방문",
        "discount_type": "percent",
        "discount_value": 10,
        "start_date": "2026-11-10",
        "end_date": "2026-11-30",
    },
    "register_new_opening": {"opening_date": "2026-11-01"},
    "get_naverpay_signup_guide": {"business_type": "individual"},
    "set_onsite_payment": {"enabled": True},
    "get_settlement_summary": {"period": "this_month"},
    "check_pos_compatibility": {"pos_name": "연습POS 플러스"},
    "diagnose_search_visibility": {},
    "get_statistics": {"period": "7d"},
    "search_help_articles": {"query": "예약 교육"},
}


def test_every_catalog_feature_has_one_tool():
    schemas = all_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert len(names) == len(set(names)) == 17
    features = {f["id"] for f in matcher.load_features()}
    assert {TOOL_FEATURES[n] for n in names} == features - DELEGATED_FEATURES
    assert DELEGATED_FEATURES < features  # review features go to agenthan.agent.review
    for schema in schemas:
        params = schema["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) <= set(params["properties"])
        assert all(p["description"] for p in params["properties"].values())
    assert set(VALID) == {t.name for t in TOOLS.values()}


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_call_succeeds_and_keeps_seed(store, name):
    seed = SEED_PATH.read_bytes()
    result = execute_feature_tool(store, name, json.dumps(VALID[name], ensure_ascii=False))
    assert result["ok"], result
    assert SEED_PATH.read_bytes() == seed


def test_writes_are_persisted(store):
    opened = {"day": "mon", "open_time": "11:00", "close_time": "20:00"}
    assert execute_feature_tool(store, "update_business_hours", opened)["record"]["closed"] is False
    execute_feature_tool(store, "update_business_hours", {"day": "2026-11-08", "closed": True})
    execute_feature_tool(store, "update_order_status", VALID["update_order_status"])
    execute_feature_tool(store, "create_coupon", VALID["create_coupon"])
    data = store.read()
    assert data["shop"]["hours"]["2026-11-08"] == {"closed": True}
    assert data["orders"][0]["status"] == "accepted"
    assert data["coupons"][0]["status"] == "saved_not_published"


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("update_business_info", {"field": "email", "value": "x"}),
        ("update_business_hours", {"day": "monday", "closed": True}),
        ("update_business_hours", {"day": "mon", "closed": False}),
        (
            "update_business_hours",
            {"day": "mon", "closed": False, "open_time": "25:00", "close_time": "10:00"},
        ),
        ("create_coupon", {**VALID["create_coupon"], "discount_value": 150}),
        ("create_coupon", {**VALID["create_coupon"], "end_date": "2026-11-01"}),
        ("create_coupon", {**VALID["create_coupon"], "start_date": "2026/11/10"}),
        ("set_representative_keywords", {"keywords": ["a", "b", "c", "d", "e", "f"]}),
        ("set_onsite_payment", {"enabled": "yes"}),
        ("get_statistics", {}),
        ("save_talktalk_reply", "not json"),
    ],
)
def test_invalid_arguments_never_write(store, name, arguments):
    result = execute_feature_tool(store, name, arguments)
    assert result["error"] == "needs_input" and result["missing_fields"]
    assert not store.path.exists()


def test_unknown_tool_and_fields(store):
    assert execute_feature_tool(store, "delete_shop", {})["error"] == "tool_not_allowed"
    extra = {**VALID["set_onsite_payment"], "force": True}
    assert execute_feature_tool(store, "set_onsite_payment", extra)["error"] == "unsupported_fields"
    assert not store.path.exists()


def test_lookup_by_quote_and_not_found(store):
    ok = execute_feature_tool(
        store, "save_talktalk_reply", {"inquiry": "단체 주문", "reply_text": "네"}
    )
    assert ok["record"]["id"] == "talk-2" and ok["record"]["status"] == "saved_not_sent"
    missing = execute_feature_tool(
        store, "update_order_status", {"order_id": "order-9", "status": "ready"}
    )
    assert missing["error"] == "order_not_found" and len(missing["candidates"]) == 2


def test_old_store_without_new_collections(store):
    old = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    old = {k: old[k] for k in ("shop", "menus", "reviews")}
    store.path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    assert execute_feature_tool(store, "list_call_history", {})["records"] == []
    assert execute_feature_tool(store, "upsert_menu", {"name": "차", "price": 3000})["ok"]


def test_menu_tool_delegates_and_review_tools_are_gone(store):
    assert execute_feature_tool(store, "upsert_menu", {"name": "차"})["error"] == "needs_input"
    for name in ("save_review_reply", "analyze_reviews"):
        assert execute_feature_tool(store, name, {})["error"] == "tool_not_allowed"


# --- tool selection evaluation ------------------------------------------------------


def test_eval_dataset_covers_every_tool():
    cases = [c for c in load_cases(FLOW_CASES) if c.source == "m0"]
    full = [c for c in cases if c.kind == "full"]
    assert {c.expected_tool for c in full} == set(TOOL_FEATURES)
    for name in TOOL_FEATURES:
        langs = {c.lang for c in full if c.expected_tool == name}
        assert langs == {"ko", "en", "ko_simple"}
    assert all(c.expected_tool is None for c in cases if c.kind != "full")
    assert all(c.near_tool in TOOL_FEATURES for c in cases if c.kind == "missing")
