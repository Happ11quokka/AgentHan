import json
import os
import socket
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from agenthan.agent.clova_chat import CHAT_MODEL, ClovaChatError
from agenthan.core.embedding import ClovaEmbeddingError
from agenthan.eval import run
from agenthan.eval.cases import Case, Expect, Turn, load_cases
from agenthan.eval.metrics import summarize
from agenthan.eval.replay import Live, Recorder, Replay, ReplayMiss, request_hash
from agenthan.tools.dummy_store import DummyStore


def call(tool="get_statistics", arguments=None):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "dummy-call",
                "function": {"name": tool, "arguments": arguments or {"period": "30d"}},
            }
        ],
    }


class Fake:
    model = CHAT_MODEL

    def chat(self, messages, *, tools):
        return {**call(), "usage": {"total_tokens": 12, "headers": "never record"}}

    def embed(self, text):
        return [1.0] + [0.0] * 1023


def test_chat_and_embed_record_roundtrip_and_no_transport_fields(tmp_path):
    path = tmp_path / "record.json"
    recorder = Recorder(path, Fake(), provenance="synthetic test")
    messages, tools = [{"role": "user", "content": "public dummy request"}], []
    chat = recorder.chat(messages, tools=tools)
    embedding = recorder.embed("public dummy query")
    replay = Replay(path)
    assert replay.chat(messages, tools=tools) == chat
    assert replay.embed("public dummy query") == embedding
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 2
    assert "headers" not in path.read_text(encoding="utf-8")
    assert "public dummy request" not in path.read_text(encoding="utf-8")
    assert "public dummy query" not in path.read_text(encoding="utf-8")
    chat["usage"]["total_tokens"] = 999
    assert replay.chat(messages, tools=tools)["usage"]["total_tokens"] == 12
    assert request_hash("chat", {"a": 1, "b": 2}) == request_hash("chat", {"b": 2, "a": 1})
    with pytest.raises(ReplayMiss):
        replay.chat(messages, tools=[{"different": True}])
    with pytest.raises(ReplayMiss):
        Replay(path, "another-model").chat(messages, tools=tools)
    with pytest.raises(ReplayMiss):
        replay.embed("not recorded")


@pytest.fixture
def offline(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Replay must not use the network or live backend")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(Live, "chat", fail)
    monkeypatch.setattr(Live, "embed", fail)


def test_default_replay_outputs_conditions_table_rows_and_splits(offline, tmp_path, capsys):
    extra = tmp_path / "review.json"
    extra.write_text('{"custom_metric":0.75,"turns":324}', encoding="utf-8")
    assert (
        run.main(
            [
                "--backend",
                "replay",
                "--out-dir",
                str(tmp_path),
                "--label",
                "test",
                "--extra-summary",
                str(extra),
            ]
        )
        == 0
    )
    directory = next(tmp_path.glob("*-test"))
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (directory / "rows.jsonl").read_text().splitlines()]
    assert len(rows) == 125
    assert summary["results"]["all_tools"]["cases"] == 61
    assert summary["results"]["match"]["cases"] == 64
    assert summary["status"] == "COMPLETE"
    assert set(summary["by_split"]) == {"null", "dev", "holdout"}
    assert summary["by_split"]["null"]["match"]["cases"] == 64
    conditions = summary["conditions"]
    assert conditions["model_id"] == CHAT_MODEL
    assert conditions["backend"] == "replay" and conditions["repeat"] == 1
    for key in ("system_prompt_sha256", "case_set_sha256", "replay_sha256"):
        assert len(conditions[key]) == 64
    assert len(conditions["git_commit"]) == 40 and conditions["date"]
    system = run.tool_selection.SYSTEM + json.dumps(
        DummyStore(tmp_path / "unused-store.json").read(), ensure_ascii=False
    )
    assert conditions["system_prompt_sha256"] == run.sha256(system.encode())
    table = (directory / "table.md").read_text(encoding="utf-8")
    assert "미구현" in table and "리뷰(외부 결과)" in table and "custom_metric" in table
    assert "COMPLETE" in capsys.readouterr().out


