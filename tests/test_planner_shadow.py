"""AGENT_MODE=shadow (ADR-0005): the planner only logs; the legacy answer does not change."""

import copy
import json
import re

import pytest

from agenthan.agent import owner_assistant as owner
from agenthan.agent import place_matcher as matcher
from agenthan.agent.clova_chat import ClovaRequestRejected
from agenthan.core.llm import ClovaV3Error
from agenthan.eval import scenario as sc
from agenthan.eval.fake_backend import FakeBackend
from agenthan.eval.replay import Recorder, Replay
from agenthan.tools.dummy_store import SEED_PATH

PLAN = {
    "scope": "store",
    "steps": [{"feature": "menu", "goal": "연습 차 5000원으로 등록"}],
    "clarify": "",
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_MODE", "legacy")  # the shadow baseline is legacy

    def no_network(*args, **kwargs):
        pytest.fail("shadow tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)


@pytest.fixture
def menu_embed(monkeypatch):
    index = {}
    for i, feature in enumerate(matcher.load_features()):
        vector = [0.0] * 1024
        vector[i] = 1.0
        index[feature["id"]] = vector
    monkeypatch.setattr(matcher, "_load_index", lambda _: index)
    return lambda _: index["menu"]


def tool_call(name, arguments, call_id="call-1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


class Wire:
    """Every model request in order, across chat, select and the planner."""

    def __init__(self, plan=PLAN, plan_error=None):
        self.log, self.plan, self.plan_error = [], plan, plan_error
        self.replies = iter(
            [
                tool_call("upsert_menu", {"name": "연습 차", "price": 5000}),
                tool_call("upsert_menu", {"name": "연습 차", "price": 6000}, "call-2"),
            ]
        )

    def chat(self, messages, *, tools):
        self.log.append(("chat", copy.deepcopy((messages, tools))))
        return next(self.replies)

    def select(self, messages, *, tools):
        self.log.append(("select", copy.deepcopy((messages, tools))))
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "s",
                    "type": "function",
                    "function": {"name": "select_feature", "arguments": '{"feature_id":"menu"}'},
                }
            ],
        }

    def plan_call(self, model, body):
        self.log.append(("plan", (model, body)))
        if self.plan_error:
            raise self.plan_error
        return {"message": {"role": "assistant", "content": json.dumps(self.plan)}}

    def kinds(self):
        return [kind for kind, _ in self.log]

    def legacy(self):
        return [entry for entry in self.log if entry[0] != "plan"]


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def run_session(tmp_path, embed, name, wire, **options):
    path = tmp_path / f"{name}.json"
    path.write_bytes(SEED_PATH.read_bytes())
    assistant = owner.OwnerAssistant(
        path,
        chat=wire.chat,
        embed=embed,
        select_chat=wire.select,
        plan_call=wire.plan_call,
        **options,
    )
    answers, traces = [], []  # New records get a random id; it is masked (as in ADR-0003).
    for text in ("신메뉴 연습 차 5000원으로 등록해 줘", "아 6000원으로", "네"):
        answers.append(UUID.sub("<id>", assistant.ask(text)))
        traces.append(json.loads(UUID.sub("<id>", json.dumps(assistant.trace, ensure_ascii=False))))
    return assistant, answers, traces, UUID.sub("<id>", path.read_text(encoding="utf-8"))


def test_shadow_keeps_answers_model_calls_and_store_the_same_as_legacy(tmp_path, menu_embed):
    legacy_wire, shadow_wire = Wire(), Wire()
    _, legacy_answers, legacy_traces, legacy_store = run_session(
        tmp_path, menu_embed, "legacy", legacy_wire, agent_mode="legacy"
    )
    _, shadow_answers, shadow_traces, shadow_store = run_session(
        tmp_path, menu_embed, "shadow", shadow_wire, agent_mode="shadow"
    )
    assert shadow_answers == legacy_answers
    assert shadow_store == legacy_store
    # The same requests in the same order; the planner's calls are extra and come after.
    assert shadow_wire.legacy() == legacy_wire.legacy()
    assert "plan" not in legacy_wire.kinds()
    for trace in shadow_traces:
        trace.pop("planner", None)
    assert shadow_traces == legacy_traces


def test_planner_call_follows_the_legacy_calls_of_its_turn(tmp_path, menu_embed):
    wire = Wire()
    run_session(tmp_path, menu_embed, "s", wire, agent_mode="shadow")
    kinds = wire.kinds()
    # turn 1: select, chat, plan; turn 2 (a fix): select, chat, plan; the final "네": no model
    assert kinds[:6] == ["select", "chat", "plan", "select", "chat", "plan"]
    assert kinds.count("plan") == 2  # the plain 네 is an approval, not a request to plan


def test_plan_lands_in_the_trace_and_sees_candidates_and_open_tasks(tmp_path, menu_embed):
    wire = Wire()
    _, _, traces, _ = run_session(tmp_path, menu_embed, "s", wire, agent_mode="shadow")
    assert traces[0]["planner"] == {
        "scope": "store",
        "steps": [{"feature": "menu", "goal": "연습 차 5000원으로 등록"}],
        "clarify": None,
    }
    assert "planner" not in traces[2]  # approval turn: nothing to plan
    plans = [entry for kind, entry in wire.log if kind == "plan"]
    first, second = (body["messages"] for _, body in plans)
    assert "메뉴" in first[1]["content"] and "열린 작업: 없음" in first[1]["content"]
    assert "진행 중 menu" in second[1]["content"] and "승인 대기" in second[1]["content"]
    assert len(first) == 2  # system + the one request: no chat history


