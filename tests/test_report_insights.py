import copy
from dataclasses import replace

import pytest

from agenthan.agent.place_matcher import load_features
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.place_report import ChannelFact, KeywordFact, load_report, report_facts
from agenthan.tools.report_insights import build_insights


@pytest.fixture
def shop(tmp_path):
    return DummyStore(tmp_path / "store.json").read()


@pytest.fixture
def facts():
    source = report_facts(load_report())
    return replace(
        source,
        place_views=replace(source.place_views, current=100, previous=100, change=0),
        smartcall_calls=replace(source.smartcall_calls, current=5, previous=5, change=0),
        conversion_percent=10.0,
        previous_conversion_percent=10.0,
        conversion_change_pp=0.0,
        channels=(ChannelFact("블로그·카페", 20, 20.0, 0),),
        top_keywords=(KeywordFact("러시안 베이커리", 20),),
        peak_hour=12,
        quiet_hour=12,
        peak_weekday="mon",
        quiet_weekday="mon",
    )


def changed(facts, kind):
    return {
        "conversion_drop": replace(
            facts,
            place_views=replace(facts.place_views, current=120, change=20),
            conversion_percent=8.0,
            conversion_change_pp=-2.0,
        ),
        "hours_mismatch": replace(facts, peak_hour=20, quiet_hour=20),
        "keyword_gap": replace(facts, top_keywords=(KeywordFact("새 검색어", 25),)),
        "blog_exposure": replace(facts, channels=(ChannelFact("블로그·카페", 5, 5.0, 0),)),
        "quiet_slot": replace(facts, quiet_hour=10, quiet_weekday="tue"),
        "smartcall_increase": replace(
            facts, smartcall_calls=replace(facts.smartcall_calls, current=8, change=3)
        ),
    }[kind]


@pytest.mark.parametrize(
    ("kind", "feature", "number"),
    [
        ("conversion_drop", "menu", "conversion_percent"),
        ("hours_mismatch", "business_hours", "peak_hour"),
        ("keyword_gap", "search_keywords", "keywords"),
        ("blog_exposure", None, "share_percent"),
        ("quiet_slot", "coupon", "quiet_hour"),
        ("smartcall_increase", "smartcall", "calls"),
    ],
)
def test_each_rule_hits_and_misses(facts, shop, kind, feature, number):
    assert build_insights(facts, shop) == []
    hits = build_insights(changed(facts, kind), shop)
    assert [hit.kind for hit in hits] == [kind]
    assert hits[0].action_feature == feature
    assert number in hits[0].evidence
    if feature:
        assert feature in {f["id"] for f in load_features()}


def test_real_report_and_seed_priorities_and_authoritative_keywords(shop):
    facts = report_facts(load_report())
    before = copy.deepcopy(shop)
    insights = build_insights(facts, shop)
    assert [i.kind for i in insights] == ["conversion_drop", "hours_mismatch", "keyword_gap"]
    assert [i.priority for i in insights] == sorted(i.priority for i in insights)
    assert insights[0].evidence["conversion_percent"] == 6.7
    assert insights[0].evidence["previous_conversion_percent"] == 10.0
    assert insights[1].evidence["peak_hour"] == 20
    schedules = insights[1].evidence["schedules"]
    assert {s["weekday"] for s in schedules} == {"mon", "tue", "wed", "thu"}
    assert all(s["close_time"] == "21:00" for s in schedules)
    gaps = insights[2].evidence
    assert gaps["representative_keywords"] == ["러시안 베이커리", "메도빅", "베이커리 카페"]
    assert gaps["keywords"] == [
        {"name": "이태원 케이크", "views": 126},
        {"name": "러시아 디저트", "views": 94},
        {"name": "꿀 케이크", "views": 52},
    ]
    assert shop == before


def test_no_shop_uses_report_keywords_and_closing_hour():
    insights = build_insights(report_facts(load_report()))
    assert insights[1].evidence["closing_hour"] == 21
    gaps = insights[2].evidence
    assert gaps["representative_keywords"] == ["러시안 베이커리", "이태원 케이크", "러시아 디저트"]
    assert [k["name"] for k in gaps["keywords"]] == ["메도빅", "꿀 케이크"]