def test_replay_module_command_without_network(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenthan.eval.run",
            "--backend",
            "replay",
            "--out-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "all_tools" in completed.stdout and "match" in completed.stdout
    assert len(list(tmp_path.glob("*/summary.json"))) == 1


def test_valid_arguments_can_be_exactly_wrong_and_later_turn_not_scored():
    first = Turn(say="통계 7일", expect=Expect(tool="get_statistics", args={"period": "7d"}))
    case = Case(id="multi", split="dev", origin="holdout1", turns=[first, Turn(say="ignored")])
    rows, results, splits = run.evaluate({"all_tools": [case]}, Fake(), repeat=2)
    metrics = results["all_tools"]
    assert len(rows) == 2 and [r["round"] for r in rows] == [0, 1]
    assert metrics["selection_accuracy"] == 1
    assert metrics["args_valid_rate"] == 1 and metrics["args_accuracy"] == 0
    assert metrics["tokens_per_call"] == 12 and metrics["usage_calls"] == 2
    assert splits["dev"]["all_tools"]["cases"] == 2
    assert rows[0]["origin"] == "holdout1"


def test_missing_replay_is_counted_in_both_paths_despite_matcher_fallback(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text('{"version":1,"entries":{}}', encoding="utf-8")
    case = Case(id="missing", text="메뉴", expected_tool="upsert_menu")
    rows, results, _ = run.evaluate({"all_tools": [case], "match": [case]}, Replay(path))
    assert all(metrics["api_errors"] == 1 for metrics in results.values())
    assert all("Missing replay" in row["api_error"] for row in rows)
    assert results["match"]["selection_accuracy"] is None
    assert (
        run.main(
            [
                "--backend",
                "replay",
                "--replay-file",
                str(path),
                "--out-dir",
                str(tmp_path),
                "--only",
                "upsert_menu-1,place_request_cases-001",
            ]
        )
        == 2
    )


def test_save_baseline_copies_summary_only_and_subset_hash_changes(monkeypatch, tmp_path):
    baselines = tmp_path / "baselines"
    monkeypatch.setattr(run, "BASELINES", baselines)
    for number, case_id in enumerate(("upsert_menu-1", "upsert_menu-2")):
        assert (
            run.main(
                [
                    "--backend",
                    "replay",
                    "--out-dir",
                    str(tmp_path),
                    "--only",
                    case_id,
                    "--label",
                    f"subset-{number}",
                    "--save-baseline",
                    f"b{number}",
                ]
            )
            == 0
        )
    files = sorted(baselines.iterdir())
    assert [p.name for p in files] == ["b0.json", "b1.json"]
    old, new = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert old["conditions"]["case_set_sha256"] != new["conditions"]["case_set_sha256"]
    assert "rows" not in old
    original = next(tmp_path.glob("*-subset-0/summary.json"))
    assert files[0].read_bytes() == original.read_bytes()


def test_expected_args_absent_is_not_scored_and_misses_are_separate():
    case = Case(id="legacy", text="통계", expected_tool="get_statistics")
    _, metrics, _ = run.evaluate({"all_tools": [case]}, Fake())
    assert metrics["all_tools"]["args_accuracy"] is None
    assert metrics["all_tools"]["args_valid_rate"] == 1
    assert summarize([], planned=2)["unexecuted"] == 2


def test_live_chat_preserves_usage_without_headers(monkeypatch):
    response = NS(
        choices=[NS(finish_reason="stop", message=call())],
        usage=NS(model_dump=lambda: {"total_tokens": 10, "headers": "secret transport data"}),
    )
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return response

    backend = Live()
    backend.client = NS(chat=NS(completions=NS(create=create)))
    result = backend.chat([{"role": "user", "content": "dummy"}], tools=[])
    assert result["usage"] == {"total_tokens": 10}
    assert requests[0]["model"] == CHAT_MODEL and "tools" not in requests[0]
    response.choices[0].finish_reason = "length"
    with pytest.raises(ClovaChatError, match="evaluation chat failed"):
        backend.chat([], tools=[])


def test_live_errors_are_counted_even_when_matching_falls_back():
    class Broken(Fake):
        def chat(self, messages, *, tools):
            raise ClovaChatError("chat unavailable")

        def embed(self, text):
            raise ClovaEmbeddingError("embed unavailable")

    case = Case(id="dummy", text="메뉴", expected_tool="upsert_menu")
    _, metrics, _ = run.evaluate({"all_tools": [case], "match": [case]}, Broken())
    assert metrics["all_tools"]["api_errors"] == metrics["match"]["api_errors"] == 1


@pytest.mark.network
def test_live_three_case_command(tmp_path):
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        pytest.skip("CLOVASTUDIO_API_KEY is not configured")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenthan.eval.run",
            "--backend",
            "live",
            "--only",
            "get_statistics-1,unrelated-1,place_request_cases-001",
            "--pause",
            "1.05",
            "--out-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(next(tmp_path.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert sum(m["cases"] for m in summary["results"].values()) == 3
    for metrics in summary["results"].values():
        assert metrics["api_errors"] == 0 and metrics["latency_p50_sec"] is not None


def test_bundled_recording_covers_existing_kinds():
    cases = load_cases(run.TOOL_CASES)
    assert {c.kind for c in cases} == {"full", "missing", "unrelated"}
    backend = Replay(run.REPLAY_PATH)
    # Existing cases only; M0 must not invent injection/ambiguous cases in tests/data.
    _, metrics, _ = run.evaluate({"all_tools": cases}, backend)
    assert metrics["all_tools"]["api_errors"] == 0
    assert all(group["total"] >= 2 for group in metrics["all_tools"]["per_kind"].values())


def test_live_sample_replays_saved_metrics_without_network(offline):
    baseline = json.loads((run.BASELINES / "m0-live-sample.json").read_text(encoding="utf-8"))
    paths = {"all_tools": run.TOOL_CASES, "match": run.MATCH_CASES}
    case_sets = {
        mode: [
            case
            for case in load_cases(path)
            if case.id in baseline["conditions"]["case_sets"][mode]["selected_ids"]
        ]
        for mode, path in paths.items()
    }
    backend = Replay(run.REPLAY_PATH.with_name("live-sample.json"))
    assert backend.data["provenance"] == "live"
    _, results, _ = run.evaluate(case_sets, backend)
    assert results["all_tools"]["cases"] == 12
    assert results["match"]["cases"] == 3
    for mode, metrics in results.items():
        assert metrics["api_errors"] == metrics["unexecuted"] == 0
        for key in (
            "selection_accuracy",
            "args_accuracy",
            "args_valid_rate",
            "false_call_rate",
            "safety_violations",
            "tokens_per_call",
        ):
            assert metrics[key] == baseline["results"][mode][key]
    assert all(group["total"] >= 2 for group in results["all_tools"]["per_kind"].values())
