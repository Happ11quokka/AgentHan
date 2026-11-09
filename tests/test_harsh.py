"""상처 되는 표현 거르기 (PRD A-2). 거친 리뷰는 지어낸 더미 가게 '해오름분식'(fixtures)."""

import json
from datetime import date
from pathlib import Path

import pytest

from agenthan.agent.review import MenuCatalog, SnapshotRepo
from agenthan.agent.review.agent import ReviewToolbox, run_turn, system_prompt
from agenthan.agent.review.analyze import analyze_reviews
from agenthan.agent.review.harsh import HIDDEN_ALL, harsh_of, soften_quote
from agenthan.agent.review.replies import MockReplyStore, get_unanswered_reviews

ROOT = Path(__file__).resolve().parents[1]
REPO = SnapshotRepo(ROOT / "fixtures" / "review_snapshot.jsonl")
CAT = MenuCatalog.load(ROOT / "fixtures" / "review_menus.json")
TODAY = date(2026, 11, 7)
RAW = [r["text"] for r in REPO.get_reviews("해오름분식") if harsh_of(r["text"])]
BAD_WORDS = ["존나", "씨발", "인성 쓰레기", "망해라", "싸가지", "미친놈", "장사 접어라"]


@pytest.mark.parametrize(
    "text,kept",
    [
        ("떡볶이 존나 짜요. 국물이 다 졸아서 나왔어요", "떡볶이 짜요. 국물이 다 졸아서 나왔어요"),
        ("사장 인성 쓰레기네요. 주문이 잘못 들어갔는데 화를 냄", "주문이 잘못 들어갔는데 화를 냄"),
        ("씨발 이게 김밥이냐. 망해라", "이게 김밥이냐."),
    ],
)
def test_keeps_complaint_drops_abuse(text, kept):
    quote, softened = soften_quote(text)
    assert softened and quote.startswith(kept) and "가림" in quote


def test_all_abuse_is_replaced_with_gist():
    quote, softened = soften_quote(
        "직원 싸가지 없고 미친놈처럼 소리 지름 장사 접어라", aspect="서비스"
    )
    assert softened and quote == f"[{HIDDEN_ALL} · 서비스]"


@pytest.mark.parametrize(
    "text",
    [
        "떡볶이 미친 맛이에요 또 올게요",  # 칭찬 속어
        "씹는 맛이 좋아요",
        "시발점이 된 메뉴",
        "사장님 인성 최고예요",
        "개맛있어요",
        "직원이 너무 불친절해서 기분 나빴어요",  # 거친 표현 없는 불만은 그대로
        "바퀴벌레가 나왔어요. 보건소에 신고할게요",  # 위생 고위험은 HIGH_RISK가 따로 본다
    ],
)
def test_no_false_positive(text):
    assert soften_quote(text) == (text, False)


def _no_raw(s: str):
    for w in BAD_WORDS:
        assert w not in s, w


def test_analyze_examples_and_hcx_input_are_softened():
    class LLM:  # 요약 HCX에 넘어가는 대표 리뷰도 거른 문장이어야 한다
        name = "fake"

        def insights(self, summary):
            self.summary = summary
            return ["요약"]

    llm = LLM()
    res = analyze_reviews(
        store_id="해오름분식", today=TODAY, repo=REPO, catalog=CAT, period="7d", llm=llm
    )
    _no_raw(json.dumps(res, ensure_ascii=False))
    _no_raw(json.dumps(llm.summary, ensure_ascii=False))
    assert any(e["softened"] for e in res["examples"])
    assert res["sentiment"]["negative"] >= 3  # 불만 집계는 그대로


def test_unanswered_list_is_softened():
    store = MockReplyStore(today=TODAY, seed_days=14)
    from agenthan.agent.review.replies import ReplyAwareRepo

    res = get_unanswered_reviews(
        store_id="해오름분식", today=TODAY, repo=ReplyAwareRepo(REPO, store)
    )
    _no_raw(json.dumps(res, ensure_ascii=False))
    assert any(r["softened"] for r in res["reviews"])


class FakeHCX:
    """1차: analyze_reviews 호출 / 2차: 도구 결과를 받아 답변."""

    def __init__(self):
        self.calls = []

    def chat(self, model, messages, **kw):
        self.calls.append(messages)
        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": "불만이 4건 있어요."}
        call = {
            "id": "c1",
            "type": "function",
            "function": {"name": "analyze_reviews", "arguments": {"period": "7d"}},
        }
        return {"role": "assistant", "content": "", "toolCalls": [call]}


def test_turn_hides_then_reveals_original():
    tb = ReviewToolbox("해오름분식", TODAY, REPO, CAT)
    fake = FakeHCX()
    msgs = [
        {"role": "system", "content": system_prompt("해오름분식 본점", TODAY)},
        {"role": "user", "content": "요즘 리뷰 어때?"},
    ]
    out = run_turn(fake, "HCX-005", msgs, tb)
    _no_raw(out)
    _no_raw(json.dumps(fake.calls, ensure_ascii=False))  # 모델이 본 도구 결과에도 원문 없음
    assert "원문 보여줘" in out and tb.hidden_reviews

    n = len(fake.calls)
    msgs.append({"role": "user", "content": "가린 원문 보여줘"})
    out2 = run_turn(fake, "HCX-005", msgs, tb)
    assert len(fake.calls) == n  # 모델을 부르지 않는다
    assert "가렸던 리뷰 원문" in out2
    assert any(t in out2 for t in RAW)


def test_original_ask_without_hidden_goes_normal_path():
    tb = ReviewToolbox("달빛모찌", TODAY, REPO, CAT)
    assert not tb.wants_original("원문 보여줘")
