"""analyze_reviews 테스트 (스냅샷 + 오프라인 감정 규칙, API 키 불필요)."""

from datetime import date
from pathlib import Path

from review_tools import MenuCatalog, RuleSentiment, SnapshotRepo, analyze_reviews, resolve_topic

ROOT = Path(__file__).resolve().parents[1]

TODAY = date(2026, 11, 7)
REPO = SnapshotRepo(ROOT / "tests" / "fixtures" / "review_snapshot.jsonl")
CAT = MenuCatalog.load(ROOT / "tests" / "fixtures" / "menus.json")


def run(**kw):
    return analyze_reviews(store_id="연습떡집", today=TODAY, repo=REPO, catalog=CAT, **kw)


def test_zero_reviews_in_window():
    # 연습떡집 스냅샷의 마지막 리뷰는 10-30 → 최근 7일을 '직접 지정'하면 0건 + 마지막 리뷰 안내
    r = run(period="7d")
    assert r["review_count"] == 0
    assert r["last_review"]["visit_date"] == "2026-10-30" and r["last_review"]["days_ago"] == 8
    assert "expand_period" in r["next_actions"]


def test_period_auto_expands_when_unspecified():
    r = run()  # 기간을 말하지 않음 → 30일로 넓혀서 찾음
    assert r["review_count"] > 0 and r["period_expanded"]["to"] == "30d"
    m = run(topic="딸기모찌")
    assert m["review_count"] > 0 and m["period_expanded"]["to"] in ("30d", "90d", "all")


def test_period_and_focus():
    r = run(period="30d")
    assert r["review_count"] > 0 and r["period"]["start"] == "2026-10-08"
    c = run(period="all", focus="complaint")
    assert all(e["sentiment"] in ("negative", "mixed") for e in c["examples"])
    assert c["review_count"] < run(period="all")["review_count"]


def test_topic_menu_and_aspect():
    assert resolve_topic("메도빅 가격", "연습떡집", CAT)["unresolved"] == ["메도빅"]  # 없는 메뉴
    t = resolve_topic("딸기모찌 가격", "연습떡집", CAT)
    assert t["menus"] == ["딸기모찌"] and t["aspects"] == ["가격"]
    assert resolve_topic("웨이팅", "연습떡집", CAT)["aspects"] == ["대기시간"]
    assert resolve_topic("очередь", "연습떡집", CAT)["aspects"] == ["대기시간"]
    amb = resolve_topic("딸기", "연습떡집", CAT)  # 여러 메뉴에 걸림
    assert amb["unresolved"] and len(amb["candidates"]) >= 2
    r = run(period="all", topic="딸기모찌")
    assert r["review_count"] > 0 and r["filters"]["menus"] == ["딸기모찌"]
    e = run(period="all", topic="케이크")
    assert e["error"] == "topic_unresolved"


def test_rule_sentiment():
    s = RuleSentiment()
    assert s.span_sentiment("딸기모찌 개 노맛...ㅜㅜ") == "negative"
    assert s.span_sentiment("많이 안달고 맛있다") == "positive"
    assert s.span_sentiment("불친절은 좀 아니지 않나요") == "negative"
    assert s.span_sentiment("가격이 조금은 사악하지만") == "negative"


def test_few_reviews_not_generalized():
    r = run(period="30d", focus="complaint")  # 최근 30일 전체 4건 중 불만은 소수
    assert r["few_reviews"] and r["total_reviews"] == 4
    assert len(r["examples"]) == r["review_count"]  # 적으면 전부 보여준다


def test_topic_recovered_from_utterance():
    from review_tools.topic import resolve_from_utterance

    assert resolve_from_utterance("Что говорят о клубничном моти?", "연습떡집", CAT)["menus"] == [
        "딸기모찌"
    ]
    # LLM이 topic을 로마자로 망가뜨려도 사장님 원문에서 찾는다 (2026-11-07 실제 발생)
    r = run(period="all", topic="clumnichchna moti", utterance="Что говорят о клубничном моти?")
    assert r["filters"]["menus"] == ["딸기모찌"] and r["topic_resolved_from"] == "utterance"
    # 원문에도 없으면 그대로 되묻는다
    assert (
        run(period="all", topic="메도빅", utterance="메도빅 어때?")["error"] == "topic_unresolved"
    )


def test_utterance_match_beats_llm_guess():
    class GreedyLLM:  # '모찌'만 보고 모찌 메뉴를 전부 고르는 추측 (실제로 발생)
        def resolve_topic(self, topic, menus, aspects):
            return {"menus": [m for m in menus if "모찌" in m], "aspects": []}

    r = run(
        period="all",
        topic="클루바니치 모찌",
        utterance="Что говорят о клубничном моти?",
        llm=GreedyLLM(),
    )
    assert r["filters"]["menus"] == ["딸기모찌"] and r["topic_resolved_from"] == "utterance"


def test_report_format():
    r = run(period="30d", format="report")
    assert r["format"] == "report" and r["previous_period"]["start"] == "2026-09-08"


