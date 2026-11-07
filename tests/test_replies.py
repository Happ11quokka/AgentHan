"""답글 흐름 테스트 (API 키 불필요): 미답글 → 초안 → 수정 → 확정 없이 등록 시도 → 등록."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

from review_tools import MenuCatalog, SnapshotRepo
from review_tools.agent import ReviewToolbox, run_turn
from review_tools.replies import MockReplyStore, check_reply

ROOT = Path(__file__).resolve().parents[1]

TODAY = date(2026, 11, 7)
REPO = SnapshotRepo(ROOT / "data" / "snapshots" / "test_baseline.jsonl")
CAT = MenuCatalog.load(ROOT / "data" / "menus_sample.json")


class FakeWriter:
    def __init__(self):
        self.calls = []

    def write_reply(
        self,
        text,
        sentiment,
        edit_request=None,
        previous=None,
        route=None,
        profile=None,
        recent=None,
    ):
        self.calls.append(
            {
                "sentiment": sentiment,
                "edit": edit_request,
                "route": route,
                "profile": profile,
                "recent": recent,
            }
        )
        if edit_request:
            return previous + " 다음엔 맛을 꼭 확인하겠습니다."
        return f"{route} 경로로 쓴 답글입니다. 리뷰 감사합니다."

    def translate(self, texts, lang, glossary=None):
        return [f"<{lang}> {t}" for t in texts]


def toolbox(lang="ko"):
    return ReviewToolbox(
        "홍만당",
        TODAY,
        REPO,
        CAT,
        writer=FakeWriter(),
        reply_store=MockReplyStore(today=TODAY),
        owner_lang=lang,
    )


def test_unanswered_seed_and_priority():
    tb = toolbox()
    r = tb.dispatch("get_unanswered_reviews", {}, utterance="답글 안 단 리뷰 있어?")
    # 데모 가정: 최근 14일(10-24 이후) 리뷰만 답글 없음 → 홍만당 10-25, 10-29, 10-30
    assert r["total"] == 3 and [x["visit_date"] for x in r["reviews"]] == [
        "2026-10-30",
        "2026-10-29",
        "2026-10-25",
    ]
    n = tb.dispatch(
        "get_unanswered_reviews", {"priority": "negative_first"}, utterance="급한 거부터"
    )
    assert n["reviews"][0]["visit_date"] == "2026-10-25"  # '맛이 없어요' 리뷰가 맨 앞


def test_draft_edit_confirm_post():
    tb = toolbox("ru")
    tb.dispatch("get_unanswered_reviews", {"priority": "negative_first"}, utterance="급한 거부터")
    d = tb.dispatch(
        "draft_review_reply", {"target": "negative"}, utterance="안 좋은 리뷰에 답글 써줘"
    )
    dr = d["drafts"][0]
    assert (
        dr["review_id"] == "r00732"
        and "경로로 쓴 답글" in dr["reply_ko"]
        and dr["reply_translated"].startswith("<ru>")
    )
    assert dr["checks"]["passed"] and d["posted"] is False
    e = tb.dispatch(
        "draft_review_reply",
        {"edit_request": "Скажи, что проверим вкус"},
        utterance="Скажи, что проверим вкус",
    )
    assert e["drafts"][0]["reply_ko"].endswith("확인하겠습니다.") and e["drafts"][0]["revised"]
    nc = tb.dispatch("post_review_reply", {}, utterance="음 잠깐만")
    assert nc["error"] == "not_confirmed"  # 확정 표현 없으면 등록 안 함
    ok = tb.dispatch("post_review_reply", {}, utterance="이대로 올려줘")
    assert ok["posted_count"] == 1 and not tb.drafts
    after = tb.dispatch("get_unanswered_reviews", {}, utterance="답글 안 단 리뷰")
    assert after["total"] == 2  # 등록한 리뷰는 목록에서 빠짐


def test_check_reply_policy():
    assert check_reply("홍*동님 감사합니다")["issues"] == ["리뷰어 이름(마스킹 포함)"]
    assert "리뷰·별점 수정이나 삭제 요구" in check_reply("다음엔 별점 수정 부탁드려요")["issues"]
    assert "비방·감정적 표현" in check_reply("거짓말 하지 마세요")["issues"]
    assert check_reply("방문해 주셔서 감사합니다. 맛을 개선하겠습니다.")["passed"]


def test_failed_check_blocks_posting():
    tb = toolbox()
    tb.dispatch("get_unanswered_reviews", {}, utterance="답글")
    tb.dispatch("draft_review_reply", {}, utterance="답글 써줘")
    rid = next(iter(tb.drafts))
    tb.drafts[rid]["reply_ko"] = "010-1234-5678로 연락주세요"
    tb.drafts[rid]["checks"] = check_reply(tb.drafts[rid]["reply_ko"])
    r = tb.dispatch("post_review_reply", {}, utterance="이대로 올려줘")
    assert r["posted_count"] == 0 and r["results"][0]["issues"] == ["전화번호 등 개인정보"]


class ReplyFlowHCX:
    """도구를 차례로 부르는 가짜 HCX: 미답글 → 초안. 최종 답변에 초안을 베껴 쓰지 않는다."""

    def __init__(self, tool, args):
        self.tool, self.args, self.calls = tool, args, []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if kw["messages"][-1]["role"] == "tool":
            return NS(
                choices=[
                    NS(message=NS(content="초안을 만들었어요. 이대로 올릴까요?", tool_calls=None))
                ]
            )
        call = NS(id="c1", function=NS(name=self.tool, arguments=json.dumps(self.args)))
        return NS(choices=[NS(message=NS(content="", tool_calls=[call]))])


def test_agent_attaches_draft_block():
    tb = toolbox()
    tb.dispatch("get_unanswered_reviews", {}, utterance="답글")
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "첫 번째 리뷰에 답글 써줘"},
    ]
    out = run_turn(ReplyFlowHCX("draft_review_reply", {}), "HCX-005", msgs, tb)
    assert (
        out.startswith("초안을 만들었어요") and "**답글 초안**" in out and "리뷰 감사합니다" in out
    )  # 안내·원문 모두 시스템


class SilentHCX:
    """도구를 전혀 부르지 않고 그럴듯하게 답하는 HCX (3·4번째 질문에서 실제 발생)."""

    def __init__(self):
        self.chat = NS(
            completions=NS(
                create=lambda **kw: NS(
                    choices=[
                        NS(
                            message=NS(
                                content=(
                                    "알겠습니다. 지금 바로 해당 리뷰에 대한 답글을 등록하겠습니다."
                                ),
                                tool_calls=None,
                            )
                        )
                    ]
                )
            )
        )


def test_edit_and_post_run_even_if_model_skips_tools():
    tb = toolbox()
    tb.dispatch("get_unanswered_reviews", {"priority": "negative_first"}, utterance="답글")
    tb.dispatch("draft_review_reply", {"target": "negative"}, utterance="안 좋은 리뷰에 답글 써줘")
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "사과를 더 정중하게, 다음엔 맛을 꼭 확인하겠다고 해줘"},
    ]
    out = run_turn(SilentHCX(), "HCX-005", msgs, tb)
    assert (
        out.startswith("말씀하신 대로 고쳤어요") and next(iter(tb.drafts.values()))["revised"]
    )  # 시스템이 수정 실행
    msgs.append({"role": "user", "content": "이대로 올려줘"})
    out = run_turn(SilentHCX(), "HCX-005", msgs, tb)
    assert (
        out.startswith("답글 1건을 등록했어요") and "r00732" in tb.reply_store.posted
    )  # 실제로 등록됨


def test_router_handles_reply_flow_without_llm():
    tb = toolbox()
    assert tb.route("답글 안 단 리뷰 있어?") == ("get_unanswered_reviews", {})
    assert tb.route("안 좋은 리뷰에 답글 써줘") == ("draft_review_reply", {"target": "negative"})
    assert tb.route("Reply to all of them") == ("draft_review_reply", {"target": "all_listed"})
    assert tb.route("Напиши ответ на плохой отзыв")[1] == {"target": "negative"}
    assert tb.route("이대로 올려줘") == (
        "post_review_reply",
        {},
    )  # 초안 없어도 등록 경로 → '초안 없음'
    assert tb.route("요즘 손님들 불만 뭐야?") is None  # 분석 질문은 LLM이 고름
    # 실제 대화 그대로: LLM이 도구를 전혀 안 불러도 끝까지 맞게 흘러간다
    msgs = [{"role": "system", "content": "s"}]
    for q, expect in [
        ("답글 안 단 리뷰 있어?", "답글 안 단 리뷰는 3건"),
        ("안 좋은 리뷰에 답글 써줘", "초안을 만들었어요"),
        ("사과를 더 정중하게, 다음엔 맛을 꼭 확인하겠다고 해줘", "말씀하신 대로 고쳤어요"),
        ("이대로 올려줘", "답글 1건을 등록했어요"),
    ]:
        msgs.append({"role": "user", "content": q})
        assert run_turn(SilentHCX(), "HCX-005", msgs, tb).startswith(expect), q
    assert "r00732" in tb.reply_store.posted


def test_rereview_request_is_blocked():
    assert (
        "리뷰·별점 수정이나 삭제 요구"
        in check_reply("저희 매장의 다른 제품을 드셔보시고 다시 한번 평가해주실 수 있으실까요?")[
            "issues"
        ]
    )


def test_fixed_messages():
    from review_tools.agent import reply_message

    assert reply_message(
        "get_unanswered_reviews", {"total": 3, "negative_count": 1}, "ko"
    ).startswith("답글 안 단 리뷰는 3건이에요, 그중")
    assert "올릴까요" not in reply_message(
        "get_unanswered_reviews", {"total": 3, "negative_count": 1}, "ko"
    )
    assert reply_message("post_review_reply", {"error": "not_confirmed"}, "en").startswith(
        "Shall I post"
    )
    assert reply_message("analyze_reviews", {"review_count": 1}, "ko") is None  # 분석은 모델 답변


def test_high_risk_detection_avoids_false_positives():
    from review_tools.replies import risk_of

    assert risk_of("식탁 위에서 바퀴벌레 기어다녀요", "negative")["words"] == ["바퀴", "벌레"]
    assert risk_of("오코노미야끼에서 머리카락 나옴. 환불해줌", "negative")["words"] == [
        "머리카락",
        "환불",
    ]
    assert risk_of("고소하고 맛있어요", "positive") is None  # '고소하다'는 맛 표현
    assert (
        risk_of("크림소스가 고소하니 좋은데 양이 적어요", "mixed") is None
    )  # 부정이어도 '고소한 맛'은 아님
    assert risk_of("이상한맛 남 맛이 없어요", "negative") is None  # '이상한'의 '상한' 오탐 방지
    assert (
        risk_of("알레르기 메뉴 안내가 좋았어요", "positive") is None
    )  # 실습이 지적한 오탐: 감정으로 거름
    assert risk_of("고소하겠습니다", "negative")["words"] == ["고소하겠"]


def test_escalate_route_for_cockroach_review():
    tb = ReviewToolbox(
        "원조남산왕돈까스",
        TODAY,
        REPO,
        CAT,
        writer=FakeWriter(),
        reply_store=MockReplyStore(today=TODAY, seed_days=200),
        owner_lang="ko",
    )
    lst = tb.dispatch(
        "get_unanswered_reviews", {"priority": "negative_first"}, utterance="급한 거부터"
    )
    assert lst["high_risk_count"] >= 1 and lst["reviews"][0]["risk"]  # 고위험이 맨 앞
    d = tb.dispatch(
        "draft_review_reply", {"target": "negative"}, utterance="안 좋은 리뷰에 답글 써줘"
    )["drafts"][0]
    assert d["route"] == "escalate" and d["owner_check"] and "바퀴" in d["risk"]["words"]
    call = tb.writer.calls[-1]
    assert call["route"] == "escalate" and call["profile"]["name"].startswith("원조남산왕돈까스")
    from review_tools.agent import reply_message

    msg = reply_message("draft_review_reply", {"drafts": [d]}, "ko")
    assert msg.startswith("위생·안전과 관련된 리뷰(감지: 바퀴, 벌레")  # 위생상태도 함께 감지됨


def test_new_checks():
    assert "손님 탓 표현" in check_reply("오해하신 것 같습니다")["issues"]
    assert any(
        "보상" in i for i in check_reply("다음 방문 때 음료를 무료로 드리겠습니다")["issues"]
    )
    assert "채우지 않은 자리표시" in check_reply("[연락처]로 연락 주세요")["issues"]
    long = "감사합니다. " * 70
    assert check_reply(long)["passed"] and check_reply(long)["warnings"]  # 길이는 경고만
    rep = check_reply(
        "방문해 주셔서 감사합니다. 또 오세요.",
        recent=["방문해 주셔서 감사합니다. 맛있게 드셨다니 기뻐요."],
    )
    assert rep["warnings"] == ["최근 답글과 같은 첫 문장"]


def test_batch_drafts_get_recent_context():
    tb = toolbox()
    tb.dispatch("get_unanswered_reviews", {}, utterance="답글")
    tb.dispatch("draft_review_reply", {"target": "all_listed"}, utterance="전부 써줘")
    recents = [c["recent"] for c in tb.writer.calls if not c["edit"]]
    assert recents[0] == [] and len(recents[-1]) >= 1  # 앞 초안을 다음 초안이 참고


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
