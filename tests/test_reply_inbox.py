"""답글 대기함: 리뷰마다 초안을 미리 달고, 카드 버튼으로 올리기·고치기·건너뛰기·다시 쓰기.

카드 버튼은 대화 턴(/actions)이 아니라 /reviews/{id}/reply를 부른다. 대화 기록·작업 원장·trace·
planner 맥락에는 아무것도 남기지 않는다."""

import copy
import json
import threading
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from agenthan.agent import feature_selector, review_delegate
from agenthan.agent.review import inbox
from agenthan.app import pilot
from agenthan.app.server import create_app

UNANSWERED = ["r19", "r18", "r17", "r15", "r14", "r13", "r12", "r09"]  # 파일럿 가게, 최신순
PHONE = "연락 주시면 010-1234-5678로 안내드리겠습니다."
SEED = {r["review_id"]: r["text"] for r in json.loads(pilot.PILOT_SEED.read_text())["reviews"]}


class Writer:
    """가짜 HCX writer. bad에 든 리뷰 문장에는 검사에 걸리는 답을 쓴다."""

    def __init__(self, bad=(), fail=(), gate=None):
        self.calls, self.bad, self.fail, self.gate = [], bad, fail, gate
        self.lock = threading.Lock()

    def write_reply(self, text, sentiment, **kw):
        if self.gate is not None:
            self.gate.wait(5)
        with self.lock:
            self.calls.append({"text": text, **kw})
            n = len(self.calls)
        if any(b in text for b in self.fail):
            raise RuntimeError("가짜 HCX 오류")
        if any(b in text for b in self.bad):
            return PHONE
        if kw.get("edit_request"):
            return f"{kw['previous']} ({kw['edit_request'][:6]})"
        return f"방문 {n} 감사합니다. 다음에도 정성껏 준비하겠습니다."

    def translate(self, texts, lang, glossary=None):
        return list(texts)


def review_text(store_path, rid):
    data = json.loads(store_path.read_text(encoding="utf-8"))
    return next(r for r in data["reviews"] if r["review_id"] == rid)


class Ctx:
    """한 세션을 다루는 도우미."""

    def __init__(self, api, sid, assistant, writer, store):
        self.api, self.sid, self.assistant, self.writer, self.store = (
            api,
            sid,
            assistant,
            writer,
            store,
        )

    def reviews(self, wait=True):
        for _ in range(200):
            body = self.api.get(f"/assistant/sessions/{self.sid}/reviews").json()
            if not wait or body["summary"]["preparing"] == 0:
                return body
            time.sleep(0.02)
        pytest.fail("초안 생성이 끝나지 않음")

    def action(self, name, args):
        return self.api.post(
            f"/assistant/sessions/{self.sid}/actions",
            json={"action_id": name, "args": args, "label": name},
        )

    def reply(self, rid, op, **kw):
        return self.api.post(
            f"/assistant/sessions/{self.sid}/reviews/{rid}/reply", json={"op": op, **kw}
        )


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_selector, "select", lambda *a, **k: pytest.fail("DASH 호출"))

    def build(writer=None, prepare_on_start=True):
        writer = writer or Writer()
        made = {}

        def assistant(path):
            def review(store, owner_lang="ko"):
                return review_delegate.new_session(
                    store, writer=writer, owner_lang=owner_lang, today=date(2026, 11, 9)
                )

            a = pilot.assistant(path, chat=lambda *a, **k: pytest.fail("모델 호출"))
            a._review_factory = review
            monkeypatch.setattr(a, "route", lambda *_: pytest.fail("라우팅 호출"))
            made[path.parent.name] = a
            return a

        api = TestClient(
            create_app(make_assistant=assistant, root=tmp_path, prepare_on_start=prepare_on_start)
        )
        sid = api.post("/assistant/sessions").json()["session_id"]
        return Ctx(api, sid, made[sid], writer, tmp_path / sid / "store.json")

    return build


def by_id(body):
    return {r["id"]: r for r in body["reviews"]}


def test_session_start_prepares_a_draft_for_each_unanswered_review(make):
    s = make()
    body = s.reviews()
    rows = by_id(body)
    assert body["summary"]["waiting"] == len(UNANSWERED) and body["summary"]["preparing"] == 0
    for rid in UNANSWERED:
        assert rows[rid]["draft"]["status"] == "ready" and rows[rid]["draft"]["text"]
        assert rows[rid]["draft"]["issues"] == []
    assert all(r["draft"] is None for r in body["reviews"] if r["has_owner_reply"])
    # 최신순으로 썼다 (첫 쓰기만; 검사 경고로 고쳐 쓴 호출은 뺀다)
    by_text = {t: rid for rid, t in SEED.items()}
    order = [by_text[c["text"]] for c in s.writer.calls if not c.get("edit_request")]
    assert order == UNANSWERED


