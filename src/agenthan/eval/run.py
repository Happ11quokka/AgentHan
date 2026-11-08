"""Run the two current evaluation paths and persist reproducible M0 summaries."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from agenthan.agent import place_matcher
from agenthan.agent.clova_chat import CHAT_MODEL
from agenthan.core.embedding import ClovaEmbeddingError
from agenthan.eval import tool_selection
from agenthan.eval.cases import Case, load_cases
from agenthan.eval.metrics import score_tool_row, summarize
from agenthan.eval.replay import EMBED_MODEL, Live, Recorder, Replay, canonical
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import TOOL_FEATURES, all_tool_schemas

ROOT = Path(__file__).resolve().parents[3]
TOOL_CASES = ROOT / "tests/data/tool_selection_cases.jsonl"
MATCH_CASES = ROOT / "tests/data/place_request_cases.jsonl"
REPLAY_PATH = ROOT / "tests/fixtures/replay/m0.json"
BASELINES = Path(__file__).with_name("baselines")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run_match(case: Case, backend) -> dict:
    tool = case.turns[0].expect.tool
    expected = case.expected_feature or TOOL_FEATURES.get(tool, tool)
    row = {
        "id": case.id,
        "lang": case.lang,
        "kind": case.kind,
        "expected": expected,
        "got": None,
        "calls": [],  # Feature matching invokes no tools.
        "safety_violation": False,
    }
    errors, attempted = [], []

    def embed(text):
        attempted.append(True)
        try:
            return backend.embed(text)
        except ClovaEmbeddingError as error:
            errors.append(str(error))
            raise

    started = time.monotonic()
    matches = place_matcher.match_features(case.turns[0].say, top_k=1, embed=embed)
    row["latency_sec"] = time.monotonic() - started
    row["got"] = matches[0].feature_id if matches else None
    row["keyword_only"] = bool(matches and matches[0].keyword_only)
    # match_features intentionally swallows embedding failures. Evaluation must not.
    if errors or not attempted:
        row["api_error"] = errors[0] if errors else "Embedding was not executed (input/index)"
    return row


def evaluate(case_sets: dict[str, list[Case]], backend, *, repeat: int = 1, pause: float = 0):
    rows = []
    tools = all_tool_schemas()
    for mode, cases in case_sets.items():
        for round_ in range(repeat):
            for case in cases:
                if mode == "all_tools":
                    first = case.turns[0]
                    single = {
                        **case.model_dump(),
                        "text": first.say,
                        "expected_tool": first.expect.tool,
                        "expected_args": first.expect.args,
                    }
                    row = tool_selection.run_case(single, backend.chat, tools)
                    score_tool_row(row, case.turns[0].expect.args)
                else:
                    row = run_match(case, backend)
                row.update(mode=mode, round=round_, split=case.split, origin=case.origin)
                rows.append(row)
                if pause:
                    time.sleep(pause)
    results = {
        mode: summarize([r for r in rows if r["mode"] == mode], planned=len(cases) * repeat)
        for mode, cases in case_sets.items()
    }
    by_split = {
        "null" if split is None else split: {
            mode: summarize([r for r in rows if r["mode"] == mode and r["split"] == split])
            for mode in case_sets
        }
        for split in (None, "dev", "holdout")
    }
    return rows, results, by_split


def render_table(summary: dict) -> str:
    columns = (
        "평가 | 선택 정확도 | 인자 정확 일치 | 인자 유효성 | 오발동률 | 안전 위반 | "
        "지연 p50/p95(초) | 토큰/호출 | API 오류 | 미실행"
    )
    lines = [f"| {columns} |", "|" + " --- |" * 10]

    def percent(value):
        return "N/A" if value is None else f"{value:.2%}"

    def number(value):
        return "N/A" if value is None else f"{value:.4f}"

    for mode, metrics in summary["results"].items():
        lines.append(
            f"| {mode} | {percent(metrics['selection_accuracy'])} | "
            f"{percent(metrics['args_accuracy'])} | {percent(metrics['args_valid_rate'])} | "
            f"{percent(metrics['false_call_rate'])} | {metrics['safety_violations']} | "
            f"{number(metrics['latency_p50_sec'])}/{number(metrics['latency_p95_sec'])} | "
            f"{number(metrics['tokens_per_call'])} | {metrics['api_errors']} | "
            f"{metrics['unexecuted']} |"
        )
    lines.append("| 기능 선택 후 도구 노출 | 미구현 | — | — | — | — | — | — | — | — |")
    for external in summary.get("extra_summaries", []):
        # Keep unknown external metric keys verbatim, without guessing their semantics.
        payload = json.dumps(external["summary"], ensure_ascii=False, sort_keys=True)
        payload = payload.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| 리뷰(외부 결과) | {payload} | — | — | — | — | — | — | — | — |")
    return "\n".join(lines) + "\n"


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["live", "replay"], default="live")
    parser.add_argument("--cases", type=Path, help="common schema JSONL for both evaluation paths")
    parser.add_argument("--match-cases", type=Path, default=MATCH_CASES)
    parser.add_argument("--split", choices=["dev", "holdout"])
    parser.add_argument("--only", help="comma-separated IDs (union of both evaluation paths)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--pause", type=float, default=0.0)
    parser.add_argument("--model", default=CHAT_MODEL)
    parser.add_argument("--label", default="m0")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "eval_results")
    parser.add_argument("--replay-file", type=Path, default=REPLAY_PATH)
    parser.add_argument(
        "--record", type=Path, help="record live calls (use only public dummy cases)"
    )
    parser.add_argument(
        "--save-baseline", help="copy only summary.json into eval/baselines/<name>.json"
    )
    parser.add_argument("--extra-summary", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.pause < 0:
        parser.error("repeat must be positive and pause nonnegative")
    if not re.fullmatch(r"[\w-]+", args.label):
        parser.error("label must contain only letters, numbers, underscores, or hyphens")
    if args.save_baseline and not re.fullmatch(r"[\w-]+", args.save_baseline):
        parser.error("baseline name must contain only letters, numbers, underscores, or hyphens")
    if args.record and args.backend != "live":
        parser.error("--record requires --backend live")
    paths = {"all_tools": args.cases or TOOL_CASES, "match": args.cases or args.match_cases}
    case_sets = {mode: load_cases(path, split=args.split) for mode, path in paths.items()}
    if args.only:
        wanted = set(args.only.split(","))
        available = {case.id for cases in case_sets.values() for case in cases}
        if wanted - available:
            parser.error("Unknown or filtered case IDs: " + ", ".join(sorted(wanted - available)))
        case_sets = {
            mode: [case for case in cases if case.id in wanted] for mode, cases in case_sets.items()
        }
    if not any(case_sets.values()):
        parser.error("No cases selected")
    backend = Replay(args.replay_file, args.model) if args.backend == "replay" else Live(args.model)
    if args.record:
        backend = Recorder(args.record, backend)
    rows, results, by_split = evaluate(case_sets, backend, repeat=args.repeat, pause=args.pause)
    with tempfile.TemporaryDirectory() as folder:
        system = tool_selection.SYSTEM + json.dumps(
            DummyStore(Path(folder) / "store.json").read(), ensure_ascii=False
        )
    now = datetime.now(UTC)
    summary = {
        "version": 1,
        "conditions": {
            "git_commit": _git("rev-parse", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=normal")),
            "date": now.isoformat(),
            "model_id": args.model,
            "embedding_model_id": EMBED_MODEL,
            "system_prompt_sha256": sha256(system.encode()),
            "case_set_sha256": sha256(
                canonical({k: [c.model_dump() for c in v] for k, v in case_sets.items()}).encode()
            ),
            "case_sets": {
                mode: {
                    "path": str(path),
                    "file_sha256": sha256(path.read_bytes()),
                    "selected_ids": [c.id for c in case_sets[mode]],
                }
                for mode, path in paths.items()
            },
            "tool_schemas_sha256": sha256(canonical(all_tool_schemas()).encode()),
            "catalog_sha256": place_matcher.catalog_sha256(),
            "embedding_index_sha256": (
                sha256(place_matcher.INDEX_PATH.read_bytes())
                if place_matcher.INDEX_PATH.is_file()
                else None
            ),
            "repeat": args.repeat,
            "backend": args.backend,
            "split": args.split,
            "pause_sec": args.pause,
        },
        "results": results,
        "by_split": by_split,
        "extra_summaries": [
            {"path": str(path), "summary": json.loads(path.read_text(encoding="utf-8"))}
            for path in args.extra_summary
        ],
    }
    if args.backend == "replay":
        summary["conditions"]["replay_sha256"] = sha256(args.replay_file.read_bytes())
        summary["conditions"]["replay_provenance"] = backend.data.get("provenance", "unknown")
    summary["status"] = (
        "INCOMPLETE"
        if any(m["api_errors"] or m["unexecuted"] for m in results.values())
        else "COMPLETE"
    )
    directory = args.out_dir / f"{now:%Y%m%d-%H%M%S}-{args.label}"
    # Never silently overwrite another run started within the same second.
    if directory.exists():
        directory = args.out_dir / f"{now:%Y%m%d-%H%M%S}-{args.label}-{now:%f}"
    directory.mkdir(parents=True, exist_ok=False)
    payload = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (directory / "summary.json").write_text(payload, encoding="utf-8")
    (directory / "rows.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    table = render_table(summary)
    (directory / "table.md").write_text(table, encoding="utf-8")
    if args.save_baseline:
        BASELINES.mkdir(parents=True, exist_ok=True)
        (BASELINES / f"{args.save_baseline}.json").write_text(payload, encoding="utf-8")
    print(table)
    print(f"{summary['status']}: {directory / 'summary.json'}")
    return 2 if summary["status"] == "INCOMPLETE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
