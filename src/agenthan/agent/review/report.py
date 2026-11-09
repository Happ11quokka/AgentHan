"""정기 보고 루틴: 보고 주기 제안 → 설정(사장님 승인) → 정해진 때 보고서 생성, 부정 리뷰는 즉시
알림.

주기 제안 기준 (최근 4주 주평균 리뷰 수):
  15건 이상 daily / 3~14건 weekly / 1~2건 biweekly / 1건 미만 monthly (+ 리뷰 늘리기 제안)
스냅샷은 표본이라 오래된 기간일수록 리뷰가 빠져 있을 수 있다. 최근 4주는 비교적 온전하다.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .analyze import analyze_reviews
from .replies import get_unanswered_reviews
from .repo import ReviewRepo
from .sentiment import RuleSentiment

FREQUENCIES = ["daily", "weekly", "biweekly", "monthly"]
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULTS = {
    "enabled": False,
    "frequency": None,
    "weekday": "mon",
    "time": "09:00",
    "focus": "all",
    "alert": "negative",
}


def suggest_frequency(repo: ReviewRepo, store_id: str, today: date) -> dict:
    n = len(repo.get_reviews(store_id, today - timedelta(days=28), today))
    avg = n / 4
    if avg >= 15:
        freq, why = "daily", "리뷰가 하루에도 여러 건 올라와요"
    elif avg >= 3:
        freq, why = "weekly", "주에 여러 건씩 올라와요"
    elif avg >= 1:
        freq, why = "biweekly", "주에 1~2건 정도라 2주에 한 번 모아 보는 게 좋아요"
    else:
        freq, why = "monthly", "리뷰가 뜸해서 한 달에 한 번이면 충분해요"
    return {
        "frequency": freq,
        "weekly_avg": round(avg, 1),
        "reviews_28d": n,
        "reason": why,
        "suggest_increase_reviews": freq == "monthly",
    }


class ReportSettingsStore:
    """매장별 보고 설정 + 마지막 알림 확인 시각. 실서비스에서는 팀 DB로 바꾼다."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.data: dict[str, dict] = {}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def get(self, store_id: str) -> dict:
        return {**DEFAULTS, **self.data.get(store_id, {})}

    def save(self, store_id: str, settings: dict) -> dict:
        self.data[store_id] = settings
        if self.path:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return settings


def _norm_time(t: str | None) -> str | None:
    if not t:
        return None
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})?", str(t).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ValueError(f"시간 형식이 잘못됨: {t}")
    return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"


