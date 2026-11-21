"""재배포 뒤에도 비서 대화가 이어진다 (2026-11-22).

재배포로 서버의 대화 목록(메모리)과 대화 폴더가 사라지자, 브라우저가 모르는 대화(404)로 보고 기본
메뉴의 새 대화를 열었다. 메뉴판으로 올린 메뉴는 DB 사본(assistant_store_docs)에 남아 있었다.
이제 서버가 모르는 대화라도 가게 파일이나 DB 사본이 있으면 그 가게로 되살린다.
"""

import json

import pytest
from fastapi.testclient import TestClient

import test_owner_assistant as base
from agenthan.agent import owner_assistant as owner
from agenthan.app.server import create_app
from agenthan.tools import dummy_store
from agenthan.tools.dummy_store import DummyStore

FakeChat = base.FakeChat
mock_index, offline_by_default = base.mock_index, base.offline_by_default  # fixtures

DRINKS = [{"id": "d1", "name": "아메리카노", "price": 4500, "aliases": []},
          {"id": "d2", "name": "카페라떼", "price": 5000, "aliases": []}]


@pytest.fixture
def make(mock_index):
    def make(path):
        return owner.OwnerAssistant(path, chat=FakeChat(), embed=lambda _: mock_index["menu"])

    return make


def _menus(client: TestClient, sid: str) -> list[str]:
    place = client.get(f"/assistant/sessions/{sid}/place")
    assert place.status_code == 200
    return [m["name"] for m in place.json()["place"]["menu"]]


def test_a_restarted_server_reopens_a_chat_from_its_store_file(tmp_path, make) -> None:
    before = TestClient(create_app(make_assistant=make, root=tmp_path))
    sid = before.post("/assistant/sessions", params={"menu": "empty"}).json()["session_id"]
    store = DummyStore(tmp_path / sid / "store.json")
    data = store.read()
    data["menus"] = DRINKS
    store.write(data)

    after = TestClient(create_app(make_assistant=make, root=tmp_path))  # 새 서버: 대화 목록 없음
    assert _menus(after, sid) == ["아메리카노", "카페라떼"]


def test_a_redeployed_server_reopens_a_chat_from_the_db_copy(tmp_path, make, monkeypatch) -> None:
    """폴더까지 사라졌어도 DB 사본이 있으면 그 가게로 연다."""
    sid = "e6f136e3c7154a33a81d0d80efb43909"
    path = (tmp_path / sid / "store.json").resolve()
    saved = {**DummyStore(tmp_path / "seed.json").read(), "menus": DRINKS}
    monkeypatch.setattr(dummy_store, "_db_copy",
                        lambda p: json.loads(json.dumps(saved)) if p.resolve() == path else None)

    client = TestClient(create_app(make_assistant=make, root=tmp_path))
    assert _menus(client, sid) == ["아메리카노", "카페라떼"]


@pytest.mark.parametrize("sid", ["0" * 32, "../etc", "E6F136E3C7154A33A81D0D80EFB43909"])
def test_an_unknown_chat_without_a_saved_store_is_still_missing(tmp_path, make, sid) -> None:
    client = TestClient(create_app(make_assistant=make, root=tmp_path))
    assert client.get(f"/assistant/sessions/{sid}/place").status_code == 404
    assert not (tmp_path / sid).exists()
