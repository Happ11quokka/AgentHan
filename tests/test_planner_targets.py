"""계획 단계의 대상 후보 주입·단계 몫 좁히기·단계 실패 재시도(각 1회)."""

import copy
import json

import pytest

from agenthan.agent import planner
from agenthan.agent.clova_chat import ClovaRequestRejected
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.agent.planner_loop import clauses, step_portion
from agenthan.eval.fake_backend import FakeBackend
from agenthan.tools.dummy_store import SEED_PATH

TEXT = "일요일에도 10시부터 6시까지 열고, 주차 되냐는 톡톡에는 '공영주차장을 써 주세요'라고 답해 줘"
HOURS_GOAL = "일요일 영업시간을 10시~18시로 변경"
TALK_GOAL = "주차 문의 톡톡에 '공영주차장을 써 주세요'로 답장"
TALKS = [
    {"id": "talk-3", "message": "주차 가능한가요?", "reply_text": "", "status": "unanswered"},
    {"id": "talk-4", "message": "일요일에도 여나요?", "reply_text": "", "status": "unanswered"},
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: pytest.fail("오프라인 테스트의 네트워크 호출")
    )


def call(tool, **args):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": tool, "arguments": args}}],
    }


def two_steps():
    return {
        "scope": "store",
        "steps": [
            {"kind": "new", "feature": "business_hours", "goal": HOURS_GOAL},
            {"kind": "new", "feature": "talktalk", "goal": TALK_GOAL},
        ],
        "write_features": ["business_hours", "talktalk"],
        "clarify": "",
    }


