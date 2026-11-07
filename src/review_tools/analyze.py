"""analyze_reviews: 도구 명세(specs.ANALYZE_REVIEWS)의 구현.

LLM이 채우는 인자: period, start_date, end_date, focus, topic, format
시스템이 넣는 인자: store_id, today, repo, catalog, sentiment
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from .repo import ReviewRepo
from .sentiment import RuleSentiment, prefetch_reviews
from .topic import MenuCatalog, mentions_menu, resolve_from_utterance, resolve_topic

PERIOD_DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
# 내부 분류명은 네이버와 같게 두고, 사장님에게 보여줄 때만 실제 범위가 드러나는 이름을 쓴다
DISPLAY_NAMES = {
    "배달": "포장·배달",
    "대기시간": "웨이팅·음식 나오는 속도",
    "예약": "예약·주문·결제",
    "방역": "좌석 간격·환기",
    "만족도": "전반적 만족·재방문",
    "메뉴": "메뉴 구성",
}
LOW_REVIEW_PER_30D = 4  # 최근 30일 리뷰가 이보다 적으면 '리뷰가 뜸함' (주 1건 미만, 가정값)
MIN_FOR_TRENDS = (
    5  # 이보다 적으면 경향을 요약하지 않고 리뷰를 그대로 보여준다 (1건으로 일반화 방지)
)


def _window(today: date, period, start_date, end_date):
    if start_date:
        return date.fromisoformat(start_date), date.fromisoformat(end_date) if end_date else today
    period = period or "7d"
    if period == "all":
        return None, today
    return today - timedelta(days=PERIOD_DAYS[period]), today


def _quote(text: str, limit: int = 60) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _spans(review: dict, sentiment) -> list[dict]:
    """[{aspect, text, sentiment}] — 분류기 특징 구간마다 감정을 붙인다."""
    out = [
        {"aspect": f["feature"], "text": h["text"]}
        for f in review.get("features", [])
        for h in f["highlights"]
    ]
    labels = (
        sentiment.spans_sentiment([s["text"] for s in out], review["text"]) if out else []
    )  # 리뷰당 한 번, 전문을 맥락으로
    for s, label in zip(out, labels, strict=False):
        s["sentiment"] = label
    return out


EXPAND_STEPS = ["7d", "30d", "90d", "all"]


def analyze_reviews(**kw) -> dict:
    """사장님이 기간을 말하지 않았는데(period·start_date 없음) 기본 7일에 리뷰가 없으면
    30일→90일→전체로 넓힌다.
    넓혔다는 사실은 period_expanded로 알려 에이전트가 '최근 7일엔 없어서 90일로 봤어요'라고 말하게
    한다."""
    if kw.get("period") or kw.get("start_date"):
        return _analyze(**kw)
    for step in EXPAND_STEPS:
        out = _analyze(**{**kw, "period": step})
        if out.get("error") or out.get("review_count"):
            break
    if step != "7d":
        out["period_expanded"] = {
            "from": "7d",
            "to": step,
            "reason": "기간 지정 없음, 최근 7일 해당 리뷰 0건",
        }
    return out


def _analyze(
    *,
    store_id: str,
    today: date,
    repo: ReviewRepo,
    catalog: MenuCatalog | None = None,
    sentiment=None,
    llm=None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    focus: str | None = None,
    topic: str | None = None,
    format: str | None = None,
    utterance: str | None = None,
) -> dict:
    catalog = catalog or MenuCatalog()
    sentiment = sentiment or RuleSentiment()
    focus = focus or "all"
    start, end = _window(today, period, start_date, end_date)

    # 순서: (1) topic 사전 매칭 → (2) 사장님 원문 사전 매칭 → (3) HCX 추측.
    # HCX 추측을 먼저 하면 '모찌' 같은 일부 단어로 메뉴를 여러 개 골라 버린다 (2026-11-07 실측)
    resolved = resolve_topic(topic, store_id, catalog, None)
    topic_from = "topic"
    if resolved["unresolved"] and utterance:  # utterance = 사장님 원문 (시스템 값)
        alt = resolve_from_utterance(utterance, store_id, catalog, None)
        if alt["menus"] or alt["aspects"]:
            resolved, topic_from = alt, "utterance"
    if resolved["unresolved"] and llm is not None:
        guess = resolve_topic(topic, store_id, catalog, llm)
        if not guess["unresolved"]:
            resolved, topic_from = guess, "llm"
    if resolved["unresolved"]:
        return {
            "error": "topic_unresolved",
            "topic": topic,
            "unresolved": resolved["unresolved"],
            "candidates": resolved["candidates"],
        }
    menus = [m for m in catalog.menus(store_id) if m["name"] in resolved["menus"]]
    aspects = resolved["aspects"]

    def topic_match(r):
        if menus and not any(mentions_menu(r["text"], m) for m in menus):
            return False
        if aspects and not set(aspects) & {f["feature"] for f in r.get("features", [])}:
            return False
        return True

    all_reviews = repo.get_reviews(store_id, None, today)
    in_window = [
        r
        for r in all_reviews
        if (start is None or r["visit_date"] >= start.isoformat())
        and r["visit_date"] <= end.isoformat()
    ]
    matched = [r for r in in_window if topic_match(r)]

    prefetch_reviews(sentiment, matched)  # HCX: 리뷰를 10개씩 묶어 한 번에 판단해 둔다
    analyzed = []
    for r in matched:
        sp = _spans(r, sentiment)
        if aspects:  # 특징을 지정했으면 그 특징 구간으로만 감정을 본다
            sp = [s for s in sp if s["aspect"] in aspects] or sp
        analyzed.append((r, sp, sentiment.review_sentiment(r, [s["sentiment"] for s in sp])))
    if focus == "complaint":
        analyzed = [a for a in analyzed if a[2] in ("negative", "mixed")]
    elif focus == "praise":
        analyzed = [a for a in analyzed if a[2] in ("positive", "mixed")]

    recent_30 = sum(
        1 for r in all_reviews if r["visit_date"] >= (today - timedelta(days=30)).isoformat()
    )
    out = {
        "period": {"start": start.isoformat() if start else None, "end": end.isoformat()},
        "filters": {
            "menus": [m["name"] for m in menus],
            "aspects": aspects,
            "aspects_display": [DISPLAY_NAMES.get(a, a) for a in aspects],
            "focus": focus,
        },
        "review_count": len(analyzed),  # 기간·topic·focus를 모두 적용한 수
        "total_reviews": len(in_window),  # 같은 기간의 전체 리뷰 수 (비율은 이 수 기준으로 말한다)
        "low_review": recent_30 < LOW_REVIEW_PER_30D,
        "sentiment_source": getattr(sentiment, "name", "custom"),
        "topic_resolved_from": topic_from if topic else None,
    }
    replies = [r.get("has_owner_reply") for r, _, _ in analyzed]
    # 답글 정보가 없거나(스냅샷) 대상 리뷰가 없으면 모른다(null)고 답한다
    out["unanswered"] = (
        None
        if not replies or any(x is None for x in replies)
        else sum(1 for x in replies if x is False)
    )

    if not analyzed:
        prior = [r for r in all_reviews if topic_match(r)]
        last = prior[0] if prior else None
        out["last_review"] = (
            None
            if not last
            else {
                "review_id": last["review_id"],
                "visit_date": last["visit_date"],
                "days_ago": (today - date.fromisoformat(last["visit_date"])).days,
                "quote": _quote(last["text"]),
            }
        )
        if menus:
            out["menu_mentions_all_time"] = len(prior)
        out["next_actions"] = (["expand_period"] if last else []) + (
            ["increase_reviews"] if out["low_review"] else []
        )
        return out

    out["sentiment"] = dict(Counter(a[2] for a in analyzed))
    agg = defaultdict(lambda: {"mentions": 0, "positive": 0, "negative": 0})
    for _, sp, _ in analyzed:
        seen = set()
        for s in sp:
            k = agg[s["aspect"]]
            if s["aspect"] not in seen:
                k["mentions"] += 1
                seen.add(s["aspect"])
            if s["sentiment"] in ("positive", "negative"):
                k[s["sentiment"]] += 1
    # 항목별 만족도 = 긍정 ÷ (긍정 + 부정) (참고: 47-area_review_analysis). 판단된 언급이 없으면
    # None
    out["aspects"] = sorted(
        (
            {
                "aspect": a,
                "display": DISPLAY_NAMES.get(a, a),
                **v,
                "satisfaction": round(v["positive"] / (v["positive"] + v["negative"]), 2)
                if v["positive"] + v["negative"]
                else None,
            }
            for a, v in agg.items()
        ),
        key=lambda x: -x["mentions"],
    )

    want = {"complaint": "negative", "praise": "positive"}.get(focus)
    examples = []
    for r, sp, rs in analyzed:  # 최신순
        pick = next((s for s in sp if want and s["sentiment"] == want), None) or next(
            (s for s in sp if s["sentiment"] != "neutral"), None
        )
        examples.append(
            {
                "review_id": r["review_id"],
                "visit_date": r["visit_date"],
                "quote": _quote(pick["text"] if pick else r["text"]),
                "aspect": pick["aspect"] if pick else None,
                "sentiment": rs,
            }
        )
        if len(examples) >= (len(analyzed) if len(analyzed) < MIN_FOR_TRENDS else 3):
            break
    out["examples"] = examples
    out["insights"], out["insights_source"] = _insights(out["aspects"], focus), "template"
    if not out["insights"]:  # 긍정·부정 판단이 하나도 없을 때도 빈 요약은 내지 않는다
        out["insights"] = [f"관련 리뷰 {len(analyzed)}건이 있지만 뚜렷한 칭찬이나 불만은 없습니다"]
    if len(analyzed) < MIN_FOR_TRENDS:  # 표본이 적으면 경향 대신 사실만
        out["insights"] = [
            f"같은 기간 전체 리뷰 {len(in_window)}건 중 해당 리뷰는 {len(analyzed)}건이라 "
            "경향을 말하기 어렵습니다. examples의 리뷰를 그대로 보여주세요"
        ]
        out["few_reviews"] = True
    elif (
        llm is not None
    ):  # HCX 요약: 정렬된 집계만 넘겨 숫자를 지어내거나 칭찬을 불만으로 뒤집지 못하게 한다
        top_neg = [
            f"{a['display']} 불만 {a['negative']}회"
            for a in sorted(out["aspects"], key=lambda a: -a["negative"])
            if a["negative"]
        ][:3]
        top_pos = [
            f"{a['display']} 칭찬 {a['positive']}회"
            for a in sorted(out["aspects"], key=lambda a: -a["positive"])
            if a["positive"]
        ][:3]
        summary = {
            "기간": out["period"],
            "기간_전체_리뷰": len(in_window),
            "조건": {
                "메뉴": out["filters"]["menus"],
                "항목": out["filters"]["aspects_display"],
                "초점": {"complaint": "불만만", "praise": "칭찬만"}.get(focus, "전체"),
            },
            "조건에_맞는_리뷰": len(analyzed),
            "리뷰_감정": out["sentiment"],
            "불만_많은_항목": top_neg,
            "칭찬_많은_항목": top_pos,
            "대표_리뷰": [e["quote"] for e in out["examples"]],
        }
        try:
            out["insights"], out["insights_source"] = llm.insights(summary), "hcx"
        except Exception as e:  # 요약 실패는 템플릿 요약으로 대체 (분석 결과 자체는 유효)
            out["insights_error"] = f"{type(e).__name__}: {str(e)[:120]}"

    if format == "report":
        out["report_ideas"] = _report_ideas(out, llm, len(analyzed))
    if format == "report" and start is not None:
        prev_start = start - (end - start)
        prev = [
            r
            for r in all_reviews
            if prev_start.isoformat() <= r["visit_date"] < start.isoformat() and topic_match(r)
        ]
        out["previous_period"] = {"start": prev_start.isoformat(), "review_count": len(prev)}
        out["format"] = "report"
    return out


def _report_ideas(out: dict, llm, n: int) -> dict:
    """보고서용: 불만·만족 TOP 3는 코드가 세고, 개선 제안 3개·마케팅 포인트 2개는 HCX가 해석
    (없으면 템플릿)."""
    asp = out["aspects"]
    top_neg = [a for a in sorted(asp, key=lambda a: -a["negative"]) if a["negative"]][:3]
    top_pos = [a for a in sorted(asp, key=lambda a: -a["positive"]) if a["positive"]][:3]
    ideas = {
        "complaints_top": [{"display": a["display"], "count": a["negative"]} for a in top_neg],
        "praise_top": [{"display": a["display"], "count": a["positive"]} for a in top_pos],
        "improvements": [
            f"{a['display']} 관련 불만 {a['negative']}건 확인하고 대응하기" for a in top_neg
        ],
        "marketing": [
            f"'{a['display']}' 칭찬 {a['positive']}건 → 새소식·리뷰 요청 문구에 활용"
            for a in top_pos[:2]
        ],
        "source": "template",
    }
    if llm is not None and n >= MIN_FOR_TRENDS and hasattr(llm, "report_ideas"):
        try:
            got = llm.report_ideas(
                {
                    "불만_많은_항목": [
                        f"{x['display']} {x['count']}회" for x in ideas["complaints_top"]
                    ],
                    "칭찬_많은_항목": [
                        f"{x['display']} {x['count']}회" for x in ideas["praise_top"]
                    ],
                    "대표_리뷰": [e["quote"] for e in out.get("examples", [])],
                }
            )
            if got["improvements"] or got["marketing"]:
                ideas.update(
                    improvements=got["improvements"], marketing=got["marketing"], source="hcx"
                )
        except Exception as e:
            ideas["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return ideas


def _insights(aspects: list[dict], focus: str) -> list[str]:
    """HCX 연결 전 임시 요약. 실제 문장은 에이전트가 사장님 언어로 다시 쓴다."""
    lines = []
    neg = sorted((a for a in aspects if a["negative"]), key=lambda a: -a["negative"])
    pos = sorted((a for a in aspects if a["positive"]), key=lambda a: -a["positive"])
    if pos and focus != "complaint":
        lines.append(f"{pos[0]['display']} 관련 긍정 언급이 가장 많습니다 ({pos[0]['positive']}회)")
    if neg and focus != "praise":
        lines.append(f"{neg[0]['display']} 관련 불만이 가장 많습니다 ({neg[0]['negative']}회)")
    if len(neg) > 1 and focus == "complaint":
        lines.append(f"그다음은 {neg[1]['display']} ({neg[1]['negative']}회)")
    return lines
