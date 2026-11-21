"""답글 대기함: 답글 없는 리뷰마다 초안을 미리 달아 둔다.

사장님은 카드마다 올리기·고치기·건너뛰기만 고른다. 초안은 대화 흐름과 같은 생성 경로
(replies.draft_review_reply, 리뷰 위임의 HCX-005 writer)와 같은 검사(replies.check_reply)로 만든다.
대화 흐름의 초안(ReviewToolbox.drafts)과는 따로 둔다: 대화로 부탁한 승인 카드 흐름은 그대로다.

상태: ready(검사 통과) | needs_check(검사 실패, issues) | posted | skipped | failed(생성 실패).
리뷰 원천은 세션의 더미 가게뿐이다. 데모용으로 simulate_review가 더미 리뷰를 한 건 더한다.
"""

from __future__ import annotations

import threading

from .agent import menu_glossary
from .replies import check_reply, draft_review_reply, post_review_reply

# 다시 쓰기 칩이 고를 수 있는 지시. 이 값만 받는다 (자유 문장은 대화로).
HINTS = {
    "shorter": "답글을 지금보다 짧게, 두 문장 안으로 줄이세요.",
    "more_apologetic": "답글을 지금보다 더 정중하게, 불편에 대한 사과가 분명히 드러나게 고치세요.",
    "warmer": "답글을 지금보다 더 따뜻하고 다정한 말투로 고치세요.",
}
WAITING = ("ready", "needs_check")

# 데모용 새 리뷰. 차례로 돌려 쓴다 (실제 리뷰 원천 연동 없음).
DEMO_REVIEWS = (
    (2, "케이크가 너무 달고 포장이 찌그러져서 왔어요. 다음엔 신경 써 주세요."),
    (5, "메도빅이 촉촉하고 커피랑 잘 어울려요. 직원분도 친절했어요."),
    (3, "맛은 괜찮았는데 자리가 좁고 주문이 오래 걸렸어요."),
    (1, "주문한 케이크가 아니라 다른 게 나왔어요. 실망입니다."),
)


