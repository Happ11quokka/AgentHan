"""맥락 칩 규칙 표: 이번 턴에 한 일 + 가게 데이터 → 칩."""

from datetime import date

import pytest

from agenthan.agent import actions, suggest
from agenthan.agent.suggest import Facts, done_in, facts, merge

TODAY = date(2026, 11, 21)
SHOP = {
    "shop": {"keywords": ["러시안 베이커리", "메도빅"]},
    "coupons": [],
    "reviews": [
        {"review_id": "r01", "rating": 5, "has_owner_reply": False},
        {"review_id": "r02", "rating": 2, "has_owner_reply": False},
        {"review_id": "r03", "rating": 1, "has_owner_reply": True},
        {"review_id": "r04", "rating": None, "has_owner_reply": False},
    ],
}


def labels(chips):
    return [c["label"] for c in chips]


def test_every_rule_uses_a_registered_button_and_fixed_short_label():
    for rule in suggest.RULES:
        assert rule.action_id in actions.ACTIONS
        assert rule.after in {"review", "statistics", "coupon"}
        assert 0 < len(rule.label) <= 12


def test_facts_read_keywords_live_coupons_low_unanswered_and_report():
    shop = {
        **SHOP,
        "coupons": [
            {"title": "지난 쿠폰", "end_date": "2026-11-01"},
            {"title": "주말 쿠폰", "end_date": "2026-11-30"},
        ],
    }
    f = facts(shop, {"enabled": True}, TODAY)
    assert f.keywords == ("러시안 베이커리", "메도빅")
    assert f.live_coupons == ("주말 쿠폰",)
    assert f.low_unanswered == ("r02",)
    assert f.report_enabled
    assert not facts(SHOP, None, TODAY).report_enabled


def test_each_context_gets_its_own_one_to_three_chips():
    f = facts(SHOP, None, TODAY)
    review, stats, coupon = (merge([], [d], f) for d in ("review", "statistics", "coupon"))
    for chips in (review, stats, coupon):
        assert 1 <= len(chips) <= 3
    assert labels(review) == ["매일 리뷰 요약 받기", "불만 리뷰 답글 초안", "이번 주 통계 보기"]
    assert review[1]["args"] == {"review_id": "r02"}
    assert review[0] == {
        "action_id": "chat.say",
        "label": "매일 리뷰 요약 받기",
        "args": {"text": "매일 리뷰 요약해서 보내 줘"},
    }
    assert labels(stats) == ["대표 키워드 추가", "쿠폰 만들기", "리뷰 요약 보기"]
    assert labels(coupon)[0] == "이번 주 통계 보기"
    assert len({tuple(labels(c)) for c in (review, stats, coupon)}) == 3


def test_skip_conditions_drop_what_is_already_set_up():
    full = Facts(
        keywords=("a", "b", "c", "d", "e"),
        live_coupons=("주말 쿠폰",),
        low_unanswered=(),
        report_enabled=True,
    )
    assert labels(merge([], ["review"], full)) == ["이번 주 통계 보기"]
    assert labels(merge([], ["statistics"], full)) == ["리뷰 요약 보기"]
    assert "매주 리뷰 요약 받기" not in labels(merge([], ["coupon"], full))


def test_merge_keeps_existing_chips_without_duplicates():
    existing = [
        {"action_id": "review.draft_reply", "label": "불만 리뷰 답글 초안", "args": {}},
        {"action_id": "coupon.create", "label": "쿠폰 검토", "args": {}},
    ]
    chips = merge(existing, ["review", "statistics"], facts(SHOP, None, TODAY))
    assert chips[:2] == existing
    ids = [c["action_id"] for c in chips]
    assert ids.count("review.draft_reply") == 1 and ids.count("coupon.create") == 1
    assert len(chips) - len(existing) <= suggest.MAX_CHIPS


@pytest.mark.parametrize("done", [["review"], ["statistics"], ["coupon"]])
def test_no_context_chips_on_an_approval_turn(done):
    existing = [
        {"action_id": "task.approve", "label": "저장", "args": {"task_id": "t1", "version": 1}},
        {"action_id": "task.cancel", "label": "취소", "args": {"task_id": "t1"}},
    ]
    assert merge(existing, done, facts(SHOP, None, TODAY)) == existing


def test_nothing_done_means_no_context_chips():
    assert merge([], [], facts(SHOP, None, TODAY)) == []


def test_done_reads_buttons_and_trace_results():
    assert done_in({}, "review.summary") == ["review"]
    assert done_in({}, "stats.day") == ["statistics"]
    trace = {
        "calls": [
            {"name": "get_statistics", "arguments": {}},
            {"name": "ask_review_agent", "arguments": {}},
            {"name": "create_coupon", "arguments": {}, "approved": True},
        ],
        "results": [
            {"ok": True, "action": "read"},
            {"ok": True, "text": "리뷰"},
            {"ok": True},
        ],
    }
    assert done_in(trace) == ["statistics", "review", "coupon"]
    # 통계 단일 기능 위임(호출 없음)·기존 승인 경로의 실제 저장
    assert done_in({"feature": "statistics", "calls": [], "results": []}) == ["statistics"]
    saved = {
        "calls": [{"name": "create_coupon", "arguments": "{}"}],
        "results": [{"ok": True, "action": "saved_not_published"}],
    }
    assert done_in(saved) == ["coupon"]
    # 미리보기만 만든 쿠폰·실패한 조회·초안 승인 대기는 한 일이 아니다.
    pending = {
        "calls": [
            {"name": "create_coupon", "arguments": {}},
            {"name": "get_statistics", "arguments": {}},
            {"name": "ask_review_agent", "arguments": {}},
        ],
        "results": [{"ok": True}, {"ok": False}, {"ok": True, "action": "pending_approval"}],
    }
    assert done_in(pending) == []


def test_report_chips_say_what_the_report_parser_understands():
    from agenthan.agent.review.report_parse import parse_report_request

    says = [r for r in suggest.RULES if r.action_id == "chat.say"]
    assert says
    for rule in says:
        kind, args = parse_report_request(rule.args(suggest.Facts())["text"])
        assert kind == "set" and args["frequency"] in ("daily", "weekly")
