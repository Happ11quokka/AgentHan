import json

import pytest

from agenthan.eval.cases import READ_ONLY_TOOLS, Case, load_cases, needs_approval
from agenthan.eval.run import MATCH_CASES, TOOL_CASES


def test_existing_sets_are_adapted_without_modifying_files():
    for path, count in ((TOOL_CASES, 61), (MATCH_CASES, 64)):
        before = path.read_bytes()
        cases = load_cases(path)
        assert len(cases) == count
        assert len({c.id for c in cases}) == count
        for case in cases:
            assert case.split is None and case.expected_args is None
            assert len(case.turns) == 1
            assert case.turns[0].say == case.text
            assert case.turns[0].expect.tool == case.expected_tool
            assert case.needs_approval == needs_approval(case.expected_tool or case.near_tool)
        assert path.read_bytes() == before
    assert load_cases(MATCH_CASES)[0].expected_tool == "update_business_info"
    assert load_cases(MATCH_CASES)[0].expected_feature == "business_info"


def test_three_goldset_lines_preserve_turns_split_and_origin(tmp_path):
    goldset = [
        {
            "id": "single",
            "category": "review",
            "store": "dummy",
            "note": "team format",
            "split": "dev",
            "origin": "holdout1",
            "turns": [
                {"say": "가짜 리뷰 요약", "expect": {"tool": "analyze_reviews", "args": {"n": 3}}}
            ],
        },
        {
            "id": "multi",
            "split": "holdout",
            "turns": [
                {"say": "통계", "expect": {"tool": "get_statistics", "args": {"period": "7d"}}},
                {"say": "이어서", "expect": {"tool": None, "result": {"report.total": 2}}},
            ],
        },
        {"id": "none", "turns": [{"say": "안녕", "expect": {"tool": None}}]},
    ]
    path = tmp_path / "goldset.jsonl"
    path.write_text("\n".join(json.dumps(c) for c in goldset), encoding="utf-8")
    single, multi, none = load_cases(path)
    assert single.category == "review" and single.store == "dummy" and single.note == "team format"
    assert single.split == "dev" and single.origin == "holdout1"
    assert single.expected_args == {"n": 3} and single.needs_approval
    assert len(multi.turns) == 2 and multi.split == "holdout"
    assert multi.turns[1].expect.result == {"report.total": 2}
    assert none.text == "안녕" and none.expected_tool is None
    assert not none.needs_approval
    assert [c.id for c in load_cases(path, split="dev")] == ["single"]
    assert [c.id for c in load_cases(path, split="holdout")] == ["multi"]


def test_new_schema_defaults_and_code_owned_approval(tmp_path):
    path = tmp_path / "v2.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "new",
                "split": "dev",
                "kind": "confusable",
                "lang": "mn",
                "text": "dummy",
                "expected_tool": "get_statistics",
                "expected_args": {"period": "7d"},
                "candidates": ["get_statistics", "get_settlement_summary"],
                "difficulty": "hard",
                "source": "public dummy",
                "needs_approval": True,
            }
        ),
        encoding="utf-8",
    )
    case = load_cases(path)[0]
    assert not case.needs_approval
    assert case.turns[0].expect.args == {"period": "7d"}
    assert case.candidates == ["get_statistics", "get_settlement_summary"]
    for tool in READ_ONLY_TOOLS:
        assert not Case(id=tool, expected_tool=tool, needs_approval=True).needs_approval
    assert Case(id="write", expected_tool="upsert_menu", needs_approval=False).needs_approval
    assert Case(id="missing", near_tool="upsert_menu").needs_approval


@pytest.mark.parametrize("fields", [{"split": "test"}, {"kind": "other"}, {"turns": []}])
def test_invalid_schema_fails_explicitly(fields):
    with pytest.raises(ValueError):
        Case(id="bad", **fields)


def test_duplicate_ids_fail(tmp_path):
    path = tmp_path / "duplicate.jsonl"
    path.write_text('{"id":"same"}\n{"id":"same"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate case id"):
        load_cases(path)
