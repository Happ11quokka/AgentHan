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


def test_mode_defaults_to_planner_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert planner.resolve_mode() == "planner"
    monkeypatch.setenv("AGENT_MODE", "Shadow")
    assert planner.resolve_mode() == "shadow"
    assert planner.resolve_mode("legacy") == "legacy"  # an explicit value wins
    with pytest.raises(ValueError, match="AGENT_MODE"):
        planner.resolve_mode("fast")


def test_planner_mode_is_available():
    assert planner.resolve_mode("planner") == "planner"


# --- planner 모드(B5b): 원장 계획 -----------------------------------------------------------

TASKS = [
    {
        "task_id": "t1",
        "feature": "coupon",
        "status": "collecting",
        "filled": {"discount_value": 1000},
        "missing": ["쿠폰 이름"],
        "preview_summary": "",
    }
]
SHOWN = [{"kind": "review", "id": "r02", "label": "별점 3 · 답글 없음 · 웨이팅"}]


@pytest.mark.parametrize("action", [None, "none", "invented"])
def test_b5d_review_action_must_be_explicit(action):
    step = {"kind": "new", "feature": "review", "goal": "미응답 리뷰 조회"}
    if action is not None:
        step["review_action"] = action
    assert planner.plan_ledger("미응답 리뷰 조회", call=FakeCall(answer(steps=[step]))) is None


def test_b5d_history_cannot_select_an_invented_or_open_task():
    content = json.dumps({"scope": "history", "steps": [], "clarify": "", "history_ids": ["t1"]})
    with pytest.raises(planner.PlanError):
        planner.parse_ledger_plan(content, {"t1": "coupon"}, [], ["t2"])


def test_ledger_schema_limits_task_ids_and_refs_to_the_input():
    step = planner.ledger_schema(["t1", "t2"], ["r02"])["properties"]["steps"]["items"]
    assert step["properties"]["kind"]["enum"] == list(planner.KINDS)
    assert step["properties"]["task_id"]["enum"] == ["t1", "t2"]
    assert step["properties"]["ref"]["enum"] == ["r02"]
    empty = planner.ledger_schema([], [])["properties"]["steps"]["items"]["properties"]
    assert empty["kind"]["enum"] == ["new"] and "task_id" not in empty and "ref" not in empty
    scopes = planner.ledger_schema([], [])["properties"]["scope"]["enum"]
    assert scopes == ["store", "advice", "out", "small_talk", "history"]


def test_ledger_plan_reads_continue_and_ref_and_takes_the_feature_from_the_task():
    step = {"kind": "continue", "task_id": "t1", "feature": "menu", "goal": "이름 받음"}
    ref = {
        "kind": "new",
        "feature": "review",
        "goal": "답글",
        "ref": "r02",
        "ref_basis": "direct",
        "review_action": "draft",
    }
    call = FakeCall(answer(steps=[step, ref]))
    plan = planner.plan_ledger("이름은 단골 감사 쿠폰이요", call=call, tasks=TASKS, shown=SHOWN)
    assert plan.steps[0] == planner.Step("coupon", "이름 받음", "continue", "t1")
    assert plan.steps[1].ref == "r02" and plan.steps[1].kind == "new"
    [(model, body)] = call.requests
    assert model == "HCX-007" and body["thinking"] == {"effort": "none"}
    assert not {"tools", "toolChoice"} & set(body)
    user = body["messages"][1]["content"]
    assert '"task_id": "t1"' in user and '"missing": ["쿠폰 이름"]' in user and "r02" in user
    assert plan.as_trace()["steps"][0]["kind"] == "continue"


@pytest.mark.parametrize(
    "step",
    [
        {"kind": "approve", "task_id": "t9", "feature": "coupon", "goal": "저장"},
        {"kind": "merge", "task_id": "t1", "feature": "coupon", "goal": "?"},
    ],
)
def test_ledger_plan_outside_the_ledger_is_none(step):
    call = FakeCall(answer(steps=[step]))
    assert planner.plan_ledger("네", call=call, tasks=TASKS, shown=SHOWN) is None


def test_small_talk_has_no_steps_and_old_plans_default_to_new():
    talk = FakeCall(answer(scope="small_talk"))
    assert planner.plan_ledger("안녕하세요", call=talk).scope == "small_talk"
    old = FakeCall(answer(steps=[COUPON]))
    assert planner.plan_ledger("쿠폰", call=old).steps[0].kind == "new"
    # shadow 계획 trace는 예전 모양 그대로다.
    assert planner.plan("쿠폰", call=old).as_trace()["steps"] == [COUPON]