def test_without_prepare_on_start_nothing_is_drafted(make):
    s = make(prepare_on_start=False)
    body = s.reviews()
    assert body["summary"]["waiting"] == 0 and body["summary"]["preparing"] == 0
    assert all(r["draft"] is None for r in body["reviews"]) and s.writer.calls == []


def test_prepare_drafts_respects_limit_and_never_writes_one_review_twice(tmp_path):
    gate = threading.Event()
    writer = Writer(gate=gate)

    def review(store, owner_lang="ko"):
        return review_delegate.new_session(
            store, writer=writer, owner_lang=owner_lang, today=date(2026, 11, 9)
        )

    a = pilot.assistant(tmp_path / "store.json")
    a._review_factory = review
    assert inbox.inbox_of(a).targets(limit=3) == UNANSWERED[:3]
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(inbox.prepare_drafts(a, limit=3)))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    time.sleep(0.05)
    gate.set()
    for t in threads:
        t.join()
    made = [rid for r in results for rid in r]
    assert sorted(made) == sorted(set(made))  # 한 리뷰를 두 번 쓰지 않았다
    assert set(made) <= set(UNANSWERED)
    assert inbox.prepare_drafts(a, limit=10) == [
        rid for rid in UNANSWERED if rid not in made
    ]  # 이미 초안 있는 리뷰는 다시 쓰지 않는다
    assert inbox.prepare_drafts(a) == []


def test_one_failure_does_not_stop_the_rest_and_checks_mark_needs_check(make):
    s = make(Writer(bad=[SEED["r15"][:10]], fail=[SEED["r12"][:10]]))
    rows = by_id(s.reviews())
    assert rows["r12"]["draft"]["status"] == "failed"
    assert rows["r15"]["draft"]["status"] == "needs_check"
    assert rows["r15"]["draft"]["issues"] == ["전화번호 등 개인정보"]
    assert rows["r09"]["draft"]["status"] == "ready"  # 실패 뒤의 리뷰도 썼다
    assert s.reviews()["summary"]["waiting"] == len(UNANSWERED) - 1


def test_post_posts_the_draft_without_another_approval_card(make):
    s = make()
    draft = by_id(s.reviews())["r19"]["draft"]["text"]
    body = s.reply("r19", "post").json()
    assert set(body) == {"review", "summary"}  # error 없음
    assert body["review"]["id"] == "r19" and body["review"]["has_owner_reply"] is True
    assert body["review"]["draft"] == {"text": draft, "status": "posted", "issues": []}
    assert body["summary"]["waiting"] == len(UNANSWERED) - 1
    saved = review_text(s.store, "r19")
    assert saved["has_owner_reply"] is True and saved["owner_reply"]["reply"] == draft
    assert s.reviews()["summary"]["waiting"] == len(UNANSWERED) - 1
    assert s.reply("r19", "post").json()["error"]["reason"] == "replied"


def test_post_with_the_owners_edit_is_checked_again(make):
    s = make()
    s.reviews()
    refused = s.reply("r18", "post", text=PHONE).json()
    assert refused["error"]["reason"] == "checks"
    assert refused["error"]["issues"] == ["전화번호 등 개인정보"]
    assert "전화번호 등 개인정보" in refused["error"]["message"]
    assert review_text(s.store, "r18")["has_owner_reply"] is False
    assert refused["review"]["draft"]["status"] == "ready"  # 초안은 그대로

    edited = "찾아 주셔서 고맙습니다. 다음에도 맛있게 준비할게요."
    body = s.reply("r18", "post", text=edited).json()
    assert "error" not in body
    assert review_text(s.store, "r18")["owner_reply"]["reply"] == edited
    assert body["review"]["draft"] == {"text": edited, "status": "posted", "issues": []}


def test_post_refuses_a_draft_that_failed_the_checks(make):
    s = make(Writer(bad=[SEED["r15"][:10]]))
    s.reviews()
    body = s.reply("r15", "post").json()
    assert body["error"]["reason"] == "checks"
    assert body["review"]["draft"]["status"] == "needs_check"
    assert review_text(s.store, "r15")["has_owner_reply"] is False


def test_skip_marks_skipped_and_leaves_the_review_unanswered(make):
    s = make()
    s.reviews()
    body = s.reply("r17", "skip").json()
    assert "error" not in body and body["review"]["draft"]["status"] == "skipped"
    assert body["summary"]["waiting"] == len(UNANSWERED) - 1
    assert review_text(s.store, "r17")["has_owner_reply"] is False


