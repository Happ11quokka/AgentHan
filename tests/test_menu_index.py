"""Store knowledge RAG over the session's own menus (ADR-0008).

The index is built from the shop copy the tool works on and rebuilt whenever its menus
change. Uses fixtures/dummy_store (same format as the real data folder)."""

import pytest

from agenthan.app import knowledge, pilot
from agenthan.tools import menu_index
from agenthan.tools.feature_tools import execute_feature_tool

DUMMY = "fixtures/dummy_store"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHAN_DATA_DIR", DUMMY)
    return pilot.assistant(tmp_path / "store.json").store


def menu(store, **args):
    return execute_feature_tool(store, "upsert_menu", args)


def names(store):
    return {m["name"]: m for m in store.read()["menus"]}


def test_a_typo_finds_the_menu(store):
    result = menu(store, name="키에프 케이크", action="sold_out")

    assert result["ok"] and result["record"]["name"] == "키예프 케이크"


def test_a_family_name_asks_which_menu(store):
    result = menu(store, name="꿀케이크", action="sold_out")

    assert result["error"] == "ambiguous_target"
    assert {c["name"] for c in result["candidates"]} >= {"메도빅", "초코 메도빅", "메도빅 홀케이크"}
    assert not any(m.get("sold_out") for m in store.read()["menus"])


def test_a_longer_name_with_a_menu_inside_is_a_new_menu(store):
    result = menu(store, name="메도빅 미니", price=6000)

    assert result["action"] == "created"
    assert names(store)["메도빅"]["price"] != 6000


def test_a_menu_added_in_the_session_is_found_by_typo(store):
    menu(store, name="오렌지 머핀", price=4000)

    result = menu(store, name="오랜지 머핀", action="sold_out")

    assert result["ok"] and result["record"]["name"] == "오렌지 머핀"


def test_a_deleted_menu_is_not_found_any_more(store):
    assert menu(store, name="키에프 케이크", action="sold_out")["ok"]  # index has 키예프
    menu(store, name="키예프 케이크", action="delete")

    result = menu(store, name="키에프 케이크", action="sold_out")

    assert result["error"] == "menu_not_found"


def test_the_index_is_rebuilt_only_when_menus_change(store):
    first = menu_index.index_for(store.read())
    assert menu_index.index_for(store.read()) is first

    menu(store, name="나폴레옹", price=9900)

    assert menu_index.index_for(store.read()) is not first


def test_the_shop_file_carries_search_fields_and_owner_edits(store):
    menu(store, name="프라하", new_name="체코 초코 케이크")

    data = store.read()
    assert data["families"], "families from the data folder are in the shop file"
    praga = menu_index.index_for(data).facts("praga")
    assert praga["name"] == "체코 초코 케이크"
    assert praga["category"]


def test_mentions_count_a_family_name_for_its_representative(store):
    index = menu_index.index_for(store.read())

    found = index.mentions("꿀케이크도 맛있고 초코 메도빅이랑 아아도 좋았어요")

    assert found == ("medovik-choco", "medovik", "americano")


def test_without_the_rag_only_exact_names_match(tmp_path):
    store = pilot.assistant(tmp_path / "store.json").store
    menu_index.configure(None)

    assert menu(store, name="메도빅크", action="sold_out")["error"] == "menu_not_found"


def test_the_pilot_shop_without_a_data_folder_still_resolves_aliases(tmp_path):
    store = pilot.assistant(tmp_path / "store.json").store  # made-up shop, ids m01…

    result = menu(store, name="메도비크으", action="sold_out")

    assert result["ok"] and result["record"]["name"] == "메도빅"


def test_a_reply_draft_gets_the_facts_of_the_menus_its_review_names(store):
    from datetime import date

    from agenthan.agent.review.replies import draft_review_reply
    from agenthan.tools.review_adapters import DummyStoreMenuCatalog, DummyStoreReviewRepo

    data = store.read()
    data["reviews"][0]["text"] = "꿀케이크랑 아아 맛있었어요"
    store.write(data)
    seen = {}

    class Writer:
        def write_reply(self, text, sentiment, **kw):
            seen.update(kw)
            return "맛있게 드셔 주셔서 감사합니다!"

    draft_review_reply(
        store_id="s",
        today=date(2026, 11, 9),
        repo=DummyStoreReviewRepo(store),
        catalog=DummyStoreMenuCatalog(store),
        review_ids=[data["reviews"][0]["review_id"]],
        writer=Writer(),
    )

    assert [f["name"] for f in seen["facts"]] == ["메도빅", "아메리카노"]
    assert all(f["price"] > 0 for f in seen["facts"])


# ---------------------------------------------------------------- vectors only for reading


class CountingEmbed:
    """Fake embedding: 수플레/폭신 words point one way, everything else the other."""

    def __init__(self):
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return [1.0 if "수플레" in text or "폭신" in text else 0.0, 1.0]


@pytest.fixture
def embed(store):
    fake = CountingEmbed()
    menu_index.configure(knowledge.index_factory(embed=fake))
    return fake


def test_writes_never_search_by_meaning(store, embed):
    result = menu(store, name="폭신한 수플레 같은 거", action="sold_out")

    assert result["error"] == "menu_not_found"
    assert embed.texts == [], "no embedding call on the write path"


def test_a_question_that_names_a_menu_needs_no_vectors(store, embed):
    index = menu_index.index_for(store.read())

    found = index.lookup("꿀케이크 선물 포장 되나요?")

    assert found.named and found.ids == ("medovik",)
    assert embed.texts == []


def test_a_question_that_names_no_menu_finds_related_menus_by_meaning(store, embed):
    index = menu_index.index_for(store.read())

    found = index.lookup("폭신한 수플레 같은 거 있어요?")

    assert not found.named and found.ids[0] == "ptichye-moloko"
    assert index.open_search.vector.enabled, "the read-only search may use vectors"


def test_a_menu_change_re_embeds_only_that_menu(store, tmp_path):
    fake = CountingEmbed()
    cache = tmp_path / "embeddings.json"
    data = store.read()
    knowledge.RagMenuIndex(data, fake, cache).lookup("폭신한 거")
    first = len(fake.texts)

    changed = {**data, "menus": [dict(m) for m in data["menus"]]}
    changed["menus"][0]["description"] = "새로 바꾼 설명"
    knowledge.RagMenuIndex(changed, fake, cache).lookup("폭신한 거")

    assert first > 1 and len(fake.texts) - first == 1


def test_a_talktalk_answer_marks_menus_found_by_meaning_as_only_related(store, embed):
    from agenthan.agent.owner_assistant import knowledge_slice, store_slice

    data = store.read()
    views = [{"message": "폭신한 수플레 같은 케이크 있나요?"}]
    values = knowledge_slice("talktalk", data, store_slice("talktalk", data), "문의 답장", views)

    assert "menu_facts" not in values
    assert values["related_menu_facts"][0]["name"] == "버드밀크 케이크"
    assert "단정하지 말 것" in values["related_note"]