def test_b5g_ledger_dependencies_and_extraction_leave_shadow_schema_unchanged():
    schema = planner.ledger_schema([], [])
    step = schema["properties"]["steps"]["items"]["properties"]
    assert step["needs"]["items"]["enum"] == planner.feature_ids()
    assert step["target_metric"]["enum"] == ["none", "menu_mentions"]
    assert "write_features" in schema["required"]
    assert set(planner.plan_schema()["properties"]) == {"scope", "steps", "clarify"}
    raw = {
        "scope": "store",
        "steps": [{**COUPON, "needs": ["settlement"]}],
        "write_features": ["coupon"],
        "clarify": "",
    }
    result = planner.parse_ledger_plan(json.dumps(raw), {}, [])
    assert result.write_features == ("coupon",)
    assert result.steps[0].needs == ("settlement",)
    assert result.as_trace()["steps"][0]["needs"] == ["settlement"]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"needs": ["invented"]}, "needs"),
        ({"needs": ["menu"]}, "needs"),
        ({"target_metric": "invented"}, "target_metric"),
        ({"target_order": "invented"}, "target_order"),
        ({"target_metric": "menu_mentions", "needs": []}, "target_metric"),
        ({"basis": "invented"}, "basis"),
        ({"review_action": "draft"}, "review_action"),
        (
            {"owner_values": [{"field": "focus", "value": "all", "evidence": "수정"}]},
            "owner_values",
        ),
    ],
)
def test_b5h_invalid_optional_field_is_dropped_and_the_plan_kept(change, field):
    """B5h: 선택 필드 하나가 틀려도 그 필드만 버린다. 의존·집계·추출은 실행에 닿지 않는다."""
    raw = {
        "scope": "store",
        "steps": [{"feature": "menu", "goal": "수정", **change}],
        "clarify": "",
    }
    plan = planner.parse_ledger_plan(json.dumps(raw), {}, [])
    step = plan.steps[0]
    assert (step.feature, step.goal) == ("menu", "수정")
    assert step.needs == () and step.target_metric is None and step.target_order == "max"
    assert step.basis == "requested" and step.review_action is None and step.owner_values == ()
    assert plan.as_trace()["dropped_fields"] == [f"steps[0].{field}"]


@pytest.mark.parametrize(
    "step",
    [
        {"kind": "new", "feature": "menu"},  # goal 없음
        {"kind": "new", "feature": "invented", "goal": "수정"},
        {"kind": "approve", "task_id": "t9", "feature": "coupon", "goal": "저장"},
        {"kind": "new", "feature": "review", "goal": "리뷰", "review_action": "invented"},
    ],
)
def test_b5h_required_field_errors_still_reject_the_plan(step):
    with pytest.raises(planner.PlanError):
        content = answer(steps=[step])["message"]["content"]
        planner.parse_ledger_plan(content, {"t1": "coupon"}, [])


@pytest.mark.parametrize("ref", ["r99", "r02"])
@pytest.mark.parametrize("feature", ["menu", "onsite_payment"])
def test_b5h_ref_survives_only_on_a_step_of_the_same_kind(ref, feature):
    """리뷰 ref 후보는 리뷰 단계에서만 남는다. 다른 기능 단계의 ref와 지어낸 ref는 버린다."""
    steps = [
        {"feature": feature, "goal": "변경", "ref": ref, "ref_basis": "direct"},
        {
            "feature": "review",
            "goal": "답글",
            "review_action": "draft",
            "ref": ref,
            "ref_basis": "direct",
        },
    ]
    plan = planner.plan_ledger("바꾸고 답글", call=FakeCall(answer(steps=steps)), shown=SHOWN)
    assert plan is not None and plan.steps[0].ref is None
    assert plan.steps[1].ref == (ref if ref == "r02" else None)
    assert "steps[0].ref" in plan.as_trace()["dropped_fields"]


def test_b5h_review_candidates_are_marked_for_review_steps_and_price_delta_is_gone():
    call = FakeCall(answer(steps=[COUPON]))
    planner.plan_ledger("쿠폰", call=call, review_candidates=SHOWN)
    body = call.requests[0][1]
    assert "리뷰 단계(feature=review)에서만 쓰는 ref 후보" in body["messages"][1]["content"]
    items = body["responseFormat"]["schema"]["properties"]["steps"]["items"]
    assert "price_delta" not in items["properties"] and "price_delta" not in items["required"]
    assert "price_delta" not in body["messages"][0]["content"]