def test_redraft_takes_only_the_fixed_hints(make):
    s = make()
    before = by_id(s.reviews())["r14"]["draft"]["text"]
    body = s.reply("r14", "redraft", hint="shorter").json()
    assert "error" not in body
    call = s.writer.calls[-1]
    assert call["edit_request"] == inbox.HINTS["shorter"] and call["previous"] == before
    assert body["review"]["draft"]["text"] != before
    assert by_id(s.reviews())["r14"]["draft"]["text"] == body["review"]["draft"]["text"]

    n = len(s.writer.calls)
    for hint in ("더 짧게", "", None, "friendlier"):
        assert s.reply("r14", "redraft", hint=hint).status_code == 400
    assert s.reply("r14", "rewrite", hint="shorter").status_code == 422
    assert s.reply("r14", "skip", text="x").status_code == 400
    assert len(s.writer.calls) == n  # 다른 값으로는 쓰지 않는다


@pytest.mark.parametrize("hint", ["shorter", "more_apologetic", "warmer"])
def test_each_hint_reaches_the_writer(make, hint):
    s = make()
    s.reviews()
    s.reply("r13", "redraft", hint=hint)
    assert s.writer.calls[-1]["edit_request"] == inbox.HINTS[hint]


def test_unknown_review_is_404_and_writes_nothing(make):
    s = make()
    s.reviews()
    n = len(s.writer.calls)
    for op, kw in (("post", {}), ("skip", {}), ("redraft", {"hint": "warmer"})):
        assert s.reply("nope", op, **kw).status_code == 404
    assert len(s.writer.calls) == n


def conversation_state(a):
    """대화 턴이 남기는 것: 대화 기록, 작업 원장, trace, planner·라우팅 맥락."""
    return copy.deepcopy(
        {
            "history": a.history,
            "history_task": a._history_task,
            "turn": a.ledger.turn,
            "stack": a.ledger.stack,
            "tasks": [vars(t) for t in a.ledger.tasks],
            "trace": a.trace,
            "turn_task": a._turn_task,
            "act": a._act,
            "adding": a._adding,
            "clarify": a._clarify_options,
            "detail": getattr(a, "_detail_subject", None),
            "turn_route": a._turn_route,
            "plan_input": a._turn_plan_input,
            "review_messages": a.review.messages if a.review else None,
        }
    )


def test_a_button_turn_leaves_a_trace_but_card_ops_leave_nothing(make):
    s = make()
    s.reviews()
    a = s.assistant
    # 대화 턴(/actions)은 대화 기록·작업 원장 차례·trace를 남긴다: 새 경로가 피해야 하는 것
    before = conversation_state(a)
    assert s.action("review.summary", {}).status_code == 200
    after = conversation_state(a)
    assert len(after["history"]) == len(before["history"]) + 2
    assert after["turn"] == before["turn"] + 1 and after["trace"] != before["trace"]

    state = conversation_state(a)
    assert "error" not in s.reply("r19", "post").json()
    assert s.reply("r18", "post", text=PHONE).json()["error"]["reason"] == "checks"
    assert "error" not in s.reply("r18", "post", text="찾아 주셔서 고맙습니다. 또 오세요.").json()
    assert "error" not in s.reply("r17", "skip").json()
    assert "error" not in s.reply("r14", "redraft", hint="warmer").json()
    assert conversation_state(a) == state
    assert s.api.get(f"/assistant/sessions/{s.sid}/place").json()["pending"] == []


def test_card_ops_leave_an_open_chat_approval_card_alone(make):
    s = make()
    s.reviews()
    [card] = s.action("review.draft_reply", {"review_id": "r09"}).json()["pending"]
    state = conversation_state(s.assistant)
    s.reply("r19", "post")
    s.reply("r17", "skip")
    assert conversation_state(s.assistant) == state
    [still] = s.api.get(f"/assistant/sessions/{s.sid}/place").json()["pending"]
    assert still["task_id"] == card["task_id"]


def test_inbox_ops_are_not_conversation_buttons(make):
    s = make()
    s.reviews()
    for name in ("review.post_reply", "review.skip_reply", "review.redraft"):
        assert s.action(name, {"review_id": "r19"}).status_code == 400


def test_simulate_adds_one_review_and_drafts_only_that_one(make):
    s = make()
    s.reviews()
    n = len(s.writer.calls)
    body = s.api.post(f"/assistant/sessions/{s.sid}/reviews/simulate").json()
    rid = body["review_id"]
    assert by_id(body)[rid]["has_owner_reply"] is False
    assert by_id(body)[rid]["visit_date"] == "2026-11-09"
    after = s.reviews()
    assert after["reviews"][0]["id"] == rid  # 최신
    assert by_id(after)[rid]["draft"]["status"] == "ready"
    assert after["summary"]["waiting"] == len(UNANSWERED) + 1
    assert {c["text"] for c in s.writer.calls[n:]} == {by_id(after)[rid]["text"]}


