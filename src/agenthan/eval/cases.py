"""Common first-turn and review-goldset case adapters (source files stay untouched)."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agenthan.tools.feature_tools import TOOL_FEATURES

READ_ONLY_TOOLS = frozenset(
    {
        "list_call_history",
        "get_naverpay_signup_guide",
        "get_settlement_summary",
        "check_pos_compatibility",
        "get_statistics",
        "search_help_articles",
    }
)
FEATURE_TO_TOOL = {feature: tool for tool, feature in TOOL_FEATURES.items()}


def needs_approval(tool: str | None) -> bool:
    """Unknown tools are conservatively treated as writes; no call needs no approval."""
    return tool is not None and tool not in READ_ONLY_TOOLS


class Expect(BaseModel):
    tool: str | None = None
    args: dict | None = None
    result: object = None  # Preserved, deliberately not scored by M0.


class Turn(BaseModel):
    say: str
    expect: Expect = Field(default_factory=Expect)


class Case(BaseModel):
    id: str
    split: Literal["dev", "holdout"] | None = None
    kind: Literal["full", "missing", "unrelated", "ambiguous", "confusable", "injection"] = "full"
    lang: Literal["ko", "ko_simple", "en", "mn", "ru"] = "ko"
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


def load_cases(path: Path, *, split: str | None = None) -> list[Case]:
    """Adapt legacy tool/feature JSONL, new cases, and turns-based review goldsets."""
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
        case = Case.model_validate(data)
        if case.id in ids:
            raise ValueError(f"Duplicate case id: {case.id}")
        ids.add(case.id)
        if split is None or case.split == split:
            cases.append(case)
    return cases
