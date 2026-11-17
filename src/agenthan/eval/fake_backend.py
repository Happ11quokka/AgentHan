"""Offline fake HCX for `scenario --backend fake`: wiring check only, never a quality measure.

Chat never calls a tool, select_feature always answers `none`, embeddings are constant, and the
review delegate gets a silent client and a canned writer, so a run needs no key and no network.
Its numbers say nothing about the model; they only prove that a suite runs to the end and
that the summary and tables are built.
"""

from datetime import date

from agenthan.agent import review_delegate
from agenthan.agent.clova_chat import CHAT_MODEL

DUMMY_ANSWER = "더미 응답: 입력을 확인해 주세요."


class _SilentReviewClient:
    """agenthan.agent.review ClovaClient shape; answers in words and never calls a tool."""

    def chat(self, model, messages, **kw):
        return {"role": "assistant", "content": DUMMY_ANSWER}


class _CannedWriter:
    def write_reply(self, text, sentiment, **kw):
        return "방문해 주셔서 감사합니다. 불편을 드려 죄송합니다."

    def translate(self, texts, lang, glossary=None):
        return list(texts)


class FakeBackend:
    model = CHAT_MODEL
    retried = 0

    def chat(self, messages, *, tools, **options):
        return {"role": "assistant", "content": DUMMY_ANSWER}

    def select(self, messages, *, tools):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "select",
                    "function": {"name": "select_feature", "arguments": '{"feature_id":"none"}'},
                }
            ],
        }

    def plan(self, model, body):
        """Shadow planner: always a valid plan that says out of scope (wiring check only)."""
        return {
            "message": {
                "role": "assistant",
                "content": '{"scope":"out","steps":[],"clarify":""}',
            }
        }

    def embed(self, text):
        return [1.0] + [0.0] * 1023

    def review_session(self, store, owner_lang: str = "ko", today: date | None = None):
        return review_delegate.new_session(
            store,
            client=_SilentReviewClient(),
            writer=_CannedWriter(),
            owner_lang=owner_lang,
            today=today,
        )
