"""FastAPI backend for the owner screen (ADR 0002).

Wraps OwnerAssistant without changing it: one assistant and one store file per session.

    uv run --env-file .env uvicorn agenthan.app.server:app
"""

import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, field_validator

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.agent.review import inbox
from agenthan.agent.review_routine import ReviewRoutine
from agenthan.app import pilot, place, screens
from agenthan.tools.dummy_store import DummyStoreError

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


def _filled(value: str) -> str:
    if not value.strip():
        raise ValueError("빈 값입니다.")
    return " ".join(value.split())


class OnboardingMenu(BaseModel):
    name: str
    price: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def filled(cls, value: str) -> str:
        return _filled(value)


class Onboarding(BaseModel):
    """처음 쓰는 사장님 온보딩(#94). 사장님이 넣은 값만 온다. 메뉴는 0~3개."""

    name: str
    category: str
    menus: list[OnboardingMenu] = Field(default=[], max_length=3)

    @field_validator("name", "category")
    @classmethod
    def filled(cls, value: str) -> str:
        return _filled(value)


class ReplyOp(BaseModel):
    """답글 대기함 카드 버튼. draft: 그 리뷰 초안 받기(없거나 실패했을 때만 새로 쓴다).
    text: 사장님이 고친 문장(post), hint: inbox.HINTS 값(redraft)."""

    op: Literal["draft", "post", "skip", "redraft"]
    text: str | None = None
    hint: str | None = None


