"""OwnerAssistant → agenthan.agent.review delegation over the DummyStore (offline, fake HCX)."""

import json
from datetime import date

import pytest

from agenthan.agent import owner_assistant as owner
from agenthan.agent import place_matcher as matcher
from agenthan.agent import review_delegate
from agenthan.agent.review.topic import resolve_from_utterance
from agenthan.agent.review.translate import Translator
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

    def chat(self, model, messages, **kw):  # ClovaClient.chat과 같은 모양
        self.calls.append({"model": model, "messages": messages, **kw})
        return {"role": "assistant", "content": "네 알겠습니다"}


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
    review = lambda _: mock_index["review"]  # noqa: E731
    assistant = owner.OwnerAssistant(path, chat=NoChat(), embed=review, review_session=factory(hcx))
    out = assistant.ask("답글 안 단 리뷰 있어?")
    assert out.startswith("답글 안 단 리뷰는 2건")  # seed: r02 and r03 have no reply
    assert "**답글 안 단 리뷰**" in out and assistant.pending_feature is None
    out = assistant.ask("안 좋은 리뷰에 답글 써줘")
    assert out.startswith("초안을 만들었어요") and "**답글 초안**" in out
    assert assistant.pending_feature == "review"  # waiting for confirm/edit
    assert next(iter(assistant.review.toolbox.drafts)) == "r03"  # the portion-size review
    # Follow-ups without a feature keyword stay in the review flow.
    other = lambda _: [0.0] * 1023 + [1.0]  # noqa: E731
    assistant.embed = other
    assert assistant.ask("더 정중하게 고쳐줘").startswith("말씀하신 대로 고쳤어요")
    assert not path.exists()  # nothing saved before confirmation
    assert assistant.route("응 그대로 등록해").via == "review_draft"  # no DASH guess needed
    assert assistant.route("영업시간은 그대로 두고 메뉴 가격 바꿔 줘").via != "review_draft"
    out = assistant.ask("이대로 올려줘")
    assert out.startswith("답글 1건을 등록했어요") and assistant.pending_feature is None
    assert assistant.route("응 그대로 등록해").via != "review_draft"  # nothing waits now
    saved = next(r for r in json.loads(path.read_text())["reviews"] if r["review_id"] == "r03")
    assert saved["has_owner_reply"] is True
    assert saved["owner_reply"]["status"] == "saved_not_sent"
    assert saved["owner_reply"]["reply"].endswith("더 빨리 준비하겠습니다.")
    assert SEED_PATH.read_bytes() == seed


