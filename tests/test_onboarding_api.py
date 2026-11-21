"""처음 쓰는 사장님 온보딩(#94): 가게 이름·업종·대표 메뉴를 그 대화의 가게 저장소에 저장한다."""

import json

import pytest
from fastapi.testclient import TestClient

import test_owner_assistant as base
from agenthan.agent import owner_assistant as owner
from agenthan.app.server import create_app
from agenthan.tools.dummy_store import DummyStore

FakeChat = base.FakeChat
mock_index, offline_by_default = base.mock_index, base.offline_by_default  # fixtures

BLANK = {"shop": {}, "menus": [], "reviews": []}


@pytest.fixture
def api(tmp_path, mock_index):
    def make(path):
        if not path.exists():
            path.write_text(json.dumps(BLANK, ensure_ascii=False), encoding="utf-8")
        return owner.OwnerAssistant(path, chat=FakeChat(), embed=lambda _: mock_index["menu"])

    return TestClient(create_app(make_assistant=make, root=tmp_path))


def store_of(tmp_path, sid):
    return DummyStore(tmp_path / sid / "store.json").read()


def test_views_carry_the_shop_name_and_category(api):
    session = api.post("/assistant/sessions").json()
    assert session["shop"] == {"name": "", "category": ""}
    assert api.get(f"/assistant/sessions/{session['session_id']}/place").json()["shop"] == {
        "name": "",
        "category": "",
    }


def test_onboarding_saves_the_shop_and_menus_the_owner_typed(api, tmp_path):
    sid = api.post("/assistant/sessions").json()["session_id"]
    body = {
        "name": "  달콤 디저트  ",
        "category": "디저트",
        "menus": [{"name": "마라 떡볶이", "price": 9000}, {"name": "딸기 케이크", "price": 6500}],
    }
    view = api.post(f"/assistant/sessions/{sid}/onboarding", json=body).json()
    assert view["shop"] == {"name": "달콤 디저트", "category": "디저트"}
    assert [(m["name"], m["price"]) for m in view["place"]["menu"]] == [
        ("마라 떡볶이", 9000),
        ("딸기 케이크", 6500),
    ]
    assert view["pending"] == []
    data = store_of(tmp_path, sid)
    assert data["shop"] == {"name": "달콤 디저트", "category": "디저트"}
    # 넣은 값만: 설명·별칭을 지어내지 않는다.
    assert [set(m) for m in data["menus"]] == [{"id", "name", "price", "aliases"}] * 2


def test_menus_can_be_skipped(api, tmp_path):
    sid = api.post("/assistant/sessions").json()["session_id"]
    response = api.post(
        f"/assistant/sessions/{sid}/onboarding", json={"name": "한강 분식", "category": "분식"}
    )
    assert response.status_code == 200
    assert store_of(tmp_path, sid)["menus"] == []


def test_the_saved_category_is_the_photo_context(api, monkeypatch):
    """저장된 업종은 메뉴 사진 인식이 그대로 쓴다(ask_photo의 shop.category)."""
    seen = []
    monkeypatch.setattr(owner.food_photo, "classify_photo", lambda image, call: "menu_board")
    monkeypatch.setattr(
        owner.menu_photo,
        "draft_menus",
        lambda image, knowledge, store_context, call: seen.append(store_context) or [],
    )
    sid = api.post("/assistant/sessions").json()["session_id"]
    api.post(f"/assistant/sessions/{sid}/onboarding", json={"name": "달콤", "category": "디저트"})
    api.post(f"/assistant/sessions/{sid}/photos", files={"image": ("m.jpg", b"x", "image/jpeg")})
    assert seen == ["달콤 디저트"]


@pytest.mark.parametrize(
    "body",
    [
        {"name": " ", "category": "디저트"},
        {"name": "달콤", "category": ""},
        {"name": "달콤", "category": "디저트", "menus": [{"name": "케이크", "price": 0}]},
        {"name": "달콤", "category": "디저트", "menus": [{"name": " ", "price": 5000}]},
        {"name": "달콤", "category": "디저트", "menus": [{"name": "a", "price": 1000}] * 4},
    ],
)
def test_blank_or_bad_values_are_refused_and_nothing_is_saved(api, tmp_path, body):
    sid = api.post("/assistant/sessions").json()["session_id"]
    assert api.post(f"/assistant/sessions/{sid}/onboarding", json=body).status_code == 422
    data = store_of(tmp_path, sid)
    assert (data["shop"], data["menus"]) == ({}, [])


def test_onboarding_keeps_the_rest_of_the_shop(tmp_path, mock_index):
    """데모(?onboarding=1): 데이터가 있는 가게도 이름·업종만 바꾸고 나머지(영업시간 등)는 둔다."""
    seeded = {
        "shop": {"name": "옛 이름", "hours": {"sun": {"closed": True}}},
        "menus": [],
        "reviews": [],
    }

    def make(path):
        if not path.exists():
            path.write_text(json.dumps(seeded, ensure_ascii=False), encoding="utf-8")
        return owner.OwnerAssistant(path, chat=FakeChat(), embed=lambda _: mock_index["menu"])

    client = TestClient(create_app(make_assistant=make, root=tmp_path))
    sid = client.post("/assistant/sessions").json()["session_id"]
    client.post(
        f"/assistant/sessions/{sid}/onboarding", json={"name": "새 이름", "category": "카페"}
    )
    shop = store_of(tmp_path, sid)["shop"]
    assert shop == {"name": "새 이름", "hours": {"sun": {"closed": True}}, "category": "카페"}


def test_unknown_session_is_404(api):
    body = {"name": "달콤", "category": "디저트"}
    assert api.post("/assistant/sessions/nope/onboarding", json=body).status_code == 404
