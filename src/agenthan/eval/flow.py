"""Route and flow evaluation over the real OwnerAssistant.

- route: which feature one utterance goes to, through the whole ask() branch
  (glossary, report regex, pending feature, embedding match), not match_features alone.
- flow: each case's turns in one fresh assistant: route → ask for missing input →
  tool call and args → store diff. Statistics and review features are scored up to the
  route only; their later turns are skipped.

Expectation fields that are omitted are not scored. Nothing is sent to Naver.
"""

import json
import tempfile
import time
from datetime import date
from pathlib import Path

from agenthan.agent import owner_assistant
from agenthan.agent.clova_chat import ClovaChatError, ClovaRequestRejected
from agenthan.agent.review_delegate import REVIEW_FEATURES
from agenthan.core.embedding import ClovaEmbeddingError
from agenthan.eval.cases import Case, Turn
from agenthan.eval.metrics import UNSAFE_KINDS, summarize
from agenthan.eval.replay import canonical
from agenthan.tools.feature_tools import TOOL_FEATURES, needs_approval

ROUTE_ONLY = frozenset({"statistics", *REVIEW_FEATURES})
UNSCORED = object()
STAGES = ("feature", "ask", "tool", "args", "store")


class Probe:
    """Pass calls through while keeping API errors and token usage for one turn."""

    def __init__(self, backend):
        self.backend = backend
        self.reset()

    def reset(self):
        self.errors: list[str] = []
        self.tokens: list[int] = []
        self.select_rejected = 0

    def chat(self, messages, *, tools):
        try:
            response = self.backend.chat(messages, tools=tools)
        except ClovaRequestRejected:
            raise  # The app answers the owner; scored as a failed turn, not an outage.
        except ClovaChatError as error:
            self.errors.append(str(error))
            raise
        usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            self.tokens.append(usage["total_tokens"])
        return response

    def select(self, messages, *, tools):
        """HCX-DASH-002 select_feature. A rejection is app behavior (it falls back)."""
        if not hasattr(self.backend, "select"):
            raise ClovaChatError("backend has no select_feature")  # Test fakes: fallback.
        try:
            response = self.backend.select(messages, tools=tools)
        except ClovaRequestRejected:
            self.select_rejected += 1
            raise
        except ClovaChatError as error:
            self.errors.append(str(error))
            raise
        usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            self.tokens.append(usage["total_tokens"])
        return response

    def embed(self, text):
        try:
            return self.backend.embed(text)
        except ClovaEmbeddingError as error:
            # match_features swallows embedding failures; evaluation must not.
            self.errors.append(str(error))
            raise


def expected_feature(case: Case, index: int, turn: Turn):
    if "feature" in turn.expect.model_fields_set:
        return turn.expect.feature
    if index:
        return UNSCORED
    tool = turn.expect.tool or case.near_tool
    if tool:
        return TOOL_FEATURES.get(tool, tool)
    return case.expected_feature


def expected_tool(turn: Turn):
    return turn.expect.tool if "tool" in turn.expect.model_fields_set else UNSCORED


def store_diff(before: object, after: object, prefix: str = "") -> list[str]:
    """Dot paths whose values differ (list items by index)."""
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after), key=str)
        return [
            path
            for key in keys
            for path in store_diff(before.get(key), after.get(key), f"{prefix}{key}.")
        ]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            path
            for i, (old, new) in enumerate(zip(before, after, strict=True))
            for path in store_diff(old, new, f"{prefix}{i}.")
        ]
    return [] if before == after else [prefix.rstrip(".")]


def lookup(data: object, path: str):
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return UNSCORED
    return data


def _arguments(value):
    if isinstance(value, str):
        try:
            return json.loads(value) if value.strip() else {}
        except ValueError:
            return None
    return value


# Cases assume this date (dummy reservations 2026-11-10/11); replay requests stay stable.
EVAL_TODAY = date(2026, 11, 10)


def _assistant(folder: str, probe: Probe):
    return owner_assistant.OwnerAssistant(
        Path(folder) / "store.json",
        chat=probe.chat,
        embed=probe.embed,
        select_chat=probe.select,
        today=EVAL_TODAY,
    )


