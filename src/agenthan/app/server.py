"""FastAPI backend for the owner screen (ADR 0002).

Wraps OwnerAssistant without changing it: one assistant and one store file per session.

    uv run --env-file .env uvicorn agenthan.app.server:app
"""

import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app import place

DEFAULT_ROOT = Path(".agenthan/sessions")


class Message(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("빈 메시지입니다.")
        return value


class Session:
    def __init__(self, assistant: OwnerAssistant):
        self.assistant = assistant
        self.lock = threading.Lock()  # OwnerAssistant keeps turn state; one turn at a time.

    def view(self) -> dict:
        data = self.assistant.store.read()
        waiting = [t for t in self.assistant.ledger.tasks if t.is_open and t.preview]
        return {
            "place": place.snapshot(data),
            "pending": [
                {
                    "task_id": t.id,
                    **place.preview(data, t.feature, t.preview.get("actions") or [t.preview]),
                }
                for t in waiting
            ],
        }


def create_app(
    make_assistant: Callable[[Path], OwnerAssistant] = OwnerAssistant,
    root: str | Path = DEFAULT_ROOT,
) -> FastAPI:
    app = FastAPI(title="AgentHan owner backend")
    sessions: dict[str, Session] = {}
    root = Path(root)

    def get(session_id: str) -> Session:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "없는 대화입니다.")
        return session

    @app.post("/assistant/sessions")
    def start() -> dict:
        session_id = uuid.uuid4().hex
        folder = root / session_id
        folder.mkdir(parents=True)
        session = sessions[session_id] = Session(make_assistant(folder / "store.json"))
        return {"session_id": session_id, **session.view()}

    @app.post("/assistant/sessions/{session_id}/messages")
    def send(session_id: str, message: Message) -> dict:
        session = get(session_id)
        with session.lock:
            reply = session.assistant.ask(message.text)
            return {"reply": reply, **session.view()}

    @app.get("/assistant/sessions/{session_id}/place")
    def show(session_id: str) -> dict:
        session = get(session_id)
        with session.lock:
            return session.view()

    return app


app = create_app()