def test_pending_drafts_version_goes_up_on_edit(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    session = factory(SilentHCX())(store)
    assert session.pending_drafts == []
    review_delegate.handle("답글 안 단 리뷰 있어?", session)
    review_delegate.handle("안 좋은 리뷰에 답글 써줘", session)
    assert session.pending_drafts == [{"review_id": "r03", "version": 1}]
    review_delegate.handle("더 정중하게 고쳐줘", session)
    assert session.pending_drafts == [{"review_id": "r03", "version": 2}]
    # 읽기 전용 사본: 바꿔도 세션의 초안은 그대로다
    session.pending_drafts[0]["version"] = 99
    assert session.toolbox.drafts["r03"]["version"] == 2
    review_delegate.handle("이대로 올려줘", session)
    assert session.pending_drafts == [] and not session.waiting
    assert session.toolbox.draft_versions == {"r03": 2}  # 등록 뒤에도 번호는 줄지 않는다


def test_draft_version_stays_out_of_tool_result(tmp_path):
    """도구 결과(모델에 돌아가는 값)는 그대로라 녹화 재생과 프롬프트가 바뀌지 않는다."""
    session = factory(SilentHCX())(DummyStore(tmp_path / "store.json"))
    res = session.toolbox.dispatch("draft_review_reply", {"target": "negative"}, "답글 써줘")
    assert res["drafts"] and all("version" not in d for d in res["drafts"])
    assert session.pending_drafts == [{"review_id": "r03", "version": 1}]


class TagTranslator:
    """번역 대신 '<언어>' 꼬리표만 붙인다. 영어 → 한국어 입력은 고정 문장으로 바꾼다."""

    name = "tag"

    def translate(self, texts, source, target):
        if target == "ko":
            return ["답글 안 단 리뷰 있어?" for _ in texts]
        return [f"<{target}> {t}" for t in texts]


def test_handle_splits_answer_and_blocks(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    session = factory(SilentHCX())(store, owner_lang="en")
    session.translator = Translator(None, TagTranslator())
    out = review_delegate.handle("Are there reviews I haven't replied to?", session)
    # 안에서는 한국어로 처리하고 사장님 언어로는 출력만 번역한다 (run_turn)
    assert out["text"].startswith("<en> 답글 안 단 리뷰는 2건")
    assert "**" in out["blocks"] and "<en>" in out["blocks"]
    assert session.messages[1]["content"] == "답글 안 단 리뷰 있어?"  # HCX는 한국어 대화만 본다
    assert "한국어" in session.messages[0]["content"]


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


def _drafted(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    session = factory(SilentHCX())(store)
    review_delegate.handle("안 좋은 리뷰에 답글 써줘", session)
    assert session.pending_drafts == [{"review_id": "r03", "version": 1}]
    return store, session


def _replied(store, review_id):
    return next(r for r in store.read()["reviews"] if r["review_id"] == review_id)[
        "has_owner_reply"
    ]


@pytest.mark.parametrize("yes", ["네", "응", "네 좋아요", "yes", "그렇게 해 주세요"])
def test_bare_yes_posts_right_after_confirm_question(tmp_path, yes):
    store, session = _drafted(tmp_path)
    out = review_delegate.handle(yes, session)
    assert out["text"].startswith("답글 1건을 등록했어요") and _replied(store, "r03")


def test_bare_yes_after_revision_posts_revised_draft(tmp_path):
    store, session = _drafted(tmp_path)
    review_delegate.handle("더 정중하게 고쳐 주세요", session)
    review_delegate.handle("응", session)
    reply = next(r for r in store.read()["reviews"] if r["review_id"] == "r03")["owner_reply"]
    assert reply["reply"].endswith("더 빨리 준비하겠습니다.")  # FakeWriter의 고친 판


def test_bare_yes_ignored_after_another_tool(tmp_path):
    """확인을 물은 뒤 다른 일을 했으면 '네'는 무엇에 대한 답인지 모르므로 등록하지 않는다."""
    store, session = _drafted(tmp_path)
    review_delegate.handle("답글 안 단 리뷰 있어?", session)
    review_delegate.handle("네", session)
    assert not _replied(store, "r03") and session.pending_drafts  # 초안은 남아 있다


def test_yes_with_edit_is_a_revision(tmp_path):
    store, session = _drafted(tmp_path)
    out = review_delegate.handle("네 근데 더 짧게 해 주세요", session)
    assert out["text"].startswith("말씀하신 대로 고쳤어요") and not _replied(store, "r03")
    assert session.pending_drafts == [{"review_id": "r03", "version": 2}]


@pytest.mark.parametrize(
    "no", ["이 답글은 안 올릴게요", "그 답글 취소해 주세요", "등록 취소해 주세요", "됐어요"]
)
def test_decline_discards_drafts(tmp_path, no):
    store, session = _drafted(tmp_path)
    out = review_delegate.handle(no, session)
    assert out["text"].startswith("알겠어요. 답글 초안은 올리지 않고 지웠어요")
    assert session.pending_drafts == [] and not session.waiting
    # 취소한 초안은 나중 승인에 섞여 올라가지 않는다
    review_delegate.handle("네 올려 주세요", session)
    assert not _replied(store, "r03")


@pytest.mark.parametrize(
    "text",
    ["예약 취소해 주세요", "취소하고 다시 써 주세요", "아니 아직 올리지 마", "올리지 말고 기다려"],
)
def test_other_cancel_words_keep_drafts(tmp_path, text):
    _, session = _drafted(tmp_path)
    review_delegate.handle(text, session)
    # 다른 기능의 취소, 다시 쓰기, 보류('아직 올리지 마')는 초안 취소가 아니다
    assert session.pending_drafts


def test_post_tool_needs_confirm_words_unless_question_pending():
    from agenthan.agent.review.replies import MockReplyStore, post_review_reply

    drafts = {"r1": {"review_id": "r1", "reply_ko": "감사합니다", "checks": {"passed": True}}}
    store = MockReplyStore(today=TODAY)
    assert post_review_reply(drafts=drafts, store=store, utterance="네")["error"] == (
        "not_confirmed"
    )
    out = post_review_reply(drafts=drafts, store=store, utterance="안 올릴게요 등록 취소")
    assert out["error"] == "not_confirmed"
    out = post_review_reply(drafts=drafts, store=store, utterance="네", allow_bare_yes=True)
    assert out["posted_count"] == 1


def test_bare_yes_after_hold_does_not_post(tmp_path):
    """보류('아직 올리지 마') 다음의 '네'는 확인 질문에 대한 답이 아니다."""
    store, session = _drafted(tmp_path)
    review_delegate.handle("아니 아직 올리지 마", session)
    review_delegate.handle("네", session)
    assert not _replied(store, "r03") and session.pending_drafts
    review_delegate.handle("이대로 올려 주세요", session)
    assert _replied(store, "r03")