class ReplyInbox:
    """review_id → {"text", "status", "issues"}. 같은 리뷰를 두 스레드가 동시에 쓰지 않는다."""

    def __init__(self, toolbox):
        self.toolbox = toolbox
        self.drafts: dict[str, dict] = {}
        self._working: set[str] = set()
        self._lock = threading.Lock()
        self._simulated = 0

    # ------------------------------------------------------------ 읽기
    @property
    def preparing(self) -> int:
        with self._lock:
            return len(self._working)

    def draft(self, review_id: str) -> dict | None:
        with self._lock:
            d = self.drafts.get(review_id)
            return dict(d, issues=list(d["issues"])) if d else None

    def _rows(self) -> list[dict]:
        box = self.toolbox
        return box.ctx["repo"].get_reviews(box.ctx["store_id"], None, box.ctx["today"])

    def _row(self, review_id: str) -> dict | None:
        return next((r for r in self._rows() if r["review_id"] == review_id), None)

    # ------------------------------------------------------------ 만들기
    def _claim(self, review_id: str, *, again: bool = False) -> bool:
        """이 리뷰의 초안을 지금 쓸 차례면 True. again이 아니면 있는 초안은 다시 쓰지 않는다."""
        with self._lock:
            if review_id in self._working or (not again and review_id in self.drafts):
                return False
            self._working.add(review_id)
            return True

    def _write(self, review_id: str, edit: str | None = None) -> dict:
        """초안 하나를 쓰고 저장한다. 실패하면 failed로 남기고 예외는 밖으로 내지 않는다."""
        box = self.toolbox
        try:
            previous = self.drafts.get(review_id, {}).get("text") if edit else None
            out = draft_review_reply(
                **box.ctx,
                review_ids=[review_id],
                writer=box.writer,
                owner_lang="ko",
                glossary=menu_glossary(box.ctx["catalog"], box.ctx["store_id"], box.owner_lang),
                edit_request=edit,
                previous={review_id: previous} if previous else None,
                profile=box.profile(),
                recent=box.reply_store.recent_replies(),
            )
            made = next((d for d in out.get("drafts", []) if d.get("reply_ko")), None)
            if made is None:
                entry = {"text": "", "status": "failed", "issues": []}
            else:
                passed = made["checks"]["passed"]
                entry = {
                    "text": made["reply_ko"],
                    "status": "ready" if passed else "needs_check",
                    "issues": [] if passed else list(made["checks"]["issues"]),
                }
        except Exception:  # 한 리뷰가 실패해도 나머지는 계속 (HCX 오류, 키 없음 등)
            entry = {"text": "", "status": "failed", "issues": []}
        with self._lock:
            self.drafts[review_id] = entry
            self._working.discard(review_id)
        return dict(entry)

    def targets(self, limit: int = 10) -> list[str]:
        """답글 없고 초안도 없는 리뷰, 최신순 최대 limit개."""
        rows = [r for r in self._rows() if not r.get("has_owner_reply")]
        rows.sort(key=lambda r: r["visit_date"], reverse=True)
        with self._lock:
            ids = [
                r["review_id"]
                for r in rows
                if r["review_id"] not in self.drafts and r["review_id"] not in self._working
            ]
        return ids[:limit]

    def prepare(self, review_ids: list[str]) -> list[str]:
        """주어진 리뷰들의 초안을 차례로 만든다. 실제로 만든 review_id를 돌려준다."""
        done = []
        for rid in review_ids:
            if self._claim(rid):
                self._write(rid)
                done.append(rid)
        return done

    def claim_all(self, review_ids: list[str]) -> list[str]:
        """백그라운드로 넘기기 전에 생성 중으로 표시한다 (그 사이 /reviews가 preparing을 보게)."""
        return [rid for rid in review_ids if self._claim(rid)]

    def write_claimed(self, review_ids: list[str]) -> None:
        for rid in review_ids:
            self._write(rid)

    # ------------------------------------------------------------ 사장님 결정
    # 카드 버튼 전용 (server의 /reviews/{id}/reply). 대화 턴이 아니므로 OwnerAssistant의 대화 기록·
    # 작업 원장·trace·planner를 거치지 않는다. 결과는 Outcome: ok, reason(실패 까닭 코드), message,
    # issues(검사에 걸린 항목). reason: checks(검사 거절) | replied(이미 답글) | busy(초안 쓰는 중)
    # | empty(올릴 문장 없음) | save_failed | draft_failed.

    @staticmethod
    def _outcome(reason: str | None, message: str, issues: list[str] | None = None) -> dict:
        return {"ok": reason is None, "reason": reason, "message": message, "issues": issues or []}

    def has_review(self, review_id: str) -> bool:
        return self._row(review_id) is not None

    def post(self, review_id: str, text: str | None = None) -> dict:
        """초안(또는 사장님이 고친 문장)을 검사한 뒤 올린다."""
        row = self._row(review_id)
        if row is None:
            return self._outcome("not_found", "답글을 올릴 리뷰를 찾을 수 없어요.")
        if row.get("has_owner_reply"):
            return self._outcome("replied", "이미 답글이 달린 리뷰예요.")
        with self._lock:
            if review_id in self._working:
                return self._outcome(
                    "busy", "아직 초안을 쓰는 중이에요. 잠시 뒤에 다시 눌러 주세요."
                )
            current = self.drafts.get(review_id)
        edited = text is not None
        reply = text.strip() if edited else (current or {}).get("text", "")
        if not reply:
            return self._outcome("empty", "올릴 답글이 없어요. 초안을 다시 받아 주세요.")
        recent = self.toolbox.reply_store.recent_replies()
        checks = check_reply(reply, recent)
        if not checks["passed"]:
            issues = list(checks["issues"])
            if not edited and current is not None:  # 고친 문장이 막히면 초안은 그대로 둔다
                with self._lock:
                    self.drafts[review_id] = {**current, "status": "needs_check", "issues": issues}
            return self._outcome("checks", f"검사에 걸렸어요({', '.join(issues)})", issues)
        result = post_review_reply(
            drafts={review_id: {"review_id": review_id, "reply_ko": reply, "checks": checks}},
            store=self.toolbox.reply_store,
            utterance="네",
            allow_bare_yes=True,
        )
        if result.get("posted_count") != 1:
            return self._outcome("save_failed", "답글을 저장하지 못했어요. 다시 시도해 주세요.")
        with self._lock:
            self.drafts[review_id] = {"text": reply, "status": "posted", "issues": []}
        # 대화 흐름에 남은 같은 리뷰의 초안은 이제 낡았으므로 버린다 (대화로 두 번 올리지 않게).
        self.toolbox.drafts.pop(review_id, None)
        return self._outcome(None, "답글을 올렸어요.")

    def skip(self, review_id: str) -> dict:
        row = self._row(review_id)
        if row is None:
            return self._outcome("not_found", "건너뛸 리뷰를 찾을 수 없어요.")
        if row.get("has_owner_reply"):
            return self._outcome("replied", "이미 답글이 달린 리뷰예요.")
        with self._lock:
            current = self.drafts.get(review_id) or {"text": "", "issues": []}
            self.drafts[review_id] = {**current, "status": "skipped"}
        return self._outcome(None, "이 리뷰는 건너뛸게요.")

    def begin_draft(self, review_id: str):
        """카드 [답글 초안 받기]를 잡는다. → (Outcome, None) 또는 (None, 초안을 쓸 함수).

        쓸 만한 초안(문장 있음, 실패 아님)이 이미 있으면 다시 쓰지 않고 ok만 돌려준다. 초안이 없거나
        실패했거나 문장 없이 건너뛴 리뷰는 새로 쓴다. 잠금 다루기는 begin_redraft와 같다."""
        row = self._row(review_id)
        if row is None:
            return self._outcome("not_found", "초안을 쓸 리뷰를 찾을 수 없어요."), None
        if row.get("has_owner_reply"):
            return self._outcome("replied", "이미 답글이 달린 리뷰예요."), None
        with self._lock:
            current = self.drafts.get(review_id)
            usable = bool(current and current["text"] and current["status"] != "failed")
        if usable and review_id not in self._working:
            return self._outcome(None, "초안이 이미 있어요."), None
        if not self._claim(review_id, again=True):
            return self._outcome(
                "busy", "아직 초안을 쓰는 중이에요. 잠시 뒤에 다시 눌러 주세요."
            ), None

        def job() -> dict:
            entry = self._write(review_id)
            if entry["status"] == "failed":
                return self._outcome("draft_failed", "초안을 쓰지 못했어요.")
            return self._outcome(None, "초안을 썼어요.")

        return None, job

    def begin_redraft(self, review_id: str, hint: str):
        """다시 쓰기를 잡는다. → (실패 Outcome, None) 또는 (None, 초안을 쓸 함수).

        잡기는 세션 잠금 안에서, 돌려받은 함수(HCX 호출)는 잠금 밖에서 부른다. hint는 HINTS 값만."""
        if hint not in HINTS:
            raise ValueError("다시 쓰기 방식을 확인해 주세요.")
        row = self._row(review_id)
        if row is None:
            return self._outcome("not_found", "다시 쓸 리뷰를 찾을 수 없어요."), None
        if row.get("has_owner_reply"):
            return self._outcome("replied", "이미 답글이 달린 리뷰예요."), None
        if not self._claim(review_id, again=True):
            return self._outcome(
                "busy", "아직 초안을 쓰는 중이에요. 잠시 뒤에 다시 눌러 주세요."
            ), None
        with self._lock:
            has_text = bool(self.drafts.get(review_id, {}).get("text"))

        def job() -> dict:
            entry = self._write(review_id, edit=HINTS[hint] if has_text else None)
            if entry["status"] == "failed":
                return self._outcome("draft_failed", "초안을 다시 쓰지 못했어요.")
            return self._outcome(None, "초안을 다시 썼어요.")

        return None, job

    def redraft(self, review_id: str, hint: str) -> dict:
        """begin_redraft를 한 번에 (잠금을 따로 다룰 필요 없을 때)."""
        failed, job = self.begin_redraft(review_id, hint)
        return failed if job is None else job()

    # ------------------------------------------------------------ 데모
    def simulate_review(self, store) -> str:
        """더미 리뷰 한 건을 가게에 더한다 (오늘 날짜). 새 review_id를 돌려준다."""
        data = store.read()
        ids = {r["review_id"] for r in data["reviews"]}
        while True:
            self._simulated += 1
            rid = f"sim-{self._simulated:02d}"
            if rid not in ids:
                break
        rating, text = DEMO_REVIEWS[(self._simulated - 1) % len(DEMO_REVIEWS)]
        data["reviews"].append(
            {
                "review_id": rid,
                "visit_date": self.toolbox.ctx["today"].isoformat(),
                "rating": rating,
                "text": text,
                "features": [],
                "has_owner_reply": False,
            }
        )
        store.write(data)
        return rid

    # ------------------------------------------------------------ 화면
    def annotate(self, view: dict) -> dict:
        """screens.reviews 결과에 draft와 summary.waiting·preparing을 더한다."""
        waiting = 0
        for row in view["reviews"]:
            d = self.draft(row["id"])
            row["draft"] = d
            if d and d["status"] in WAITING and not row["has_owner_reply"]:
                waiting += 1
        view["summary"]["waiting"] = waiting
        view["summary"]["preparing"] = self.preparing
        return view


