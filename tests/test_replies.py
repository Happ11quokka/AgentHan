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
        instruction=None,
        glossary=None,
    ):
        self.calls.append(
            {
                "sentiment": sentiment,
                "edit": edit_request,
                "route": route,
                "profile": profile,
                "recent": recent,
                "instruction": instruction,
                "glossary": glossary,
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


def test_english_and_russian_list_questions_use_router():
    # 'haven't replied to'가 규칙에 안 걸려 LLM이 목록과 초안을 한 번에 부름 (2026-11-07 실측)
    tb = toolbox("en")
    assert tb.route("Are there reviews I haven't replied to?") == ("get_unanswered_reviews", {})
    assert tb.route("Which reviews have not been replied to yet?")[0] == "get_unanswered_reviews"
    assert tb.route("Есть отзывы, на которые я не ответил?")[0] == "get_unanswered_reviews"
    assert tb.route("Reply to the negative one") == ("draft_review_reply", {"target": "negative"})


class ListThenDraftHCX:
    """목록만 물었는데 미답글 조회와 초안 작성을 한 응답에서 같이 부르는 HCX (2026-11-07 실측)."""

    def __init__(self):
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        if kw["messages"][-1]["role"] == "tool":
            return NS(choices=[NS(message=NS(content="Here are the drafts.", tool_calls=None))])
        calls = [
            NS(id="c1", function=NS(name="get_unanswered_reviews", arguments="{}")),
            NS(
                id="c2",
                function=NS(name="draft_review_reply", arguments='{"target": "all_listed"}'),
            ),
        ]
        return NS(choices=[NS(message=NS(content="", tool_calls=calls))])


def test_no_draft_in_same_turn_as_list_question():
    tb = toolbox("en")
    q = "Anything new from customers that needs my answer?"  # 규칙에 안 걸리는 표현
    assert tb.route(q) is None
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": q}]
    out = run_turn(ListThenDraftHCX(), "HCX-005", msgs, tb, owner_lang="en")
    assert out.startswith("There are 3 reviews without a reply")  # 목록 안내로 끝난다
    assert "Reply draft" not in out and tb.drafts == {}  # 초안은 만들지 않는다
    # 다음 턴에 쓰라고 하면 그때 만든다
    assert tb.dispatch(
        "draft_review_reply", {"target": "negative"}, utterance="Reply to the bad one"
    )["drafts"]


def test_cause_speculation_is_blocked():
    real = (
        "딸기모찌에서 화장품 맛이 느껴졌다는 부분은 특히 안타깝습니다. 이는 원재료 중 일부가 "
        "잘못 사용되었거나 보관 과정에서 문제가 발생했을 가능성이 큽니다."
    )  # 2026-11-07 실제 초안
    assert "확인 전 원인 추정·단정" in check_reply(real)["issues"]
    assert "확인 전 원인 추정·단정" in check_reply("직원 실수로 늦어진 것 같습니다. ")["issues"]
    for ok in [
        "원인을 파악하여 재발 방지를 위해 노력하겠습니다.",
        "현재 사실관계를 확인 중이며 철저히 조사하겠습니다.",
        "재료의 선정 및 관리 과정을 더욱 꼼꼼히 살피도록 하겠습니다.",
    ]:
        assert check_reply(ok)["passed"], ok


# ------------------------------------------------ 제안 구조: 지시 원문·용어집·체크리스트·약속 표시
PROMISE_REPLY = (
    "불편을 드려 정말 죄송합니다. 딸기모찌 맛은 다시 꼼꼼히 확인하겠습니다. "
    "다음 주문 때 음료를 서비스로 드릴게요."
)


class PromiseWriter(FakeWriter):
    def write_reply(self, text, sentiment, **kw):
        super().write_reply(text, sentiment, **kw)
        return PROMISE_REPLY


def test_owner_instruction_reaches_first_draft():
    tb = ReviewToolbox(
        "홍만당",
        TODAY,
        REPO,
        CAT,
        writer=FakeWriter(),
        reply_store=MockReplyStore(today=TODAY),
        owner_lang="en",
    )
    q = "Reply to the bad one and say we'll give a free drink next time"
    tb.dispatch("draft_review_reply", {"target": "negative"}, utterance=q)
    call = tb.writer.calls[0]
    assert call["instruction"] == q  # 번역하지 않고 원어 그대로
    assert call["glossary"]["딸기모찌"] == "strawberry mochi"  # 생성 프롬프트에 용어집


def test_owner_requested_promise_is_warning_not_block():
    assert check_reply(PROMISE_REPLY)["issues"] == ["보상·환불 약속 (실제로 줄 수 있는지 확인)"]
    chk = check_reply(PROMISE_REPLY, owner_promise=True)
    assert chk["passed"] and chk["warnings"] == ["약속 포함 (사장님 지시, 게시 전 확인)"]
    # 지시에 약속이 있으면 등록 가능 (확정 후), 없으면 막힌다
    for q, ok in [("lần sau tặng nước uống", True), ("Reply to the bad one", False)]:
        tb = ReviewToolbox(
            "홍만당",
            TODAY,
            REPO,
            CAT,
            writer=PromiseWriter(),
            reply_store=MockReplyStore(today=TODAY),
            owner_lang="vi",
        )
        d = tb.dispatch("draft_review_reply", {"target": "negative"}, utterance=q)["drafts"][0]
        assert d["checks"]["passed"] is ok and d["owner_promise"] is ok, q


def test_checklist_in_owner_language():
    from review_tools.agent import render_block

    cat = MenuCatalog({"홍만당": [{"name": "딸기모찌", "aliases": ["Mochi dâu"]}]})
    tb = ReviewToolbox(
        "홍만당",
        TODAY,
        REPO,
        cat,
        writer=PromiseWriter(),
        reply_store=MockReplyStore(today=TODAY),
        owner_lang="vi",
    )
    res = tb.dispatch("draft_review_reply", {"target": "negative"}, utterance="lần sau tặng nước")
    c = res["drafts"][0]["checklist"]
    assert c["apology"] and c["promises"] == ["다음 주문 때 음료를 서비스로 드릴게요."]
    assert c["menus"] == [("딸기모찌", "Mochi dâu")]
    out = render_block(None, "m", "draft_review_reply", res, "vi")
    lines = out.splitlines()
    i = lines.index("**Kiểm tra ý định**")
    assert lines[i + 1].startswith("- ⚠️ Hứa: <vi> 다음 주문 때")  # 약속이 맨 위, 사장님 언어로
    assert "- ✔ Đã xin lỗi" in lines and "- Món: Mochi dâu (딸기모찌)" in lines
    assert any(x.startswith("> (Bản dịch đầy đủ: <vi>") for x in lines)  # 전체 번역은 아래에
    # 한국어 사장님에게는 약속만 표시
    ko = ReviewToolbox(
        "홍만당", TODAY, REPO, cat, writer=PromiseWriter(), reply_store=MockReplyStore(today=TODAY)
    )
    r = ko.dispatch(
        "draft_review_reply", {"target": "negative"}, utterance="음료 서비스 준다고 해줘"
    )
    out = render_block(None, "m", "draft_review_reply", r, "ko")
    assert "⚠️ 약속: 다음 주문 때 음료를 서비스로 드릴게요." in out and "확인할 점" not in out


def test_apology_is_not_a_promise():
    # 사과 문장을 약속으로 오탐해 일반 답글이 막힘 (2026-11-07 실측, 시나리오 4)
    for ok in [
        "저희가 제공한 디저트의 맛에 만족을 드리지 못해 진심으로 죄송합니다.",
        "좋은 서비스를 드리지 못해 죄송합니다.",
        "소중한 의견을 남겨 주시면 더 나은 서비스로 보답드리겠습니다.",
    ]:
        assert check_reply(ok)["passed"], ok
    assert not check_reply("사과의 의미로 음료를 무료로 드리겠습니다.")["passed"]
