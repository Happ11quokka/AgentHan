"""메뉴 대표 그림: 세션을 열면 그림 없는 메뉴가 채워지고, 스냅샷 메뉴 줄에 그림 주소가 온다."""

import json

from fastapi.testclient import TestClient

import test_owner_assistant as base
from agenthan.agent import owner_assistant as owner
from agenthan.app import place
from agenthan.app.server import create_app
from agenthan.illustration import library
from agenthan.tools.dummy_store import SEED_PATH

FakeChat = base.FakeChat
mock_index, offline_by_default = base.mock_index, base.offline_by_default  # fixtures

SEED = json.loads(SEED_PATH.read_text(encoding="utf-8"))


def client(tmp_path, index):
    def make(path):
        return owner.OwnerAssistant(path, chat=FakeChat(), embed=lambda _: index["menu"])

    return TestClient(create_app(make_assistant=make, root=tmp_path))


def menu_row(**record):
    data = {**SEED, "menus": [{"id": "x1", "name": "가나다", "price": 5000, **record}]}
    return place.snapshot(data, lambda m: "/p/x1" if m.get("photo") else None)["menu"][0]


def test_snapshot_sends_the_illustration_url():
    assert menu_row(illustration="default_cake")["illustration_url"] == (
        "/illustrations/default_cake.svg"
    )


def test_snapshot_sends_null_for_an_unknown_or_missing_illustration():
    assert menu_row(illustration="cake_that_was_removed")["illustration_url"] is None
    assert menu_row()["illustration_url"] is None


def test_snapshot_sends_the_illustration_even_with_a_photo():
    """우선순위는 화면이 정한다."""
    row = menu_row(illustration="default_tea", photo="photos/x1.jpg")
    assert row["photo_url"] == "/p/x1"
    assert row["illustration_url"] == "/illustrations/default_tea.svg"


def test_opening_the_seed_shop_fills_every_menu_with_an_illustration(tmp_path, mock_index):
    api = client(tmp_path, mock_index)
    body = api.post("/assistant/sessions").json()
    menu = body["place"]["menu"]
    assert len(menu) == len(SEED["menus"])
    for row in menu:
        assert row["photo_url"] is None
        assert row["illustration_url"].startswith(library.URL_PREFIX)
        assert row["ai"] == [] and row["filling"] == []  # 그림은 AI 표시 대상이 아니다
    store = json.loads((tmp_path / body["session_id"] / "store.json").read_text("utf-8"))
    assert all(m["illustration"] in library.catalog_ids() for m in store["menus"])
    again = api.get(f"/assistant/sessions/{body['session_id']}/place").json()
    assert again["place"]["menu"] == menu  # 같은 메뉴는 늘 같은 그림


def test_seed_menus_without_a_clue_get_a_category_default(tmp_path, mock_index):
    body = client(tmp_path, mock_index).post("/assistant/sessions").json()
    urls = {row["name"]: row["illustration_url"] for row in body["place"]["menu"]}
    assert urls["아메리카노"] in {
        f"/illustrations/{i}.svg" for i in library.catalog_ids() if i != "default_other"
    }


def test_an_empty_menu_session_stays_empty(tmp_path, mock_index):
    body = client(tmp_path, mock_index).post("/assistant/sessions?menu=empty").json()
    assert body["place"]["menu"] == []


def test_an_owner_rename_picks_the_illustration_of_the_new_name(tmp_path, mock_index):
    api = client(tmp_path, mock_index)
    body = api.post("/assistant/sessions").json()
    sid, first = body["session_id"], body["place"]["menu"][0]
    url = f"/assistant/sessions/{sid}/menus/{first['id']}"

    renamed = api.patch(url, json={"name": "가나다 수프"}).json()["place"]["menu"]
    row = next(m for m in renamed if m["id"] == first["id"])
    assert row["illustration_url"] == "/illustrations/default_soup.svg"
    assert "illustration" not in row["ai"]

    api.patch(url, json={"name": "가나다 에이드"})
    again = api.get(f"/assistant/sessions/{sid}/place").json()["place"]["menu"]
    row = next(m for m in again if m["id"] == first["id"])
    assert row["illustration_url"] == "/illustrations/default_ade.svg"
    store = json.loads((tmp_path / sid / "store.json").read_text("utf-8"))
    saved = next(m for m in store["menus"] if m["id"] == first["id"])
    assert saved["illustration"] == "default_ade"


def test_a_price_edit_keeps_the_illustration(tmp_path, mock_index):
    api = client(tmp_path, mock_index)
    body = api.post("/assistant/sessions").json()
    sid, first = body["session_id"], body["place"]["menu"][0]
    url = f"/assistant/sessions/{sid}/menus/{first['id']}"
    row = api.patch(url, json={"price": first["price"] + 500}).json()["place"]["menu"][0]
    assert row["illustration_url"] == first["illustration_url"]
