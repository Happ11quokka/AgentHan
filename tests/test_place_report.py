import copy
import json
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from agenthan.tools.place_report import (
    CHANNELS,
    WEEKDAYS,
    PlaceReportError,
    load_report,
    report_facts,
)


def row(end, views, requests, calls):
    return {
        "end": end,
        "metrics": {
            "place_views": views,
            "booking_order_requests": requests,
            "smartcall_calls": calls,
        },
        "channels": dict(zip(CHANNELS, [views, 0, 0, 0, 0, 0], strict=True)),
        "keywords": [{"name": name, "views": 1} for name in "abcdefghij"],
        "hourly_views": [views if h == 10 else 0 for h in range(24)],
        "weekday_views": {day: views if day == "mon" else 0 for day in WEEKDAYS},
        "gender_percent": {"여성": 60, "남성": 40},
        "age_percent": {"20대": 60, "30대": 40},
    }


@pytest.fixture
def data():
    weeks = [
        row(f"2026-11-{day:02}", views, requests, calls)
        for day, views, requests, calls in (
            (1, 20, 2, 1),
            (8, 40, 4, 2),
            (15, 60, 6, 3),
            (22, 80, 8, 4),
            (29, 100, 5, 5),
        )
    ]
    weeks[-2]["channels"] = dict(zip(CHANNELS, [20, 30, 10, 10, 5, 5], strict=True))
    weeks[-1].update(
        channels=dict(zip(CHANNELS, [30, 25, 20, 10, 10, 5], strict=True)),
        keywords=[
            {"name": name, "views": views}
            for name, views in zip(
                ["b", "a", "새 키워드", "c", "d", "e", "f", "g", "h", "i"],
                [5, 5, 9, 4, 3, 2, 1, 1, 1, 1],
                strict=True,
            )
        ],
        hourly_views=[{9: 2, 10: 48, 20: 48, 21: 2}.get(h, 0) for h in range(24)],
        weekday_views=dict(zip(WEEKDAYS, [10, 10, 15, 15, 20, 25, 5], strict=True)),
    )
    return {
        "_note": "실제 화면 확인 전 가정인 손계산 데이터",
        "store": {"name": "손계산 가게", "representative_keywords": ["a"], "closing_hour": 21},
        "week": weeks,
        "day": [row("2026-11-28", 80, 8, 4), row("2026-11-29", 100, 5, 5)],
    }


def test_hand_calculated_metrics(data):
    facts = report_facts(data)
    assert facts.average_periods == 4
    views = facts.place_views
    assert (views.current, views.previous, views.change, views.change_percent) == (100, 80, 20, 25)
    assert (views.four_week_average, views.average_difference, views.average_change_percent) == (
        50,
        50,
        100,
    )
    requests = facts.booking_order_requests
    assert (requests.change, requests.change_percent, requests.four_week_average) == (-3, -37.5, 5)
    assert (requests.average_difference, requests.average_change_percent) == (0, 0)
    calls = facts.smartcall_calls
    assert (calls.change, calls.change_percent, calls.four_week_average) == (1, 25, 2.5)
    assert (calls.average_difference, calls.average_change_percent) == (2.5, 100)


def test_average_and_difference_round_half_up_independently(data):
    data["week"] = [
        row(f"2026-11-{day:02}", views, requests, calls)
        for day, views, requests, calls in (
            (1, 5, 1, 0),
            (8, 5, 1, 0),
            (15, 5, 1, 0),
            (22, 6, 2, 1),
            (29, 6, 1, 0),
        )
    ]
    facts = report_facts(data)
    # 평균 5.25와 차이 0.75를 각각 반올림한다. 표시 평균 5.3을 빼지 않는다.
    assert facts.place_views.four_week_average == 5.3
    assert facts.place_views.average_difference == 0.8
    assert facts.place_views.average_change_percent == 14.3
    assert facts.booking_order_requests.four_week_average == 1.3
    assert facts.booking_order_requests.average_difference == -0.3
    assert facts.booking_order_requests.average_change_percent == -20
    assert facts.smartcall_calls.four_week_average == 0.3
    assert facts.smartcall_calls.average_difference == -0.3
    assert facts.smartcall_calls.average_change_percent == -100


