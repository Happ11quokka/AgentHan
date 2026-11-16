"""S2 (planner target): schema, trace-first metrics, button skips, judge, offline end to end."""

import json
from collections import Counter
from pathlib import Path

import pytest

from agenthan.eval import judge as jd
from agenthan.eval import metrics as mt
from agenthan.eval import scenario as sc
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import execute_feature_tool

PHONE = {"phone": "02-123-4567"}


# ---------------------------------------------------------------- schema of s2.jsonl


@pytest.fixture(scope="module")
def s2():
    return sc.load_scenarios(sc.S2_SCENARIOS, sc.S2Scenario)


def test_s2_composition(s2):
    assert sc.S2_SCENARIOS.name == "s2.jsonl" and sc.SUITES == {
        "s1": sc.SCENARIOS,
        "s2": sc.S2_SCENARIOS,
    }
    assert Counter(s.family for s in s2) == {
        "multi_step": 12,
        "chain": 8,
        "lexical_trap": 10,
        "advice": 8,
        "out_of_scope": 5,
        "button_mix": 5,
    }
    assert Counter(s.split for s in s2) == {"dev": 40, "sealed": 8}
    # Every family keeps at least one sealed scenario for the final verdict.
    assert {s.family for s in s2 if s.split == "sealed"} == {s.family for s in s2}
    assert {s.lang for s in s2} <= {"ko", "ko_simple"}
    assert all(s.repeat == 3 and s.today and s.source == f"s2:{s.id}" for s in s2)


def test_s2_gold_is_feature_sets_and_expected_state(s2):
    for s in s2:
        assert (s.scope == "out") == (s.family == "out_of_scope")
        assert bool(s.steps) == (s.scope == "in"), s.id
        # Reads end at a call, writes at the preview: a write feature can't be a "call" step.
        for step in s.steps:
            assert step.feature in sc.FEATURE_IDS
        # Scenarios that never approve may not allow any write.
        approves = any(t.act == "approve" for t in s.turns)
        if not approves:
            assert s.expect.writes == [], s.id
        if s.family == "out_of_scope":
            assert [t.judge.kind for t in s.turns] == ["refusal"] and s.expect.writes == []
        if s.family == "advice":
            assert s.turns[0].judge and s.turns[0].judge.kind == "advice"
    # multi_step: one utterance, two or three features.
    for s in (x for x in s2 if x.family == "multi_step"):
        assert 2 <= len({st.feature for st in s.steps}) <= 3 and len(s.turns[0].say) > 0


def test_s2_button_turns_use_the_b1_action_shape(s2):
    buttons = [(s, t) for s in s2 for t in s.turns if t.action]
    assert {s.family for s, _ in buttons} == {"button_mix"}
    for s, t in buttons:
        assert (
            set(t.action) == {"action_id", "args", "label"}
            and t.action["action_id"] in sc.ACTION_IDS
        )
        assert t.say == t.action["label"]  # the owner "says" the label
        assert s.uses_buttons
    assert {t.action["action_id"] for _, t in buttons} >= {"review.summary", "task.approve"}
    # No other family needs run_action: only button_mix scenarios may be skipped.
    assert not any(s.uses_buttons for s in s2 if s.family != "button_mix")


def test_s2_schema_rejects_bad_rows():
    base = {
        "id": "x",
        "family": "multi_step",
        "steps": [{"feature": "menu", "reach": "preview"}],
        "turns": [{"say": "a", "act": "request"}],
        "expect": {"final": {}},
    }
    sc.S2Scenario.model_validate(base)
    for patch in (
        {"steps": []},  # in scope needs steps
        {"steps": [{"feature": "nope", "reach": "call"}]},
        {"scope": "out"},  # out scope only for out_of_scope
        {"family": "single_task"},
        {
            "turns": [
                {"act": "request", "action": {"action_id": "no.such", "args": {}, "label": "x"}}
            ]
        },
    ):
        with pytest.raises(ValueError):
            sc.S2Scenario.model_validate({**base, **patch})


def test_s1_models_are_untouched_by_s2():
    """S2 fields live in subclasses, so S1 dumps (hashed into baselines) keep their shape."""
    assert "steps" not in sc.Scenario.model_fields and "action" not in sc.ScenarioTurn.model_fields
    s1 = sc.load_scenarios()
    assert len(s1) == 92 and "s2" not in sc.summarize([])  # no S2 block for S1 runs


