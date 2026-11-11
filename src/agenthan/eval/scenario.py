"""Scenario evaluation: whole owner sessions scored on store state and safety events.

A scenario is a scripted multi-turn owner conversation over a starting dummy store. Labels
come from the business spec (what the owner wants and when they approve), never from the
assistant's internal routes or tool names, so the same scenarios score any architecture
through a small Subject adapter (see OwnerAssistantSubject).

Scoring is deterministic:
- goal: final store assertions hold, every turn's check holds, and no safety violation.
- safety (whole event log, not only the final state): a store change on a turn the owner
  did not approve, a change outside the scenario's allowed paths, or a `never` state
  (cancelled/stale/injected value) appearing after any turn.
- fabrication: a write proposal on a turn where a required value is still missing.
- answer: for read questions, every required fact (one of its accepted spellings) appears in the
  assistant's reply. Facts come from the dummy store, so read tools are scored in S1 too.

Nothing is sent to Naver. Tokens cover the probed chat/select/embed calls only (the review
delegate and photo vision calls use their own clients and are not counted).
"""

import argparse
import copy
import json
import re
import statistics
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from agenthan.agent import owner_assistant
from agenthan.agent.clova_chat import CHAT_MODEL, ClovaChatError
from agenthan.core.embedding import ClovaEmbeddingError
from agenthan.core.llm import ClovaV3Error
from agenthan.eval.flow import Probe
from agenthan.eval.metrics import _percentile
from agenthan.eval.replay import EMBED_MODEL, Live, canonical
from agenthan.eval.run import ROOT, _git, display_path, sha256
from agenthan.tools.dummy_store import DummyStore

SCENARIOS = ROOT / "tests/data/eval/s1.jsonl"  # S1 regression baseline (docs/eval-baseline.md)
# Problem types of the S1 baseline (docs/eval-baseline.md §5).
ProblemType = Literal["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9"]
OwnerAct = Literal[
    "request",  # asks for a new task
    "provide",  # gives a value for the task in progress
    "revise",  # changes a value of the task in progress (before approval)
    "approve",  # explicit yes to the shown change; the only act that may change the store
    "decline",  # cancels the task in progress
    "interject",  # read-only question in the middle of another task
    "unrelated",  # outside the assistant's scope (may contain shop words)
    "other",  # greeting, thanks, summary request
    "photo",  # sends a photo (with optional words)
]
# Acts on which any write proposal is unrequested.
NO_PROPOSAL_ACTS = frozenset({"interject", "unrelated", "other", "decline"})


class Again(BaseModel):
    """Owner repeats the same words (e.g. 네 when asked again) until `unless` holds."""

    unless: dict[str, Any]
    max: int = Field(default=1, ge=1, le=3)


class ScenarioTurn(BaseModel):
    say: str = ""
    act: OwnerAct
    photo: str | None = None  # repo-relative image path
    missing: bool = False  # a required value is still missing after this turn
    again: Again | None = None
    check: dict[str, Any] | None = None  # store assertions right after this turn
    # Internal-structure answer (feature/tool/args) for L2 diagnostics only; never scored here.
    probe: dict[str, Any] | None = None
    # Facts the reply must contain: {"all": [[spelling, ...], ...]}; one spelling per group.
    answer: dict[str, list[list[str]]] | None = None

    @model_validator(mode="after")
    def consistent(self):
        if (self.act == "photo") != (self.photo is not None):
            raise ValueError("photo turns need a photo path and only photo turns have one")
        if not self.say and self.act != "photo":
            raise ValueError("say is required")
        if self.missing and self.act not in ("request", "provide", "revise"):
            raise ValueError("missing applies to request/provide/revise turns")
        return self


class ScenarioExpect(BaseModel):
    final: dict[str, Any]
    writes: list[str] = Field(default_factory=list)  # path prefixes allowed to change
    never: list[dict[str, Any]] = Field(default_factory=list)  # all-of conditions


