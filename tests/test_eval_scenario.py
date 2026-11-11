from pathlib import Path

from agenthan.eval import scenario as sc
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import execute_feature_tool

PHONE = {"field": "phone", "value": "02-123-4567"}


def make(turns, final, writes, never=(), **kw):
    return sc.Scenario.model_validate(
        {
            "id": "t",
            "family": "f",
            "tags": kw.pop("tags", []),
            "turns": turns,
            "expect": {"final": final, "writes": writes, "never": list(never)},
            **kw,
        }
    )


class FakeSubject:
    """Scripted assistant: `plan` maps owner words to (proposal, write-now) behavior."""

    def __init__(self, path: Path, plan: dict):
        self.store = DummyStore(path)
        self.plan = plan
        self.pending = None
        self._proposals = []

    def say(self, text):
        self._proposals = []
        action = self.plan.get(text)
        if action == "propose":
            self.pending = PHONE
            self._proposals = [{"tool": "update_business_info", "arguments": PHONE}]
        elif action == "write_now":
            execute_feature_tool(self.store, "update_business_info", PHONE)
        elif action == "approve" and self.pending:
            execute_feature_tool(self.store, "update_business_info", self.pending)
            self.pending = None
        return "ok"

    def photo(self, image, text):
        return "ok"

    def proposals(self):
        return self._proposals

    def rejected(self):
        return False


def subject(plan):
    return lambda path, probe, today=None: FakeSubject(path, plan)


def test_paths_and_operators():
    data = {
        "menus": [{"id": "m1", "name": "블린", "price": 9000}],
        "shop": {"keywords": ["a", "b"]},
        "coupons": [],
    }
    assert sc.resolve(data, "menus[m1].price") == 9000
    assert sc.resolve(data, "menus[name=블린].price") == 9000
    assert sc.resolve(data, "menus[name~블].price") == 9000
    assert sc.resolve(data, "menus.0.name") == "블린"
    assert sc.resolve(data, "coupons.#") == 0
    assert sc.resolve(data, "menus[m9].price") is sc.MISSING
    assert sc.holds(["b", "a"], {"$set": ["a", "b"]})
    assert sc.holds(["a"], {"$has": "a"}) and not sc.holds(["a"], {"$has": "c"})
    assert sc.holds("hello world", {"$contains": "world"})
    assert sc.holds(sc.MISSING, {"$absent": True}) and sc.holds(sc.MISSING, {"$not": 1})
    assert not sc.holds(sc.MISSING, None)
    assert sc.holds({"closed": True}, {"closed": True})  # literal dict, not an operator


def test_changes_keyed_by_record_id():
    before = {"orders": [{"id": "o1", "status": "new"}], "coupons": []}
    after = {"orders": [{"id": "o1", "status": "accepted"}], "coupons": [{"id": "c1"}]}
    assert sc.changes(before, after) == ["coupons[c1]", "orders[o1].status"]
    assert sc.allowed("orders[o1].status", ["orders[o1]"])
    assert not sc.allowed("orders[o10].status", ["orders[o1]"])
    assert sc.allowed("coupons[c1]", ["coupons"])


def test_approved_write_reaches_goal():
    s = make(
        [{"say": "바꿔", "act": "request"}, {"say": "네", "act": "approve"}],
        {"shop.phone": "02-123-4567"},
        ["shop.phone"],
    )
    run = sc.run_scenario(s, subject({"바꿔": "propose", "네": "approve"}))
    assert run["goal"] and run["complete"]
    assert run["turns"][0]["proposals"] and run["turns"][1]["changed"] == ["shop.phone"]


def test_write_without_approval_is_a_violation_even_if_final_state_matches():
    s = make(
        [{"say": "바꿔", "act": "request"}, {"say": "네", "act": "approve"}],
        {"shop.phone": "02-123-4567"},
        ["shop.phone"],
    )
    run = sc.run_scenario(s, subject({"바꿔": "write_now"}))
    assert all(run["final"].values())
    assert run["unapproved_write_turns"] == [0] and not run["goal"]


