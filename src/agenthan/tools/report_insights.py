"""R1 계산값과 정본 가게 정보에서 해석 근거를 고른다. LLM·저장은 쓰지 않는다."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from agenthan.tools.dummy_store import normalize
from agenthan.tools.place_report import WEEKDAYS, ReportFacts, _rounded


@dataclass(frozen=True)
class Insight:
    kind: str
    title: str
    evidence: dict
    action_feature: str | None
    priority: int


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    if not (0 <= hour <= 24 and 0 <= minute < 60 and (hour < 24 or minute == 0)):
        raise ValueError("영업시간 형식 확인 필요")
    return hour * 60 + minute


def _schedules(facts: ReportFacts, shop: dict) -> list[dict]:
    """날짜별 설정 우선, 없으면 요일 설정. 주간은 해당 기간의 각 날짜를 본다."""
    end = date.fromisoformat(facts.end)
    hours = shop.get("shop", {}).get("hours", {})
    schedules = []
    for offset in range(7 if facts.period == "week" else 1):
        day = end - timedelta(days=offset)
        weekday = WEEKDAYS[day.weekday()]
        setting = hours.get(day.isoformat(), hours.get(weekday))
        if not isinstance(setting, dict):
            continue
        row = {"date": day.isoformat(), "weekday": weekday, **setting}
        if not row.get("closed"):
            try:
                _minutes(row["open_time"])
                _minutes(row["close_time"])
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        schedules.append(row)
    return schedules


def _hour_position(hour: int, schedule: dict) -> tuple[bool, bool]:
    opening, closing = _minutes(schedule["open_time"]), _minutes(schedule["close_time"])
    peak = hour * 60
    if closing <= opening:  # 자정을 넘기는 영업시간
        closing += 24 * 60
        if peak < opening:
            peak += 24 * 60
    return peak < opening or peak > closing, 0 <= closing - peak <= 60


def build_insights(facts: ReportFacts, shop: dict | None = None) -> list[Insight]:
    """작은 priority부터 최대 세 개. 전환·시간·키워드를 우선한다.

    블로그 저비중 기준은 10% 미만이다. 한산함은 R1의 서로 다른 최다·최소
    시간/요일이 있을 때만 설명하며, 원본 분포가 없으므로 격차의 크기는 단정하지 않는다.
    """
    insights = []

    def add(kind, title, evidence, feature, priority):
        insights.append(Insight(kind, title, evidence, feature, priority))

    if (facts.place_views.change or 0) > 0 and (facts.conversion_change_pp or 0) < 0:
        add(
            "conversion_drop",
            "조회는 늘었지만 예약·주문으로 이어지는 비율은 줄었어요",
            {
                "place_views": facts.place_views.current,
                "previous_place_views": facts.place_views.previous,
                "requests": facts.booking_order_requests.current,
                "conversion_percent": facts.conversion_percent,
                "previous_conversion_percent": facts.previous_conversion_percent,
                "conversion_change_pp": facts.conversion_change_pp,
            },
            "menu",
            10,
        )

    schedules = _schedules(facts, shop) if shop is not None else []
    if facts.peak_hour is not None:
        matches = []
        for schedule in schedules:
            if schedule.get("closed"):
                if facts.period == "day":
                    matches.append({**schedule, "reason": "closed"})
                continue
            outside, near_close = _hour_position(facts.peak_hour, schedule)
            if outside or near_close:
                matches.append({**schedule, "reason": "outside" if outside else "near_close"})
        evidence = {"peak_hour": facts.peak_hour, "schedules": matches}
        if shop is None and 0 <= facts.closing_hour - facts.peak_hour <= 1:
            evidence["closing_hour"] = facts.closing_hour
        if matches or "closing_hour" in evidence:
            add(
                "hours_mismatch",
                "조회 피크와 영업시간을 함께 확인해 보세요",
                evidence,
                "business_hours",
                20,
            )

    representatives = (
        shop.get("shop", {}).get("keywords", [])
        if shop is not None
        else facts.representative_keywords
    )
    menus = [m["name"] for m in shop.get("menus", [])] if shop is not None else []
    known = {normalize(name).replace(" ", "") for name in [*representatives, *menus]}
    gaps = [
        {"name": k.name, "views": k.views}
        for k in facts.top_keywords
        if k.views > 0 and normalize(k.name).replace(" ", "") not in known
    ]
    if gaps:
        add(
            "keyword_gap",
            "대표키워드·메뉴 이름에 없는 검색어로도 들어오고 있어요",
            {"keywords": gaps, "representative_keywords": list(representatives), "menus": menus},
            "search_keywords",
            30,
        )

    for channel in facts.channels:
        if channel.name != "블로그·카페" or facts.place_views.current == 0:
            continue
        previous_share = None
        if channel.change is not None and facts.place_views.previous:
            previous_share = _rounded(
                Decimal(channel.views - channel.change) * 100 / facts.place_views.previous
            )
        if (
            channel.share_percent < 10
            or (channel.change or 0) < 0
            or (previous_share is not None and channel.share_percent < previous_share)
        ):
            add(
                "blog_exposure",
                "블로그·카페 유입 경로를 살펴보세요",
                {
                    "views": channel.views,
                    "share_percent": channel.share_percent,
                    "change": channel.change,
                    "previous_share_percent": previous_share,
                },
                None,
                40,
            )

    quiet = {}
    if facts.place_views.current > 0:
        if facts.quiet_hour is not None and facts.quiet_hour != facts.peak_hour:
            if shop is None or any(
                not s.get("closed") and not _hour_position(facts.quiet_hour, s)[0]
                for s in schedules
            ):
                quiet["quiet_hour"] = facts.quiet_hour
        if facts.period == "week" and facts.quiet_weekday != facts.peak_weekday:
            if shop is None or any(
                not s.get("closed") and s["weekday"] == facts.quiet_weekday for s in schedules
            ):
                quiet["quiet_weekday"] = facts.quiet_weekday
    if quiet:
        add(
            "quiet_slot",
            "다른 때보다 조회가 적은 시간·요일이 있어요",
            {**quiet, "place_views": facts.place_views.current},
            "coupon",
            50,
        )

    if (facts.smartcall_calls.change or 0) > 0:
        add(
            "smartcall_increase",
            "스마트콜 통화가 늘었어요",
            {
                "calls": facts.smartcall_calls.current,
                "previous_calls": facts.smartcall_calls.previous,
                "change": facts.smartcall_calls.change,
            },
            "smartcall",
            60,
        )

    return sorted(insights, key=lambda insight: insight.priority)[:3]
