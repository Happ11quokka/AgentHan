"""OwnerAssistant → agenthan.agent.review delegation over the DummyStore (offline, fake HCX)."""

import json
from datetime import date
from types import SimpleNamespace as NS

import pytest

from agenthan.agent import owner_assistant as owner
from agenthan.agent import place_matcher as matcher
from agenthan.agent import review_delegate
from agenthan.agent.review.topic import resolve_from_utterance
from agenthan.tools.dummy_store import SEED_PATH, DummyStore
from agenthan.tools.review_adapters import (
    DummyStoreMenuCatalog,
    DummyStoreReplyStore,
    DummyStoreReviewRepo,
)

TODAY = date(2026, 11, 7)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)


@pytest.fixture
def mock_index(monkeypatch):
    index = {}
    for i, feature in enumerate(matcher.load_features()):
        vector = [0.0] * 1024
        vector[i] = 1.0
        index[feature["id"]] = vector
    monkeypatch.setattr(matcher, "_load_index", lambda _: index)
    return index


class SilentHCX:
    """Never calls a tool: the agenthan.agent.review router must carry the reply flow."""

    def __init__(self):
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return NS(choices=[NS(message=NS(content="네 알겠습니다", tool_calls=None))])


class FakeWriter:
    def __init__(self):
        self.calls = []

    def write_reply(self, text, sentiment, **kw):
        self.calls.append({"text": text, **kw})
        if kw.get("edit_request"):
            return kw["previous"] + " 다음엔 더 빨리 준비하겠습니다."
        return "방문해 주셔서 감사합니다. 불편을 드려 죄송합니다."

    def translate(self, texts, lang, glossary=None):
        return [f"<{lang}> {t}" for t in texts]


def factory(client):
    def make(store, owner_lang="ko"):
        return review_delegate.new_session(
            store, client=client, writer=FakeWriter(), owner_lang=owner_lang, today=TODAY
        )

    return make


class NoChat:
    def __call__(self, *args, **kwargs):
        pytest.fail("review features must not use the agenthan tool loop")


def test_review_flow_list_draft_edit_post(tmp_path, mock_index):
    path = tmp_path / "store.json"
    seed = SEED_PATH.read_bytes()
    hcx = SilentHCX()
    review = lambda _: mock_index["review_reply"]  # noqa: E731
    assistant = owner.OwnerAssistant(path, chat=NoChat(), embed=review, review_session=factory(hcx))
    out = assistant.ask("답글 안 단 리뷰 있어?")
    assert out.startswith("답글 안 단 리뷰는 2건")  # seed: r02 and r03 have no reply
    assert "**답글 안 단 리뷰**" in out and assistant.pending_feature is None
    out = assistant.ask("안 좋은 리뷰에 답글 써줘")
    assert out.startswith("초안을 만들었어요") and "**답글 초안**" in out
    assert assistant.pending_feature == "review_reply"  # waiting for confirm/edit
    assert next(iter(assistant.review.toolbox.drafts)) == "r03"  # the portion-size review
    # Follow-ups without a feature keyword stay in the review flow.
    other = lambda _: [0.0] * 1023 + [1.0]  # noqa: E731
    assistant.embed = other
    assert assistant.ask("더 정중하게 고쳐줘").startswith("말씀하신 대로 고쳤어요")
    assert not path.exists()  # nothing saved before confirmation
    out = assistant.ask("이대로 올려줘")
    assert out.startswith("답글 1건을 등록했어요") and assistant.pending_feature is None
    saved = next(r for r in json.loads(path.read_text())["reviews"] if r["review_id"] == "r03")
    assert saved["has_owner_reply"] is True
    assert saved["owner_reply"]["status"] == "saved_not_sent"
    assert saved["owner_reply"]["reply"].endswith("더 빨리 준비하겠습니다.")
    assert SEED_PATH.read_bytes() == seed


def test_handle_splits_answer_and_blocks(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    session = factory(SilentHCX())(store, owner_lang="en")
    out = review_delegate.handle("Are there reviews I haven't replied to?", session)
    assert out["text"].startswith("There are 2 reviews without a reply")
    assert out["blocks"].startswith("**Reviews without a reply**")
    assert session.messages[0]["role"] == "system" and "English" in session.messages[0]["content"]


def test_adapters_share_store_data(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    rows = DummyStoreReviewRepo(store).get_reviews("dummy-shop", None, TODAY)
    assert [r["review_id"] for r in rows][:3] == ["r03", "r02", "r01"]
    assert rows[0]["store"] == "샘플 러시안 베이커리" and rows[0]["category"] == "베이커리·카페"
    assert (
        DummyStoreReviewRepo(store).get_reviews("dummy-shop", date(2026, 11, 4), TODAY)[0][
            "review_id"
        ]
        == "r03"
    )
    # A menu registered through upsert_menu is matched in review questions right away.
    store.upsert_menu("유자 에이드", 5500)
    catalog = DummyStoreMenuCatalog(store)
    assert "유자 에이드" in [m["name"] for m in catalog.menus("dummy-shop")]
    found = resolve_from_utterance("유자 에이드 반응 어때?", "dummy-shop", catalog)
    assert found["menus"] == ["유자 에이드"]
    replies = DummyStoreReplyStore(store, today=TODAY)
    assert not replies.has_reply(rows[0]) and replies.has_reply(rows[-1])


def test_old_store_reviews_are_migrated(tmp_path):
    path = tmp_path / "store.json"
    old = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    old["reviews"] = [
        {"id": "review-1", "review_text": "맛있어요.", "reply_text": "감사합니다", "status": "s"},
        {"id": "review-2", "review_text": "늦어요.", "reply_text": "", "status": "unanswered"},
    ]
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    reviews = DummyStore(path).read()["reviews"]
    assert reviews[0]["review_id"] == "review-1" and reviews[0]["has_owner_reply"] is True
    assert reviews[1]["text"] == "늦어요." and reviews[1]["has_owner_reply"] is False


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("답글 써줘", "ko"),
        ("Reply to the bad one", "en"),
        ("Напиши ответ", "ru"),
        ("lần sau tặng nước uống", "vi"),
        ("回复差评", "zh"),
        ("123", "ko"),
    ],
)
def test_detect_lang(text, lang):
    assert review_delegate.detect_lang(text) == lang
