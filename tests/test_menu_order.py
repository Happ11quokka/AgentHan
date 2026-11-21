"""메뉴 탭 [순서 바꾸기]: 사장님이 정한 메뉴 순서를 승인 카드 없이 바로 저장한다.

전화가 그 순서를 따르는지는 tests/test_phone_menu.py에서 본다.
"""

import pytest
from fastapi.testclient import TestClient

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app.server import create_app
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.tools.dummy_store import DummyStore, DummyStoreError, watch


def store_with(tmp_path, *names):
    store = DummyStore(tmp_path / "store.json")
    store.write({"shop": {"name": "베이커리"}, "menus": [], "reviews": []})
    for price, name in enumerate(names, start=1):
        store.upsert_menu(name, price * 1000)
    return store


def names(store):
    return [m["name"] for m in store.read()["menus"]]


def ids(store):
    return {m["name"]: m["id"] for m in store.read()["menus"]}


def test_reorder_saves_the_owner_order(tmp_path):
    store = store_with(tmp_path, "메도빅", "나폴레옹", "아메리카노")
    by = ids(store)
    saved = store.reorder_menus([by["아메리카노"], by["메도빅"], by["나폴레옹"]])
    assert saved == [by["아메리카노"], by["메도빅"], by["나폴레옹"]]
    assert names(store) == ["아메리카노", "메도빅", "나폴레옹"]
    # 다른 칸은 그대로다
    assert {m["name"]: m["price"] for m in store.read()["menus"]} == {
        "메도빅": 1000,
        "나폴레옹": 2000,
        "아메리카노": 3000,
    }


def test_reorder_tolerates_menus_that_changed_meanwhile(tmp_path):
    """화면이 순서를 정하는 사이 지운 메뉴는 건너뛰고, 새로 올라온 메뉴는 뒤에 그대로 둔다."""
    store = store_with(tmp_path, "메도빅", "나폴레옹", "아메리카노", "블린")
    by = ids(store)
    store.reorder_menus([by["블린"], "deleted-menu", by["메도빅"]])
    assert names(store) == ["블린", "메도빅", "나폴레옹", "아메리카노"]


def test_reorder_refuses_a_menu_twice_and_skips_a_write_when_nothing_moves(tmp_path):
    store = store_with(tmp_path, "메도빅", "나폴레옹")
    by = ids(store)
    with pytest.raises(DummyStoreError):
        store.reorder_menus([by["메도빅"], by["메도빅"]])
    heard: list[dict] = []
    watch(store.path, heard.append)
    store.reorder_menus([by["메도빅"], by["나폴레옹"]])  # 지금 순서 그대로
    assert heard == []
    store.reorder_menus([by["나폴레옹"]])
    assert [[m["name"] for m in data["menus"]] for data in heard] == [["나폴레옹", "메도빅"]]


@pytest.fixture
def api(tmp_path):
    def make(path):
        return OwnerAssistant(path, knowledge=FakeStoreKnowledge([]))

    return TestClient(create_app(make_assistant=make, root=tmp_path))


def test_reorder_endpoint_saves_without_an_approval_card(api, tmp_path):
    sid = api.post("/assistant/sessions").json()["session_id"]
    menu = api.get(f"/assistant/sessions/{sid}/place").json()["place"]["menu"]
    order = [m["id"] for m in reversed(menu)]

    body = api.put(f"/assistant/sessions/{sid}/menus/order", json={"ids": order})

    assert body.status_code == 200
    assert [m["id"] for m in body.json()["place"]["menu"]] == order
    assert body.json()["pending"] == []  # 승인 카드 없이 바로 저장
    saved = DummyStore(tmp_path / sid / "store.json").read()["menus"]
    assert [m["id"] for m in saved] == order
    again = api.get(f"/assistant/sessions/{sid}/place").json()["place"]["menu"]
    assert [m["id"] for m in again] == order


def test_reorder_endpoint_rejects_bad_orders(api):
    sid = api.post("/assistant/sessions").json()["session_id"]
    first = api.get(f"/assistant/sessions/{sid}/place").json()["place"]["menu"][0]["id"]
    url = f"/assistant/sessions/{sid}/menus/order"
    assert api.put(url, json={"ids": []}).status_code == 422
    assert api.put(url, json={"ids": [first, first]}).status_code == 422
    assert api.put("/assistant/sessions/nope/menus/order", json={"ids": [first]}).status_code == 404