class HCX:
    def __init__(self, *responses, plans=(), plan_failures=0):
        self.responses = iter(responses)
        self.plans = list(plans)
        self.plan_failures = plan_failures
        self.plan_calls = 0
        self.requests = []

    def plan(self, model, body):
        self.plan_calls += 1
        if self.plan_failures:
            self.plan_failures -= 1
            raise RuntimeError("계획 호출 실패")
        return {"message": {"content": json.dumps(self.plans.pop(0))}}

    def chat(self, messages, *, tools, **options):
        self.requests.append(copy.deepcopy((messages, tools, options)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def assistant(tmp_path, hcx):
    path = tmp_path / "store.json"
    store = json.loads(SEED_PATH.read_bytes())
    store["talktalk"] = copy.deepcopy(TALKS)
    path.write_text(json.dumps(store, ensure_ascii=False))
    return OwnerAssistant(
        path,
        agent_mode="planner",
        plan_call=hcx.plan,
        chat=hcx.chat,
        embed=FakeBackend().embed,
        select_chat=lambda messages, *, tools: call("select_feature", feature_id="none"),
    )


HOURS = call("update_business_hours", days=["sun"], open_time="10:00", close_time="18:00")


def talk_request(hcx):
    """톡톡 단계의 실행 요청(현재 단계 도구가 맨 앞)."""
    return next(r for r in hcx.requests if r[1][0]["function"]["name"] == "save_talktalk_reply")


# --- 단계 몫 -----------------------------------------------------------------


def test_clauses_keep_quoted_text_whole():
    assert clauses("월요일 쉬고, '네, 됩니다'라고 답해 줘") == [
        "월요일 쉬고",
        "'네, 됩니다'라고 답해 줘",
    ]


def test_step_portion_leaves_out_the_other_steps_clause():
    hours = planner.Step("business_hours", HOURS_GOAL)
    talk = planner.Step("talktalk", TALK_GOAL)
    portion = step_portion(TEXT, talk, [hours, talk])
    assert "주차" in portion and "일요일" not in portion
    assert "일요일" in step_portion(TEXT, hours, [hours, talk])


def test_step_portion_drops_other_step_words_inside_one_clause():
    text = "일요일에도 열고 주차 되냐는 톡톡에 답해 줘"
    hours = planner.Step("business_hours", HOURS_GOAL)
    talk = planner.Step("talktalk", TALK_GOAL)
    portion = step_portion(text, talk, [hours, talk])
    assert "주차" in portion and "일요일" not in portion


def test_step_portion_single_step_and_follow_up_answer_stay_whole():
    talk = planner.Step("talktalk", TALK_GOAL)
    assert step_portion(TEXT, talk, [talk]) == TEXT
    hours = planner.Step("business_hours", HOURS_GOAL)
    text = TEXT + "\n사장님 후속 답: 일요일 문의도요"
    assert step_portion(text, talk, [hours, talk]).endswith("사장님 후속 답: 일요일 문의도요")


# --- 후보 주입 ---------------------------------------------------------------


def test_talktalk_step_gets_step_candidates_and_id_enum_before_the_model_call(tmp_path):
    hcx = HCX(
        HOURS,
        call("save_talktalk_reply", inquiry="talk-3", reply_text="공영주차장을 써 주세요"),
        plans=[two_steps()],
    )
    app = assistant(tmp_path, hcx)
    app.ask(TEXT)
    messages, tools, _ = talk_request(hcx)
    schema = next(t for t in tools if t["function"]["name"] == "save_talktalk_reply")
    assert schema["function"]["parameters"]["properties"]["inquiry"]["enum"] == ["talk-3"]
    prompt = messages[1]["content"]
    assert "주차 가능한가요" in prompt and "일요일에도 여나요" not in prompt
    # 실행·값 출처 게이트도 같은 후보로 본다: 두 단계 모두 미리보기까지 간다.
    previews = {t.feature for t in app.ledger.open_tasks() if t.preview}
    assert previews == {"business_hours", "talktalk"}
    assert not any(t["reply_text"] for t in app.store.read()["talktalk"])  # 승인 전 미저장


def test_no_candidates_means_no_enum(tmp_path):
    hcx = HCX(
        HOURS,
        {"role": "assistant", "content": "답할 톡톡이 없어요."},
        {"role": "assistant", "content": "답할 톡톡이 없어요."},
        plans=[two_steps()],
    )
    app = assistant(tmp_path, hcx)
    store = app.store.read()
    store["talktalk"] = []
    app.store.write(store)
    app.ask(TEXT)
    _, tools, _ = talk_request(hcx)
    schema = next(t for t in tools if t["function"]["name"] == "save_talktalk_reply")
    assert "enum" not in schema["function"]["parameters"]["properties"]["inquiry"]


# --- 재시도 ------------------------------------------------------------------


def test_candidate_failure_retries_the_step_once_with_candidates_observed(tmp_path):
    hcx = HCX(
        HOURS,
        call(
            "save_talktalk_reply", inquiry="주차 되냐는 문의", reply_text="공영주차장을 써 주세요"
        ),
        call("save_talktalk_reply", inquiry="talk-3", reply_text="공영주차장을 써 주세요"),
        plans=[two_steps()],
    )
    app = assistant(tmp_path, hcx)
    app.ask(TEXT)
    assert app.trace["candidate_retry"] is True
    assert "대상 후보" in hcx.requests[-1][0][1]["content"]
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {
        "business_hours",
        "talktalk",
    }


def test_candidate_failure_retries_only_once_then_asks(tmp_path):
    bad = call("save_talktalk_reply", inquiry="없는 문의", reply_text="공영주차장을 써 주세요")
    worse = call("save_talktalk_reply", inquiry="다른 문의", reply_text="공영주차장을 써 주세요")
    hcx = HCX(HOURS, bad, worse, plans=[two_steps()])
    app = assistant(tmp_path, hcx)
    reply = app.ask(TEXT)
    talk_calls = [c for c in app.trace["calls"] if c["name"] == "save_talktalk_reply"]
    assert len(talk_calls) == 2 and len(hcx.requests) == 3
    assert reply.strip()
    talk = next(t for t in app.ledger.open_tasks() if t.feature == "talktalk")
    assert talk.preview is None


def test_rejected_step_is_asked_again_once_with_the_tool_forced(tmp_path):
    hcx = HCX(
        HOURS,
        ClovaRequestRejected("40009"),
        call("save_talktalk_reply", inquiry="talk-3", reply_text="공영주차장을 써 주세요"),
        plans=[two_steps()],
    )
    app = assistant(tmp_path, hcx)
    app.ask(TEXT)
    assert hcx.requests[2][2]["tool_choice"]["function"]["name"] == "save_talktalk_reply"
    # 처음 요청에는 앞 단계 관찰이 있고, 재요청에는 없다(열린 작업 줄로만 남는다).
    assert "영업시간" in hcx.requests[1][0][1]["content"].split("관찰:")[1]
    assert hcx.requests[2][0][1]["content"].split("관찰:")[1].strip() == ""
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {
        "business_hours",
        "talktalk",
    }


def test_plan_call_failure_replans_once_before_the_single_feature_fallback(tmp_path):
    hcx = HCX(
        HOURS,
        call("save_talktalk_reply", inquiry="talk-3", reply_text="공영주차장을 써 주세요"),
        plans=[two_steps()],
        plan_failures=1,
    )
    app = assistant(tmp_path, hcx)
    app.ask(TEXT)
    assert hcx.plan_calls == 2 and app.trace["plan_retry"] is True
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {
        "business_hours",
        "talktalk",
    }


def test_plan_call_failure_retries_only_once(tmp_path):
    hcx = HCX(plans=[], plan_failures=5)
    app = assistant(tmp_path, hcx)
    app.ask(TEXT)
    assert hcx.plan_calls == 2
