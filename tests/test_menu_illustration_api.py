"""메뉴 대표 그림: 세션을 열면 그림 없는 메뉴가 채워지고, 스냅샷 메뉴 줄에 그림 주소가 온다."""

import json

from fastapi.testclient import TestClient

import test_owner_assistant as base
from agenthan.agent import owner_assistant as owner
from agenthan.app import place
from agenthan.app.server import create_app
from agenthan.illustration import embedding_index, library
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


def test_opening_a_session_embeds_an_unknown_menu_once(tmp_path, mock_index, monkeypatch):
    """라이브러리에 없는 이름(키워드도 끝말도 없음)은 임베딩으로 가까운 그림을 받고,
    같은 이름은 한 번만, 다시 열 때는 부르지 않는다."""
    tea = next(row for row in library.load_catalog() if row["id"] == "default_tea")
    near_tea = [1.0] + [0.0] * 1023
    monkeypatch.setattr(
        embedding_index,
        "stored",
        lambda: {"default_tea": {"text": embedding_index.embedding_text(tea), "vector": near_tea}},
    )
    calls = []

    def embed(text):
        calls.append(text)
        return near_tea

    def make(path):
        assistant = owner.OwnerAssistant(path, chat=FakeChat(), embed=lambda _: mock_index["menu"])
        data = assistant.store.read()
        data["menus"] = [
            {"id": "x1", "name": "쀍뷁 한 잔", "price": 5000},
            {"id": "x2", "name": "쀍뷁 한 잔", "price": 6000},
        ]
        assistant.store.write(data)
        return assistant

    api = TestClient(create_app(make_assistant=make, root=tmp_path, illustration_embed=embed))
    body = api.post("/assistant/sessions").json()
    urls = [row["illustration_url"] for row in body["place"]["menu"]]
    assert urls == ["/illustrations/default_tea.svg"] * 2
    assert calls == ["쀍뷁 한 잔"]
    api.get(f"/assistant/sessions/{body['session_id']}/place")
    assert calls == ["쀍뷁 한 잔"]
