import copy
import json

import pytest

from agenthan.agent import feature_selector as fs
from agenthan.agent import owner_assistant as owner
from agenthan.agent import place_matcher as matcher
from agenthan.agent.clova_chat import ClovaChatError, ClovaRequestRejected


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)


@pytest.fixture
def index(monkeypatch):
    vectors = {}
    for i, feature in enumerate(matcher.load_features()):
        vector = [0.0] * 1024
        vector[i] = 1.0
        vectors[feature["id"]] = vector
    monkeypatch.setattr(matcher, "_load_index", lambda _: vectors)
    return vectors


def picked(feature_id, name="select_feature"):
    arguments = json.dumps({"feature_id": feature_id})
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "s", "function": {"name": name, "arguments": arguments}}],
    }


class Select:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, messages, *, tools):
        self.requests.append(copy.deepcopy((messages, tools)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_candidates_are_top5_without_threshold_and_keep_pending(index):
    ids, ranked = fs.candidates("아무 말", lambda _: index["menu"])
    assert len(ids) == fs.TOP_K and ids[0] == "menu"
    assert len(ranked) == len(matcher.load_features())
    ids, _ = fs.candidates("아무 말", lambda _: index["menu"], keep="learning_support")
    assert ids[0] == "learning_support" and len(ids) == fs.TOP_K


def test_select_sends_candidates_as_enum_and_reads_choice(index):
    select = Select(picked("reservation"))
    ids = ["menu", "reservation"]
    choice = fs.select("예약 확정", ids, select_chat=select, context="비서가 물었다: 몇 번?")
    assert choice == fs.Selection("reservation", "feature", ("menu", "reservation"))
    messages, tools = select.requests[0]
    enum = tools[0]["function"]["parameters"]["properties"]["feature_id"]["enum"]
    assert enum == ["menu", "reservation", "none", "clarify"]
    assert "직전 대화: 비서가 물었다" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "예약 확정"}


@pytest.mark.parametrize(
    "reply",
    [
        picked("coupon"),  # Outside the candidates.
        picked("menu", name="other_tool"),
        {"role": "assistant", "content": "메뉴입니다."},
        ClovaRequestRejected("400"),
        ClovaChatError("down"),
    ],
)
def test_unusable_answers_fall_back(reply):
    assert fs.select("메뉴", ["menu"], select_chat=Select(reply)) is None


def test_no_candidates_skip_the_call():
    select = Select()
    assert fs.select("x", [], select_chat=select) is None and select.requests == []


def test_default_select_chat_forces_dash_tool():
    assert fs.default_select_chat.keywords == {
        "model": "HCX-DASH-002",
        "tool_choice": {"type": "function", "function": {"name": "select_feature"}},
    }


def test_assistant_uses_selection_none_and_clarify(tmp_path, index):
    select = Select(picked("none"), picked("clarify"), picked("menu"))
    main = Select({"role": "assistant", "content": "메뉴 이름과 가격을 알려주세요."})
    assistant = owner.OwnerAssistant(
        tmp_path / "store.json", chat=main, embed=lambda _: index["menu"], select_chat=select
    )
    assert assistant.ask("오늘 날씨 어때?") == owner.HELP
    assert assistant.trace["via"] == "select" and assistant.trace["feature"] is None
    answer = assistant.ask("이거 바꾸고 싶어요")
    assert assistant.trace["via"] == "clarify" and "골라 말씀해 주세요" in answer
    assert matcher.get_feature("menu")["name_ko"] in answer
    assert "메뉴 이름" in assistant.ask("메뉴요")
    # The clarify question is passed to DASH as context for the answer.
    assert "비서가 되물었다" in select.requests[2][0][0]["content"]
    assert main.requests[0][1][0]["function"]["name"] == "upsert_menu"


def test_pending_question_is_context_and_candidate(tmp_path, index):
    select = Select(picked("menu"), picked("menu"))
    main = Select(
        {"role": "assistant", "content": "가격을 알려주세요."},
        {"role": "assistant", "content": "가격을 다시 알려주세요."},
    )
    assistant = owner.OwnerAssistant(
        tmp_path / "store.json",
        chat=main,
        embed=lambda _: index["learning_support"],
        select_chat=select,
    )
    assistant.ask("메뉴 등록")
    assistant.ask("5000원")
    messages, tools = select.requests[1]
    assert "기능에서 물었다: 가격을 알려주세요." in messages[0]["content"]
    assert tools[0]["function"]["parameters"]["properties"]["feature_id"]["enum"][0] == "menu"


def test_dash_failure_uses_previous_thresholded_rule(tmp_path, index):
    select = Select(ClovaChatError("down"))
    assistant = owner.OwnerAssistant(
        tmp_path / "store.json",
        chat=Select({"role": "assistant", "content": "무엇을 등록할까요?"}),
        embed=lambda _: index["menu"],
        select_chat=select,
    )
    assistant.ask("메뉴")
    assert assistant.trace["via"] == "fallback" and assistant.trace["feature"] == "menu"