def toolbox_of(assistant):
    """사장님 비서의 리뷰 도구 상자 (없으면 run_action과 같은 방법으로 만든다)."""
    if assistant.review is None:
        assistant.review = assistant._review_factory(
            assistant.store, owner_lang=assistant.owner_lang or "ko"
        )
    return assistant.review.toolbox


def inbox_of(assistant) -> ReplyInbox:
    """세션(비서)마다 대기함 하나. 리뷰 세션이 새로 만들어지면 그 도구 상자를 따라간다."""
    box = toolbox_of(assistant)
    inbox = getattr(assistant, "_reply_inbox", None)
    if inbox is None:
        inbox = assistant._reply_inbox = ReplyInbox(box)
    inbox.toolbox = box
    return inbox


def annotate(assistant, view: dict) -> dict:
    """/reviews 화면에 대기함 필드를 더한다. 대기함이 아직 없으면 초안 없음으로 (만들지 않는다)."""
    inbox = getattr(assistant, "_reply_inbox", None)
    if inbox is not None:
        return inbox.annotate(view)
    for row in view["reviews"]:
        row["draft"] = None
    view["summary"].update(waiting=0, preparing=0)
    return view


def prepare_drafts(assistant, limit: int = 10) -> list[str]:
    """답글 없고 초안 없는 리뷰를 최신순 최대 limit개 미리 쓴다 (동기)."""
    inbox = inbox_of(assistant)
    return inbox.prepare(inbox.targets(limit))
