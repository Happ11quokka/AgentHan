"""Planner node (ADR-0005): schema, parsing, rejection of invented features, quiet failure."""

import json

import pytest

from agenthan.agent import clova_chat, planner
from agenthan.agent.place_matcher import load_features
from agenthan.core.llm import ClovaV3Error


def answer(**plan):
    """A v3 `result` whose message content is the given plan as JSON text."""
    body = {"scope": "store", "steps": [], "clarify": "", **plan}
    return {"message": {"role": "assistant", "content": json.dumps(body, ensure_ascii=False)}}


class FakeCall:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.requests = result, error, []

    def __call__(self, model, body):
        self.requests.append((model, body))
        if self.error:
            raise self.error
        return self.result


COUPON = {"feature": "coupon", "goal": "쿠폰 만들기"}
STATS = {"feature": "statistics", "goal": "지난주 조회 보기"}


def test_parses_scope_steps_and_clarify():
    call = FakeCall(answer(steps=[STATS, COUPON]))
    plan = planner.plan("지난주 조회수 보고 쿠폰 만들어 줘", call=call)
    assert plan.scope == "store" and plan.clarify is None
    assert [(s.feature, s.goal) for s in plan.steps] == [
        ("statistics", "지난주 조회 보기"),
        ("coupon", "쿠폰 만들기"),
    ]
    assert plan.first_feature == "statistics"
    assert plan.as_trace() == {
        "scope": "store",
        "steps": [STATS, COUPON],
        "clarify": None,
    }


def test_advice_out_and_clarify_shapes():
    advice = planner.plan("손님이 줄었어요", call=FakeCall(answer(scope="advice", steps=[STATS])))
    assert advice.scope == "advice" and advice.first_feature == "statistics"
    out = planner.plan("환율 알려 줘", call=FakeCall(answer(scope="out")))
    assert out.scope == "out" and out.steps == () and out.first_feature is None
    unsure = planner.plan("그거 해 줘", call=FakeCall(answer(clarify="예약인지 주문인지 모호")))
    assert unsure.steps == () and unsure.clarify == "예약인지 주문인지 모호"


def test_a_feature_outside_the_catalog_rejects_the_whole_plan():
    call = FakeCall(answer(steps=[STATS, {"feature": "send_money", "goal": "송금"}]))
    assert planner.plan("송금해 줘", call=call) is None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "계획이 없습니다",
        "[1, 2]",
        json.dumps({"scope": "weather", "steps": [], "clarify": ""}),  # scope outside the enum
        json.dumps({"scope": "store", "steps": "coupon", "clarify": ""}),
        json.dumps({"scope": "store", "steps": [{"feature": "coupon"}], "clarify": ""}),  # no goal
        json.dumps({"scope": "store", "steps": [], "clarify": ""}),  # nothing to do, nothing to ask
        json.dumps({"scope": "out", "steps": [COUPON], "clarify": ""}),  # out yet acting
        json.dumps({"scope": "store", "steps": [STATS] * 6, "clarify": ""}),  # over MAX_STEPS
        json.dumps({"scope": "store", "steps": [STATS], "clarify": 3}),
    ],
)
def test_malformed_or_inconsistent_answers_are_none(content):
    call = FakeCall({"message": {"role": "assistant", "content": content}})
    assert planner.plan("메뉴 올려 줘", call=call) is None


@pytest.mark.parametrize(
    "error",
    [
        ClovaV3Error("CLOVA HTTP 오류 (400)"),  # a 40009 rejection comes back like this
        clova_chat.ClovaRequestRejected("rejected"),
        TimeoutError("slow"),
        RuntimeError("boom"),
    ],
)
def test_failure_or_rejection_is_none_and_never_raises(error, caplog):
    caplog.set_level("INFO", logger=planner.log.name)
    assert planner.plan("메뉴 올려 줘", call=FakeCall(error=error)) is None
    assert any("planner shadow" in r.getMessage() for r in caplog.records)  # logged, quietly
    assert all("boom" not in r.getMessage() for r in caplog.records)  # no server text


def test_no_result_body_is_none():
    assert planner.plan("메뉴", call=FakeCall(None)) is None
    assert planner.plan("메뉴", call=FakeCall({"message": None})) is None
    assert planner.plan("   ", call=FakeCall(answer(steps=[STATS]))) is None  # nothing said


def test_request_is_hcx007_structured_output_with_reasoning_off_and_no_tools():
    call = FakeCall(answer(steps=[COUPON]))
    planner.plan(
        '쿠폰 """ 만들어 줘',
        call=call,
        candidates=["coupon", "menu"],
        tasks="진행 중 menu(메뉴 등록·수정, 승인 대기)",
    )
    [(model, body)] = call.requests
    assert model == "HCX-007"
    assert body["thinking"] == {"effort": "none"}  # reasoning cannot go with Structured Outputs
    assert body["responseFormat"]["type"] == "json"
    assert not {"tools", "toolChoice", "tool_choice"} & set(body)  # and not with function calling
    assert body["maxCompletionTokens"] >= 1024
    system, user = (m["content"] for m in body["messages"])
    assert [m["role"] for m in body["messages"]] == ["system", "user"]  # no chat history
    for feature in load_features():
        assert f"- {feature['id']}: {feature['name_ko']}" in system  # one line per feature
    assert "참고 후보" in user and "coupon, menu" in user
    assert "진행 중 menu" in user
    assert user.count('"""') == 2  # the quote fence cannot be closed from inside the request


def test_schema_makes_features_and_scopes_enums():
    schema = planner.plan_schema()
    props = schema["properties"]
    assert props["scope"]["enum"] == ["store", "advice", "out"]
    step = props["steps"]["items"]["properties"]
    assert step["feature"]["enum"] == [f["id"] for f in load_features()]
    assert schema["required"] == ["scope", "steps", "clarify"]
    json.dumps(schema)  # serializable as is


def test_reference_candidates_default_to_keywords_without_any_embedding(monkeypatch):
    def no_embedding(*args, **kwargs):
        raise AssertionError("the planner must not request an embedding of its own")

    monkeypatch.setattr("agenthan.agent.place_matcher.embed_text", no_embedding)
    call = FakeCall(answer(steps=[COUPON]))
    planner.plan("쿠폰 만들어 줘", call=call)
    user = call.requests[0][1]["messages"][1]["content"]
    assert "coupon" in user
    assert planner.keyword_candidates("ㅎㅎㅎ") == []
    assert (
        "참고 후보(임베딩·키워드 순위, 틀릴 수 있음): 없음"
        in planner.plan_body("ㅎㅎㅎ", [], None)["messages"][1]["content"]
    )


def test_mode_defaults_to_legacy_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert planner.resolve_mode() == "legacy"
    monkeypatch.setenv("AGENT_MODE", "Shadow")
    assert planner.resolve_mode() == "shadow"
    assert planner.resolve_mode("legacy") == "legacy"  # an explicit value wins
    with pytest.raises(ValueError, match="AGENT_MODE"):
        planner.resolve_mode("fast")


def test_planner_mode_is_a_clear_not_implemented_error():
    with pytest.raises(NotImplementedError, match="B5"):
        planner.resolve_mode("planner")
