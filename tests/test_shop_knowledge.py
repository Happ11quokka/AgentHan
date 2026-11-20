"""비서 가게 지식: 대화마다 그 가게 파일(DummyStore)의 메뉴로 Part B 검색 (rag/shop.py)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agenthan.app import pilot
from agenthan.rag import LiveShopKnowledge
from agenthan.rag.shop import shop_knowledge
from agenthan.tools.dummy_store import DummyStore

PILOT = Path(pilot.PILOT_SEED)


def _store(tmp_path: Path) -> DummyStore:
    path = tmp_path / "store.json"
    shutil.copyfile(PILOT, path)
    return DummyStore(path)


def test_knows_the_shop_menus_not_the_phone_sample(tmp_path) -> None:
    store = _store(tmp_path)
    kb = LiveShopKnowledge(store.read, embed=False)
    names = {m.name_ko for m in kb.list_menu()}
    assert names == {m["name"] for m in json.loads(PILOT.read_text("utf-8"))["menus"]}
    assert kb.search_menu("꿀케이크")[0].item.name_ko == "메도빅"  # 별칭
    assert kb.search_menu("메도빅 케잌")[0].item.name_ko == "메도빅"  # 오타 허용
    assert kb.search_menu("티라미수") == []  # 없는 메뉴는 빈 목록
    assert len(kb.recent_reviews(100)) == len(store.read()["reviews"])


def test_rebuilds_after_the_shop_changes(tmp_path) -> None:
    store = _store(tmp_path)
    kb = LiveShopKnowledge(store.read, embed=False)
    assert kb.search_menu("메밀 카샤") == []
    store.upsert_menu("메밀 카샤", 4500, description="메밀을 우유에 끓인 러시아식 죽")
    assert kb.search_menu("메밀 카샤")[0].item.name_ko == "메밀 카샤"


def test_empty_menu_shop_answers_nothing(tmp_path) -> None:
    store = _store(tmp_path)
    data = store.read()
    data["menus"] = []
    store.write(data)
    kb = LiveShopKnowledge(store.read, embed=False)
    assert kb.list_menu() == [] and kb.search_menu("메도빅") == []


def test_pilot_assistant_uses_its_own_shop_knowledge(tmp_path) -> None:
    assistant = pilot.assistant(tmp_path / "s" / "store.json")
    assert isinstance(assistant.knowledge, LiveShopKnowledge)
    assert "메도빅" in {m.name_ko for m in assistant.knowledge.list_menu()}


def test_a_shop_file_with_families_finds_the_whole_family():
    """A pilot shop built from a data folder keeps families in the file (app/store_data.py)."""
    data = {
        "menus": [
            {"id": "a", "name": "꿀 케이크", "price": 8000, "family": "honey"},
            {"id": "b", "name": "초코 꿀 케이크", "price": 8500, "family": "honey"},
            {"id": "c", "name": "아메리카노", "price": 4500},
        ],
        "families": [
            {
                "id": "honey",
                "name_ko": "메도빅",
                "representative": "a",
                "members": ["a", "b", "gone"],
                "aliases": ["메도빅"],
            },
        ],
    }
    kb = shop_knowledge(data, embed=False)

    assert [h.item.id for h in kb.search_menu("메도빅")] == ["a", "b"]
    assert kb.families["honey"].members == ("a", "b"), "a deleted menu leaves the family"
