"""FastAPI backend for the owner screen (ADR 0002).

Wraps OwnerAssistant without changing it: one assistant and one store file per session.

    uv run --env-file .env uvicorn agenthan.app.server:app
"""

import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, field_validator

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app import pilot, place, screens

DEFAULT_ROOT = Path(".agenthan/sessions")
DRAFT_PHOTO_PREFIX = "draft-"  # photo URL name of the n-th waiting draft: draft-<n>


class Message(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("빈 메시지입니다.")
        return value


class ButtonAction(BaseModel):
    action_id: str
    args: dict = {}
    label: str


class Session:
    def __init__(self, session_id: str, assistant: OwnerAssistant):
        self.id = session_id
        self.assistant = assistant
        self.lock = threading.Lock()  # OwnerAssistant keeps turn state; one turn at a time.

    def _photo_path(self, name: str) -> str:
        return f"/assistant/sessions/{self.id}/photos/{name}"

    def _menu_photo_url(self, record: dict) -> str | None:
        return self._photo_path(record["id"]) if record.get("photo") else None

    def view(self) -> dict:
        data = self.assistant.store.read()
        waiting = [
            t for t in self.assistant.ledger.tasks if t.is_open and t.preview and t.kind != "review"
        ]
        pending = [
            {
                "task_id": t.id,
                "version": t.version,
                **place.preview(
                    data,
                    t.feature,
                    t.preview.get("actions") or [t.preview],
                    self._menu_photo_url,
                ),
            }
            for t in waiting
        ]
        pending.extend(
            {
                "task_id": t.id,
                "version": t.version,
                "feature": "review",
                "kind": "review",
                "review_id": t.preview["review_id"],
                "reply": t.preview["draft"]["reply_ko"],
            }
            for t in self.assistant.ledger.tasks
            if t.is_open and t.preview and t.preview.get("button_review")
        )
        drafts = self.assistant.menu_drafts
        photo_task = next(
            (t for t in self.assistant.ledger.tasks if t.is_open and t.kind == "photo"), None
        )
        if photo_task is not None and drafts:
            pending.append(
                {
                    "task_id": photo_task.id,
                    **place.photo_preview(
                        data,
                        drafts,
                        lambda i: self._photo_path(f"{DRAFT_PHOTO_PREFIX}{i}"),
                        self._menu_photo_url,
                    ),
                }
            )
        return {"place": place.snapshot(data, self._menu_photo_url), "pending": pending}

    def photo(self, name: str) -> tuple[bytes, str] | None:
        """A waiting draft's photo or a saved menu's photo, as (bytes, media type)."""
        if name.startswith(DRAFT_PHOTO_PREFIX):
            drafts = self.assistant.menu_drafts or []
            index = name.removeprefix(DRAFT_PHOTO_PREFIX)
            if not index.isdigit() or int(index) >= len(drafts):
                return None
            draft = drafts[int(index)]
            return (draft.photo, draft.photo_mime) if draft.photo is not None else None
        folder = self.assistant.store.path.parent.resolve()
        record = next((m for m in self.assistant.store.read()["menus"] if m["id"] == name), None)
        if record is None or not record.get("photo"):
            return None
        path = (folder / record["photo"]).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            return None
        mime = "image/png" if path.suffix == ".png" else "image/jpeg"
        return path.read_bytes(), mime


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
        session = sessions[session_id] = Session(session_id, make_assistant(folder / "store.json"))
        return {"session_id": session_id, **session.view()}

    @app.post("/assistant/sessions/{session_id}/messages")
    def send(session_id: str, message: Message) -> dict:
        session = get(session_id)
        with session.lock:
            reply = session.assistant.ask(message.text)
            return {"reply": reply, **session.view()}

    @app.post("/assistant/sessions/{session_id}/actions")
    def run_action(session_id: str, action: ButtonAction) -> dict:
        session = get(session_id)
        with session.lock:
            try:
                result = session.assistant.run_action(action.action_id, action.args, action.label)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {**result, **session.view()}

    @app.post("/assistant/sessions/{session_id}/photos")
    def send_photo(
        session_id: str,
        image: Annotated[UploadFile, File()],
        text: Annotated[str, Form()] = "",
    ) -> dict:
        session = get(session_id)
        data = image.file.read()
        if not data:
            raise HTTPException(422, "빈 사진입니다.")
        with session.lock:
            reply = session.assistant.ask_photo(data, text)
            return {"reply": reply, **session.view()}

    @app.get("/assistant/sessions/{session_id}/photos/{name}")
    def show_photo(session_id: str, name: str) -> Response:
        session = get(session_id)
        with session.lock:
            found = session.photo(name)
        if found is None:
            raise HTTPException(404, "없는 사진입니다.")
        content, mime = found
        return Response(content, media_type=mime)

    @app.get("/assistant/sessions/{session_id}/place")
    def show(session_id: str) -> dict:
        session = get(session_id)
        with session.lock:
            return session.view()

    @app.get("/assistant/sessions/{session_id}/reviews")
    def show_reviews(session_id: str) -> dict:
        session = get(session_id)
        with session.lock:
            return screens.reviews(session.assistant.store.read())

    @app.get("/assistant/sessions/{session_id}/report")
    def show_report(session_id: str, period: Literal["week", "day"] = "week") -> dict:
        session = get(session_id)
        with session.lock:
            return screens.report(session.assistant.store.read(), period)

    return app


app = create_app(make_assistant=pilot.assistant)