def test_setup_list_appends_each_item():
    data = sc.apply_setup({"reviews": [{"id": "a"}]}, {"reviews[+]": [{"id": "b"}, {"id": "c"}]})
    assert [r["id"] for r in data["reviews"]] == ["a", "b", "c"]


# ---------------------------------------------------------------- metrics (pure)


def call(feature, action="pending_approval"):
    return {"feature": feature, "action": action}


def test_step_done_needs_the_right_feature_and_reach():
    preview = {"feature": "menu", "reach": "preview"}
    read = {"feature": "statistics", "reach": "call"}
    assert mt.step_done(preview, [call("menu")])
    assert not mt.step_done(preview, [call("menu", "ok")])  # executed/read is not a preview
    assert not mt.step_done(preview, [call("coupon")])
    assert mt.step_done(read, [call("statistics", "delegated")])
    assert not mt.step_done(read, [call("statistics", "error")])  # a failed call did not get there


def test_score_plan_scope_and_planned_set():
    steps = [{"feature": "menu", "reach": "preview"}, {"feature": "coupon", "reach": "preview"}]
    done = mt.score_plan(steps, "in", ["menu", "coupon"], [call("menu"), call("coupon")])
    assert done["gold_features"] == ["coupon", "menu"] and all(s["done"] for s in done["steps"])
    half = mt.score_plan(steps, "in", ["menu"], [call("menu")])
    assert [s["done"] for s in half["steps"]] == [True, False]
    assert mt.score_plan([], "out", [], [])["scope_ok"] is True
    assert mt.score_plan([], "out", ["menu"], [call("menu", "ok")])["scope_ok"] is False
    assert mt.score_plan([], "out", [], [], wrote=True)["scope_ok"] is False  # state changed
    assert mt.score_plan(steps, "in", [], [])["scope_ok"] is None  # only out-of-scope runs count


def run(steps, planned, calls, *, scope="in", turns=2, model_calls=2, budget=0, wrote=False):
    return {
        "s2": mt.score_plan(steps, scope, planned, calls, wrote=wrote)
        | {"turns": turns, "budget_turns": budget},
        "model_calls": model_calls,
    }


def test_summarize_s2_metrics():
    both = [{"feature": "menu", "reach": "preview"}, {"feature": "coupon", "reach": "preview"}]
    runs = [
        run(both, ["menu", "coupon"], [call("menu"), call("coupon")], model_calls=4),  # perfect
        run(
            both, ["menu"], [call("menu"), call("order")], model_calls=6, budget=1
        ),  # 1 step, 1 stray
        run([], [], [], scope="out", turns=1, model_calls=1),  # polite refusal
        run([], ["menu"], [call("menu", "ok")], scope="out", turns=1, model_calls=3),  # store tool
    ]
    m = mt.summarize_s2(runs)
    assert m["runs"] == 4 and m["plan_accuracy"] == 2 / 4  # run 1 and the refusal
    assert m["step_completion"] == 3 / 4 and m["steps_scored"] == 4
    assert m["unneeded_call_rate"] == 2 / 5  # order, and the menu call under out of scope
    assert m["scope_accuracy"] == 1 / 2 and m["scope_scored"] == 2
    assert m["budget_exhausted_rate"] == 1 / 6  # one of six turns hit the budget
    assert m["model_calls_per_turn"] == 14 / 6
    empty = mt.summarize_s2([])
    assert empty["plan_accuracy"] is None and empty["model_calls_per_turn"] is None


# ---------------------------------------------------------------- normalized trace


def test_normalize_trace_counts_previews_not_approval_executions():
    trace = {
        "feature": "menu",
        "via": "select",
        "calls": [{"name": "upsert_menu"}, {"name": "upsert_menu"}],
        "results": [{"ok": True, "action": "pending_approval"}, {"ok": False}],
    }
    out = sc.normalize_trace(trace)
    assert [(c["feature"], c["action"]) for c in out["calls"]] == [
        ("menu", "pending_approval"),
        ("menu", "error"),
    ]
    assert out["features"] == ["menu"] and out["budget"] is False
    approval = {**trace, "via": "approval"}
    assert sc.normalize_trace(approval)["calls"] == []  # executing an approved change
    limit = {**trace, "results": [{"ok": False, "error": "tool_limit"}] * 2}
    assert sc.normalize_trace(limit)["budget"] is True
    assert sc.normalize_trace(None) is None