class _FakeClient:
    """HCX 응답 흉내: 감정 요청에는 구절 수만큼 negative, 요약 요청에는 고정 문장."""

    def __init__(self):
        import json as _j
        from types import SimpleNamespace as NS

        self.calls, self._j, self._NS = [], _j, NS
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        sys_msg, user = kw["messages"][0]["content"], kw["messages"][-1]["content"]
        if "감정 판단기" in sys_msg and "[R0]" in user:  # 묶음 판단: 구절 번호가 묶음 전체에 이어짐
            import re as _re

            body = {
                "items": [
                    {"i": int(m), "label": "negative"}
                    for m in _re.findall(r"^\s+\[(\d+)\]", user, _re.M)
                ]
            }
        elif "감정 판단기" in sys_msg:
            n = user.split("판단할 구절:")[-1].strip().count("\n") + 1
            body = {"items": [{"i": i, "label": "negative"} for i in range(n)]}
        else:
            body = {"insights": ["가짜 요약 문장입니다"]}
        msg = self._NS(content="```json\n" + self._j.dumps(body, ensure_ascii=False) + "\n```")
        return self._NS(choices=[self._NS(message=msg)])


def test_hcx_wiring_with_fake_client():
    from review_tools.hcx_llm import HCXReviewLLM, HCXSentiment

    fc = _FakeClient()
    llm = HCXReviewLLM("HCX-DASH-002", client=fc, min_interval=0)
    r = run(period="all", llm=llm, sentiment=HCXSentiment(llm))  # 표본이 충분해야 HCX 요약을 쓴다
    assert r["sentiment_source"] == "hcx" and r["insights_source"] == "hcx"
    assert r["insights"] == ["가짜 요약 문장입니다"]
    assert set(r["sentiment"]) <= {"negative", "mixed"}  # 가짜 응답이 모두 negative
    assert all(
        "response_format" not in c for c in fc.calls
    )  # DASH에는 response_format을 보내지 않음
    import math

    n_calls = len(fc.calls)
    sent_calls = sum("감정 판단기" in c["messages"][0]["content"] for c in fc.calls)
    with_spans = sum(
        1 for x in REPO.get_reviews("연습떡집") if any(f["highlights"] for f in x["features"])
    )
    assert (
        sent_calls <= math.ceil(with_spans / 10) + 2
    )  # 리뷰 10개씩 묶어서 판단 (리뷰당 1회가 아님)
    assert n_calls == sent_calls + 1  # + 요약 1회


def test_batch_uses_global_numbering():
    from review_tools.hcx_llm import HCXReviewLLM

    seen = {}

    class Echo(_FakeClient):
        def _create(self, **kw):
            seen["user"] = kw["messages"][-1]["content"]
            return super()._create(**kw)

    llm = HCXReviewLLM("HCX-DASH-002", client=Echo(), min_interval=0)
    out = llm.sentiments_batch([("A 리뷰", ["a1", "a2"]), ("B 리뷰", ["b1"])])
    assert "  [2] b1" in seen["user"] and out == [["negative", "negative"], ["negative"]]


def test_satisfaction_and_report_ideas():
    r = run(period="all", format="report")
    a = next(x for x in r["aspects"] if x["positive"] + x["negative"])
    assert a["satisfaction"] == round(a["positive"] / (a["positive"] + a["negative"]), 2)
    ideas = r["report_ideas"]
    assert (
        ideas["source"] == "template"
        and len(ideas["complaints_top"]) <= 3
        and len(ideas["marketing"]) <= 2
    )


def test_batch_miss_falls_back_to_single():
    """묶음 답에서 빠진 리뷰는 단건으로 다시 판단한다."""
    from review_tools.hcx_llm import HCXReviewLLM, HCXSentiment

    class Partial(_FakeClient):
        def _create(self, **kw):
            user = kw["messages"][-1]["content"]
            if "[R0]" in user:  # 첫 리뷰(구절 0)만 답하고 나머지는 빠뜨림
                self.calls.append(kw)
                body = {"items": [{"i": 0, "label": "positive"}]}
                return self._NS(choices=[self._NS(message=self._NS(content=self._j.dumps(body)))])
            return super()._create(**kw)

    fc = Partial()
    hs = HCXSentiment(HCXReviewLLM("HCX-DASH-002", client=fc, min_interval=0))
    pairs = [("맛있어요", ["맛있어요"]), ("맛없어요", ["맛없어요"])]
    hs.prefetch(pairs)
    assert hs.stats == {"batch_calls": 1, "batched_reviews": 1, "batch_misses": 1}
    assert hs.spans_sentiment(["맛있어요"], "맛있어요") == ["positive"]  # 묶음 결과 사용
    assert hs.spans_sentiment(["맛없어요"], "맛없어요") == ["negative"]  # 단건으로 다시 판단


def test_missed_reviews_get_second_batch():
    from review_tools.hcx_llm import HCXReviewLLM, HCXSentiment

    class SkipFirstPass(_FakeClient):  # 첫 묶음(리뷰 6개)은 아무것도 답하지 않고, 두 번째부터 정상
        n = 0

        def _create(self, **kw):
            if "[R0]" in kw["messages"][-1]["content"] and SkipFirstPass.n == 0:
                SkipFirstPass.n += 1
                self.calls.append(kw)
                return self._NS(choices=[self._NS(message=self._NS(content='{"items": []}'))])
            return super()._create(**kw)

    hs = HCXSentiment(
        HCXReviewLLM("HCX-DASH-002", client=SkipFirstPass(), min_interval=0), batch_size=10
    )
    hs.prefetch([(f"리뷰{i}", [f"구절{i}"]) for i in range(6)])
    assert (
        hs.stats["batched_reviews"] == 6 and hs.stats["batch_misses"] == 0
    )  # 두 번째 묶음에서 모두 회수
    assert hs.stats["batch_calls"] == 1 + 2  # 10개 1회 + 5개씩 2회


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
