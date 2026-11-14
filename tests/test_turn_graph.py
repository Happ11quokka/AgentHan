"""OwnerAssistant turn flow as a LangGraph StateGraph (ADR-0003)."""

import copy
import json

import pytest

from agenthan.agent import owner_assistant as owner
from agenthan.agent import place_matcher as matcher
from agenthan.tools.dummy_store import SEED_PATH


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        pytest.fail("turn graph tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)


@pytest.fixture
def menu_embed(monkeypatch):
    index = {}
    for i, feature in enumerate(matcher.load_features()):
        vector = [0.0] * 1024
        vector[i] = 1.0
        index[feature["id"]] = vector
    monkeypatch.setattr(matcher, "_load_index", lambda _: index)
    return lambda _: index["menu"]


def call(name, arguments, call_id="call-1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


class FakeChat:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    def __call__(self, messages, *, tools):
        self.requests.append(copy.deepcopy((messages, tools)))
        return next(self.replies)


def menu_preview(tmp_path, menu_embed, *more):
    path = tmp_path / "store.json"
    path.write_bytes(SEED_PATH.read_bytes())
    fake = FakeChat(call("upsert_menu", {"name": "연습 차", "price": 5000}), *more)
    assistant = owner.OwnerAssistant(path, chat=fake, embed=menu_embed)
    first = assistant.ask("신메뉴 연습 차 5000원으로 등록해 줘")
    assert "바꿀까요" in first
    return assistant, fake, path


def test_graph_has_the_turn_nodes(tmp_path):
    nodes = set(owner.OwnerAssistant(tmp_path / "s.json").turns.get_graph().nodes)
    assert {"dispatch", "photo_reply", "approval", "route", "respond", "wait_approval"} <= nodes


def test_graph_draws_as_mermaid(tmp_path):
    text = owner.OwnerAssistant(tmp_path / "s.json").turns.get_graph().draw_mermaid()
    assert "wait_approval" in text and "respond" in text


def test_preview_pauses_at_interrupt_and_yes_resumes(tmp_path, menu_embed):
    assistant, fake, path = menu_preview(tmp_path, menu_embed)
    assert assistant.awaiting_owner
    assert assistant.pending_action is not None
    assert path.read_bytes() == SEED_PATH.read_bytes()

    saved = assistant.ask("네")
    assert "더미 저장 확인" in saved
    assert len(fake.requests) == 1  # Approval runs the previewed call without the model.
    assert not assistant.awaiting_owner
    menus = json.loads(path.read_text(encoding="utf-8"))["menus"]
    assert next(m for m in menus if m["name"] == "연습 차")["price"] == 5000


def test_no_drops_the_preview_and_ends_the_wait(tmp_path, menu_embed):
    assistant, _, path = menu_preview(tmp_path, menu_embed)
    assistant.ask("취소")
    assert not assistant.awaiting_owner and assistant.pending_action is None
    assert path.read_bytes() == SEED_PATH.read_bytes()


def test_other_reply_resumes_into_route_and_waits_again(tmp_path, menu_embed):
    assistant, fake, path = menu_preview(
        tmp_path, menu_embed, call("upsert_menu", {"name": "연습 차", "price": 6000}, "call-2")
    )
    again = assistant.ask("아 6000원으로")
    assert "6000" in again
    assert len(fake.requests) == 2
    assert assistant.awaiting_owner
    assistant.ask("네")
    menus = json.loads(path.read_text(encoding="utf-8"))["menus"]
    assert next(m for m in menus if m["name"] == "연습 차")["price"] == 6000


def test_each_assistant_has_its_own_thread(tmp_path, menu_embed):
    first, _, _ = menu_preview(tmp_path, menu_embed)
    other = owner.OwnerAssistant(tmp_path / "other.json", chat=FakeChat(), embed=menu_embed)
    assert first.awaiting_owner and not other.awaiting_owner


def test_a_failed_turn_does_not_stick_the_thread(tmp_path, menu_embed):
    def boom(*_, **__):
        raise RuntimeError("model down")

    assistant, _, path = menu_preview(tmp_path, menu_embed)
    assistant.chat = boom
    with pytest.raises(RuntimeError):
        assistant.ask("아 6000원으로")  # An edit voids the preview before the model call.
    assert not assistant.awaiting_owner
    assistant.chat = FakeChat(call("upsert_menu", {"name": "연습 차", "price": 6000}, "call-2"))
    assert "6000" in assistant.ask("6000원으로 해 줘")
    assert assistant.awaiting_owner
    assert "더미 저장 확인" in assistant.ask("네")
    menus = json.loads(path.read_text(encoding="utf-8"))["menus"]
    assert next(m for m in menus if m["name"] == "연습 차")["price"] == 6000
