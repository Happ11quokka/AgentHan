"""로컬 플레이스 리포트의 숫자만 계산한다. LLM·네트워크·저장소 쓰기는 없다.

항목 이름과 단위는 실제 화면 확인 전 가정이다. 조회·신청·통화는 건수,
비중·증감률은 %, 전환율 차이는 %p다. 리뷰 항목은 다루지 않는다.

JSON 계약: _note, store(name, representative_keywords, closing_hour), day/week 배열.
각 행은 end(YYYY-MM-DD, 포함), metrics(요약 카드 3개), channels(6개 채널),
keywords([{name, views}], 최대 10개), hourly_views(0~23시 24칸),
weekday_views(mon~sun), gender_percent, age_percent를 가진다.
채널·시간·요일 건수의 합은 각각 place_views와 같아야 한다.
기간별 행은 날짜순일 필요가 없지만 중복·누락 없이 일간은 1일, 주간은 7일 간격이다.
일간·주간은 각각 독립된 자료이며 서로 합산하지 않는다.

end는 조회 기준일(문자열 또는 date)이며 그날까지 끝난 가장 최근 행을 고른다.
이전 행이 없으면 이전 값·증감·평균은 None이다. 4주 평균은 현재 행을 제외하고
직전 28일 안의 일간 행 또는 직전 4개 주간 행의 평균이다. 자료가 짧으면
가용 행만 쓰며 average_periods에 표본 수를 표시한다. 미래 자료는 쓰지 않는다.
평균 대비는 건수 차이와 증감률 모두 반환한다. 분모가 0이면 비율은 None이다.
계산은 반올림 전 값으로 하고 출력만 소수 1자리(사사오입), 건수는 정수로 둔다.
채널 비중은 0.1% 단위 최대 나머지법으로 합계 100%를 보장한다(조회 0이면 모두 0).
동점은 이름의 유니코드 오름차순, 시간은 이른 순이다. 한산한 시간만 0을 제외한다.
증가/감소 채널은 양수/음수 변화가 있을 때만 반환한다. 신규 키워드는 현재 상위
10개 중 이전 상위 10개에 없던 이름이며, 이전 행이 없으면 판별하지 않는다.
"""

import json
import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

ReportPeriod = Literal["day", "week"]
DEFAULT_PATH = Path(__file__).with_name("place_report_data.json")
METRICS = ("place_views", "booking_order_requests", "smartcall_calls")
CHANNELS = ("네이버 검색", "네이버 지도", "플레이스 목록", "블로그·카페", "외부", "기타")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NUMBER_STEP = Decimal("0.1")
SHARE_UNITS = int(Decimal(100) / NUMBER_STEP)


class PlaceReportError(Exception):
    """경로·원시 데이터·내부 예외를 노출하지 않는 리포트 오류."""


@dataclass(frozen=True)
class MetricChange:
    current: int
    previous: int | None
    change: int | None
    change_percent: float | None
    four_week_average: float | None
    average_difference: float | None
    average_change_percent: float | None


@dataclass(frozen=True)
class ChannelFact:
    name: str
    views: int
    share_percent: float
    change: int | None


@dataclass(frozen=True)
class KeywordFact:
    name: str
    views: int


@dataclass(frozen=True)
class BreakdownChange:
    current: int
    previous: int | None
    change: int | None
    change_percent: float | None


@dataclass(frozen=True)
class ReportFacts:
    period: ReportPeriod
    end: str
    store_name: str
    representative_keywords: tuple[str, ...]
    closing_hour: int
    average_periods: int
    place_views: MetricChange
    booking_order_requests: MetricChange
    smartcall_calls: MetricChange
    channels: tuple[ChannelFact, ...]
    top_channel: str | None
    most_increased_channel: ChannelFact | None
    most_decreased_channel: ChannelFact | None
    top_keywords: tuple[KeywordFact, ...]
    new_keywords: tuple[KeywordFact, ...]
    peak_hour: int | None
    quiet_hour: int | None
    peak_weekday: str
    quiet_weekday: str
    largest_gender_group: str | None
    largest_age_group: str | None
    conversion_percent: float | None
    previous_conversion_percent: float | None
    conversion_change_pp: float | None
    hourly_changes: tuple[BreakdownChange, ...]
    weekday_changes: dict[str, BreakdownChange]


def _rounded(value: Decimal) -> float:
    """모든 비율·평균 출력에 쓰는 유일한 반올림 규칙."""
    return float(value.quantize(NUMBER_STEP, rounding=ROUND_HALF_UP))


