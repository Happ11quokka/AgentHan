"""리뷰 정기 보고 루틴(#98): 보고가 켜진 대화에 정해진 때 보고 한 회분을 비서 메시지로 넣는다.

따로 도는 스케줄러는 없다. 서버가 세션을 조회할 때마다(대화 턴·버튼 응답·/place) tick()을
부르고, 다음 보고 시각이 지났으면 run_report 결과를 메시지 하나로 쌓는다. 화면은 /place를
주기적으로 읽어 쌓인 메시지를 받는다(take, 한 번 건넨 메시지는 다시 주지 않는다).

같은 회차는 한 번만 보낸다: 마지막으로 보낸 시각(또는 지금 설정을 처음 본 시각) 뒤의 첫 보고
시각만 본다. 오래 조회가 없었으면 밀린 회차를 몰아 보내지 않고 한 번만 보낸다.
설정은 대화(리뷰 세션)의 메모리에만 있다. 서버를 다시 켜면 대화와 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from agenthan.agent.review.report import (
    DEFAULTS,
    next_run,
    report_digest,
    run_report,
    schedule_text,
)

DRAFT_CHIP = "불만 리뷰 답글 초안"


class ReviewRoutine:
    def __init__(self, assistant, clock: Callable[[], datetime] = datetime.now):
        self.a = assistant
        self.clock = clock
        self.outbox: list[dict] = []  # 아직 화면에 건네지 않은 비서 메시지
        self.last_sent: datetime | None = None  # 마지막 정기 보고를 보낸 시각
        self._seen: tuple | None = None  # 마지막으로 본 보고 일정
        self._since: datetime | None = None  # 그 일정을 처음 본 시각
        self._count = 0

    def _box(self, create: bool = False):
        session = self.a.review
        if session is None and create:
            loop = getattr(self.a, "planner_loop", None)
            if loop is not None:
                session = loop.session()
            else:
                session = self.a.review = self.a._review_factory(
                    self.a.store, owner_lang=self.a.owner_lang or "ko"
                )
        return session.toolbox if session is not None else None

    def settings(self) -> dict:
        box = self._box()
        if box is None:  # 리뷰 세션이 아직 없으면 보고를 켠 적도 없다
            return dict(DEFAULTS)
        return box.settings_store.get(box.ctx["store_id"])

    def _due(self, settings: dict) -> datetime | None:
        base = max(t for t in (self._since, self.last_sent) if t is not None)
        return next_run(settings, base)

    def tick(self) -> None:
        """다음 보고 시각이 지났으면 보고 한 회분을 쌓는다."""
        settings, now = self.settings(), self.clock()
        key = tuple(settings.get(k) for k in ("enabled", "frequency", "weekday", "time", "anchor"))
        if key != self._seen:  # 일정이 바뀌면 그때부터 센다 (바뀌기 전 회차는 보내지 않는다)
            self._seen, self._since = key, now
        due = self._due(settings)
        if due is not None and due <= now:
            self._deliver(settings, now, "리뷰 정기 보고")
            self.last_sent = now

    def run_now(self) -> None:
        """[지금 받아 보기]: 설정과 상관없이 한 회분을 쌓는다. 설정·일정은 바꾸지 않는다."""
        self._box(create=True)
        settings = self.settings()
        if not settings.get("frequency"):
            settings = {**settings, "frequency": "weekly"}
        self._deliver(settings, self.clock(), "리뷰 보고")

    def status(self) -> dict:
        """리뷰 탭 한 줄: '매일 09:00 리뷰 요약 받는 중' / '정기 보고 꺼짐'."""
        settings = self.settings()
        when = schedule_text(settings)
        due = self._due(settings) if when and self._since else None
        return {
            "enabled": bool(when),
            "schedule": when,
            "next_run": due.isoformat(timespec="minutes") if due else None,
            "text": f"{when} 리뷰 요약 받는 중" if when else "정기 보고 꺼짐",
        }

    def take(self) -> list[dict]:
        out, self.outbox = self.outbox, []
        return out

    def _deliver(self, settings: dict, now: datetime, title: str) -> None:
        box = self._box(create=True)
        # 보고 기간의 기준일은 가게 데이터(리뷰 스냅샷)의 오늘이다. 시계는 언제 보낼지에만 쓴다
        # (시계 날짜로 바꾸면 스냅샷 뒤 기간을 봐서 '리뷰 0건'이 됨, 2026-11-21 검수)
        result = run_report(**box.ctx, settings=settings)
        text = report_digest(result, title)
        ids = result["unanswered"].get("negative_ids") or []
        chips = (
            [
                {
                    "action_id": "review.draft_reply",
                    "label": DRAFT_CHIP,
                    "args": {"review_id": ids[0]},
                }
            ]
            if ids
            else []
        )
        self._count += 1
        self.outbox.append(
            {"id": f"report-{self._count}", "role": "agent", "text": text, "next_actions": chips}
        )
        # 다음 턴의 대화 맥락에도 남긴다 (사장님 말 없이 비서가 먼저 한 말)
        self.a.history.append({"role": "assistant", "content": text})
        self.a._history_task.append(None)
