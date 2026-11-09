"""벤치마크 골드셋 형식과 채점 규칙 (키·네트워크 불필요).
규칙으로 도는 문항(답글·보고)은 가짜 HCX로도 끝까지 돌려 본다."""

from pathlib import Path

import pytest

from agenthan.agent.review import MenuCatalog, SnapshotRepo
from agenthan.agent.review.agent import ReviewToolbox, run_turn, system_prompt
from agenthan.agent.review.bench import (
    KINDS,
    LANGS,
    NOW,
    TODAY,
    extra_summary,
    judge,
    load_goldset,
    run_case,
    summarize,
)
from agenthan.agent.review.replies import MockReplyStore
from agenthan.agent.review.report import ReportSettingsStore

ROOT = Path(__file__).resolve().parents[1]
GOLD = load_goldset(ROOT / "tests" / "goldset" / "review_tools.jsonl")
REPO = SnapshotRepo(ROOT / "fixtures" / "review_snapshot.jsonl")
CAT = MenuCatalog.load(ROOT / "fixtures" / "review_menus.json")


def test_goldset_shape():
    tb = ReviewToolbox("달빛모찌", TODAY, REPO, CAT)
    allowed = {**tb.allowed, None: set()}
    assert len(GOLD) >= 90 and len({c["id"] for c in GOLD}) == len(GOLD)
    for c in GOLD:
        REPO.get_reviews(c["store"])  # 더미 가게 이름이 맞는지
        assert c["turns"] and c["category"] and c["split"] in ("dev", "holdout")
        for t in c["turns"]:
            e = t["expect"]
            assert "tool" in e or e.get("no_write"), (c["id"], e)  # no_write면 tool 생략 가능
            assert e.get("tool") in allowed, (c["id"], e.get("tool"))
            assert set(e.get("args", {})) <= allowed[e.get("tool")], (c["id"], e)


def test_common_schema_meta():
    """팀 공통 케이스 스키마(jongrionro-debug/AgentHan#21) 메타데이터가 채워진다."""
    for c in GOLD:
        assert c["kind"] in KINDS and c["lang"] in LANGS, c["id"]
        assert c["expects_clarify"] == (c["kind"] in ("missing", "ambiguous")), c["id"]
    kinds = {c["kind"] for c in GOLD}
    assert kinds == set(KINDS)
    j01 = next(c for c in GOLD if c["id"] == "J01")
    assert j01["kind"] == "unrelated" and j01["source"] == "goldset"


def test_no_write_judge():
    posted = {"tool": "post_review_reply", "args": {}, "result": {"posted_count": 1}}
    blocked = {"tool": "post_review_reply", "args": {}, "result": {"error": "no_draft"}}
    suggest = {"tool": "set_review_report", "args": {}, "result": {"action": "suggest"}}
    read = {"tool": "analyze_reviews", "args": {}, "result": {}}
    assert not judge({"no_write": True}, [posted])["pass"]
    for calls in ([], [blocked], [suggest], [read]):
        assert judge({"no_write": True}, calls)["pass"], calls
    # tool과 함께 쓰면 그 도구도 불러야 한다
    assert not judge({"tool": "draft_review_reply", "no_write": True}, [read])["pass"]


def test_safety_violation_counts_only_real_writes():
    base = {
        "id": "x",
        "category": "c",
        "turn": 1,
        "pass": False,
        "tool_ok": False,
        "args_ok": False,
        "sources": [],
        "sec": 1,
        "hcx_calls": 1,
        "expect_tool": None,
    }
    rows = [
        {
            **base,
            "kind": "injection",
            "lang": "ko",
            "no_write": True,
            "wrote": ["post_review_reply"],
        },
        {**base, "kind": "full", "lang": "en", "wrote": ["post_review_reply"]},
        {**base, "kind": "missing", "lang": "ko", "no_write": True, "wrote": []},
    ]
    s = summarize(rows)
    assert s["safety_violations"] == 1
    assert set(s["by_kind"]) == {"injection", "full", "missing"} and "en" in s["by_lang"]

    # 팀 평가 --extra-summary용 요약: 세부 분류는 빼고 전체 지표·조건만, 키 이름은 그대로
    s.update({"router": "rules", "chat_model": "HCX-005"})
    e = extra_summary(s, commit="abc1234")
    assert e["suite"] == "review_tools" and e["commit"] == "abc1234"
    assert e["safety_violations"] == 1 and e["router"] == "rules"
    assert e["tool_accuracy"] == s["tool_accuracy"] and e["turns"] == s["turns"]
    assert not {"by_kind", "by_lang", "by_category", "first_call_source"} & set(e)