def _ratio(numerator: int | Decimal, denominator: int | Decimal) -> Decimal | None:
    return Decimal(numerator) * 100 / Decimal(denominator) if denominator else None


def _optional_round(value: Decimal | None) -> float | None:
    return None if value is None else _rounded(value)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def _date(value: str) -> date:
    _require(isinstance(value, str))
    parsed = date.fromisoformat(value)
    _require(parsed.isoformat() == value)
    return parsed


def _counts(values: dict, keys: tuple[str, ...]) -> None:
    _require(isinstance(values, dict) and set(values) == set(keys))
    _require(all(_count(v) for v in values.values()))


def _distribution(values: dict, views: int) -> None:
    _require(isinstance(values, dict) and bool(values))
    _require(all(_text(k) for k in values))
    _require(
        all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 100 for v in values.values())
    )
    total = sum(values.values())
    _require(abs(total - 100) <= 0.1 or (views == 0 and total == 0))


def _validate(data: dict) -> None:
    try:
        _require(isinstance(data, dict) and _text(data["_note"]))
        store = data["store"]
        _require(isinstance(store, dict) and _text(store["name"]))
        _require(type(store["closing_hour"]) is int and 1 <= store["closing_hour"] <= 24)
        _require(isinstance(store["representative_keywords"], list))
        _require(all(_text(k) for k in store["representative_keywords"]))
        for period, step in (("day", 1), ("week", 7)):
            rows = data[period]
            _require(isinstance(rows, list) and bool(rows))
            dates = []
            for row in rows:
                _require(isinstance(row, dict))
                dates.append(_date(row["end"]))
                _counts(row["metrics"], METRICS)
                views = row["metrics"]["place_views"]
                _counts(row["channels"], CHANNELS)
                _counts(row["weekday_views"], WEEKDAYS)
                hourly = row["hourly_views"]
                _require(isinstance(hourly, list) and len(hourly) == 24)
                _require(all(_count(v) for v in hourly))
                _require(sum(hourly) == sum(row["channels"].values()) == views)
                _require(sum(row["weekday_views"].values()) == views)
                keywords = row["keywords"]
                _require(isinstance(keywords, list) and len(keywords) <= 10)
                for keyword in keywords:
                    _require(isinstance(keyword, dict) and _text(keyword["name"]))
                    _require(_count(keyword["views"]))
                _require(len({k["name"] for k in keywords}) == len(keywords))
                _distribution(row["gender_percent"], views)
                _distribution(row["age_percent"], views)
            dates.sort()
            _require(all((b - a).days == step for a, b in zip(dates, dates[1:], strict=False)))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PlaceReportError(
            "리포트 형식과 필수 항목·날짜·음수가 아닌 숫자를 확인해 주세요."
        ) from None