@pytest.mark.parametrize(
    "error", [ClovaV3Error("CLOVA HTTP 오류 (400)"), ClovaRequestRejected("40009"), OSError("net")]
)
def test_planner_failure_is_quiet_and_changes_nothing(tmp_path, menu_embed, error):
    ok = Wire()
    _, ok_answers, _, ok_store = run_session(tmp_path, menu_embed, "a", ok, agent_mode="shadow")
    broken = Wire(plan_error=error)
    _, answers, traces, store = run_session(tmp_path, menu_embed, "b", broken, agent_mode="shadow")
    assert answers == ok_answers and store == ok_store
    assert traces[0]["planner"] is None and traces[1]["planner"] is None


def test_unusable_plan_is_none_in_the_trace(tmp_path, menu_embed):
    wire = Wire(plan={"scope": "store", "steps": [{"feature": "teleport", "goal": "x"}]})
    _, _, traces, _ = run_session(tmp_path, menu_embed, "s", wire, agent_mode="shadow")
    assert traces[0]["planner"] is None


def test_legacy_never_calls_the_planner_and_has_no_shadow_node(tmp_path, menu_embed):
    wire = Wire()
    assistant, _, traces, _ = run_session(tmp_path, menu_embed, "l", wire)
    assert assistant.agent_mode == "legacy" and "plan" not in wire.kinds()
    assert all("planner" not in trace for trace in traces)
    assert "plan_shadow" not in assistant.turns.get_graph().nodes


def test_shadow_adds_one_node_and_draws(tmp_path, menu_embed):
    assistant = owner.OwnerAssistant(tmp_path / "s.json", agent_mode="shadow")
    graph = assistant.turns.get_graph()
    assert {"route", "respond", "plan_shadow", "wait_approval"} <= set(graph.nodes)
    assert "plan_shadow" in graph.draw_mermaid()


def test_mode_comes_from_the_environment_and_planner_mode_is_available(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "shadow")
    assert owner.OwnerAssistant(tmp_path / "e.json").agent_mode == "shadow"
    assert owner.OwnerAssistant(tmp_path / "p.json", agent_mode="planner").agent_mode == "planner"
    monkeypatch.setenv("AGENT_MODE", "nonsense")
    with pytest.raises(ValueError):
        owner.OwnerAssistant(tmp_path / "x.json")


def test_without_a_key_shadow_default_call_fails_quietly(tmp_path, menu_embed):
    """No plan_call injected, no key: the planner returns None and the answer is the legacy one."""
    wire = Wire()
    path = tmp_path / "k.json"
    path.write_bytes(SEED_PATH.read_bytes())
    assistant = owner.OwnerAssistant(
        path, chat=wire.chat, embed=menu_embed, select_chat=wire.select, agent_mode="shadow"
    )
    assert "바꿀까요" in assistant.ask("신메뉴 연습 차 5000원으로 등록해 줘")
    assert assistant.trace["planner"] is None


# ---------------------------------------------------------------- replay: legacy recording


def test_legacy_recording_replays_identically_under_shadow(tmp_path):
    """Record S1 scenarios with legacy, replay them with shadow: the same turns and outcomes.

    The planner's request is absent from the recording, so it misses and is None, which must not
    count as an outage or change a turn."""
    path = tmp_path / "rec.json"
    scenarios = [s for s in sc.load_scenarios() if s.split == "dev"][:6]
    recorder = Recorder(path, FakeBackend())
    legacy = [sc.run_scenario(s, sc.SUBJECTS["current"], recorder) for s in scenarios]

    def shadow_subject(store, probe, today):
        return sc.OwnerAssistantSubject(store, probe, today, agent_mode="shadow")

    shadow = [sc.run_scenario(s, shadow_subject, Replay(path)) for s in scenarios]

    def outcome(run):
        return {
            "goal": run["goal"],
            "complete": run["complete"],
            "final": run["final"],
            "turns": [
                {k: t[k] for k in ("say", "answer", "proposals", "changed", "model_calls")}
                for t in run["turns"]
            ],
        }

    assert [outcome(r) for r in shadow] == [outcome(r) for r in legacy]
    assert all(r["complete"] for r in shadow)  # a missing plan recording is not an API error
    traces = [t["trace"] for r in shadow for t in r["turns"] if t.get("trace")]
    assert traces and all(t["planner"] is None for t in traces if "planner" in t)


def test_s1_first_feature_accuracy_reads_the_planner_only_in_shadow():
    """L2 diagnostic, not the gate: the planner's first step against each turn's probe feature."""
    scenarios = [s for s in sc.load_scenarios() if s.split == "dev"][:6]

    def shadow_subject(store, probe, today):
        return sc.OwnerAssistantSubject(store, probe, today, agent_mode="shadow")

    legacy = sc.summarize(
        [sc.run_scenario(s, sc.SUBJECTS["current"], FakeBackend()) for s in scenarios]
    )
    shadow = sc.summarize([sc.run_scenario(s, shadow_subject, FakeBackend()) for s in scenarios])
    assert legacy["planner_scored_turns"] == 0 and legacy["planner_first_feature_accuracy"] is None
    assert legacy["routed_first_feature_accuracy"] is not None
    # The fake planner always says "out" (no steps), so every probed turn is a miss.
    assert shadow["planner_scored_turns"] > 0 and shadow["planner_first_feature_accuracy"] == 0
    assert shadow["routed_first_feature_accuracy"] == legacy["routed_first_feature_accuracy"]
    table = sc.render_table({"results": shadow})
    assert "첫 기능 정확도" in table and "첫 기능 정확도" not in sc.render_table(
        {"results": legacy}
    )