def run_route(case: Case, backend) -> dict:
    """Route the first turn in a fresh assistant: embedding candidates + DASH select only."""
    turn = case.turns[0]
    feature = expected_feature(case, 0, turn)
    row = {
        "id": case.id,
        "lang": case.lang,
        "kind": case.kind,
        "expected": None if feature is UNSCORED else feature,
        "got": None,
        "calls": [],
        "safety_violation": False,
    }
    probe = Probe(backend)
    with tempfile.TemporaryDirectory() as folder:
        started = time.monotonic()
        route = _assistant(folder, probe).route(turn.say)
        row["latency_sec"] = time.monotonic() - started
    got = "clarify" if route.via == "clarify" else route.feature_id
    row.update(got=got, via=route.via, candidates=list(route.candidates))
    if probe.tokens:
        row["usage"] = {"total_tokens": sum(probe.tokens)}
    if probe.select_rejected:
        row["select_rejected"] = True
    if probe.errors:
        row["api_error"] = probe.errors[0]
    return row


def summarize_route(rows: list[dict], *, planned: int | None = None) -> dict:
    """Shared metrics plus where routing failed: candidate recall vs DASH choice."""
    summary = summarize(rows, planned=planned)
    answered = [r for r in rows if not r.get("api_error")]
    positives = [r for r in answered if r["expected"] not in (None, "clarify")]
    recalled = [r for r in positives if r["expected"] in r.get("candidates", [])]
    selected = [r for r in answered if r.get("via") in ("select", "clarify")]
    summary.update(
        candidate_recall=_rate([r in recalled for r in positives]),
        select_accuracy_when_recalled=_rate(
            [r["got"] == r["expected"] for r in recalled if r in selected]
        ),
        via={
            v: sum(r.get("via") == v for r in answered)
            for v in sorted({r.get("via") for r in answered})
        },
        select_rejected=sum(bool(r.get("select_rejected")) for r in rows),
    )
    return summary


def run_flow(case: Case, backend) -> list[dict]:
    """One row per executed turn; later turns after a route-only feature are skipped."""
    rows = []
    probe = Probe(backend)
    with tempfile.TemporaryDirectory() as folder:
        assistant = _assistant(folder, probe)
        for index, turn in enumerate(case.turns):
            feature = expected_feature(case, index, turn)
            tool = expected_tool(turn)
            row = {
                "id": case.id,
                "turn": index,
                "lang": case.lang,
                "kind": case.kind,
                "expected_feature": None if feature is UNSCORED else feature,
                "feature_scored": feature is not UNSCORED,
                "expected": None if tool is UNSCORED else tool,
                "tool_scored": tool is not UNSCORED and feature not in ROUTE_ONLY,
                "expected_args": turn.expect.args,
                "expected_ask": turn.expect.ask_missing,
                "expected_store": turn.expect.store,
            }
            probe.reset()
            before = assistant.store.read()
            started = time.monotonic()
            route_only = False
            try:
                if assistant.reply_to_approval(turn.say) is None:
                    route = assistant.route(turn.say)
                    route_only = route.feature_id in ROUTE_ONLY
                    if route_only:
                        assistant.trace = owner_assistant.new_trace(route.feature_id, route.via)
                    else:
                        assistant.respond(turn.say, route)
            except (ClovaChatError, ClovaEmbeddingError) as error:
                probe.errors.append(str(error))
            row["latency_sec"] = time.monotonic() - started
            if probe.tokens:
                row["usage"] = {"total_tokens": sum(probe.tokens)}
            trace = assistant.trace
            after = assistant.store.read()
            calls = trace["calls"]
            results = trace["results"]
            row.update(
                got_feature=trace["feature"],
                via=trace["via"],
                calls=[c["name"] for c in calls],
                got=calls[0]["name"] if calls else None,
                arguments=calls[0]["arguments"] if calls else None,
                missing=trace["missing"],
                pending_approval=trace["pending_approval"],
                rejected=trace.get("rejected", False),
                changed=store_diff(before, after),
                route_only=route_only,
            )
            if results and calls:
                first = results[0]
                row["args_ok"] = bool(first.get("ok"))
            if probe.errors:
                row["api_error"] = probe.errors[0]
            score_turn(row, after)
            rows.append(row)
            rest = len(case.turns) - index - 1
            if route_only:
                row["skipped_turns"] = rest  # By design, not counted as unexecuted.
                break
            if "api_error" in row:
                rows.extend(
                    {
                        "id": case.id,
                        "turn": index + 1 + i,
                        "lang": case.lang,
                        "kind": case.kind,
                        "executed": False,
                        "tool_scored": False,
                        "route_only": False,
                        "stages": {},
                        "first_failure": None,
                    }
                    for i in range(rest)
                )
                break
    return rows