def test_review_and_statistics_answers_stand_in_as_delegated_calls():
    review = {"feature": "review", "via": "select", "calls": [], "results": []}
    assert sc.normalize_trace(review)["calls"][0]["action"] == "delegated"
    assert (
        sc.normalize_trace(review, review_waiting=True)["calls"][0]["action"] == "pending_approval"
    )
    stats = {"feature": "statistics", "via": "report", "calls": [], "results": []}
    assert sc.normalize_trace(stats)["calls"] == [
        {"tool": "statistics", "feature": "statistics", "action": "delegated"}
    ]
    none = {"feature": None, "via": "none", "calls": [], "results": []}
    assert sc.normalize_trace(none) == {"features": [], "calls": [], "budget": False}


# ---------------------------------------------------------------- runner with a scripted subject


class TraceSubject:
    """Scripted assistant that reports a normalized trace; `actions` enables run_action (B1)."""

    def __init__(self, path: Path, plan: dict, *, actions: bool = False):
        self.store = DummyStore(path)
        self.plan, self._calls, self._proposals = plan, [], []
        self.pending = None
        if actions:
            self.supports_actions = True
            self.run_action = self._action

    def say(self, text):
        self._calls, self._proposals = self.plan.get(text, []), []
        if text == "네" and self.pending:
            execute_feature_tool(self.store, "update_business_info", PHONE)
            self.pending, self._calls = None, []  # executing an approved change: no new call
        elif any(a == "pending_approval" for _, a in self._calls):
            self.pending = PHONE
            self._proposals = [{"tool": "update_business_info", "arguments": PHONE}]
        return "ok"

    def _action(self, action):
        return self.say(action["label"])

    def photo(self, image, text):
        return "ok"

    def trace(self):
        calls = [{"tool": f, "feature": f, "action": a} for f, a in self._calls]
        return {"features": [f for f, _ in self._calls], "calls": calls, "budget": False}

    def proposals(self):
        return self._proposals

    def rejected(self):
        return False


def s2_scenario(turns, steps, final, writes, **kw):
    return sc.S2Scenario.model_validate(
        {
            "id": "t",
            "family": kw.pop("family", "multi_step"),
            "steps": steps,
            "turns": turns,
            "expect": {"final": final, "writes": writes},
            **kw,
        }
    )


def make(plan, **kw):
    return lambda path, probe, today=None: TraceSubject(path, plan, **kw)


def test_run_scores_steps_from_the_trace_and_goal_needs_them():
    s = s2_scenario(
        [{"say": "바꿔", "act": "request"}, {"say": "네", "act": "approve"}],
        [{"feature": "business_info", "reach": "preview"}],
        {"shop.phone": "02-123-4567"},
        ["shop.phone"],
    )
    ok = sc.run_scenario(s, make({"바꿔": [("business_info", "pending_approval")]}))
    assert ok["goal"] and ok["s2"]["steps"][0]["done"] and ok["s2"]["planned"] == ["business_info"]
    # Writing without ever previewing through a tool: the state is right but the step isn't reached.
    miss = sc.run_scenario(s, make({"바꿔": []}))
    assert not miss["s2"]["steps"][0]["done"] and not miss["goal"]


def test_out_of_scope_fails_when_a_store_tool_runs():
    s = s2_scenario(
        [{"say": "은행 몇 시에 닫아?", "act": "unrelated"}],
        [],
        {},
        [],
        family="out_of_scope",
        scope="out",
    )
    polite = sc.run_scenario(s, make({}))
    assert polite["goal"] and polite["s2"]["scope_ok"] is True
    called = sc.run_scenario(s, make({"은행 몇 시에 닫아?": [("business_hours", "ok")]}))
    assert called["s2"]["scope_ok"] is False and not called["goal"]


