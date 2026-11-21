"""What the owner screen's review and customer-stats tabs show (#72).

Read-only views, no LLM: reviews come from the session's dummy store with harsh words
softened by the review rules, stats are the local place report's numbers.
"""

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from agenthan.agent.review.analyze import _spans
from agenthan.agent.review.harsh import soften_quote
from agenthan.agent.review.sentiment import RuleSentiment
from agenthan.tools.place_report import METRICS, WEEKDAYS, load_report, report_facts
from agenthan.tools.report_insights import build_insights, reply_insight

FULL_TEXT = 10_000  # soften_quote cuts quotes; the tab shows the whole review


def reviews(data: dict) -> dict:
    """Newest first, with the average rating and how many still have no owner reply."""
    rows = []
    for r in sorted(data.get("reviews", []), key=lambda r: r["visit_date"], reverse=True):
        text, softened = soften_quote(r["text"], FULL_TEXT)
        rows.append(
            {
                "id": r["review_id"],
                "visit_date": r["visit_date"],
                "rating": r["rating"],
                "text": text,
                "softened": softened,
                "original": r["text"] if softened else None,
                "has_owner_reply": r.get("has_owner_reply", False),
            }
        )
    count = len(rows)
    # Naver reviews often have no star rating; average only the rated ones.
    rated = [r["rating"] for r in rows if r["rating"] is not None]
    return {
        "summary": {
            "count": count,
            "average_rating": round(sum(rated) / len(rated), 1) if rated else None,
            "unanswered": sum(not r["has_owner_reply"] for r in rows),
        },
        "reviews": rows,
    }


def report(data: dict, period: str) -> dict:
    """The latest finished week or day: cards, channels, hours, weekdays, keywords, a tip."""
    facts = report_facts(load_report(), period=period)
    new = {k.name for k in facts.new_keywords}
    insights = build_insights(facts, data)
    return {
        "period": facts.period,
        "end": facts.end,
        "cards": [
            {
                "key": key,
                "value": getattr(facts, key).current,
                "change": getattr(facts, key).change,
                "change_percent": getattr(facts, key).change_percent,
            }
            for key in METRICS
        ],
        "channels": [
            {"name": c.name, "views": c.views, "share_percent": c.share_percent, "change": c.change}
            for c in facts.channels
        ],
        "hourly": [h.current for h in facts.hourly_changes],
        "weekday": [{"day": day, "views": facts.weekday_changes[day].current} for day in WEEKDAYS],
        "peak_hour": facts.peak_hour,
        "quiet_hour": facts.quiet_hour,
        "keywords": [
            {"name": k.name, "views": k.views, "new": k.name in new} for k in facts.top_keywords
        ],
        "conversion_percent": facts.conversion_percent,
        "conversion_change_pp": facts.conversion_change_pp,
        "tip": insights[0].title if insights else None,
    }


# ── 통계 탭 › AI 리뷰 통계 ──────────────────────────────
# 리뷰 분류기의 특징을 네이버 AI 리뷰 통계의 9개 테마로 묶어 보여 준다.
THEMES = {
    "맛": "음식",
    "음식량": "음식",
    "메뉴": "메뉴",
    "가격": "가격",
    "분위기": "분위기",
    "전망": "분위기",
    "청결도": "매장",
    "위치": "매장",
    "주차": "시설",
    "방역": "시설",
    "서비스": "서비스",
    "친절": "서비스",
    "포장": "서비스",
    "배달": "서비스",
    "대기시간": "운영",
    "예약": "운영",
    "만족도": "전반",
    "목적": "전반",
}
STATS_DAYS = {"day": 1, "week": 7, "month": 30}


def _feature_counts(reviews: list[dict]) -> tuple[Counter, Counter]:
    sentiment = RuleSentiment()
    liked, disliked = Counter(), Counter()
    for review in reviews:
        for span in _spans(review, sentiment):
            if span["sentiment"] == "positive":
                liked[span["aspect"]] += 1
            elif span["sentiment"] == "negative":
                disliked[span["aspect"]] += 1
    return liked, disliked


def review_stats(data: dict, period: str) -> dict:
    """좋아요·아쉬워요 특징 TOP3와 테마별 합계. 기간은 가게 데이터의 마지막 리뷰 날짜까지.
    LLM 없이 규칙 감정(RuleSentiment)으로 센다. 직전 같은 길이 기간에 리뷰가 있으면 증감도 준다."""
    rows = data.get("reviews", [])
    end = max((date.fromisoformat(r["visit_date"]) for r in rows), default=date.today())
    days = STATS_DAYS[period]
    start = end - timedelta(days=days - 1)
    before = start - timedelta(days=days)

    def within(lo: date, hi: date) -> list[dict]:
        return [r for r in rows if lo <= date.fromisoformat(r["visit_date"]) <= hi]

    current = within(start, end)
    previous = within(before, start - timedelta(days=1))
    liked, disliked = _feature_counts(current)
    prev_liked, prev_disliked = _feature_counts(previous)

    def top(counts: Counter, prev: Counter) -> list[dict]:
        return [
            {
                "name": name,
                "theme": THEMES.get(name, "기타"),
                "count": count,
                "change": count - prev[name] if previous else None,
            }
            for name, count in counts.most_common(3)
        ]

    themes: dict[str, dict] = {}
    for counts, key in ((liked, "positive"), (disliked, "negative")):
        for name, count in counts.items():
            theme = THEMES.get(name, "기타")
            row = themes.setdefault(theme, {"name": theme, "positive": 0, "negative": 0})
            row[key] += count
    return {
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "review_count": len(current),
        "positive": top(liked, prev_liked),
        "negative": top(disliked, prev_disliked),
        "themes": sorted(themes.values(), key=lambda t: -(t["positive"] + t["negative"])),
    }


