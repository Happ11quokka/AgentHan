"""의도 분류 라우터 (실험, router="intent"). 가짜 HCX로 흐름만 확인한다 (키·네트워크 불필요)."""

import json

from agenthan.agent.review.agent import HOLD_MSG, run_turn
from agenthan.agent.review.intent import SCHEMA, classify, plan
from test_bench import make_toolbox as _make


def make_toolbox(store="달빛모찌"):
    tb = _make(store)
    tb.router = "intent"
    return tb


class FakeHCX:
    """HCX-007(JSON 스키마) 호출은 정해 둔 의도를, 나머지는 도구 없이 짧게 답한다."""

    def __init__(self, intent, target="none", negative_first=False, tool_call=None):
        self.intent = {"intent": intent, "target": target, "negative_first": negative_first}
        self.tool_call = tool_call
        self.calls = []

    def chat(self, model, messages, **kw):
        self.calls.append({"model": model, **kw})
        if "responseFormat" in kw:
            return {"role": "assistant", "content": json.dumps(self.intent)}
        if self.tool_call and not any(m.get("role") == "tool" for m in messages):
            name, args = self.tool_call
            fn = {"name": name, "arguments": args}
            return {
                "role": "assistant",
                "content": "",
                "toolCalls": [{"id": "c1", "type": "function", "function": fn}],
            }
        return {"role": "assistant", "content": "알겠습니다."}


def turn(tb, hcx, say):
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": say}]
    calls = []
    orig = tb.dispatch
    tb.dispatch = lambda n, a, utterance=None: calls.append(n) or orig(n, a, utterance=utterance)
    out = run_turn(hcx, "HCX-005", msgs, tb)
    tb.dispatch = orig
    return out, calls


def test_classifier_uses_hcx007_json_schema():
    hcx = FakeHCX("analyze")
    assert classify(hcx, "리뷰 어때?", make_toolbox())["intent"] == "analyze"
    c = hcx.calls[0]
    assert c["model"] == "HCX-007" and c["responseFormat"]["schema"] == SCHEMA


def test_plan_maps_intents_to_tools():
    tb = make_toolbox()
    p = lambda i, **kw: plan({"intent": i, "target": "none", **kw}, "x", tb)  # noqa: E731
    assert p("list_unanswered", negative_first=True)["routed"] == (
        "get_unanswered_reviews",
        {"priority": "negative_first"},
    )
    assert p("draft_reply", target="negative")["routed"] == (
        "draft_review_reply",
        {"target": "negative"},
    )
    assert p("report_now")["routed"] == ("run_review_report", {})
    assert p("analyze")["tool_choice"]["function"]["name"] == "analyze_reviews"
    assert p("other") == {"specs": [], "tool_choice": None}
    assert p("hold")["specs"] == []  # 초안이 없으면 미룰 것이 없다 → 일반 대화
    assert plan({"intent": None}, "x", tb) is None  # 분류 실패 → 규칙 라우터


def test_unrelated_question_gets_no_tools_and_no_nudge():  # holdout1 J11 같은 경우
    tb = make_toolbox()
    hcx = FakeHCX("other")
    out, calls = turn(tb, hcx, "배달앱 수수료 얼마야?")
    assert calls == [] and out == "알겠습니다."
    assert "tools" not in hcx.calls[1] or not hcx.calls[1]["tools"]


def test_analyze_forces_only_analyze_tool():
    tb = make_toolbox()
    hcx = FakeHCX("analyze", tool_call=("analyze_reviews", {"topic": "웨이팅"}))
    _, calls = turn(tb, hcx, "기다리는 시간 길대?")
    chat = hcx.calls[1]
    assert calls == ["analyze_reviews"]
    assert [s["function"]["name"] for s in chat["tools"]] == ["analyze_reviews"]
    assert chat["tool_choice"]["function"]["name"] == "analyze_reviews"
    assert "tool_choice" not in hcx.calls[2]  # 도구 결과 뒤에는 지정하지 않는다


def test_reply_flow_and_hold():
    tb = make_toolbox()
    _, calls = turn(tb, FakeHCX("list_unanswered"), "답글 필요한 리뷰 있어?")
    assert calls == ["get_unanswered_reviews"]
    _, calls = turn(tb, FakeHCX("draft_reply", target="negative"), "나쁜 리뷰에 답글 부탁")
    assert calls == ["draft_review_reply"] and tb.drafts
    out, calls = turn(tb, FakeHCX("hold"), "조금 있다가 할게")
    assert calls == [] and out == HOLD_MSG and tb.drafts


def test_post_still_needs_confirmation_words():  # 안전장치는 규칙 그대로
    tb = make_toolbox()
    turn(tb, FakeHCX("list_unanswered"), "답글 안 단 리뷰")
    turn(tb, FakeHCX("draft_reply", target="negative"), "안 좋은 리뷰에 답글 써줘")
    r = tb.dispatch("post_review_reply", {}, utterance="음")
    assert r.get("error") == "not_confirmed" and tb.drafts


def test_classifier_failure_falls_back_to_rules():
    class Broken(FakeHCX):
        def chat(self, model, messages, **kw):
            if "responseFormat" in kw:
                return {"role": "assistant", "content": "의도는 분석입니다"}  # JSON 아님
            return super().chat(model, messages, **kw)

    tb = make_toolbox()
    _, calls = turn(tb, Broken("x"), "답글 안 단 리뷰 있어?")
    assert calls == ["get_unanswered_reviews"]  # 규칙 라우터가 처리
    assert tb.last_intent["intent"] is None


# ---- 혼합 라우터 (router="hybrid") ----


def hybrid_tb():
    tb = _make("달빛모찌")
    tb.router = "hybrid"
    return tb


def test_hybrid_rules_first_without_classifier():
    tb = hybrid_tb()
    hcx = FakeHCX("other")
    _, calls = turn(tb, hcx, "답글 안 단 리뷰 있어?")
    assert calls == ["get_unanswered_reviews"] and hcx.calls == []  # 규칙이 잡으면 분류 안 함


def test_hybrid_other_without_review_hint_gets_no_tools():  # holdout4 J37 같은 경우
    tb = hybrid_tb()
    hcx = FakeHCX("other")
    out, calls = turn(tb, hcx, "오늘 재료 뭐 사야 돼?")
    assert calls == [] and out == "알겠습니다." and not hcx.calls[1].get("tools")


def test_hybrid_other_with_review_hint_keeps_rules_flow():  # 분류가 틀려도 리뷰 질문이면 도구
    tb = hybrid_tb()
    hcx = FakeHCX("other")
    _, calls = turn(tb, hcx, "딸기크림떡 어때?")  # 메뉴 단어 → 규칙 버전 흐름(재요청 → 시스템 실행)
    assert calls == ["analyze_reviews"]


def test_hybrid_report_but_not_analysis_format():
    tb = hybrid_tb()
    _, calls = turn(tb, FakeHCX("report_now"), "보고서 당장 확인하고 싶어")
    assert calls in (["run_review_report"], ["set_review_report"])  # 규칙이 먼저 잡을 수도 있다
    tb = hybrid_tb()
    _, calls = turn(
        tb,
        FakeHCX("report_now", tool_call=("analyze_reviews", {"format": "report"})),
        "이번 달 리뷰를 보고서로 정리해줘",
    )
    assert calls == ["analyze_reviews"]  # 분석 + 보고서 형식은 분석으로
