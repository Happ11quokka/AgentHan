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

from agenthan.agent import menu_board
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.agent.review import inbox
from agenthan.agent.review_routine import ReviewRoutine
from agenthan.app import pilot, place, screens
from agenthan.tools.dummy_store import DummyStoreError, watch

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


class ReportRoutine(BaseModel):
    """리뷰 정기 보고 설정 중 바꿀 값만. 빠진 값은 그대로 둔다."""

    enabled: bool | None = None
    frequency: Literal["daily", "weekly", "biweekly", "monthly"] | None = None
    weekday: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] | None = None
    time: str | None = None


class MenuEdit(BaseModel):
    """메뉴 탭에서 사장님이 직접 고친 칸만. 대화 턴이 아니고 승인 카드도 없다."""

    name: str | None = None
    price: int | None = Field(default=None, gt=0)
    description: str | None = None


class Routines(BaseModel):
    """사이드카 루틴 설정(#33). report: 리뷰 정기 보고, drafts: 답글 초안 미리 쓰기,
    alert: 나쁜 리뷰 바로 알림."""

    report: ReportRoutine | None = None
    drafts: bool | None = None
    alert: bool | None = None


class Session:
    def __init__(
        self,
        session_id: str,
        assistant: OwnerAssistant,
        clock: Callable[[], datetime] = datetime.now,
        drafts: bool = False,
    ):
        self.id = session_id
        self.assistant = assistant
        # 답글 초안 미리 쓰기(#33): 켜져 있으면 새 리뷰마다 초안을 백그라운드로 미리 쓴다
        self.drafts = drafts
        assistant.clock = clock  # 리뷰 보고 미리보기의 '다음 보고' 시각도 같은 시계로
        # 리뷰 정기 보고(#98): 조회할 때마다 기한 지난 보고를 대화 메시지로 쌓는다
        self.routine = ReviewRoutine(assistant, clock)
        self.lock = threading.Lock()  # OwnerAssistant keeps turn state; one turn at a time.

    def _photo_path(self, name: str) -> str:
        return f"/assistant/sessions/{self.id}/photos/{name}"

    def _menu_photo_url(self, record: dict) -> str | None:
        if not record.get("photo"):
            return None
        # 흰 배경 사진을 새로 만들면 버전이 올라 화면이 예전 사진을 캐시에서 꺼내지 않는다.
        return f"{self._photo_path(record['id'])}?v={record.get('photo_version') or 0}"

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
        settings = self.routine.settings()
        return {
            "place": place.snapshot(data, self._menu_photo_url),
            "pending": pending,
            # 화면이 온보딩을 띄울지 정한다(#94): 이름이나 업종이 비면 처음 쓰는 가게.
            "shop": {k: shop.get(k) or "" for k in ("name", "category")},
            # 리뷰 정기 보고(#98): 지금 설정 한 줄과, 사장님 말 없이 비서가 보낸 새 메시지
            # (정해진 때의 보고·[지금 받아 보기]). messages는 한 번만 건넨다.
            "review_report": self.routine.status(),
            "messages": self.routine.take(),
            # 사이드카 루틴 설정(#33): 화면 스위치·주기·시각 선택이 보여 줄 지금 값
            "routines": {
                "drafts": self.drafts,
                # 나쁜 리뷰 바로 알림: 리뷰가 들어오면 안 좋은 리뷰를 비서가 먼저 알린다
                "alert": settings.get("alert", "negative") != "none",
                "report": {k: settings.get(k) for k in ("enabled", "frequency", "weekday", "time")},
            },
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
    on_save: Callable[[dict], None] | None = None,
) -> FastAPI:
    """prepare_on_start: 세션을 시작하면 답글 없는 리뷰의 초안을 백그라운드로 미리 쓴다.
    clock: 리뷰 정기 보고가 보는 지금 시각(테스트가 시간을 흉내 낸다).
    on_save: 대화의 가게 파일을 저장할 때마다 그 가게 한 벌로 부른다(대화·버튼·메뉴 탭·사진의
        백그라운드 채우기 모두). 통합 서버가 전화용 가게로 게시한다(전화 메뉴 티켓 02). 대화를
        시작할 때 파일럿 가게를 깔아 두는 것과 미리보기 사본은 저장으로 치지 않는다."""
    app = FastAPI(title="AgentHan owner backend")
    sessions: dict[str, Session] = {}
    root = Path(root)

    def get(session_id: str) -> Session:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "없는 대화입니다.")
        return session

    @app.post("/assistant/sessions")
    def start(menu: Literal["empty"] | None = None) -> dict:
        """menu=empty: 시연용. 메뉴만 비운 가게로 시작한다(메뉴판 사진으로 채우는 장면)."""
        session_id = uuid.uuid4().hex
        folder = root / session_id
        folder.mkdir(parents=True)
        session = sessions[session_id] = Session(
            session_id, make_assistant(folder / "store.json"), clock, drafts=prepare_on_start
        )
        if on_save is not None:
            watch(session.assistant.store.path, on_save)
        if menu == "empty":
            store = session.assistant.store
            data = store.read()
            data["menus"] = []
            store.write(data)
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
            job = session.assistant.take_photo_job()  # "메도빅 사진 더 예쁘게" → 사진 다듬기
            result = {"reply": reply, **session.view()}
            if session.assistant.agent_mode == "planner":
                result["next_actions"] = session.assistant.next_actions
        if job is not None:
            in_background(job)
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
            job = session.assistant.take_photo_job()
            result = {"reply": reply, **session.view()}
        if job is not None:
            in_background(job)  # 설명·흰 배경 사진 채우기. 화면은 /place로 채워지는 걸 본다.
        return result

    @app.patch("/assistant/sessions/{session_id}/menus/{menu_id}")
    def edit_menu(session_id: str, menu_id: str, body: MenuEdit) -> dict:
        """메뉴 탭에서 사장님이 직접 고친 값. 세션 잠금 없이 저장소 잠금만 쓴다(대화 턴이
        길어도 바로 저장된다)."""
        session = get(session_id)
        fields = body.model_dump(exclude_none=True)
        if not fields:
            raise HTTPException(422, "고칠 값이 없습니다.")
        try:
            record = session.assistant.store.patch_menu(menu_id, fields, by_owner=True)
        except DummyStoreError as exc:
            raise HTTPException(422, str(exc)) from exc
        if record is None:
            raise HTTPException(404, "없는 메뉴입니다.")
        return session.view()

    @app.post("/assistant/sessions/{session_id}/menus/{menu_id}/polish")
    def polish_menu_photo(session_id: str, menu_id: str) -> dict:
        """메뉴 탭 [사진 다듬기] 2차: CPU 스튜디오 보정(몇 초), GPU 서버가 있으면 SEED Omni(1~2분).
        message: 사장님께 보일 한 줄(시작했어요 / 이미 다듬는 중 / 사진이 없어요)."""
        session = get(session_id)
        try:
            message, job = menu_board.start_polish(session.assistant.store, menu_id)
        except DummyStoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        if job is not None:
            in_background(job)
        return {"message": message, "started": job is not None, **session.view()}

    @app.delete("/assistant/sessions/{session_id}/menus/{menu_id}")
    def delete_menu(session_id: str, menu_id: str) -> dict:
        session = get(session_id)
        try:
            if not session.assistant.store.delete_menu(menu_id):
                raise HTTPException(404, "없는 메뉴입니다.")
        except DummyStoreError as exc:
            raise HTTPException(422, str(exc)) from exc
        return session.view()

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
            session.routine.on_new_review(review_id)  # 리뷰 도착 트리거: 나쁜 리뷰면 바로 알림
            # 초안 미리 쓰기가 꺼져 있으면 사장님이 카드에서 [답글 초안 받기]를 누를 때까지 둔다
            job = claim_drafts(session, [review_id]) if session.drafts else None
            view = inbox.annotate(session.assistant, screens.reviews(store.read()))
        if job is not None:
            in_background(job)
        return {"review_id": review_id, **view}

    @app.put("/assistant/sessions/{session_id}/routines")
    def set_routines(session_id: str, body: Routines) -> dict:
        """사이드카 루틴 설정(#33): 사장님이 화면 스위치로 직접 바꾼 값이라 승인 카드 없이 저장한다.
        초안 미리 쓰기를 켜는 순간 답글 없는 리뷰의 초안을 백그라운드로 쓰기 시작한다."""
        session = get(session_id)
        job = None
        with session.lock:
            if body.report is not None:
                try:
                    session.routine.update(body.report.model_dump())
                except ValueError as error:
                    raise HTTPException(400, "보고 시각을 확인해 주세요.") from error
            if body.alert is not None:
                session.routine.set_alert(body.alert)
            if body.drafts is not None:
                if body.drafts and not session.drafts:
                    job = claim_drafts(session)
                session.drafts = body.drafts
            view = session.view()
        if job is not None:
            in_background(job)
        return view

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

    @app.get("/assistant/sessions/{session_id}/reviews/stats")
    def show_review_stats(
        session_id: str, period: Literal["day", "week", "month"] = "week"
    ) -> dict:
        """통계 탭 › AI 리뷰 통계: 좋아요·아쉬워요 특징 TOP3와 테마별 합계."""
        session = get(session_id)
        with session.lock:
            return screens.review_stats(session.assistant.store.read(), period)

    @app.get("/assistant/sessions/{session_id}/suggestions")
    def show_suggestions(session_id: str) -> dict:
        """왼쪽 [비서 제안]: 지난주 통계와 리뷰로 고른 해 볼 일 카드."""
        session = get(session_id)
        with session.lock:
            return screens.suggestions(session.assistant.store.read())

    @app.get("/assistant/sessions/{session_id}/report")
    def show_report(session_id: str, period: Literal["week", "day"] = "week") -> dict:
        session = get(session_id)
        with session.lock:
            return screens.report(session.assistant.store.read(), period)

    return app


app = create_app(make_assistant=pilot.assistant, prepare_on_start=True)
