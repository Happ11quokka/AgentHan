"""Run route and flow evaluation over OwnerAssistant and persist reproducible summaries."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from agenthan.agent import owner_assistant, place_matcher
from agenthan.agent.clova_chat import CHAT_MODEL
from agenthan.eval.cases import Case, load_cases
from agenthan.eval.flow import run_flow, run_route, summarize_flow, summarize_route
from agenthan.eval.replay import EMBED_MODEL, Live, Recorder, Replay, canonical
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import all_tool_schemas

ROOT = Path(__file__).resolve().parents[3]
# One file per evaluation path. Each case carries its own split (dev/holdout) and source
# (m0, aug:…, aug2:…, review:근형); see tests/data/eval/README.md.
CASES_DIR = ROOT / "tests/data/eval"
FLOW_CASES = CASES_DIR / "flow.jsonl"
ROUTE_CASES = CASES_DIR / "route.jsonl"
DEFAULT_PATHS = {"route": [ROUTE_CASES], "flow": [FLOW_CASES]}
REPLAY_PATH = ROOT / "tests/fixtures/replay/m0.json"
# Optional id → split for --cases files without inline splits (the default files need none).
SPLITS_PATH = CASES_DIR / "splits.json"
MODES = ("route", "flow")


def load_splits(path: Path = SPLITS_PATH) -> dict[str, str]:
    """Fixed id → dev/holdout assignment (see cases.make_splits)."""
    return json.loads(path.read_text(encoding="utf-8"))["splits"] if path.is_file() else {}


def load_case_files(
    paths: list[Path], *, split: str | None = None, splits: dict[str, str] | None = None
) -> list[Case]:
    """Concatenate case files in order; IDs must stay unique across them."""
    cases = [c for path in paths for c in load_cases(path, split=split, splits=splits)]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case id across case files")
    return cases


def display_path(path: Path) -> str:
    """Repo-relative path for summaries, so saved baselines never carry a local home path."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


BASELINES = Path(__file__).with_name("baselines")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def evaluate(case_sets: dict[str, list[Case]], backend, *, repeat: int = 1, pause: float = 0):
    rows = []
    for mode, cases in case_sets.items():
        for round_ in range(repeat):
            for case in cases:
                produced = (
                    [run_route(case, backend)] if mode == "route" else run_flow(case, backend)
                )
                for row in produced:
                    row.update(mode=mode, round=round_, split=case.split, origin=case.origin)
                rows.extend(produced)
                if pause:
                    time.sleep(pause)

    def summary(mode, selected, planned=None):
        if mode == "route":
            return summarize_route(selected, planned=planned)
        return summarize_flow(selected, planned_cases=planned)

    results = {
        mode: summary(mode, [r for r in rows if r["mode"] == mode], len(cases) * repeat)
        for mode, cases in case_sets.items()
    }
    by_split = {
        "null" if split is None else split: {
            mode: summary(mode, [r for r in rows if r["mode"] == mode and r["split"] == split])
            for mode in case_sets
        }
        for split in (None, "dev", "holdout")
    }
    return rows, results, by_split


def render_table(summary: dict) -> str:
    def percent(value):
        return "N/A" if value is None else f"{value:.2%}"

    def number(value):
        return "N/A" if value is None else f"{value:.4f}"

    lines = []
    route = summary["results"].get("route")
    if route:
        lines += [
            "| 기능 매칭 | 정확도 | 후보 재현율@5 | 후보 안 선택 정확도 | 오분기율 | "
            "지연 p50/p95(초) | API 오류 | 미실행 |",
            "|" + " --- |" * 8,
            f"| route | {percent(route['selection_accuracy'])} | "
            f"{percent(route.get('candidate_recall'))} | "
            f"{percent(route.get('select_accuracy_when_recalled'))} | "
            f"{percent(route['false_call_rate'])} | "
            f"{number(route['latency_p50_sec'])}/{number(route['latency_p95_sec'])} | "
            f"{route['api_errors']} | {route['unexecuted']} |",
            "",
            "분기 경로: " + ", ".join(f"{k} {v}" for k, v in route.get("via", {}).items()),
            "",
        ]
    flow = summary["results"].get("flow")
    if flow:
        lines += [
            "| 전체 흐름 | 분기 | 질문 | 도구 | 인자 일치 | 저장소 | 완주율 | 오발동률 | "
            "안전 위반 | 지연 p50/p95(초) | 토큰/턴 | API 오류 | 미실행 |",
            "|" + " --- |" * 13,
            f"| flow | {percent(flow['feature_accuracy'])} | {percent(flow['ask_accuracy'])} | "
            f"{percent(flow['selection_accuracy'])} | {percent(flow['args_accuracy'])} | "
            f"{percent(flow['store_accuracy'])} | {percent(flow['completion_rate'])} | "
            f"{percent(flow['false_call_rate'])} | {flow['safety_violations']} | "
            f"{number(flow['latency_p50_sec'])}/{number(flow['latency_p95_sec'])} | "
            f"{number(flow['tokens_per_call'])} | {flow['api_errors']} | {flow['unexecuted']} |",
            "",
            "처음 실패한 단계: "
            + (", ".join(f"{k} {v}" for k, v in sorted(flow["first_failures"].items())) or "없음"),
            "",
        ]
    for external in summary.get("extra_summaries", []):
        # Keep unknown external metric keys verbatim, without guessing their semantics.
        payload = json.dumps(external["summary"], ensure_ascii=False, sort_keys=True)
        payload = payload.replace("|", "\\|").replace("\n", " ")
        lines.append(f"리뷰(외부 결과): {payload}")
    return "\n".join(lines).rstrip() + "\n"


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["live", "replay"], default="live")
    parser.add_argument("--cases", type=Path, help="common schema JSONL for both route and flow")
    parser.add_argument("--route-cases", type=Path, nargs="+", default=DEFAULT_PATHS["route"])
    parser.add_argument("--flow-cases", type=Path, nargs="+", default=DEFAULT_PATHS["flow"])
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
    paths = {
        "route": [args.cases] if args.cases else args.route_cases,
        "flow": [args.cases] if args.cases else args.flow_cases,
    }
    splits = load_splits()
    case_sets = {
        mode: load_case_files(files, split=args.split, splits=splits)
        for mode, files in paths.items()
    }
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
    source = Replay(args.replay_file, args.model) if args.backend == "replay" else Live(args.model)
    backend = source
    if args.record:
        backend = Recorder(args.record, backend)
    rows, results, by_split = evaluate(case_sets, backend, repeat=args.repeat, pause=args.pause)
    with tempfile.TemporaryDirectory() as folder:
        system = owner_assistant.prompt_fingerprint(DummyStore(Path(folder) / "store.json").read())
    now = datetime.now(UTC)
    summary = {
        "version": 2,
        "conditions": {
            "git_commit": _git("rev-parse", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=normal")),
            # Transient live failures absorbed by Live's retry (0 for replay).
            "transient_retries": getattr(source, "retried", 0),
            "date": now.isoformat(),
            "model_id": args.model,
            "embedding_model_id": EMBED_MODEL,
            "system_prompt_sha256": sha256(system.encode()),
            "case_set_sha256": sha256(
                canonical({k: [c.model_dump() for c in v] for k, v in case_sets.items()}).encode()
            ),
            "case_sets": {
                mode: {
                    "files": [
                        {"path": display_path(p), "file_sha256": sha256(p.read_bytes())}
                        for p in files
                    ],
                    "selected_ids": [c.id for c in case_sets[mode]],
                }
                for mode, files in paths.items()
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
            {"path": display_path(path), "summary": json.loads(path.read_text(encoding="utf-8"))}
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
