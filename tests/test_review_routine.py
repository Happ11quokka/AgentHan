"""리뷰 정기 보고(#98): 켜진 대화에서 정해진 때가 지나면 보고가 비서 메시지 하나로 온다."""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app.server import create_app
from agenthan.eval.fake_backend import FakeBackend
from agenthan.tools.dummy_store import SEED_PATH, DummyStore
from test_planner_loop import HCX, paraphrased


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: pytest.fail("오프라인 테스트의 네트워크 호출")
    )


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def api_with(tmp_path, clock, *plans, data_today=date(2026, 11, 7)):
    hcx = HCX([], plans=list(plans))

    def review_session(store, owner_lang="ko", **_):
        return FakeBackend().review_session(store, owner_lang=owner_lang, today=data_today)

    def make(path):
        path.write_bytes(SEED_PATH.read_bytes())
        store = DummyStore(path)
        data = store.read()
        data["reviews"].append(  # 답글 안 단 불만 리뷰 (보고 메시지의 답글 칩 대상)
            {
                "review_id": "r09",
                "visit_date": "2026-11-06",
                "rating": 1,
                "text": "맛이 없어요. 최악이에요.",
                "has_owner_reply": False,
            }
        )
        store.write(data)
        return OwnerAssistant(
            path,
            agent_mode="planner",
            chat=hcx.chat,
            plan_call=hcx.plan,
            review_session=review_session,
        )

    return TestClient(create_app(make_assistant=make, root=tmp_path, clock=clock))


def place(api, sid):
    return api.get(f"/assistant/sessions/{sid}/place").json()


def turn_on_daily(api, sid):
    preview = api.post(
        f"/assistant/sessions/{sid}/messages", json={"text": "매일 리뷰 요약해서 보내 줘"}
    ).json()
    assert preview["review_report"]["text"] == "정기 보고 꺼짐"  # 승인 전
    [pending] = preview["pending"]
    assert pending["kind"] == "settings" and pending["settings"]["after"]["frequency"] == "daily"
    approve = next(a for a in preview["next_actions"] if a["action_id"] == "task.approve")
    return api.post(f"/assistant/sessions/{sid}/actions", json=approve).json()


def test_due_report_arrives_once_as_one_assistant_message(tmp_path):
    clock = Clock(datetime(2026, 11, 7, 8, 0))
    api = api_with(tmp_path, clock, paraphrased("리뷰 요약", "read"))
    sid = api.post("/assistant/sessions").json()["session_id"]
    first = place(api, sid)
    assert first["review_report"] == {
        "enabled": False,
        "schedule": None,
        "next_run": None,
        "text": "정기 보고 꺼짐",
    }
    assert first["messages"] == []

    saved = turn_on_daily(api, sid)
    assert saved["pending"] == [] and saved["messages"] == []
    assert saved["review_report"]["text"] == "매일 09:00 리뷰 요약 받는 중"
    assert saved["review_report"]["next_run"] == "2026-11-07T09:00"

    clock.now = datetime(2026, 11, 7, 8, 59)
    assert place(api, sid)["messages"] == []  # 아직 때가 아니다

    clock.now = datetime(2026, 11, 7, 9, 3)
    [message] = place(api, sid)["messages"]
    assert message["role"] == "agent" and message["text"].startswith("리뷰 정기 보고")
    assert "아직 답글을 안 단 불만 리뷰가 모두 1건 있어요" in message["text"]
    assert message["next_actions"] == [
        {
            "action_id": "review.draft_reply",
            "label": "불만 리뷰 답글 초안",
            "args": {"review_id": "r09"},
        }
    ]
    # 같은 회차는 다시 오지 않는다
    again = place(api, sid)
    assert again["messages"] == [] and again["review_report"]["next_run"] == "2026-11-08T09:00"
    clock.now = datetime(2026, 11, 7, 23, 0)
    assert place(api, sid)["messages"] == []

    # 다음 날 회차는 한 번 온다 (며칠 밀려도 몰아 보내지 않는다)
    clock.now = datetime(2026, 11, 10, 9, 30)
    assert len(place(api, sid)["messages"]) == 1
    assert place(api, sid)["messages"] == []