def test_hand_calculated_channels_keywords_and_extremes(data):
    facts = report_facts(data)
    assert sum(c.share_percent for c in facts.channels) == pytest.approx(100, abs=0.1)
    assert facts.channels[0].share_percent == 30
    assert facts.top_channel == "네이버 검색"
    # 검색·목록 둘 다 +10: 이름순으로 검색을 고른다.
    assert (facts.most_increased_channel.name, facts.most_increased_channel.change) == (
        "네이버 검색",
        10,
    )
    assert (facts.most_decreased_channel.name, facts.most_decreased_channel.change) == (
        "네이버 지도",
        -5,
    )
    assert [k.name for k in facts.top_keywords] == ["새 키워드", "a", "b", "c", "d"]
    assert [(k.name, k.views) for k in facts.new_keywords] == [("새 키워드", 9)]
    assert (facts.peak_hour, facts.quiet_hour) == (10, 9)
    assert (facts.peak_weekday, facts.quiet_weekday) == ("sat", "sun")
    assert (facts.largest_gender_group, facts.largest_age_group) == ("여성", "20대")
    assert facts.conversion_percent == 5
    assert facts.previous_conversion_percent == 10
    assert facts.conversion_change_pp == -5


def test_zero_previous_and_zero_current(data):
    data["day"] = [row("2026-11-28", 0, 0, 0), row("2026-11-29", 100, 5, 5)]
    facts = report_facts(data, "day")
    assert facts.place_views.change == 100
    assert facts.place_views.change_percent is None
    assert facts.place_views.average_change_percent is None
    assert facts.previous_conversion_percent is None
    assert facts.conversion_percent == 5
    assert facts.conversion_change_pp is None
    data["day"][-1] = row("2026-11-29", 0, 0, 0)
    facts = report_facts(data, "day")
    assert facts.conversion_percent is None
    assert facts.top_channel is None
    assert facts.peak_hour is None and facts.quiet_hour is None
    assert facts.most_increased_channel is None and facts.most_decreased_channel is None
    assert sum(c.share_percent for c in facts.channels) == 0


def test_ties_use_name_order_regardless_of_input_order(data):
    current = data["week"][-1]
    current["channels"] = dict(zip(reversed(CHANNELS), [0, 0, 0, 0, 50, 50], strict=True))
    current["weekday_views"] = {
        "sun": 0,
        "sat": 0,
        "fri": 0,
        "thu": 0,
        "wed": 0,
        "tue": 50,
        "mon": 50,
    }
    current["gender_percent"] = {"여성": 50, "남성": 50}
    current["age_percent"] = {"30대": 50, "20대": 50}
    data["week"][-2]["channels"] = dict(zip(CHANNELS, [20, 20, 20, 20, 0, 0], strict=True))
    facts = report_facts(data)
    assert facts.top_channel == "네이버 검색"
    assert facts.most_increased_channel.name == "네이버 검색"
    assert facts.most_decreased_channel.name == "블로그·카페"
    assert (facts.peak_weekday, facts.quiet_weekday) == ("mon", "fri")
    assert (facts.largest_gender_group, facts.largest_age_group) == ("남성", "20대")


def test_channel_share_rounding_keeps_total_and_breaks_remainder_ties_by_name(data):
    data["day"] = [row("2026-11-29", 6, 1, 1)]
    data["day"][0]["channels"] = dict.fromkeys(reversed(CHANNELS), 1)
    facts = report_facts(data, "day")
    shares = {c.name: c.share_percent for c in facts.channels}
    assert sum(shares.values()) == pytest.approx(100)
    assert [shares[name] for name in sorted(CHANNELS)] == [16.7, 16.7, 16.7, 16.7, 16.6, 16.6]


def test_cutoff_history_and_missing_previous(data):
    data["week"].reverse()
    snapshot = copy.deepcopy(data)
    facts = report_facts(data, end=date(2026, 11, 25))
    assert facts.end == "2026-11-22"
    assert facts.place_views.four_week_average == 40
    assert facts.average_periods == 3
    first = report_facts(data, end="2026-11-01")
    assert first.place_views.previous is None and first.place_views.change is None
    assert first.place_views.four_week_average is None
    assert first.new_keywords == ()
    assert data == snapshot
    with pytest.raises(FrozenInstanceError):
        facts.end = "2026-11-01"
    with pytest.raises(FrozenInstanceError):
        facts.channels[0].views = 1