def score_turn(row: dict, store: dict) -> None:
    """Mark each scored stage pass/fail and the first failing stage."""
    stages = {}
    if row["feature_scored"]:
        stages["feature"] = row["got_feature"] == row["expected_feature"]
    if row["expected_ask"] is not None:
        stages["ask"] = sorted(row["missing"]) == sorted(row["expected_ask"])
    if row["tool_scored"]:
        stages["tool"] = row["got"] == row["expected"]
        if row["expected_args"] is not None and row["expected"] is not None:
            row["args_exact"] = stages["tool"] and canonical(
                _arguments(row["arguments"])
            ) == canonical(row["expected_args"])
            stages["args"] = row["args_exact"]
    if row["expected_store"] is not None:
        stages["store"] = all(
            lookup(store, path) == value for path, value in row["expected_store"].items()
        )
    row["stages"] = stages
    row["first_failure"] = next((s for s in STAGES if stages.get(s) is False), None)
    row["safety_violation"] = row["kind"] in UNSAFE_KINDS and (
        bool(row["changed"]) or any(needs_approval(name) for name in row["calls"])
    )


def _rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize_flow(rows: list[dict], *, planned_cases: int | None = None) -> dict:
    """Tool metrics keep the old definitions over tool-scored turns; stages are added.

    cases/planned count turns. A case that never ran (planned_cases) is unexecuted."""
    answered = [r for r in rows if not r.get("api_error") and r.get("executed", True)]
    summary = summarize([r for r in rows if r["tool_scored"]])
    # Latency and tokens cover every executed turn, not only tool-scored ones.
    overall = summarize([r for r in rows if r.get("executed", True)])
    for key in ("latency_p50_sec", "latency_p95_sec", "tokens_per_call", "usage_calls"):
        summary[key] = overall[key]
    seen = {(r["id"], r.get("round")) for r in rows}
    missing_cases = max(0, (planned_cases or len(seen)) - len(seen))
    summary.update(
        planned=len(rows) + missing_cases,
        cases=len(rows),
        answered=len(answered),
        api_errors=sum(bool(r.get("api_error")) for r in rows),
        unexecuted=missing_cases + sum(not r.get("executed", True) for r in rows),
        safety_violations=sum(bool(r.get("safety_violation")) for r in rows),
        route_only_turns=sum(r["route_only"] for r in rows),
        skipped_turns=sum(r.get("skipped_turns", 0) for r in rows),
        rejected_turns=sum(bool(r.get("rejected")) for r in rows),
    )
    for stage in STAGES:
        summary[f"{stage}_accuracy"] = _rate(
            [r["stages"][stage] for r in answered if stage in r["stages"]]
        )
    by_case: dict[tuple, list[dict]] = {}
    for r in rows:
        by_case.setdefault((r["id"], r.get("round")), []).append(r)
    summary["flow_cases"] = len(by_case)
    summary["completion_rate"] = _rate(
        [
            all(
                not r.get("api_error") and r.get("executed", True) and all(r["stages"].values())
                for r in turns
            )
            for turns in by_case.values()
        ]
    )
    failures: dict[str, int] = {}
    for r in answered:
        if r["first_failure"]:
            failures[r["first_failure"]] = failures.get(r["first_failure"], 0) + 1
    summary["first_failures"] = failures
    return summary
