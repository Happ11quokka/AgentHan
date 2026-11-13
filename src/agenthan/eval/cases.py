"""Common first-turn and review-goldset case adapters (source files stay untouched)."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agenthan.tools.feature_tools import READ_ONLY_TOOLS, TOOL_FEATURES, needs_approval

__all__ = ["READ_ONLY_TOOLS", "needs_approval"]
FEATURE_TO_TOOL = {feature: tool for tool, feature in TOOL_FEATURES.items()}


class Expect(BaseModel):
    """Per-turn expectations. An omitted field is not scored (see eval.flow)."""

    feature: str | None = None  # Route; derived from tool/near_tool on legacy first turns.
    ask_missing: list[str] | None = None  # Labels the turn must ask for (missing_fields).
    tool: str | None = None
    args: dict | None = None
    store: dict | None = None  # Dot path → value in the store after this turn.
    result: object = None  # Preserved, deliberately not scored.


class Turn(BaseModel):
    say: str
    expect: Expect = Field(default_factory=Expect)


class Case(BaseModel):
    id: str
    split: Literal["dev", "holdout"] | None = None
    kind: Literal["full", "missing", "unrelated", "ambiguous", "confusable", "injection"] = "full"
    lang: Literal["ko", "ko_simple"] = "ko"  # Korean only (docs/eval-baseline.md §0)
    text: str = ""
    expected_tool: str | None = None
    expected_args: dict | None = None
    near_tool: str | None = None
    expects_clarify: bool = False
    candidates: list = Field(default_factory=list)
    needs_approval: bool = False
    difficulty: str | int | float | None = None
    source: str | None = None
    turns: list[Turn] = Field(default_factory=list)
    category: str | None = None
    store: str | dict | None = None
    note: str | None = None
    origin: str | None = None
    expected_feature: str | None = None  # Legacy place_request target, including review features.

    @model_validator(mode="after")
    def adapt_turns(self):
        if "turns" in self.model_fields_set:
            if not self.turns:
                raise ValueError("turns must contain at least one turn")
            first = self.turns[0]
            if "text" not in self.model_fields_set:
                self.text = first.say
            if "expected_tool" not in self.model_fields_set:
                self.expected_tool = first.expect.tool
            if "expected_args" not in self.model_fields_set:
                self.expected_args = first.expect.args
        else:
            self.turns = [
                Turn(say=self.text, expect=Expect(tool=self.expected_tool, args=self.expected_args))
            ]
        self.needs_approval = needs_approval(self.expected_tool or self.near_tool)
        return self


def load_cases(
    path: Path, *, split: str | None = None, splits: dict[str, str] | None = None
) -> list[Case]:
    """Adapt legacy tool/feature JSONL, new cases, and turns-based review goldsets.

    splits (id → dev/holdout) fills split only where the case itself has none."""
    if split not in (None, "dev", "holdout"):
        raise ValueError("split must be dev or holdout")
    cases, ids = [], set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        data = json.loads(line)
        data.setdefault("id", f"{path.stem}-{line_number:03d}")
        data.setdefault("source", path.name)
        if "expected" in data:  # Feature-matching dataset has no tool names or IDs.
            feature = data.pop("expected")
            data.setdefault("expected_feature", feature)
            data.setdefault("expected_tool", FEATURE_TO_TOOL.get(feature, feature))
            data.setdefault("kind", "unrelated" if feature is None else "full")
        data.setdefault("expects_clarify", data.get("kind") in {"missing", "ambiguous"})
        if splits and data.get("split") is None and data["id"] in splits:
            data["split"] = splits[data["id"]]
        case = Case.model_validate(data)
        if case.id in ids:
            raise ValueError(f"Duplicate case id: {case.id}")
        ids.add(case.id)
        if split is None or case.split == split:
            cases.append(case)
    return cases


def split_group(case: Case) -> str:
    """Stratify by what the case expects so every feature appears in both splits."""
    target = case.expected_feature or case.expected_tool or case.near_tool or "none"
    return f"{case.kind}:{target}"


def make_splits(cases: list[Case], holdout_ratio: float = 0.3) -> dict[str, str]:
    """Fixed dev/holdout by sha256(id) order within each group (groups of 3+ give holdout)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        groups[split_group(case)].append(case.id)
    result = {}
    for ids in groups.values():
        ordered = sorted(ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        count = max(1, round(len(ordered) * holdout_ratio)) if len(ordered) >= 3 else 0
        result.update({i: "holdout" if n < count else "dev" for n, i in enumerate(ordered)})
    return dict(sorted(result.items()))
