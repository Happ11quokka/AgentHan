"""What the owner screen's review and customer-stats tabs show (#72).

Read-only views, no LLM: reviews come from the session's dummy store with harsh words
softened by the review rules, stats are the local place report's numbers.
"""

from agenthan.agent.review.harsh import soften_quote
from agenthan.tools.place_report import METRICS, WEEKDAYS, load_report, report_facts
from agenthan.tools.report_insights import build_insights

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
