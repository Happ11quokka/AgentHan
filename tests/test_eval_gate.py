import json

import pytest

from agenthan.eval import scenario as sc
from agenthan.eval.flow import score_turn
from agenthan.eval.gate import check, main
from agenthan.eval.metrics import summarize


def detail(goal, runs=3, kind="P1", split="dev", unsafe=0, fabricated=0):
    return {
        "type": kind,
        "split": split,
        "runs": runs,
        "complete": runs,
        "goal": goal,
        "unsafe": unsafe,
        "fabricated": fabricated,
        "answer_failed": 0,
    }


def s1(details, *, file="v1", model="HCX-005", dirty=False, status="COMPLETE", **results):
    return {
        "conditions": {"cases": {"file_sha256": file}, "model_id": model, "git_dirty": dirty},
        "status": status,
        "results": {
            "api_error_turns": 0,
            "incomplete_runs": 0,
            "tokens_per_turn": 1000,
            "latency_p95_sec": 6.0,
            "answer_rate": 0.9,
            "per_scenario_detail": details,
            **results,
        },
    }


def many(goals, **kw):
    return {f"s{i:02d}": detail(g, **kw) for i, g in enumerate(goals)}


def test_same_results_pass_without_a_claimed_change():
    base = s1(many([3, 2, 3, 1] * 10))
    code, report = check(base, base)
    assert code == 0 and report.startswith("PASS (차이 없음)")


def test_clear_improvement_and_clear_regression():
    low, high = s1(many([1] * 40)), s1(many([3] * 40))
    code, report = check(low, high)
    assert code == 0 and report.startswith("PASS (향상)")
    code, report = check(high, low)
    assert code == 1 and "목표 달성률이 확실히 떨어짐" in report


def test_one_flaky_scenario_warns_but_does_not_fail():
    base = s1(many([3] * 40))
    details = many([3] * 40)
    details["s00"] = detail(1)
    code, report = check(base, s1(details))
    assert code == 0 and "크게 떨어진 시나리오 1개: s00" in report


def test_more_safety_violations_or_fabrication_fail_even_with_same_goal():
    base = s1(many([3] * 10))
    worse = many([3] * 10)
    worse["s03"] = detail(3, unsafe=1)
    assert check(base, s1(worse))[0] == 1
    worse["s03"] = detail(3, fabricated=1)
    code, report = check(base, s1(worse))
    assert code == 1 and "값 지어내기 0회 → 1회" in report


def test_incomplete_or_different_dataset_is_never_compared():
    base = s1(many([3] * 5))
    assert check(base, s1(many([3] * 5), file="v2"))[0] == 2
    assert check(base, s1(many([3] * 5), status="INCOMPLETE"))[0] == 2
    assert check(base, s1(many([3] * 5), api_error_turns=1))[0] == 2
    extra = many([3] * 5)
    extra["new"] = detail(3)
    assert check(base, s1(extra))[0] == 2
    assert check(base, {"results": {"route": {}}})[0] == 2


def test_partial_run_compares_only_the_shared_scenarios():
    base = s1({**many([1] * 30, split="dev"), "sealed-1": detail(3, split="sealed")})
    code, report = check(base, s1(many([3] * 30, split="dev")))
    assert code == 0 and "부분 비교: 시나리오 30/31개" in report and "PASS (향상)" in report


def test_dev_up_sealed_down_is_flagged_as_overfitting():
    base = s1(
        {**many([1] * 30, split="dev"), **{f"x{i}": detail(3, split="sealed") for i in range(5)}}
    )
    cur = s1(
        {**many([3] * 30, split="dev"), **{f"x{i}": detail(2, split="sealed") for i in range(5)}}
    )
    assert "과적합 의심" in check(base, cur)[1]


def test_conditions_and_cost_warnings():
    base = s1(many([3] * 5))
    cur = s1(many([3] * 5), model="HCX-007", dirty=True, tokens_per_turn=1300)
    report = check(base, cur)[1]
    assert "모델 다름" in report and "커밋 안 된 변경" in report and "토큰/턴" in report


def test_real_scenario_summary_feeds_the_gate():
    run = {
        "id": "a",
        "family": "f",
        "type": "P1",
        "area": "menu",
        "split": "dev",
        "tags": [],
        "complete": True,
        "goal": True,
        "final": {},
        "unapproved_write_turns": [],
        "out_of_scope_writes": [],
        "never_hits": [],
        "turns": [
            {
                "act": "request",
                "missing": False,
                "proposals": [],
                "changed": [],
                "latency_sec": 1.0,
                "attempt": 0,
            }
        ],
    }
    results = sc.summarize([run, run, {**run, "goal": False}])
    assert results["per_scenario_detail"]["a"] == detail(2)
    summary = {
        "conditions": {"cases": {"file_sha256": "v1"}},
        "status": "COMPLETE",
        "results": results,
    }
    assert check(summary, summary)[0] == 0


def test_cli_reads_files_and_rejects_malformed(tmp_path, capsys):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(s1(many([3] * 5))), encoding="utf-8")
    assert main(["--baseline", str(path), "--current", str(path)]) == 0
    path.write_text("{", encoding="utf-8")
    assert main(["--baseline", str(path), "--current", str(path)]) == 2
    assert "미완료" in capsys.readouterr().out


# L2 flow scoring (not a gate): read-only calls are not violations, store changes are.
def flow_row(kind, calls, changed=()):
    row = {
        "kind": kind,
        "lang": "ko",
        "expected": None,
        "got": calls[0] if calls else None,
        "calls": list(calls),
        "changed": list(changed),
        "feature_scored": False,
        "tool_scored": True,
        "expected_args": None,
        "expected_ask": None,
        "expected_store": None,
    }
    score_turn(row, {})
    return row


def test_injection_write_is_flow_safety_violation():
    # A read first must not conceal a write later in the same turn.
    row = flow_row("injection", ["get_statistics", "upsert_menu"])
    assert row["safety_violation"]
    assert summarize([row])["safety_violations"] == 1


@pytest.mark.parametrize("kind", ["injection", "unrelated", "missing", "ambiguous"])
def test_read_only_calls_are_not_safety_violations(kind):
    assert not flow_row(kind, ["get_statistics"])["safety_violation"]
    # Any store change in an unsafe turn is a violation, whatever tool caused it.
    assert flow_row(kind, [], changed=["menus.0.price"])["safety_violation"]
