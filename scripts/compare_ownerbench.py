"""OwnerBench: compare the naive baseline with AgentHan over S1 + S2 as one benchmark.

Reads the rows.jsonl of four scenario runs (eval_results/<run>/, not committed) and prints the
goal rate of each system, the paired difference with a scenario-level bootstrap 95% interval,
safety, model calls, latency, and the goal rate per scenario type.

    uv run python scripts/compare_ownerbench.py \
        NAIVE_S1_DIR NAIVE_S2_DIR AGENTHAN_S1_DIR AGENTHAN_S2_DIR

Button-mix and photo scenarios are left out (code paths, not the model; ADR-0009). Runs with an
API error are dropped and counted. Tokens are not compared: the planner call is not counted in
the per-turn token figure. The 2026-11-21 run summaries are in docs/runs/ownerbench-*.summary.json.
"""

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

EXCLUDE = {"button_mix", "photo_interject"}
TYPES = {
    "single_task": "단일 작업",
    "missing_value": "빠진 값",
    "revise_decline": "고치기·거절",
    "unrelated": "관계없는 말",
    "review_reply": "리뷰 답글",
    "approval_interject": "끼어들기·재개",
    "slot_switch": "끼어들기·재개",
    "same_feature_two": "같은 기능 두 번",
    "ambiguous_ref": "모호·주입·긴 대화",
    "injection": "모호·주입·긴 대화",
    "long_session": "모호·주입·긴 대화",
    "multi_step": "다단계",
    "lexical_trap": "단어 함정",
    "chain": "연쇄",
    "advice": "조언",
    "out_of_scope": "범위 밖",
}


def load(directories: list[Path]) -> list[dict]:
    runs = []
    for directory in directories:
        for line in (directory / "rows.jsonl").open(encoding="utf-8"):
            run = json.loads(line)
            if not run.get("skipped") and run["family"] not in EXCLUDE:
                runs.append(run)
    return runs


def api_error(run: dict) -> bool:
    return any(t.get("api_error") for t in run["turns"])


def unsafe(run: dict) -> bool:
    return bool(run["unapproved_write_turns"] or run["out_of_scope_writes"] or run["never_hits"])


def stats(runs: list[dict]) -> dict:
    valid = [r for r in runs if not api_error(r)]
    turns = [t for r in valid for t in r["turns"]]
    latency = sorted(t["latency_sec"] for t in turns)
    by_scenario = defaultdict(list)
    for run in valid:
        by_scenario[run["id"]].append(run["goal"])
    return {
        "runs": len(runs),
        "valid": len(valid),
        "goal": statistics.mean(r["goal"] for r in valid),
        "pass_all": statistics.mean(all(v) for v in by_scenario.values()),
        "unsafe_runs": sum(unsafe(r) for r in valid),
        "unapproved_runs": sum(bool(r["unapproved_write_turns"]) for r in valid),
        "model_calls_per_turn": statistics.mean(t.get("model_calls", 0) for t in turns),
        "latency_p50": latency[len(latency) // 2],
        "latency_p95": latency[int(len(latency) * 0.95)],
        "by_scenario": by_scenario,
    }


def paired_bootstrap(a: dict, b: dict, samples: int = 5000, seed: int = 7):
    """Mean of (b - a) over scenarios both systems ran, with a 95% scenario bootstrap."""
    ids = sorted(set(a) & set(b))
    diff = {i: statistics.mean(b[i]) - statistics.mean(a[i]) for i in ids}
    rng = random.Random(seed)
    draws = sorted(statistics.mean(diff[rng.choice(ids)] for _ in ids) for _ in range(samples))
    return (
        statistics.mean(diff.values()),
        draws[int(samples * 0.025)],
        draws[int(samples * 0.975)],
        len(ids),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("naive_s1", type=Path)
    parser.add_argument("naive_s2", type=Path)
    parser.add_argument("agenthan_s1", type=Path)
    parser.add_argument("agenthan_s2", type=Path)
    args = parser.parse_args()
    naive = load([args.naive_s1, args.naive_s2])
    agenthan = load([args.agenthan_s1, args.agenthan_s2])
    a, b = stats(naive), stats(agenthan)
    point, low, high, n = paired_bootstrap(a["by_scenario"], b["by_scenario"])
    print(f"valid runs: naive {a['valid']}/{a['runs']}, AgentHan {b['valid']}/{b['runs']}")
    print(f"goal rate: naive {a['goal']:.1%}, AgentHan {b['goal']:.1%}")
    print(f"difference: {point * 100:+.1f}%p [{low * 100:+.1f}, {high * 100:+.1f}] ({n} scenarios)")
    for key in ("pass_all", "unsafe_runs", "unapproved_runs", "model_calls_per_turn"):
        print(f"{key}: naive {a[key]:.3g}, AgentHan {b[key]:.3g}")
    print(
        f"latency p50/p95: naive {a['latency_p50']:.2f}/{a['latency_p95']:.2f}s, "
        f"AgentHan {b['latency_p50']:.2f}/{b['latency_p95']:.2f}s"
    )
    groups = defaultdict(lambda: {"naive": [], "agenthan": [], "ids": set()})
    for name, runs in (("naive", naive), ("agenthan", agenthan)):
        for run in runs:
            if not api_error(run):
                group = groups[TYPES.get(run["family"], run["family"])]
                group[name].append(run["goal"])
                group["ids"].add(run["id"])
    print("\n| type | scenarios | naive | AgentHan |\n| --- | --- | --- | --- |")
    for name, group in groups.items():
        if group["naive"] and group["agenthan"]:
            print(
                f"| {name} | {len(group['ids'])} | {statistics.mean(group['naive']):.1%} "
                f"| {statistics.mean(group['agenthan']):.1%} |"
            )


if __name__ == "__main__":
    main()
