"""메뉴 대표 그림: 세션을 열면 그림 없는 메뉴가 채워지고, 스냅샷 메뉴 줄에 그림 주소가 온다."""

import json

import pytest
from fastapi.testclient import TestClient

import test_owner_assistant as base
from agenthan.agent import owner_assistant as owner
from agenthan.app import place, server
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


@pytest.fixture
def background(monkeypatch):
    """서버의 백그라운드 일을 바로 돌리지 않고 모아 둔다. run()이 모은 일을 차례로 돌린다
    (그 일이 새로 맡긴 일까지)."""
    jobs = []

    class Background:
        queued = jobs

        @staticmethod
        def run():
            while jobs:
                jobs.pop(0)()

    monkeypatch.setattr(server, "in_background", jobs.append)
    return Background


@pytest.fixture
def near_tea(monkeypatch):
    """임베딩 인덱스를 블랙티 하나로: 낯선 이름이 [1, 0, …]이면 블랙티와 가깝다."""
    tea = next(row for row in library.load_catalog() if row["id"] == "drink_black_tea")
    vector = [1.0] + [0.0] * 1023
    monkeypatch.setattr(
        embedding_index,
        "stored",
        lambda: {
            "drink_black_tea": {"text": embedding_index.embedding_text(tea), "vector": vector}
        },
    )
    return vector


def recording(calls, vector=None):
    def embed(text):
        calls.append(text)
        if vector is None:
            raise RuntimeError("임베딩 꺼짐")
        return vector

    return embed


def with_menus(menus):
    def make(path):
        assistant = owner.OwnerAssistant(path, chat=FakeChat())
        data = assistant.store.read()
        data["menus"] = menus
        assistant.store.write(data)
        return assistant

    return make


def test_opening_a_session_embeds_an_unknown_menu_once_in_the_background(
    tmp_path, near_tea, background
):
    """라이브러리에 없는 이름(키워드도 끝말도 없음)은 바로 분류 기본 그림을 받고, 백그라운드
    채우기가 임베딩으로 가까운 그림을 찾는다. 같은 이름은 한 번만, 다시 열 때는 부르지 않는다."""
    calls = []
    make = with_menus(
        [
            {"id": "x1", "name": "쀍뷁 한 잔", "price": 5000},
            {"id": "x2", "name": "쀍뷁 한 잔", "price": 6000},
        ]
    )
    embed = recording(calls, near_tea)
    api = TestClient(create_app(make_assistant=make, root=tmp_path, illustration_embed=embed))
    body = api.post("/assistant/sessions").json()
    assert [row["illustration_url"] for row in body["place"]["menu"]] == [
        "/illustrations/default_other.svg"
    ] * 2  # 응답은 임베딩을 기다리지 않는다
    assert calls == []
    background.run()
    sid = body["session_id"]
    urls = [r["illustration_url"] for r in api.get(f"/assistant/sessions/{sid}/place").json()[
        "place"
    ]["menu"]]
    assert urls == ["/illustrations/drink_black_tea.svg"] * 2
    assert calls == ["쀍뷁 한 잔"]
    api.get(f"/assistant/sessions/{sid}/place")
    background.run()
    assert calls == ["쀍뷁 한 잔"]


def test_store_paths_never_call_the_embedding(tmp_path, mock_index, background):
    """메뉴 탭 이름 변경·온보딩 메뉴 추가는 저장소 잠금 안에서 임베딩 없이 바로 고른다. 임베딩은
    응답 뒤 백그라운드 채우기에서만, 그림 임베딩(create_app의 illustration_embed)으로만 부른다.
    비서의 embed(기능 찾기용)는 쓰지 않는다."""
    assistant_calls, calls = [], []

    def make(path):
        def assistant_embed(text):
            assistant_calls.append(text)
            return mock_index["menu"]

        return owner.OwnerAssistant(path, chat=FakeChat(), embed=assistant_embed)

    far = [0.0] * 1023 + [1.0]  # 어느 그림과도 멀다
    embed = recording(calls, far)
    api = TestClient(create_app(make_assistant=make, root=tmp_path, illustration_embed=embed))
    body = api.post("/assistant/sessions").json()
    background.run()  # 세션 열 때의 채우기는 따로 본다
    calls.clear()
    sid, first = body["session_id"], body["place"]["menu"][0]
    row = api.patch(
        f"/assistant/sessions/{sid}/menus/{first['id']}", json={"name": "쀍뷁 수프"}
    ).json()["place"]["menu"][0]
    assert row["illustration_url"] == "/illustrations/default_soup.svg"  # 바로, 끝말로
    api.post(
        f"/assistant/sessions/{sid}/onboarding",
        json={"name": "가게", "category": "카페", "menus": [{"name": "쀍뷁 에이드", "price": 50}]},
    )
    assert calls == []  # 응답까지는 임베딩을 부르지 않았다
    background.run()
    assert sorted(calls) == ["쀍뷁 수프", "쀍뷁 에이드"]  # 잠금 밖 채우기가 한 번씩
    assert not {"쀍뷁 수프", "쀍뷁 에이드"} & set(assistant_calls)
    store = json.loads((tmp_path / sid / "store.json").read_text("utf-8"))
    saved = {m["name"]: m["illustration"] for m in store["menus"]}
    assert saved["쀍뷁 수프"] == "default_soup" and saved["쀍뷁 에이드"] == "default_ade"