def test_button_scenarios_are_skipped_not_failed_without_run_action():
    s = s2_scenario(
        [
            {
                "act": "request",
                "action": {"action_id": "review.summary", "args": {}, "label": "요즘 리뷰"},
            }
        ],
        [{"feature": "review", "reach": "call"}],
        {},
        [],
        family="button_mix",
    )
    skipped = sc.run_scenario(s, make({}))
    assert skipped["skipped"] == "unsupported_action" and "goal" not in skipped
    summary = sc.summarize([skipped, skipped])
    assert summary["runs"] == 0 and summary["incomplete_runs"] == 0  # not a failure, not a run
    assert summary["s2"]["unsupported_runs"] == 2 and summary["s2"]["unsupported_scenarios"] == [
        "t"
    ]
    assert "건너뜀" in sc.render_table({"results": summary})
    played = sc.run_scenario(s, make({"요즘 리뷰": [("review", "delegated")]}, actions=True))
    assert (
        not played.get("skipped")
        and played["goal"]
        and played["turns"][0]["action"] == "review.summary"
    )


def test_sentence_judge_runs_only_with_a_judge():
    s = s2_scenario(
        [
            {
                "say": "손님이 줄었는데?",
                "act": "request",
                "judge": {"kind": "advice", "evidence": {"v": 1}},
            }
        ],
        [{"feature": "statistics", "reach": "call"}],
        {},
        [],
        family="advice",
    )
    plan = {"손님이 줄었는데?": [("statistics", "ok")]}
    unjudged = sc.run_scenario(s, make(plan))
    assert unjudged["goal"] and unjudged["turns"][0]["judge_skipped"]
    assert sc.summarize([unjudged])["s2"]["judge_unscored_turns"] == 1
    fake = jd.FakeJudge()
    assert sc.run_scenario(s, make(plan), judge=fake)["goal"] and fake.calls == [
        ("advice", "손님이 줄었는데?")
    ]
    failed = sc.run_scenario(s, make(plan), judge=jd.FakeJudge(verdict=False))
    assert not failed["goal"] and failed["turns"][0]["judge_failed"]
    assert sc.summarize([failed])["s2"]["judge_rate"] == 0.0


def test_judge_rubrics_cover_advice_and_refusal():
    assert {c.id for c in jd.ADVICE_RUBRIC} >= {
        "evidence",
        "playbook",
        "no_invention",
        "no_done_claim",
    }
    assert {c.id for c in jd.REFUSAL_RUBRIC} >= {"declines", "polite", "alternative"}
    assert jd.FakeJudge().grade("refusal", "q", "어렵지만 도와드릴게요", {}).ok
    assert (
        not jd.FakeJudge().grade("refusal", "q", "  ", {}).ok
    )  # empty reply fails every criterion


# ---------------------------------------------------------------- offline end to end


def test_suite_s2_runs_to_the_end_offline_with_family_table(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    code = sc.main(
        [
            "--suite",
            "s2",
            "--backend",
            "fake",
            "--judge",
            "fake",
            "--repeat",
            "1",
            "--label",
            "s2-fake",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 0  # COMPLETE: nothing hit the network
    [folder] = list(tmp_path.iterdir())
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    table = (folder / "table.md").read_text(encoding="utf-8")
    s2 = summary["results"]["s2"]
    assert summary["status"] == "COMPLETE" and summary["conditions"]["suite"] == "s2"
    assert summary["conditions"]["backend"] == "fake" and summary["conditions"]["judge"] == "fake"
    assert set(s2["per_family"]) == {
        "multi_step",
        "chain",
        "lexical_trap",
        "advice",
        "out_of_scope",
    }
    assert s2["unsupported_scenarios"] == ["bm-01", "bm-02", "bm-03", "bm-04", "bm-05"]
    assert s2["runs"] == 43 and s2["scope_accuracy"] == 1.0  # the fake never calls a store tool
    assert s2["model_calls_per_turn"] is not None
    for family in ("multi_step", "chain", "lexical_trap", "advice", "out_of_scope"):
        assert f"| {family} |" in table
    assert "계획 정확도" in table and "scope 정확도" in table and "미지원" in table
    assert summary["conditions"]["cases"]["path"].endswith("tests/data/eval/s2.jsonl")


def test_default_suite_is_still_s1(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    code = sc.main(
        [
            "--backend",
            "fake",
            "--only",
            "unrelated-chatter",
            "--repeat",
            "1",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 0
    [folder] = list(tmp_path.iterdir())
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["conditions"]["suite"] == "s1" and "s2" not in summary["results"]
    assert summary["conditions"]["cases"]["path"].endswith("tests/data/eval/s1.jsonl")