class Session:
    def __init__(
        self,
        session_id: str,
        assistant: OwnerAssistant,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.id = session_id
        self.assistant = assistant
        assistant.clock = clock  # 리뷰 보고 미리보기의 '다음 보고' 시각도 같은 시계로
        # 리뷰 정기 보고(#98): 조회할 때마다 기한 지난 보고를 대화 메시지로 쌓는다
        self.routine = ReviewRoutine(assistant, clock)
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
        pending.extend(
            {
                "task_id": t.id,
                "version": t.version,
                "feature": "review",
                "kind": "settings",
                "after": None,
                "changes": {},
                "settings": {"before": t.preview["before"], "after": t.preview["after"]},
            }
            for t in self.assistant.ledger.tasks
            if t.is_open and t.preview and t.preview.get("report_settings")
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
        shop = data.get("shop", {})
        self.routine.tick()
        return {
            "place": place.snapshot(data, self._menu_photo_url),
            "pending": pending,
            # 화면이 온보딩을 띄울지 정한다(#94): 이름이나 업종이 비면 처음 쓰는 가게.
            "shop": {k: shop.get(k) or "" for k in ("name", "category")},
            # 리뷰 정기 보고(#98): 지금 설정 한 줄과, 사장님 말 없이 비서가 보낸 새 메시지
            # (정해진 때의 보고·[지금 받아 보기]). messages는 한 번만 건넨다.
            "review_report": self.routine.status(),
            "messages": self.routine.take(),
        }

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


def claim_drafts(session: Session, review_ids: list[str] | None = None) -> Callable[[], None]:
    """답글 대기함: 대상을 먼저 '생성 중'으로 잡고(그 사이 /reviews가 preparing을 보게) 초안을 쓸
    함수를 돌려준다. 세션 잠금 안에서 부르고, 돌려받은 함수는 잠금 밖 백그라운드에서 돌린다."""
    box = inbox.inbox_of(session.assistant)
    claimed = box.claim_all(review_ids if review_ids is not None else box.targets())
    return lambda: box.write_claimed(claimed)


def in_background(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True).start()


def create_app(
    make_assistant: Callable[[Path], OwnerAssistant] = OwnerAssistant,
    root: str | Path = DEFAULT_ROOT,
    prepare_on_start: bool = False,
    clock: Callable[[], datetime] = datetime.now,
) -> FastAPI:
    """prepare_on_start: 세션을 시작하면 답글 없는 리뷰의 초안을 백그라운드로 미리 쓴다.
    clock: 리뷰 정기 보고가 보는 지금 시각(테스트가 시간을 흉내 낸다)."""
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
        session = sessions[session_id] = Session(
            session_id, make_assistant(folder / "store.json"), clock
        )
        if prepare_on_start:
            with session.lock:
                job = claim_drafts(session)
            in_background(job)
        return {"session_id": session_id, **session.view()}

    @app.post("/assistant/sessions/{session_id}/messages")
    def send(session_id: str, message: Message) -> dict:
        session = get(session_id)
        with session.lock:
            reply = session.assistant.ask(message.text)
            result = {"reply": reply, **session.view()}
            if session.assistant.agent_mode == "planner":
                result["next_actions"] = session.assistant.next_actions
            return result

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

    @app.post("/assistant/sessions/{session_id}/onboarding")
    def onboard(session_id: str, body: Onboarding) -> dict:
        """처음 쓰는 사장님(#94): 사장님이 넣은 가게 이름·업종(shop)과 대표 메뉴(menus)를 그대로
        저장한다. 대화 턴이 아니고 네이버로 보내지 않는다(더미 저장소만)."""
        session = get(session_id)
        with session.lock:
            store = session.assistant.store
            try:
                store.set_shop_info(body.name, body.category)
                for menu in body.menus:
                    store.upsert_menu(menu.name, menu.price)
            except DummyStoreError as exc:
                raise HTTPException(422, str(exc)) from exc
            return session.view()

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
            view = screens.reviews(session.assistant.store.read())
            return inbox.annotate(session.assistant, view)

    @app.post("/assistant/sessions/{session_id}/reviews/simulate")
    def simulate_review(session_id: str) -> dict:
        """데모: 더미 리뷰 한 건을 더하고 그 리뷰의 초안만 백그라운드로 쓴다."""
        session = get(session_id)
        with session.lock:
            store = session.assistant.store
            review_id = inbox.inbox_of(session.assistant).simulate_review(store)
            job = claim_drafts(session, [review_id])
            view = inbox.annotate(session.assistant, screens.reviews(store.read()))
        in_background(job)
        return {"review_id": review_id, **view}

    @app.post("/assistant/sessions/{session_id}/reviews/report")
    def run_review_report(session_id: str) -> dict:
        """리뷰 탭 [지금 받아 보기](시연): 설정과 상관없이 정기 보고 한 회분을 대화 메시지로
        넣는다(messages). 보고 설정·다음 보고 시각은 바꾸지 않는다."""
        session = get(session_id)
        with session.lock:
            session.routine.run_now()
            return session.view()

    @app.post("/assistant/sessions/{session_id}/reviews/{review_id}/reply")
    def reply_to_review(session_id: str, review_id: str, body: ReplyOp) -> dict:
        """답글 대기함 카드: 초안 받기·올리기·건너뛰기·다시 쓰기. 대화 턴이 아니다: 비서의 대화
        기록·작업 원장·trace·planner를 거치지 않고 review/inbox.py와 답글 저장 경로만 쓴다.
        → {review: 그 리뷰(+draft), summary, error?: {reason, message, issues}}"""
        session = get(session_id)
        if body.op == "redraft" and body.hint not in inbox.HINTS:
            raise HTTPException(400, "다시 쓰기 방식을 확인해 주세요.")
        if body.op != "post" and body.text is not None:
            raise HTTPException(400, "고친 문장은 올리기에만 보낼 수 있어요.")
        with session.lock:
            box = inbox.inbox_of(session.assistant)
            if not box.has_review(review_id):
                raise HTTPException(404, "없는 리뷰입니다.")
            job = None
            if body.op == "post":
                outcome = box.post(review_id, body.text)
            elif body.op == "skip":
                outcome = box.skip(review_id)
            elif body.op == "draft":
                outcome, job = box.begin_draft(review_id)
            else:
                outcome, job = box.begin_redraft(review_id, body.hint)
        if job is not None:
            outcome = job()  # HCX 호출은 세션 잠금 밖에서 (그 리뷰는 inbox가 잡아 둠)
        with session.lock:
            view = inbox.annotate(
                session.assistant, screens.reviews(session.assistant.store.read())
            )
        row = next(r for r in view["reviews"] if r["id"] == review_id)
        result = {"review": row, "summary": view["summary"]}
        if not outcome["ok"]:
            result["error"] = {k: outcome[k] for k in ("reason", "message", "issues")}
        return result

    @app.get("/assistant/sessions/{session_id}/report")
    def show_report(session_id: str, period: Literal["week", "day"] = "week") -> dict:
        session = get(session_id)
        with session.lock:
            return screens.report(session.assistant.store.read(), period)

    return app


app = create_app(make_assistant=pilot.assistant, prepare_on_start=True)