def next_run(settings: dict, now: datetime) -> datetime | None:
    """설정 기준 다음 보고 시각. 꺼져 있으면 None. biweekly는 설정일 기준 2주 간격."""
    if not settings.get("enabled") or not settings.get("frequency"):
        return None
    hh, mm = map(int, settings["time"].split(":"))
    at = time(hh, mm)
    if settings["frequency"] == "daily":
        cand = datetime.combine(now.date(), at)
        return cand if cand > now else cand + timedelta(days=1)
    if settings["frequency"] == "monthly":  # 매달 1일
        first = now.date().replace(day=1)
        cand = datetime.combine(first, at)
        if cand <= now:
            nxt = (first + timedelta(days=32)).replace(day=1)
            cand = datetime.combine(nxt, at)
        return cand
    wd = WEEKDAYS.index(settings["weekday"])
    days = (wd - now.weekday()) % 7
    cand = datetime.combine(now.date() + timedelta(days=days), at)
    if cand <= now:
        cand += timedelta(days=7)
    if settings["frequency"] == "biweekly" and settings.get("anchor"):
        anchor = date.fromisoformat(settings["anchor"])
        while ((cand.date() - anchor).days // 7) % 2:
            cand += timedelta(days=7)
    return cand


def set_review_report(
    *,
    store_id: str,
    today: date,
    repo: ReviewRepo,
    settings_store: ReportSettingsStore,
    action: str | None = None,
    frequency: str | None = None,
    weekday: str | None = None,
    time: str | None = None,
    focus: str | None = None,
    alert: str | None = None,
    now: datetime | None = None,
    **_,
) -> dict:
    """action: set(기본) / suggest(제안만, 바꾸지 않음) / off. set은 넘어온 항목만 바꾼다."""
    action = action or "set"
    now = now or datetime.combine(today, datetime.min.time())
    cur = settings_store.get(store_id)
    if action == "suggest":
        return {
            "action": "suggest",
            "suggested": suggest_frequency(repo, store_id, today),
            "settings": cur,
        }
    if action == "off":
        cur["enabled"] = False
        settings_store.save(store_id, cur)
        return {"action": "off", "settings": cur, "next_run": None}
    changed = {}
    if frequency:
        if frequency not in FREQUENCIES:
            raise ValueError(f"알 수 없는 주기: {frequency}")
        changed["frequency"] = frequency
    if weekday:
        if weekday not in WEEKDAYS:
            raise ValueError(f"알 수 없는 요일: {weekday}")
        changed["weekday"] = weekday
    if time:
        changed["time"] = _norm_time(time)
    if focus in ("all", "complaint"):
        changed["focus"] = focus
    if alert in ("negative", "every_new", "none"):
        changed["alert"] = alert
    if not (
        changed.get("frequency") or cur.get("frequency")
    ):  # 주기를 처음 정하는데 말하지 않았으면 제안값으로
        changed["frequency"] = suggest_frequency(repo, store_id, today)["frequency"]
        changed["from_suggestion"] = True
    new = {
        **cur,
        **{k: v for k, v in changed.items() if k != "from_suggestion"},
        "enabled": True,
        "updated_at": now.isoformat(timespec="minutes"),
    }
    if new["frequency"] == "biweekly" and (changed.get("frequency") or not new.get("anchor")):
        new["anchor"] = today.isoformat()
    settings_store.save(store_id, new)
    nr = next_run(new, now)
    return {
        "action": "set",
        "changed": changed,
        "settings": new,
        "next_run": nr.isoformat(timespec="minutes") if nr else None,
    }


PERIOD_OF = {"daily": {"period": "1d"}, "weekly": {"period": "7d"}, "monthly": {"period": "30d"}}


def run_report(
    *,
    store_id: str,
    today: date,
    repo: ReviewRepo,
    settings: dict,
    sentiment=None,
    llm=None,
    catalog=None,
    **_,
) -> dict:
    """정기 보고서 한 회분. '지금 실행'(데모)과 스케줄러가 같은 함수를 쓴다."""
    freq = settings.get("frequency") or "weekly"
    window = PERIOD_OF.get(freq) or {"start_date": (today - timedelta(days=14)).isoformat()}
    analysis = analyze_reviews(
        store_id=store_id,
        today=today,
        repo=repo,
        catalog=catalog,
        sentiment=sentiment,
        llm=llm,
        focus=settings.get("focus") or "all",
        format="report",
        **window,
    )
    unanswered = get_unanswered_reviews(
        store_id=store_id, today=today, repo=repo, sentiment=sentiment or RuleSentiment()
    )
    return {
        "frequency": freq,
        "analysis": analysis,
        "unanswered": {
            "total": unanswered["total"],
            "negative_count": unanswered["negative_count"],
        },
        "suggest_increase_reviews": bool(analysis.get("low_review")),
    }


def check_alerts(
    *,
    store_id: str,
    today: date,
    repo: ReviewRepo,
    settings: dict,
    since: date,
    sentiment=None,
    **_,
) -> dict:
    """since 이후 새 리뷰 중 알림 대상(부정만 / 전부)을 찾는다. 스케줄러가 짧은 간격으로 부른다."""
    mode = settings.get("alert", "negative")
    if mode == "none":
        return {"alerts": [], "mode": mode}
    from .harsh import harsh_of
    from .replies import _quote, _review_sentiment, risk_of

    sentiment = sentiment or RuleSentiment()
    new = [r for r in repo.get_reviews(store_id, since + timedelta(days=1), today)]
    from .sentiment import prefetch_reviews

    prefetch_reviews(sentiment, new)
    out = []
    for r in new:
        s = _review_sentiment(r, sentiment)
        if mode == "every_new" or s in ("negative", "mixed"):
            out.append(
                {
                    "review_id": r["review_id"],
                    "visit_date": r["visit_date"],
                    "sentiment": s,
                    "risk": risk_of(r["text"], s),
                    "quote": _quote(r["text"]),
                    "softened": bool(harsh_of(r["text"])),
                }
            )
    out.sort(key=lambda x: 0 if x["risk"] else 1)  # 고위험 먼저
    return {"alerts": out, "mode": mode, "since": since.isoformat()}
