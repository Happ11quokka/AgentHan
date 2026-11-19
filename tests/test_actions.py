"""버튼 실행은 기능 선택 없이 원장의 미리보기와 승인을 따른다."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from agenthan.agent import feature_selector, review_delegate
from agenthan.app import pilot
from agenthan.app.server import create_app
from test_review_delegate import FakeWriter


@pytest.fixture
def setup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(feature_selector, "select", lambda *a, **k: pytest.fail("DASH 호출"))

    def chat(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("가짜 모델")

    def make(path):
        def review(store, owner_lang="ko"):
            return review_delegate.new_session(
                store, writer=FakeWriter(), owner_lang=owner_lang, today=date(2026, 11, 9)
            )

        assistant = pilot.assistant(path, chat=chat)
        assistant._review_factory = review
        monkeypatch.setattr(assistant, "route", lambda *_: pytest.fail("라우팅 호출"))
        return assistant

    api = TestClient(create_app(make_assistant=make, root=tmp_path))
    sid = api.post("/assistant/sessions").json()["session_id"]

    def action(name, args=None):
        return api.post(
            f"/assistant/sessions/{sid}/actions",
            json={
                "action_id": name,
                "args": args or {},
                "label": name,
            },
        )

    return api, sid, action, calls


COUPON = {
    "title": "기념 쿠폰",
    "discount_type": "percent",
    "discount_value": 10,
    "start_date": "2026-11-10",
    "end_date": "2026-11-30",
}


@pytest.mark.parametrize(
    "name,args",
    [
        ("review.summary", {}),
        ("stats.week", {}),
        ("stats.day", {}),
        ("coupon.create", COUPON),
        ("hours.edit", {"entries": [{"days": ["sun"], "closed": True}]}),
    ],
)
def test_actions_without_routing(setup, name, args):
    _, _, action, calls = setup
    result = action(name, args)
    assert result.status_code == 200
    assert result.json()["reply"]
    assert isinstance(result.json()["next_actions"], list)
    assert all("DASH" not in str(c) for c in calls)


def test_coupon_version_and_cancel(setup):
    _, _, action, _ = setup
    first = action("coupon.create", COUPON).json()
    [pending] = first["pending"]
    assert not any(c["title"] == COUPON["title"] for c in first["place"]["coupon"])
    stale = action(
        "task.approve", {"task_id": pending["task_id"], "version": pending["version"] - 1}
    ).json()
    assert stale["place"] == first["place"] and stale["pending"]
    done = action(
        "task.approve", {"task_id": pending["task_id"], "version": pending["version"]}
    ).json()
    assert any(c["title"] == COUPON["title"] for c in done["place"]["coupon"])
    p = action("coupon.create", {**COUPON, "title": "다른 쿠폰"}).json()["pending"][0]
    cancelled = action("task.cancel", {"task_id": p["task_id"]}).json()
    assert not cancelled["pending"]


def test_review_id_and_approval(setup):
    api, sid, action, _ = setup
    reviews = api.get(f"/assistant/sessions/{sid}/reviews").json()["reviews"]
    rid = next(r["id"] for r in reviews if not r["has_owner_reply"])
    first = action("review.draft_reply", {"review_id": rid}).json()
    [p] = first["pending"]
    assert p["review_id"] == rid and p["reply"]
    stale = action("task.approve", {"task_id": p["task_id"], "version": p["version"] - 1}).json()
    assert stale["pending"]
    assert not next(
        r for r in api.get(f"/assistant/sessions/{sid}/reviews").json()["reviews"] if r["id"] == rid
    )["has_owner_reply"]
    done = action("task.approve", {"task_id": p["task_id"], "version": p["version"]}).json()
    assert not done["pending"]
    assert next(
        r for r in api.get(f"/assistant/sessions/{sid}/reviews").json()["reviews"] if r["id"] == rid
    )["has_owner_reply"]


def test_unknown_action(setup):
    _, _, action, _ = setup
    assert action("없는 버튼").status_code == 400


def test_summary_followup_and_held_task(setup):
    _, _, action, _ = setup
    pending = action("coupon.create", COUPON).json()["pending"][0]
    summary = action("review.summary").json()
    assert summary["next_actions"]
    followup = summary["next_actions"][0]
    draft = action(followup["action_id"], followup["args"]).json()
    assert len(draft["pending"]) == 2
    review = next(p for p in draft["pending"] if p["feature"] == "review")
    done = action("task.cancel", {"task_id": review["task_id"]}).json()
    assert "쿠폰" in done["reply"]
    assert done["pending"][0]["task_id"] == pending["task_id"]