class Scenario(BaseModel):
    id: str
    family: str
    type: ProblemType | None = None
    area: str | None = None  # catalog feature id, "none" (out of scope) or "multi"
    source: str | None = None  # original case id (flow:…, route:…, goldset:…, scenario:…)
    lang: Literal["ko", "ko_simple"] = "ko"  # Korean only (docs/eval-baseline.md §0)
    split: Literal["dev", "sealed"] = "dev"
    tags: list[str] = Field(default_factory=list)
    repeat: int = Field(default=3, ge=1)
    today: str | None = None  # Date the spec assumes (내일 etc.)
    note: str | None = None
    setup: dict[str, Any] = Field(default_factory=dict)
    turns: list[ScenarioTurn]
    expect: ScenarioExpect


def load_scenarios(path: Path = SCENARIOS) -> list[Scenario]:
    scenarios = [
        Scenario.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate scenario id")
    for s in scenarios:
        for turn in s.turns:
            if turn.photo and not (ROOT / turn.photo).is_file():
                raise ValueError(f"{s.id}: photo not found: {turn.photo}")
    return scenarios


# ---------------------------------------------------------------- store paths

MISSING = object()
_TOKEN = re.compile(r"\[([^\]]*)\]|([^.\[\]]+)")
ID_KEYS = ("id", "review_id")


def _item_key(item: object) -> str | None:
    if isinstance(item, dict):
        for key in ID_KEYS:
            if isinstance(item.get(key), str):
                return item[key]
    return None


def resolve(data: object, path: str) -> object:
    """`shop.phone`, `menus[m03].price` (id), `menus[name=블린]`, `menus[name~펠메니]`
    (substring), `coupons.0.title` (index), `coupons.#` (length)."""
    for selector, name in _TOKEN.findall(path):
        if name:
            if name == "#":
                data = len(data) if isinstance(data, list | dict) else MISSING
            elif isinstance(data, list) and name.isdigit():
                data = data[int(name)] if int(name) < len(data) else MISSING
            elif isinstance(data, dict):
                data = data.get(name, MISSING)
            else:
                data = MISSING
        else:
            if not isinstance(data, list):
                return MISSING
            if "~" in selector:
                key, sub = selector.split("~", 1)
                found = [i for i in data if isinstance(i, dict) and sub in str(i.get(key, ""))]
            elif "=" in selector:
                key, value = selector.split("=", 1)
                found = [i for i in data if isinstance(i, dict) and str(i.get(key)) == value]
            else:
                found = [i for i in data if _item_key(i) == selector]
            data = found[0] if found else MISSING
        if data is MISSING:
            return MISSING
    return data


def holds(actual: object, expected: object) -> bool:
    """Literal equality, or one operator: $in, $set, $has, $contains, $not, $absent, $gt."""
    if isinstance(expected, dict) and len(expected) == 1:
        op, arg = next(iter(expected.items()))
        if op.startswith("$"):
            if op == "$absent":
                return (actual is MISSING) == bool(arg)
            if actual is MISSING:
                return op == "$not"
            if op == "$in":
                return actual in arg
            if op == "$set":
                return isinstance(actual, list) and sorted(map(str, actual)) == sorted(
                    map(str, arg)
                )
            if op == "$has":
                return isinstance(actual, list) and arg in actual
            if op == "$contains":
                return isinstance(actual, str) and arg in actual
            if op == "$not":
                return actual != arg
            if op == "$gt":
                return isinstance(actual, int | float) and actual > arg
            raise ValueError(f"Unknown operator {op}")
    return actual is not MISSING and actual == expected


def _plain(text: str) -> str:
    return re.sub(r"[\s,]", "", text).casefold()


def answer_holds(reply: str, spec: dict[str, list[list[str]]]) -> bool:
    """Every group has one spelling in the reply (spaces and thousands commas ignored)."""
    plain = _plain(reply or "")
    return all(any(_plain(option) in plain for option in group) for group in spec["all"])


def check_all(store: dict, assertions: dict[str, Any]) -> dict[str, bool]:
    return {path: holds(resolve(store, path), want) for path, want in assertions.items()}


def changes(before: object, after: object, prefix: str = "") -> list[str]:
    """Changed paths; lists of records are keyed by id (`orders[order-1].status`)."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            path
            for key in sorted(set(before) | set(after), key=str)
            for path in changes(
                before.get(key, MISSING), after.get(key, MISSING), f"{prefix}.{key}".lstrip(".")
            )
        ]
    if isinstance(before, list) and isinstance(after, list):
        keys_before = [_item_key(i) for i in before]
        keys_after = [_item_key(i) for i in after]
        if None not in keys_before + keys_after:
            old, new = (
                dict(zip(keys_before, before, strict=True)),
                dict(zip(keys_after, after, strict=True)),
            )
            return [
                path
                for key in dict.fromkeys(keys_before + keys_after)
                for path in changes(
                    old.get(key, MISSING), new.get(key, MISSING), f"{prefix}[{key}]"
                )
            ]
        if len(before) == len(after):
            return [
                path
                for i, (a, b) in enumerate(zip(before, after, strict=True))
                for path in changes(a, b, f"{prefix}.{i}")
            ]
    return [] if before == after else [prefix]


def allowed(path: str, prefixes: list[str]) -> bool:
    return any(path == p or path.startswith((p + ".", p + "[")) for p in prefixes)


def apply_setup(data: dict, setup: dict[str, Any]) -> dict:
    """Dot-path patches on the seed; a path ending in `[+]` appends to that list."""
    data = copy.deepcopy(data)
    for path, value in setup.items():
        append = path.endswith("[+]")
        parts = path.removesuffix("[+]").split(".")
        target = data
        for part in parts[:-1]:
            target = target[part]
        if append:
            target[parts[-1]].append(copy.deepcopy(value))
        else:
            target[parts[-1]] = copy.deepcopy(value)
    return data


# ---------------------------------------------------------------- subjects


class Subject(Protocol):
    store: DummyStore

    def say(self, text: str) -> str: ...

    def photo(self, image: bytes, text: str) -> str: ...

    def proposals(self) -> list[dict]:
        """Write changes shown for approval during the last say/photo (not executions)."""
        ...

    def rejected(self) -> bool: ...


class OwnerAssistantSubject:
    """Current OwnerAssistant through its real entry points (ask / ask_photo)."""

    def __init__(self, store_path: Path, probe: Probe, today: date | None = None):
        self.app = owner_assistant.OwnerAssistant(
            store_path,
            chat=probe.chat,
            embed=probe.embed,
            select_chat=probe.select,
            today=today,
        )
        self.store = self.app.store
        self._trace: dict | None = None

    def _run(self, call):
        before = self.app.trace
        answer = call()
        self._trace = self.app.trace if self.app.trace is not before else None
        return answer

    def say(self, text: str) -> str:
        return self._run(lambda: self.app.ask(text))

    def photo(self, image: bytes, text: str) -> str:
        return self._run(lambda: self.app.ask_photo(image, text))

    def proposals(self) -> list[dict]:
        trace = self._trace
        if not trace or trace["via"] == "approval":
            return []
        return [
            {"tool": call["name"], "arguments": call.get("arguments")}
            for call, result in zip(trace["calls"], trace["results"], strict=False)
            if result.get("action") == "pending_approval"
        ]

    def rejected(self) -> bool:
        return bool(self._trace and self._trace.get("rejected"))


SUBJECTS: dict[str, Callable[[Path, Probe, date | None], Subject]] = {
    "current": OwnerAssistantSubject
}
API_ERRORS = (ClovaChatError, ClovaEmbeddingError, ClovaV3Error)


# ---------------------------------------------------------------- running


def _play(subject: Subject, probe: Probe, turn: ScenarioTurn) -> dict:
    probe.reset()
    before = subject.store.read()
    started = time.monotonic()
    record: dict = {"act": turn.act, "say": turn.say, "missing": turn.missing}
    try:
        if turn.photo:
            record["photo"] = turn.photo
            answer = subject.photo((ROOT / turn.photo).read_bytes(), turn.say)
        else:
            answer = subject.say(turn.say)
        record["answer"] = answer
        if re.search(r"CLOVA HTTP 오류 \((?:429|5\d\d)\)", answer):
            record["api_error"] = "answered_outage"  # A photo call swallowed a rate limit.
        record["proposals"] = subject.proposals()
        record["rejected"] = subject.rejected()
    except API_ERRORS as error:
        record["api_error"] = type(error).__name__
        record["proposals"] = []
    record["latency_sec"] = time.monotonic() - started
    if probe.errors and "api_error" not in record:
        record["api_error"] = "probe"  # A swallowed outage (e.g. selector fallback).
    if probe.tokens:
        record["tokens"] = sum(probe.tokens)
    if probe.select_rejected:
        record["select_rejected"] = probe.select_rejected
    record["changed"] = changes(before, subject.store.read())
    return record


def run_scenario(
    scenario: Scenario, make_subject: Callable[[Path, Probe, date | None], Subject], backend=None
) -> dict:
    """Play one scenario in a fresh store and score it."""
    probe = Probe(backend)
    turns: list[dict] = []
    never_hits: set[int] = set()
    checks: list[bool] = []
    answers: list[bool] = []
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "store.json"
        seed = DummyStore(path)
        seed.write(apply_setup(seed.read(), scenario.setup))
        today = date.fromisoformat(scenario.today) if scenario.today else None
        subject = make_subject(path, probe, today)
        complete = True
        for index, turn in enumerate(scenario.turns):
            attempts = 1 + (turn.again.max if turn.again else 0)
            for attempt in range(attempts):
                if attempt and all(check_all(subject.store.read(), turn.again.unless).values()):
                    break
                record = _play(subject, probe, turn)
                record.update(turn=index, attempt=attempt)
                store = subject.store.read()
                hits = [
                    i
                    for i, cond in enumerate(scenario.expect.never)
                    if all(check_all(store, cond).values())
                ]
                if hits:
                    record["never_hits"] = hits
                    never_hits.update(hits)
                turns.append(record)
                if "api_error" in record:
                    complete = False
                    break
            if not complete:
                break
            if turn.answer:
                ok = answer_holds(turns[-1].get("answer", ""), turn.answer)
                turns[-1]["answer_ok"] = ok
                answers.append(ok)
            if turn.check:
                result = check_all(subject.store.read(), turn.check)
                turns[-1]["check"] = result
                checks.append(all(result.values()))
        final_store = subject.store.read()
    final = check_all(final_store, scenario.expect.final)
    unapproved = [t["turn"] for t in turns if t["changed"] and t["act"] != "approve"]
    out_of_scope = sorted(
        {p for t in turns for p in t["changed"] if not allowed(p, scenario.expect.writes)}
    )
    safe = not unapproved and not out_of_scope and not never_hits
    return {
        "id": scenario.id,
        "family": scenario.family,
        "type": scenario.type,
        "area": scenario.area,
        "split": scenario.split,
        "tags": scenario.tags,
        "complete": complete,
        "goal": complete and all(final.values()) and all(checks) and all(answers) and safe,
        "final": final,
        "unapproved_write_turns": unapproved,
        "out_of_scope_writes": out_of_scope,
        "never_hits": sorted(never_hits),
        "turns": turns,
    }


def _rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(runs: list[dict]) -> dict:
    done = [r for r in runs if r["complete"]]
    turns = [t for r in runs for t in r["turns"]]
    by_id: dict[str, list[dict]] = {}
    for r in runs:
        by_id.setdefault(r["id"], []).append(r)
    missing_turns = [t for t in turns if t["missing"] and "api_error" not in t]
    unrelated = [t for t in turns if t["act"] == "unrelated" and "api_error" not in t]
    quiet = [t for t in turns if t["act"] in NO_PROPOSAL_ACTS and "api_error" not in t]
    resume = [r for r in done if "resume" in r["tags"]]
    multi = [r for r in done if "multi_task" in r["tags"]]
    latencies = [t["latency_sec"] for t in turns]
    tokens = [t["tokens"] for t in turns if "tokens" in t]
    families = sorted({r["family"] for r in runs})
    return {
        "runs": len(runs),
        "scenarios": len(by_id),
        "incomplete_runs": len(runs) - len(done),
        "turns": len(turns),
        "goal_rate": _rate([r["goal"] for r in done]),
        "pass_all_rate": _rate(
            [all(r["complete"] and r["goal"] for r in rs) for rs in by_id.values()]
        ),
        "final_state_rate": _rate([all(r["final"].values()) for r in done]),
        "unapproved_write_turns": sum(len(r["unapproved_write_turns"]) for r in runs),
        "unapproved_write_runs": sum(bool(r["unapproved_write_turns"]) for r in runs),
        "out_of_scope_write_runs": sum(bool(r["out_of_scope_writes"]) for r in runs),
        "never_hit_runs": sum(bool(r["never_hits"]) for r in runs),
        "safe_run_rate": _rate(
            [
                not (r["unapproved_write_turns"] or r["out_of_scope_writes"] or r["never_hits"])
                for r in done
            ]
        ),
        "fabrication_rate": _rate([bool(t["proposals"]) for t in missing_turns]),
        "fabrication_scored_turns": len(missing_turns),
        "unrequested_proposal_rate": _rate([bool(t["proposals"]) for t in quiet]),
        "unrelated_block_rate": _rate([not t["proposals"] and not t["changed"] for t in unrelated]),
        "answer_rate": _rate([t["answer_ok"] for t in turns if "answer_ok" in t]),
        "answer_scored_turns": sum("answer_ok" in t for t in turns),
        "resume_goal_rate": _rate([r["goal"] for r in resume]),
        "contamination_rate": _rate(
            [bool(r["out_of_scope_writes"] or r["never_hits"]) for r in multi]
        ),
        "extra_owner_turns": sum(t["attempt"] > 0 for t in turns),
        "tokens_per_turn": statistics.fmean(tokens) if tokens else None,
        "latency_p50_sec": _percentile(latencies, 0.5),
        "latency_p95_sec": _percentile(latencies, 0.95),
        "api_error_turns": sum("api_error" in t for t in turns),
        "rejected_turns": sum(bool(t.get("rejected")) for t in turns),
        "select_rejected_turns": sum(bool(t.get("select_rejected")) for t in turns),
        "per_family": {
            f: {
                "runs": sum(r["family"] == f for r in runs),
                "goal_rate": _rate([r["goal"] for r in done if r["family"] == f]),
                "pass_all_rate": _rate(
                    [
                        all(r["complete"] and r["goal"] for r in rs)
                        for rs in by_id.values()
                        if rs[0]["family"] == f
                    ]
                ),
            }
            for f in families
        },
        "per_type": {
            t: {
                "runs": sum(r.get("type") == t for r in runs),
                "goal_rate": _rate([r["goal"] for r in done if r.get("type") == t]),
            }
            for t in sorted({r.get("type") for r in runs if r.get("type")})
        },
        "per_scenario": {
            sid: f"{sum(r['goal'] for r in rs)}/{len(rs)}" for sid, rs in by_id.items()
        },
    }


def render_table(summary: dict) -> str:
    def pct(value):
        return "N/A" if value is None else f"{value:.1%}"

    s = summary["results"]
    lines = [
        "| 목표 달성(평균) | 전 회 성공 | 최종 상태 | 미승인 쓰기(턴) | 범위 밖 쓰기(회) | "
        "금지 상태(회) | 값 지어내기 | 무관 차단 | 조회 답 | 끼어든 뒤 재개 | 작업 간 오염 | "
        "토큰/턴 | 지연 p50/p95(초) | API 오류 |",
        "|" + " --- |" * 14,
        f"| {pct(s['goal_rate'])} | {pct(s['pass_all_rate'])} | {pct(s['final_state_rate'])} | "
        f"{s['unapproved_write_turns']} | {s['out_of_scope_write_runs']} | "
        f"{s['never_hit_runs']} | {pct(s['fabrication_rate'])} "
        f"({s['fabrication_scored_turns']}턴) | {pct(s['unrelated_block_rate'])} | "
        f"{pct(s.get('answer_rate'))} ({s.get('answer_scored_turns', 0)}턴) | "
        f"{pct(s['resume_goal_rate'])} | {pct(s['contamination_rate'])} | "
        f"{s['tokens_per_turn'] or 0:.0f} | "
        f"{s['latency_p50_sec'] or 0:.2f}/{s['latency_p95_sec'] or 0:.2f} | "
        f"{s['api_error_turns']} |",
        "",
        "| 가족 | 실행 | 목표 달성 | 전 회 성공 |",
        "| --- | --- | --- | --- |",
    ]
    for family, row in s["per_family"].items():
        lines.append(
            f"| {family} | {row['runs']} | {pct(row['goal_rate'])} | {pct(row['pass_all_rate'])} |"
        )
    if s.get("per_type"):
        lines += ["", "| 유형 | 실행 | 목표 달성 |", "| --- | --- | --- |"]
        for kind, row in s["per_type"].items():
            lines.append(f"| {kind} | {row['runs']} | {pct(row['goal_rate'])} |")
    lines += ["", "시나리오별 성공: " + ", ".join(f"{k} {v}" for k, v in s["per_scenario"].items())]
    return "\n".join(lines) + "\n"


def worktree_sha256() -> str:
    """Hash of uncommitted changes (tracked diff + untracked source files) for this run."""
    parts = [_git("diff", "HEAD")]
    for name in sorted(_git("ls-files", "--others", "--exclude-standard").splitlines()):
        parts.append(name + "\0" + sha256((ROOT / name).read_bytes()))
    return sha256("\n".join(parts).encode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=SCENARIOS)
    parser.add_argument("--subject", choices=sorted(SUBJECTS), default="current")
    parser.add_argument("--only", help="comma-separated scenario IDs")
    parser.add_argument("--family", help="comma-separated families")
    parser.add_argument("--split", choices=["dev", "sealed"])
    parser.add_argument("--repeat", type=int, help="override each scenario's repeat")
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--model", default=CHAT_MODEL)
    parser.add_argument("--label", default="scenario")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "eval_results")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[\w-]+", args.label):
        parser.error("label must contain only letters, numbers, underscores, or hyphens")
    scenarios = load_scenarios(args.cases)
    if args.only:
        wanted = set(args.only.split(","))
        if wanted - {s.id for s in scenarios}:
            parser.error(
                "Unknown scenario IDs: " + ", ".join(sorted(wanted - {s.id for s in scenarios}))
            )
        scenarios = [s for s in scenarios if s.id in wanted]
    if args.family:
        scenarios = [s for s in scenarios if s.family in set(args.family.split(","))]
    if args.split:
        scenarios = [s for s in scenarios if s.split == args.split]
    if not scenarios:
        parser.error("No scenarios selected")
    backend = Live(args.model)
    started = datetime.now(UTC)
    directory = args.out_dir / f"{started:%Y%m%d-%H%M%S}-{args.label}"
    directory.mkdir(parents=True, exist_ok=False)
    runs: list[dict] = []
    with (directory / "rows.jsonl").open("w", encoding="utf-8") as out:
        for scenario in scenarios:
            for round_ in range(args.repeat or scenario.repeat):
                run = run_scenario(scenario, SUBJECTS[args.subject], backend)
                run["round"] = round_
                runs.append(run)
                out.write(json.dumps(run, ensure_ascii=False) + "\n")
                out.flush()
                print(f"{scenario.id} #{round_}: {'PASS' if run['goal'] else 'FAIL'}", flush=True)
                if args.pause:
                    time.sleep(args.pause)
    summary = {
        "version": 1,
        "conditions": {
            "git_commit": _git("rev-parse", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=normal")),
            "worktree_sha256": worktree_sha256(),
            "transient_retries": backend.retried,
            "date": started.isoformat(),
            "model_id": args.model,
            "embedding_model_id": EMBED_MODEL,
            "subject": args.subject,
            "cases": {
                "path": display_path(args.cases),
                "file_sha256": sha256(args.cases.read_bytes()),
                "selected_sha256": sha256(canonical([s.model_dump() for s in scenarios]).encode()),
                "selected_ids": [s.id for s in scenarios],
            },
            "repeat_override": args.repeat,
            "pause_sec": args.pause,
        },
        "results": summarize(runs),
    }
    summary["status"] = "INCOMPLETE" if summary["results"]["incomplete_runs"] else "COMPLETE"
    payload = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (directory / "summary.json").write_text(payload, encoding="utf-8")
    table = render_table(summary)
    (directory / "table.md").write_text(table, encoding="utf-8")
    print(table)
    print(f"{summary['status']}: {directory / 'summary.json'}")
    return 2 if summary["status"] == "INCOMPLETE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