# ── 비서 제안: 통계·리뷰 해석 근거를 카드로 ─────────────────
FEATURE_NAMES = {
    f["id"]: f["name_ko"]
    for f in json.loads(
        (Path(__file__).parents[1] / "agent" / "place_features.json").read_text(encoding="utf-8")
    )
}
DAY_KO = dict(zip(WEEKDAYS, "월화수목금토일", strict=True))


def _hour(h: int) -> str:
    if h == 0:
        return "밤 12시"
    if h < 12:
        return f"오전 {h}시"
    return "낮 12시" if h == 12 else f"오후 {h - 12}시"


def _days(rows: list[dict]) -> str:
    """월요일부터 차례로: '월·화·수·목요일'."""
    picked = {r["weekday"] for r in rows}
    return "·".join(DAY_KO[d] for d in WEEKDAYS if d in picked) + "요일"


def _card(insight) -> dict:
    """해석 근거 하나 → 화면 카드(근거 문장·버튼 이름·비서에게 보낼 말). 숫자는 근거 그대로."""
    e = insight.evidence
    tab = None
    if insight.kind == "conversion_drop":
        source = "방문·유입 통계"
        evidence = (
            f"플레이스 조회 {e['previous_place_views']:,} → {e['place_views']:,}, "
            f"예약·주문 전환 {e['previous_conversion_percent']}% → {e['conversion_percent']}%."
        )
        action = "메뉴 손보기"
        ask = "조회는 늘었는데 예약·주문으로 이어지는 비율이 줄었어. 메뉴를 어떻게 손보면 좋을까?"
    elif insight.kind == "hours_mismatch":
        source = "시간대별 조회"
        peak = _hour(e["peak_hour"])
        near = [s for s in e["schedules"] if s["reason"] == "near_close"]
        outside = [s for s in e["schedules"] if s["reason"] == "outside"]
        if near:
            close = near[0]["close_time"]
            tail = f"{_days(near)}은 {close}에 닫아요."
        elif outside:
            tail = f"{_days(outside)}은 그 시간에 영업하지 않아요."
        elif "closing_hour" in e:
            tail = f"{_hour(e['closing_hour'])}에 닫아요."
        else:
            tail = "그날은 쉬는 날이에요."
        evidence = f"손님이 가장 많이 보는 때가 {peak}인데, {tail}"
        action = "영업시간 살펴보기"
        ask = f"손님이 가장 많이 보는 시간이 {peak}래. 영업시간을 바꾸는 게 좋을까?"
    elif insight.kind == "keyword_gap":
        source = "검색어 통계"
        words = e["keywords"][:2]
        evidence = " ".join(f"‘{k['name']}’로 {k['views']:,}번 들어왔어요." for k in words)
        if e["representative_keywords"]:
            evidence += f" 지금 대표키워드는 {' · '.join(e['representative_keywords'])}예요."
        action = "대표키워드에 추가하기"
        ask = f"‘{words[0]['name']}’를 대표키워드에 추가해 줘"
    elif insight.kind == "blog_exposure":
        source = "유입 경로"
        evidence = f"블로그·카페로 {e['views']:,}번 들어왔어요(전체의 {e['share_percent']}%)."
        action = "블로그 유입 알아보기"
        ask = "블로그·카페로 들어오는 손님을 늘리려면 어떻게 해야 해?"
    elif insight.kind == "quiet_slot":
        source = "시간대별 조회"
        parts = []
        if "quiet_hour" in e:
            parts.append(_hour(e["quiet_hour"]))
        if "quiet_weekday" in e:
            parts.append(f"{DAY_KO[e['quiet_weekday']]}요일")
        when = " · ".join(parts)
        evidence = f"{when}에 조회가 가장 적어요. 그때 쓰는 쿠폰으로 손님을 불러 볼 수 있어요."
        action = "쿠폰 만들기"
        ask = f"{when}에 쓸 쿠폰을 만들어 줘"
    elif insight.kind == "smartcall_increase":
        source = "스마트콜"
        evidence = f"전화 문의가 {e['previous_calls']:,} → {e['calls']:,}통으로 늘었어요."
        action = "전화 기록 보기"
        ask = "요즘 전화 문의 기록 보여 줘"
    elif insight.kind == "reply_gap":
        source = "리뷰"
        evidence = f"별점 3점 이하인데 아직 답글을 달지 않은 리뷰가 {e['count']}개예요."
        action = "답글 초안 받기"
        ask, tab = None, "reviews"
    else:  # 새 규칙이 생기면 제목만으로도 보이게
        source, evidence, action, ask = "통계", insight.title, "비서에게 물어보기", insight.title
    return {
        "kind": insight.kind,
        "priority": insight.priority,
        "feature": insight.action_feature,
        "feature_name": FEATURE_NAMES.get(insight.action_feature or "statistics", "통계"),
        "source": source,
        "title": insight.title,
        "evidence": evidence,
        "action": action,
        "ask": ask,
        "tab": tab,
    }


def suggestions(data: dict) -> dict:
    """지난주 통계와 리뷰로 고른 해 볼 일(작은 priority부터). LLM 없이 규칙(report_insights)만."""
    facts = report_facts(load_report(), period="week")
    insights = build_insights(facts, data)
    reply = reply_insight(data)
    if reply is not None:
        insights.append(reply)
    insights.sort(key=lambda i: i.priority)
    return {"end": facts.end, "items": [_card(i) for i in insights]}
