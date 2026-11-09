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
    # The utterance is framed as data to classify, not a question to answer.
    assert messages[-1] == {"role": "user", "content": '분류할 사장님 요청:\n"""예약 확정"""'}


@pytest.mark.parametrize(
    "reply",
    [
        picked("coupon"),  # Outside the candidates.
        picked("menu", name="other_tool"),
        {"role": "assistant", "content": "메뉴입니다."},
        ClovaChatError("down"),
    ],
)
def test_unusable_answers_fall_back(reply):
    assert fs.select("메뉴", ["menu"], select_chat=Select(reply)) is None


def test_rejection_retries_once_as_text_answer():
    # DASH rejects the forced call (40009) at random; the retry has no tools and reads the ID.
    select = Select(ClovaRequestRejected("400"), {"role": "assistant", "content": "ai_review: AI"})
    choice = fs.select("리뷰 요약", ["review_reply", "ai_review"], select_chat=select)
    assert choice == fs.Selection("ai_review", "feature", ("review_reply", "ai_review"))
    (first, first_tools), (retry, retry_tools) = select.requests
    assert first_tools and retry_tools == []
    assert "항상 select_feature만 호출한다" in first[0]["content"]
    assert "select_feature" not in retry[0]["content"]
    assert "답은 후보 ID, none, clarify 중 하나만 쓴다" in retry[0]["content"]
    assert retry[1] == first[1]  # Same utterance, same framing.


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("none", fs.Selection(None, "none", ("menu",))),
        ('select_feature("clarify")', fs.Selection(None, "clarify", ("menu",))),
        ("menu — 메뉴 등록", fs.Selection("menu", "feature", ("menu",))),
        ("coupon", None),  # Outside the candidates.
        ("메뉴입니다.", None),
    ],
)
def test_text_retry_reads_first_allowed_id(content, expected):
    select = Select(ClovaRequestRejected("400"), {"role": "assistant", "content": content})
    assert fs.select("메뉴", ["menu"], select_chat=select) == expected


@pytest.mark.parametrize("retry", [ClovaRequestRejected("400"), ClovaChatError("down")])
def test_failed_text_retry_falls_back(retry):
    select = Select(ClovaRequestRejected("400"), retry)
    assert fs.select("메뉴", ["menu"], select_chat=select) is None and len(select.requests) == 2


def test_text_retry_feature_needs_confident_score():
    # DASH often rejects unrelated questions it wants to answer; a weak text pick must not stick.
    def run(confident):
        reply = {"role": "assistant", "content": "menu"}
        select = Select(ClovaRequestRejected("400"), reply)
        return fs.select("김치찌개 만드는 법", ["menu"], select_chat=select, confident=confident)

    assert run(set()) is None  # Caller falls back to the thresholded rule.
    assert run({"menu"}) == fs.Selection("menu", "feature", ("menu",))
    none = Select(ClovaRequestRejected("400"), {"role": "assistant", "content": "none"})
    assert fs.select("x", ["menu"], select_chat=none, confident=set()).decision == "none"


def test_assistant_passes_threshold_features_as_confident(tmp_path, index, monkeypatch):
    seen = {}

    def fake_select(text, ids, **kw):
        seen.update(kw)
        return fs.Selection(None, "none", tuple(ids))

    monkeypatch.setattr(owner.feature_selector, "select", fake_select)
    assistant = owner.OwnerAssistant(
        tmp_path / "store.json", chat=Select(), embed=lambda _: index["menu"], select_chat=Select()
    )
    assistant.route("메뉴")
    assert seen["confident"] == {"menu"}


def test_read_choice_takes_the_earliest_whole_id():
    ids = ["review_reply", "ai_review"]
    assert fs.read_choice("ai_review 또는 review_reply", ids) == "ai_review"
    assert fs.read_choice("my_ai_review_x", ids) is None


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