def test_run_now_puts_one_report_in_the_chat_without_changing_settings(tmp_path):
    clock = Clock(datetime(2026, 11, 7, 8, 0))
    api = api_with(tmp_path, clock)
    sid = api.post("/assistant/sessions").json()["session_id"]

    body = api.post(f"/assistant/sessions/{sid}/reviews/report").json()
    [message] = body["messages"]
    assert message["text"].startswith("리뷰 보고")
    assert body["review_report"]["text"] == "정기 보고 꺼짐"  # 설정은 그대로
    assert place(api, sid)["messages"] == []
    clock.now = datetime(2026, 11, 9, 9, 0)
    assert place(api, sid)["messages"] == []  # 꺼진 대화에는 정기 보고가 오지 않는다


def test_run_now_does_not_shift_the_scheduled_report(tmp_path):
    clock = Clock(datetime(2026, 11, 7, 8, 0))
    api = api_with(tmp_path, clock, paraphrased("리뷰 요약", "read"))
    sid = api.post("/assistant/sessions").json()["session_id"]
    turn_on_daily(api, sid)
    clock.now = datetime(2026, 11, 7, 8, 30)
    assert len(api.post(f"/assistant/sessions/{sid}/reviews/report").json()["messages"]) == 1
    clock.now = datetime(2026, 11, 7, 9, 1)
    assert len(place(api, sid)["messages"]) == 1


def test_report_message_is_part_of_the_conversation_history(tmp_path):
    clock = Clock(datetime(2026, 11, 7, 8, 0))
    sessions = {}

    def make(path):
        path.write_bytes(SEED_PATH.read_bytes())
        app = OwnerAssistant(
            path,
            agent_mode="planner",
            plan_call=HCX([]).plan,
            review_session=FakeBackend().review_session,
        )
        sessions["app"] = app
        return app

    api = TestClient(create_app(make_assistant=make, root=tmp_path, clock=clock))
    sid = api.post("/assistant/sessions").json()["session_id"]
    [message] = api.post(f"/assistant/sessions/{sid}/reviews/report").json()["messages"]
    assert sessions["app"].history[-1] == {"role": "assistant", "content": message["text"]}


def test_report_period_follows_the_store_data_date_not_the_clock(tmp_path):
    # 시계는 11-21인데 가게 데이터(리뷰 스냅샷)의 오늘은 11-09: 보고 기간은 11-09까지여야 한다
    clock = Clock(datetime(2026, 11, 21, 8, 0))
    api = api_with(tmp_path, clock, paraphrased("리뷰 요약", "read"), data_today=date(2026, 11, 9))
    sid = api.post("/assistant/sessions").json()["session_id"]

    [now] = api.post(f"/assistant/sessions/{sid}/reviews/report").json()["messages"]
    head, body, todo = now["text"].splitlines()
    assert head.startswith("리뷰 보고 (") and head.endswith(" ~ 2026-11-09)")
    assert body.startswith("리뷰 3건")  # 11-03·11-05·11-06 리뷰가 기간 안에 있다
    assert todo == "아직 답글을 안 단 불만 리뷰가 모두 1건 있어요."

    saved = turn_on_daily(api, sid)
    assert saved["review_report"]["next_run"] == "2026-11-21T09:00"  # 언제 보낼지는 시계 기준
    clock.now = datetime(2026, 11, 21, 9, 1)
    [daily] = place(api, sid)["messages"]
    head, body, todo = daily["text"].splitlines()
    assert head.endswith(" ~ 2026-11-09)")
    # 하루 기간에는 리뷰가 없다: 0건을 말로 쓰고, 답글 안 단 불만은 전체 기준임을 드러낸다
    assert body == "이번 기간에는 새 리뷰가 없어요."
    assert todo == "아직 답글을 안 단 불만 리뷰가 모두 1건 있어요."