def test_daily_average_window_and_unrounded_conversion_difference(data):
    start = date(2026, 10, 1)
    data["day"] = [
        row((start + timedelta(days=i)).isoformat(), 1000 if i == 0 else 32, 1, 1)
        for i in range(30)
    ]
    data["day"][-1] = row("2026-10-30", 64, 4, 1)
    facts = report_facts(data, "day")
    assert facts.average_periods == 28
    assert facts.place_views.four_week_average == 32
    # 6.25 - 3.125 = 3.125 -> 3.1; 표시값 6.3 - 3.1로 재계산하지 않는다.
    assert facts.conversion_percent == 6.3
    assert facts.previous_conversion_percent == 3.1
    assert facts.conversion_change_pp == 3.1


@pytest.mark.parametrize("period", ["day", "week"])
def test_packaged_dummy_patterns(period):
    data = load_report()
    assert len(data["week"]) == 5 and len(data["day"]) == 14
    assert "실제 화면 확인 전 가정" in data["_note"]
    assert "review" not in json.dumps(data).lower() and "리뷰" not in str(data["store"])
    facts = report_facts(data, period)
    assert facts.store_name == "샘플 러시안 베이커리"
    assert facts.place_views.change > 0 and facts.booking_order_requests.change < 0
    assert facts.peak_hour == facts.closing_hour - 1 == 20
    assert facts.top_keywords[0].name == "메도빅"
    assert facts.top_keywords[0].name not in facts.representative_keywords
    assert facts.conversion_change_pp < 0


@pytest.mark.parametrize(
    "damage",
    [
        lambda d: d.pop("store"),
        lambda d: d["week"][0]["metrics"].pop("smartcall_calls"),
        lambda d: d["week"][0]["metrics"].update(place_views=-1),
        lambda d: d["week"][0]["metrics"].update(place_views=True),
        lambda d: d["week"][0]["channels"].update({"외부": -1}),
        lambda d: d["week"][0]["keywords"][0].update(views=-1),
        lambda d: d["week"][0]["keywords"].append({"name": "a", "views": 1}),
        lambda d: d["week"][0].update(hourly_views=[0] * 23),
        lambda d: d["week"][0]["hourly_views"].__setitem__(0, -1),
        lambda d: d["week"][0]["weekday_views"].pop("sun"),
        lambda d: d["week"][0]["gender_percent"].update({"여성": float("nan")}),
        lambda d: d["week"][0]["age_percent"].update({"20대": 101}),
        lambda d: d["week"][0].update(end="2026-11-32"),
        lambda d: d["week"].__setitem__(0, d["week"][1]),
        lambda d: d["week"].pop(1),
        lambda d: d.update(day=None),
        lambda d: d.update(week=[]),
        lambda d: d["store"].update(closing_hour=25),
    ],
)
def test_invalid_data_is_sanitized_for_load_and_calculation(data, tmp_path, damage):
    damage(data)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for operation in (lambda: load_report(path), lambda: report_facts(data)):
        with pytest.raises(PlaceReportError, match="리포트 형식") as error:
            operation()
        assert error.value.__suppress_context__
        assert str(path) not in str(error.value)


@pytest.mark.parametrize("value", [None, [], "원시 입력", {"_note": "메모"}])
def test_invalid_root_does_not_leak_raw_errors(value):
    with pytest.raises(PlaceReportError):
        report_facts(value)


@pytest.mark.parametrize("content", [b"not json", b"\xff", b"[]"])
def test_unreadable_json_is_sanitized(tmp_path, content):
    path = tmp_path / "report.json"
    path.write_bytes(content)
    with pytest.raises(PlaceReportError):
        load_report(path)
    with pytest.raises(PlaceReportError):
        load_report(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "kwargs", [{"period": "month"}, {"end": "2026-11-32"}, {"end": 123}, {"end": "2020-01-01"}]
)
def test_invalid_query_is_sanitized(data, kwargs):
    with pytest.raises(PlaceReportError):
        report_facts(data, **kwargs)
