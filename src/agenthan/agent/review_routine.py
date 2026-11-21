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
    _norm_time,
    next_run,
    report_digest,
    run_report,
    schedule_text,
)

DRAFT_CHIP = "불만 리뷰 답글 초안"
ALERT_CHIP = "답글 초안 받기"


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

    def update(self, changes: dict) -> None:
        """사이드카 루틴 설정(#33): 사장님이 화면에서 직접 바꾼 값(enabled·frequency·weekday·time)을
        승인 카드 없이 바로 저장한다. 주기 없이 켜면 매일(시각은 기본 09:00).
        잘못된 시각이면 ValueError (아무것도 저장하지 않는다)."""
        changed = {k: v for k, v in changes.items() if v is not None}
        if "time" in changed:
            changed["time"] = _norm_time(changed["time"])
        box = self._box(create=True)
        store_id = box.ctx["store_id"]
        new = {**box.settings_store.get(store_id), **changed}
        if new.get("enabled") and not new.get("frequency"):
            new["frequency"] = "daily"
        now = self.clock()
        if new.get("frequency") == "biweekly" and (
            changed.get("frequency") or not new.get("anchor")
        ):
            new["anchor"] = now.date().isoformat()
        new["updated_at"] = now.isoformat(timespec="minutes")
        box.settings_store.save(store_id, new)

    def set_alert(self, on: bool) -> None:
        """사이드카 루틴 설정: 나쁜 리뷰 바로 알림 켜기(불만 리뷰만)·끄기.
        승인 카드 없이 저장한다."""
        box = self._box(create=True)
        store_id = box.ctx["store_id"]
        new = {**box.settings_store.get(store_id), "alert": "negative" if on else "none"}
        new["updated_at"] = self.clock().isoformat(timespec="minutes")
        box.settings_store.save(store_id, new)

    def on_new_review(self, review_id: str) -> None:
        """리뷰 도착 트리거: 알림이 켜져 있고 안 좋은(부정·혼합) 리뷰면 비서 메시지를 바로 쌓는다.
        답글 초안 칩으로 사장님이 그 자리에서 대응할 수 있게 한다."""
        mode = self.settings().get("alert", "negative")
        if mode == "none":
            return
        from agenthan.agent.review.replies import _quote, _review_sentiment, risk_of

        box = self._box(create=True)
        review = next(
            (r for r in self.a.store.read()["reviews"] if r["review_id"] == review_id), None
        )
        if review is None:
            return
        sentiment = box.ctx.get("sentiment")
        if sentiment is None:
            from agenthan.agent.review.sentiment import RuleSentiment

            sentiment = RuleSentiment()
        label = _review_sentiment({"features": [], **review}, sentiment)
        if label not in ("negative", "mixed") and mode != "every_new":
            return
        bad = label in ("negative", "mixed")
        stars = f" (★{review['rating']})" if review.get("rating") else ""
        lines = [
            ("안 좋은 리뷰가 방금 들어왔어요" if bad else "새 리뷰가 방금 들어왔어요") + stars,
            f"“{_quote(review['text'])}”",
        ]
        risk = risk_of(review["text"], label)
        if risk:
            lines.append(f"직접 연락이 필요해 보여요: {', '.join(risk['words'])}")
        if bad:
            lines.append("답글로 바로 마음을 전해 보세요.")
        text = "\n".join(lines)
        self._count += 1
        self.outbox.append(
            {
                "id": f"alert-{self._count}",
                "role": "agent",
                "text": text,
                "next_actions": [
                    {
                        "action_id": "review.draft_reply",
                        "label": ALERT_CHIP,
                        "args": {"review_id": review_id},
                    }
                ],
            }
        )
        self.a.history.append({"role": "assistant", "content": text})
        self.a._history_task.append(None)

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