def test_reviews_view_reports_preparing_while_drafts_are_written(make):
    gate = threading.Event()
    s = make(Writer(gate=gate))
    early = s.reviews(wait=False)
    assert early["summary"]["preparing"] == len(UNANSWERED) and early["summary"]["waiting"] == 0
    gate.set()
    assert s.reviews()["summary"]["preparing"] == 0


def test_chat_approval_card_flow_still_works(make):
    s = make()
    s.reviews()
    first = s.action("review.draft_reply", {"review_id": "r09"}).json()
    [card] = first["pending"]
    assert card["kind"] == "review" and card["review_id"] == "r09"
    done = s.action("task.approve", {"task_id": card["task_id"], "version": card["version"]}).json()
    assert done["pending"] == [] and "저장했어요" in done["reply"]


# ---------------------------------------------------------------- 카드 [답글 초안 받기] (#96)
def test_draft_op_writes_a_fresh_draft_into_the_card_without_a_chat_turn(make):
    s = make(prepare_on_start=False)
    s.reviews()
    inbox.inbox_of(
        s.assistant
    )  # 리뷰 도구 상자는 대기함이 처음 쓸 때 만든다 (대화 기록은 비어 있음)
    state = conversation_state(s.assistant)
    body = s.reply("r09", "draft").json()
    assert set(body) == {"review", "summary"}  # error 없음
    assert body["review"]["id"] == "r09" and body["review"]["has_owner_reply"] is False
    assert body["review"]["draft"]["status"] == "ready" and body["review"]["draft"]["text"]
    assert body["summary"]["waiting"] == 1 and body["summary"]["preparing"] == 0
    [call] = s.writer.calls
    assert call["text"] == SEED["r09"] and not call.get("edit_request")  # 새로 쓰기
    # 대화 턴이 아니다: 대화 기록·원장·trace·승인 카드 모두 그대로
    assert conversation_state(s.assistant) == state
    assert s.api.get(f"/assistant/sessions/{s.sid}/place").json()["pending"] == []
    assert review_text(s.store, "r09")["has_owner_reply"] is False  # 올리지는 않는다
    assert by_id(s.reviews())["r09"]["draft"] == body["review"]["draft"]


def test_draft_op_keeps_an_existing_draft_and_retries_a_failed_one(make):
    writer = Writer(fail=[SEED["r12"][:10]])
    s = make(writer)
    rows = by_id(s.reviews())
    n = len(writer.calls)
    # 이미 초안이 있으면 다시 쓰지 않고 그대로 돌려준다 (두 번 눌러도 안전)
    body = s.reply("r14", "draft").json()
    assert "error" not in body and body["review"]["draft"] == rows["r14"]["draft"]
    assert len(writer.calls) == n
    # 실패한 초안은 다시 써 보고, 또 실패하면 draft_failed
    failed = s.reply("r12", "draft").json()
    assert failed["error"]["reason"] == "draft_failed"
    assert failed["review"]["draft"]["status"] == "failed"
    writer.fail = ()
    again = s.reply("r12", "draft").json()
    assert "error" not in again and again["review"]["draft"]["status"] == "ready"
    # 답글이 달린 리뷰, 고친 문장, 없는 리뷰
    assert "error" not in s.reply("r19", "post").json()
    assert s.reply("r19", "draft").json()["error"]["reason"] == "replied"
    assert s.reply("r13", "draft", text="x").status_code == 400
    assert s.reply("nope", "draft").status_code == 404


def test_draft_op_is_busy_while_that_review_is_being_written(make):
    gate = threading.Event()
    s = make(Writer(gate=gate), prepare_on_start=False)
    s.reviews()
    results = []
    worker = threading.Thread(target=lambda: results.append(s.reply("r09", "draft").json()))
    worker.start()
    for _ in range(200):  # 그 리뷰를 잡을 때까지
        if s.reviews(wait=False)["summary"]["preparing"] == 1:
            break
        time.sleep(0.01)
    # HCX를 기다리는 동안 세션 잠금은 풀려 있다: 같은 리뷰를 또 누르면 busy로 바로 답한다
    assert s.reply("r09", "draft").json()["error"]["reason"] == "busy"
    gate.set()
    worker.join(5)
    assert "error" not in results[0] and results[0]["review"]["draft"]["status"] == "ready"
    assert len(s.writer.calls) == 1