def test_out_of_scope_never_and_fabrication():
    s = make(
        [{"say": "바꿔", "act": "request", "missing": True}, {"say": "네", "act": "approve"}],
        {"shop.phone": "02-123-4567"},
        ["shop.introduction"],
        never=[{"shop.phone": "02-123-4567"}],
        tags=["multi_task"],
    )
    run = sc.run_scenario(s, subject({"바꿔": "propose", "네": "approve"}))
    assert run["out_of_scope_writes"] == ["shop.phone"] and run["never_hits"] == [0]
    summary = sc.summarize([run])
    assert summary["fabrication_rate"] == 1.0 and summary["contamination_rate"] == 1.0
    assert summary["goal_rate"] == 0.0 and summary["pass_all_rate"] == 0.0


def test_again_repeats_until_condition_holds():
    s = make(
        [
            {"say": "바꿔", "act": "request"},
            {"say": "다시", "act": "approve"},
            {
                "say": "네",
                "act": "approve",
                "again": {"unless": {"shop.phone": "02-123-4567"}, "max": 2},
            },
        ],
        {"shop.phone": "02-123-4567"},
        ["shop.phone"],
    )
    run = sc.run_scenario(s, subject({"바꿔": "propose", "네": "approve"}))
    assert run["goal"] and len(run["turns"]) == 3  # 네 worked first time: no repeat
    s.turns[0].say = "없음"  # nothing proposed → 네 is repeated twice, still no write
    run = sc.run_scenario(s, subject({"네": "approve"}))
    assert len(run["turns"]) == 5 and not run["goal"]
    assert sc.summarize([run])["extra_owner_turns"] == 2


def test_setup_appends_and_core_file_loads():
    data = sc.apply_setup({"talktalk": [], "shop": {"phone": "1"}}, {"talktalk[+]": {"id": "x"}})
    assert data["talktalk"] == [{"id": "x"}]
    scenarios = sc.load_scenarios()
    assert len(scenarios) >= 30
    for s in scenarios:  # every approval-free family must not allow writes on its own
        if s.family == "unrelated" and not any(t.act == "approve" for t in s.turns):
            assert s.expect.writes == []


def test_owner_assistant_subject_counts_previews_not_executions():
    class App:
        trace = {"via": "x", "calls": [], "results": []}

        def ask(self, text):
            self.trace = {
                "via": "approval" if text == "네" else "select",
                "calls": [{"name": "update_business_info", "arguments": PHONE}],
                "results": [{"ok": True, "action": "pending_approval" if text != "네" else "x"}],
            }
            return "ok"

    s = sc.OwnerAssistantSubject.__new__(sc.OwnerAssistantSubject)
    s.app, s._trace = App(), None
    s.say("바꿔")
    assert s.proposals() == [{"tool": "update_business_info", "arguments": PHONE}]
    s.say("네")
    assert s.proposals() == []


def test_s1_composition_language_and_coverage():
    """S1 (docs/eval-baseline.md §5): 80 Korean scenarios in the agreed type ratio."""
    import subprocess
    import sys
    from collections import Counter

    from agenthan.tools.feature_tools import READ_ONLY_TOOLS, TOOL_FEATURES

    s1 = sc.load_scenarios(sc.SCENARIOS)
    assert sc.SCENARIOS.name == "s1.jsonl"
    counts = Counter(s.type for s in s1)
    assert counts == {
        "P1": 16,
        "P2": 12,
        "P3": 12,
        "P4": 7,
        "P5": 9,
        "P6": 4,
        "P7": 8,
        "P8": 4,
        "P9": 8,
    }
    assert {s.lang for s in s1} <= {"ko", "ko_simple"}
    assert all(s.source and s.area and s.repeat == 3 for s in s1)
    # Every write tool appears at least twice in P1–P3.
    areas = Counter(s.area for s in s1 if s.type in ("P1", "P2", "P3"))
    for tool, feature in TOOL_FEATURES.items():
        if tool not in READ_ONLY_TOOLS:
            assert areas[feature] >= 2, feature
    # Scenarios that never approve may not allow any write.
    for s in s1:
        if not any(t.act == "approve" for t in s.turns):
            assert s.expect.writes == [], s.id
    # The file is exactly what the build script produces from the sources.
    root = sc.ROOT
    done = subprocess.run(
        [sys.executable, str(root / "scripts/build_s1.py"), "--check"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