def test_a_failed_embedding_is_tried_again_on_the_next_fill(tmp_path, near_tea):
    calls = []
    session = server.Session(
        "s", with_menus([{"id": "x1", "name": "쀍뷁", "price": 5000}])(tmp_path / "store.json")
    )
    session.illustration_embed = recording(calls)  # 꺼짐
    server.fill_illustrations(session)
    session.illustration_embed = recording(calls, near_tea)  # 다시 켜짐
    server.fill_illustrations(session)
    assert calls == ["쀍뷁", "쀍뷁"]
    assert session.assistant.store.read()["menus"][0]["illustration"] == "drink_black_tea"
    server.fill_illustrations(session)
    assert calls == ["쀍뷁", "쀍뷁"]  # 된 이름은 다시 부르지 않는다


def test_a_board_menu_whose_localized_name_stays_the_same_is_still_embedded(tmp_path, near_tea):
    """메뉴판 메뉴는 이름을 채우는 동안 임베딩하지 않는다. 현지화가 같은 이름을 주거나 이름을
    주지 못해 이름이 그대로여도, 채우기가 끝난 뒤의 그림 채우기가 그 이름을 임베딩해 본다."""
    calls = []
    session = server.Session(
        "s", with_menus([])(tmp_path / "store.json"), illustration_embed=recording(calls, near_tea)
    )
    store = session.assistant.store
    [saved] = store.add_board_menus([{"name": "Кисель", "price": 4000, "filling": ["name"]}])
    menu_id = saved["record"]["id"]
    assert saved["record"]["illustration"] == "default_other"
    server.fill_illustrations(session)  # 이름을 채우는 중: 아직 임베딩하지 않는다
    assert calls == []
    store.patch_menu(menu_id, {"name": "Кисель"}, by_owner=False, done=("name", "description"))
    server.fill_illustrations(session)  # 이름은 그대로지만 채우기가 끝났다
    assert calls == ["Кисель"]
    assert store.read()["menus"][0]["illustration"] == "drink_black_tea"


def test_a_board_photo_fill_is_followed_by_an_illustration_fill(tmp_path, background):
    """메뉴판 채우기(사진 일)가 끝나면 그림 채우기가 이어서 돈다."""
    session = server.Session("s", with_menus([])(tmp_path / "store.json"))
    done = []
    session.then_illustrate(lambda: done.append("board"))()
    assert done == ["board"] and len(background.queued) == 1  # 그림 채우기가 맡겨졌다


def test_an_illustration_fill_already_running_runs_once_more_instead_of_twice(
    tmp_path, background
):
    session = server.Session("s", with_menus([])(tmp_path / "store.json"))
    session.illustrate_later()
    session.illustrate_later()  # 도는 중(아직 안 끝남): 새 스레드 없이 한 번 더 표시
    assert len(background.queued) == 1
    background.run()
    session.illustrate_later()  # 끝난 뒤에는 다시 맡긴다
    assert len(background.queued) == 1


def test_default_illustrations_are_repicked_only_when_the_catalog_changes(
    tmp_path, monkeypatch
):
    """기본 그림을 받은 메뉴는 카탈로그 지문이 바뀐 뒤 처음 열 때만 다시 고른다
    (같은 이름을 세션을 열 때마다 다시 임베딩하지 않는다). 임베딩이 된 이름은 기록해 둔다."""
    calls = []

    def embed(text):
        calls.append(text)
        return [0.0] * 1023 + [1.0]  # 어느 그림과도 멀다

    make = with_menus([{"id": "x1", "name": "쀍뷁 한 잔", "price": 5000}])
    session = server.Session("s", make(tmp_path / "store.json"), illustration_embed=embed)
    server.fill_illustrations(session)
    store = session.assistant.store
    assert store.read()["menus"][0]["illustration"] == "default_other"
    assert calls == ["쀍뷁 한 잔"]

    server.fill_illustrations(session)  # 다시 열기: 지문이 그대로면 임베딩하지 않는다
    assert calls == ["쀍뷁 한 잔"]

    monkeypatch.setattr(server, "catalog_version", lambda: "grown")
    server.fill_illustrations(session)  # 카탈로그가 커졌다: 기본 그림을 한 번 다시 고른다
    assert calls == ["쀍뷁 한 잔"] * 2
    assert store.read()[server.ILLUSTRATION_VERSION_KEY] == "grown"
    server.fill_illustrations(session)
    assert calls == ["쀍뷁 한 잔"] * 2