def test_menu_names_and_keywords_ignore_spacing_and_case(facts, shop):
    facts = replace(
        facts,
        top_keywords=(KeywordFact("메 도 빅", 12), KeywordFact("BAKERY", 10)),
    )
    shop["shop"]["keywords"] = ["bakery"]
    assert build_insights(facts, shop) == []
    # Empty shop values are still authoritative; do not fall back to report values.
    assert build_insights(facts, {"shop": {}, "menus": []})[0].kind == "keyword_gap"


@pytest.mark.parametrize(("change", "conversion"), [(0, -1.0), (-5, -1.0), (5, 0.0), (5, None)])
def test_conversion_needs_both_view_growth_and_rate_drop(facts, shop, change, conversion):
    facts = replace(
        facts,
        place_views=replace(facts.place_views, change=change),
        conversion_change_pp=conversion,
    )
    assert build_insights(facts, shop) == []


@pytest.mark.parametrize(
    ("opening", "closing", "peak", "hit"),
    [
        ("09:00", "21:00", 20, True),
        ("09:00", "21:01", 20, False),
        ("09:30", "21:00", 9, True),
        ("09:00", "21:00", 9, False),
        ("18:00", "02:00", 1, True),
        ("18:00", "02:00", 23, False),
        ("09:00", "24:00", 23, True),
    ],
)
def test_hour_boundaries_minutes_and_overnight(facts, shop, opening, closing, peak, hit):
    facts = replace(facts, period="day", end="2026-11-05", peak_hour=peak, quiet_hour=peak)
    shop["shop"]["hours"]["thu"] = {"open_time": opening, "close_time": closing}
    assert bool(build_insights(facts, shop)) is hit


def test_daily_uses_actual_weekday_and_date_override(facts, shop):
    facts = replace(facts, period="day", end="2026-11-06", peak_hour=20, quiet_hour=20)
    assert build_insights(facts, shop) == []  # Friday closes at 22, report says 21.
    shop["shop"]["hours"][facts.end] = {"open_time": "09:00", "close_time": "20:30"}
    assert build_insights(facts, shop)[0].kind == "hours_mismatch"
    shop["shop"]["hours"][facts.end] = {"closed": True}
    assert build_insights(facts, shop)[0].evidence["schedules"][0]["reason"] == "closed"


def test_missing_hours_never_use_report_fallback(facts, shop):
    facts = replace(facts, peak_hour=20, quiet_hour=20)
    shop["shop"]["hours"] = {}
    assert build_insights(facts, shop) == []
    assert build_insights(facts)[0].kind == "hours_mismatch"


@pytest.mark.parametrize(
    ("views", "share", "change", "hit"),
    [(9, 9.0, 0, True), (10, 10.0, 0, False), (15, 15.0, -1, True), (20, 20.0, None, False)],
)
def test_blog_low_share_or_decline(facts, shop, views, share, change, hit):
    facts = replace(facts, channels=(ChannelFact("블로그·카페", views, share, change),))
    assert bool(build_insights(facts, shop)) is hit


def test_blog_share_can_decline_while_count_grows(facts, shop):
    facts = replace(
        facts,
        place_views=replace(facts.place_views, current=200, previous=100, change=100),
        channels=(ChannelFact("블로그·카페", 30, 15.0, 10),),
    )
    insight = build_insights(facts, shop)[0]
    assert insight.kind == "blog_exposure"
    assert insight.evidence["previous_share_percent"] == 20.0


def test_quiet_does_not_confuse_other_days_or_closed_hours(facts, shop):
    facts = replace(facts, period="day", end="2026-11-05", quiet_weekday="tue")
    assert build_insights(facts, shop) == []
    facts = replace(facts, quiet_hour=3)
    assert build_insights(facts, shop) == []
    facts = replace(facts, quiet_hour=10)
    assert build_insights(facts, shop)[0].evidence["quiet_hour"] == 10
    facts = replace(facts, quiet_hour=20)  # 마감 전이어도 아직 영업 중인 한산한 시간이다.
    assert build_insights(facts, shop)[0].evidence["quiet_hour"] == 20


def test_empty_or_no_comparison_data_has_no_spurious_insights(facts, shop):
    facts = replace(
        facts,
        place_views=replace(facts.place_views, current=0, previous=None, change=None),
        smartcall_calls=replace(facts.smartcall_calls, current=0, previous=None, change=None),
        peak_hour=None,
        quiet_hour=None,
        top_keywords=(),
        channels=(ChannelFact("블로그·카페", 0, 0.0, None),),
        conversion_change_pp=None,
    )
    assert build_insights(facts, shop) == []