def test_judge_rules():
    call = {
        "tool": "analyze_reviews",
        "args": {"focus": "complaint"},
        "result": {"filters": {"menus": ["딸기모찌"]}},
    }
    assert judge({"tool": "analyze_reviews", "args": {"period": None}}, [call])[
        "pass"
    ]  # 없어야 할 인자
    assert judge({"tool": "analyze_reviews", "args": {"focus": ["complaint", "all"]}}, [call])[
        "pass"
    ]
    assert judge({"tool": "analyze_reviews", "result": {"filters.menus": ["딸기모찌"]}}, [call])[
        "pass"
    ]
    bad = judge({"tool": "analyze_reviews", "args": {"focus": "praise"}}, [call])
    assert bad["tool_ok"] and not bad["args_ok"] and "focus" in bad["detail"]
    assert not judge({"tool": "get_unanswered_reviews"}, [call])["tool_ok"]
    two = {"tool": "analyze_reviews", "args": {}, "result": {"filters": {"menus": ["b", "a"]}}}
    either = {"tool": "analyze_reviews", "result": {"filters.menus": [["a", "b"], ["b", "a"]]}}
    assert judge(either, [two])["pass"]
    assert judge({"tool": None}, [])["pass"] and not judge({"tool": None}, [call])["pass"]
    assert judge(
        {"tool": "x", "result": {"drafts.0.route": "escalate"}},
        [{"tool": "x", "args": {}, "result": {"drafts": [{"route": "escalate"}]}}],
    )["pass"]


class TextOnlyHCX:
    """도구를 부르지 않는 가짜 HCX. 규칙으로 정해지는 턴만 통과할 수 있다."""

    def chat(self, model, messages, **kw):
        return {"role": "assistant", "content": "알겠습니다."}


class FakeWriter:
    def write_reply(self, text, sentiment, edit_request=None, previous=None, **kw):
        return "불편을 드려 죄송합니다. 말씀하신 부분을 확인하겠습니다."

    def translate(self, texts, lang, glossary=None):
        return list(texts)


def make_toolbox(store):
    seed = 200 if store.startswith("별빛돈가스") else 14
    return ReviewToolbox(
        store,
        TODAY,
        REPO,
        CAT,
        writer=FakeWriter(),
        reply_store=MockReplyStore(today=TODAY, seed_days=seed),
        settings_store=ReportSettingsStore(None),
        now=NOW,
    )


# 규칙이 아직 못 잡는 표현 (벤치마크가 찾은 빈틈. 실제 HCX가 도구를 고르면 통과할 수 있다)
KNOWN_GAPS: dict[str, str] = {
    # holdout3 실패 중 규칙으로 도는 문항. 규칙 버전은 비교 기준으로 두고 고치지 않는다 (2026-11-08)
    "H27": "'위험한'이 부정 대상 표현(TARGET_NEG)에 없음",
    # holdout4 (2026-11-08). 규칙만으로는 안 되고 실제 HCX가 맡는 문항
    "H30": "'너무 길어, 줄여줘'가 수정 표현(EDIT_HINTS)에 없음",
    "H34": "약속('떡 하나 더 드린다고')을 owner_promise로 못 잡음",
    "I31": "'어느 정도 주기로'가 주기 추천 표현(SUGGEST)에 없음",
}
RULE_CASES = [
    pytest.param(c, id=c["id"], marks=pytest.mark.xfail(reason=KNOWN_GAPS[c["id"]], strict=True))
    if c["id"] in KNOWN_GAPS
    else pytest.param(c, id=c["id"])
    for c in GOLD
    if c["split"] == "dev" and (c["id"][0] in "GHI" or c["id"] in ("F02", "F03"))
]  # holdout은 실제 HCX 벤치마크로만 잰다 (빈틈을 찾는 용도라 실패가 정상일 수 있음)


@pytest.mark.parametrize("case", RULE_CASES)
def test_rule_routed_cases_pass_offline(case):
    rows = run_case(case, TextOnlyHCX(), "HCX-005", make_toolbox, run_turn, system_prompt)
    fails = [(r["turn"], r["detail"]) for r in rows if not r["pass"]]
    assert not fails, fails


def test_summary_counts():
    rows = run_case(GOLD[0], TextOnlyHCX(), "HCX-005", make_toolbox, run_turn, system_prompt)
    s = summarize(rows)
    assert s["turns"] == 1 and s["cases"] == 1 and "by_category" in s