def load_report(path: str | Path = DEFAULT_PATH) -> dict:
    """검사한 로컬 JSON을 반환한다. 파일 경로나 원문은 오류에 넣지 않는다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise PlaceReportError(
            "리포트 파일을 읽을 수 없습니다. JSON 파일을 확인해 주세요."
        ) from None
    _validate(data)
    return data


def _metric(current: int, previous: int | None, history: list[int]) -> MetricChange:
    average = Decimal(sum(history)) / len(history) if history else None
    change = current - previous if previous is not None else None
    difference = Decimal(current) - average if average is not None else None
    return MetricChange(
        current=current,
        previous=previous,
        change=change,
        change_percent=_optional_round(_ratio(change, previous)) if previous is not None else None,
        four_week_average=_optional_round(average),
        average_difference=_optional_round(difference),
        average_change_percent=(
            _optional_round(_ratio(difference, average)) if average is not None else None
        ),
    )


def _breakdown(current: int, previous: int | None) -> BreakdownChange:
    change = current - previous if previous is not None else None
    return BreakdownChange(
        current, previous, change, _optional_round(_ratio(change, previous)) if previous else None
    )


def _extreme(values: dict, *, minimum: bool = False, positive: bool = False):
    candidates = [k for k, v in values.items() if not positive or v > 0]
    return min(candidates, key=lambda k: (values[k] if minimum else -values[k], k), default=None)


def _shares(channels: dict) -> dict[str, float]:
    """0.1%를 배분한 뒤 큰 나머지 순(동점 이름순)으로 잔여 단위를 나눈다."""
    total = sum(channels.values())
    if not total:
        return dict.fromkeys(channels, 0.0)
    units = {k: v * SHARE_UNITS // total for k, v in channels.items()}
    order = sorted(channels, key=lambda k: (-(channels[k] * SHARE_UNITS % total), k))
    for key in order[: SHARE_UNITS - sum(units.values())]:
        units[key] += 1
    return {k: _rounded(Decimal(v) * NUMBER_STEP) for k, v in units.items()}


def report_facts(
    data: dict, period: ReportPeriod = "week", end: str | date | None = None
) -> ReportFacts:
    """기준일까지 끝난 가장 최근 한 기간의 검증된 숫자를 반환한다."""
    _validate(data)
    if period not in ("day", "week"):
        raise PlaceReportError("기간은 day 또는 week로 지정해 주세요.")
    try:
        cutoff = date.max if end is None else _date(end.isoformat() if type(end) is date else end)
    except (ValueError, TypeError):
        raise PlaceReportError("종료일은 YYYY-MM-DD 날짜로 지정해 주세요.") from None
    rows = sorted((r for r in data[period] if _date(r["end"]) <= cutoff), key=lambda r: r["end"])
    if not rows:
        raise PlaceReportError("종료일 이전에 조회할 리포트가 없습니다.")
    current = rows[-1]
    previous = rows[-2] if len(rows) > 1 else None
    # 날짜를 빼지 않고 차이로 비교하므로 date.min 근처에서도 안전하다.
    history = [r for r in rows[:-1] if (_date(current["end"]) - _date(r["end"])).days <= 28]
    metrics = {
        key: _metric(
            current["metrics"][key],
            previous["metrics"][key] if previous else None,
            [r["metrics"][key] for r in history],
        )
        for key in METRICS
    }
    shares = _shares(current["channels"])
    channels = tuple(
        ChannelFact(
            name,
            views,
            shares[name],
            views - previous["channels"][name] if previous else None,
        )
        for name, views in sorted(current["channels"].items(), key=lambda item: (-item[1], item[0]))
    )
    increases = sorted(
        (c for c in channels if c.change is not None and c.change > 0),
        key=lambda c: (-c.change, c.name),
    )
    decreases = sorted(
        (c for c in channels if c.change is not None and c.change < 0),
        key=lambda c: (c.change, c.name),
    )
    keywords = tuple(
        KeywordFact(k["name"], k["views"])
        for k in sorted(current["keywords"], key=lambda k: (-k["views"], k["name"]))
    )
    old_keywords = {k["name"] for k in previous["keywords"]} if previous else set()
    conversion = _ratio(
        current["metrics"]["booking_order_requests"], metrics["place_views"].current
    )
    old_conversion = (
        _ratio(previous["metrics"]["booking_order_requests"], metrics["place_views"].previous)
        if previous
        else None
    )
    return ReportFacts(
        period=period,
        end=current["end"],
        store_name=data["store"]["name"],
        representative_keywords=tuple(data["store"]["representative_keywords"]),
        closing_hour=data["store"]["closing_hour"],
        average_periods=len(history),
        **metrics,
        channels=channels,
        top_channel=_extreme(current["channels"], positive=True),
        most_increased_channel=increases[0] if increases else None,
        most_decreased_channel=decreases[0] if decreases else None,
        top_keywords=keywords[:5],
        new_keywords=tuple(k for k in keywords if k.name not in old_keywords) if previous else (),
        peak_hour=_extreme(dict(enumerate(current["hourly_views"])), positive=True),
        quiet_hour=_extreme(dict(enumerate(current["hourly_views"])), minimum=True, positive=True),
        peak_weekday=_extreme(current["weekday_views"]),
        quiet_weekday=_extreme(current["weekday_views"], minimum=True),
        largest_gender_group=_extreme(current["gender_percent"], positive=True),
        largest_age_group=_extreme(current["age_percent"], positive=True),
        conversion_percent=_optional_round(conversion),
        previous_conversion_percent=_optional_round(old_conversion),
        conversion_change_pp=(
            _rounded(conversion - old_conversion)
            if conversion is not None and old_conversion is not None
            else None
        ),
        hourly_changes=tuple(
            _breakdown(value, previous["hourly_views"][hour] if previous else None)
            for hour, value in enumerate(current["hourly_views"])
        ),
        weekday_changes={
            day: _breakdown(
                current["weekday_views"][day], previous["weekday_views"][day] if previous else None
            )
            for day in WEEKDAYS
        },
    )
