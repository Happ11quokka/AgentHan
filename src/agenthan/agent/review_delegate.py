"""Delegate review features (review_reply, ai_review) to agenthan.agent.review.

OwnerAssistant matches the feature; this module wraps agenthan.agent.review.agent.run_turn so the
owner assistant only sees handle(utterance, session) -> {"text", "blocks"}.

- One ReviewSession per owner conversation keeps the ReviewToolbox (listed reviews,
  drafts, report suggestion) and the review conversation messages.
- System values come from here, not from the model: store_id, today, owner_lang.
- Shop data is the same DummyStore the agenthan tools use (see tools.review_adapters).
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from agenthan.agent.clova_chat import CHAT_MODEL, ClovaChatError, make_client
from agenthan.agent.review.agent import LANG_NAMES, ReviewToolbox, run_turn, system_prompt
from agenthan.agent.review.report import ReportSettingsStore
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.review_adapters import (
    DummyStoreMenuCatalog,
    DummyStoreReplyStore,
    DummyStoreReviewRepo,
)

REVIEW_FEATURES = frozenset({"review_reply", "ai_review"})

_VI = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.I)


def detect_lang(text: str, default: str = "ko") -> str:
    """Owner language from the script of the utterance (ko, ru, zh, vi, en)."""
    if re.search(r"[가-힣]", text):
        return "ko"
    if re.search(r"[А-Яа-яЁё]", text):
        return "ru"
    if re.search(r"[一-鿿]", text):
        return "zh"
    if _VI.search(text):
        return "vi"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return default


@dataclass
class ReviewSession:
    toolbox: ReviewToolbox
    store_name: str
    today: date
    owner_lang: str
    client: object = None
    model: str = CHAT_MODEL
    messages: list[dict] = field(default_factory=list)
    log: object = None

    @property
    def waiting(self) -> bool:
        """True while the owner is expected to answer (draft to confirm or edit, suggestion)."""
        return bool(self.toolbox.drafts or self.toolbox.pending_suggestion)


def new_session(
    store: DummyStore,
    *,
    client=None,
    model: str = CHAT_MODEL,
    owner_lang: str = "ko",
    today: date | None = None,
    writer=None,
    sentiment=None,
    llm=None,
    settings_store: ReportSettingsStore | None = None,
    log=None,
) -> ReviewSession:
    """Build a session over the store. With writer=None an HCX writer is made lazily."""
    today = today or date.today()
    shop = store.read()["shop"]
    if writer is None:
        from agenthan.agent.review.hcx_llm import HCXReviewLLM

        client = client or make_client()
        writer = HCXReviewLLM(model, client=client)
    toolbox = ReviewToolbox(
        shop["id"],
        today,
        DummyStoreReviewRepo(store),
        DummyStoreMenuCatalog(store),
        sentiment=sentiment,
        llm=llm,
        writer=writer,
        reply_store=DummyStoreReplyStore(store, today=today),
        owner_lang=owner_lang,
        settings_store=settings_store,
        now=datetime.combine(today, datetime.now().time()),
    )
    return ReviewSession(toolbox, shop["name"], today, owner_lang, client, model, log=log)


def handle(utterance: str, session: ReviewSession) -> dict:
    """Run one review turn. text = answer, blocks = system-attached originals/drafts."""
    lang = session.owner_lang
    if lang not in LANG_NAMES:
        lang = "ko"
    session.toolbox.owner_lang = lang
    system = {"role": "system", "content": system_prompt(session.store_name, session.today, lang)}
    if session.messages:
        session.messages[0] = system  # owner language may change between turns
    else:
        session.messages.append(system)
    session.messages.append({"role": "user", "content": utterance})
    if session.client is None:
        session.client = make_client()
    from openai import OpenAIError

    failed = False
    try:
        out = run_turn(
            session.client,
            session.model,
            session.messages,
            session.toolbox,
            log=session.log,
            owner_lang=lang,
        )
    except OpenAIError:
        failed = True
    if failed:  # outside except: the SDK error may carry a server body
        raise ClovaChatError("CLOVA 리뷰 도구 요청에 실패했습니다.")
    # run_turn returns answer + rendered block and stores only the answer in messages.
    last = session.messages[-1]
    text = last["content"] if last.get("role") == "assistant" else out
    if out.startswith(text):
        return {"text": text, "blocks": out[len(text) :].strip("\n")}
    return {"text": out, "blocks": ""}
