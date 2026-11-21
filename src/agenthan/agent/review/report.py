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
    if not (changed.get("frequency") or cur.get("frequency")):
        # 주기를 처음 정하는데 사장님이 말하지 않았다. 말하지 않은 값으로는 저장하지 않는다:
        # 말한 값(알림·시간 등)만 저장하고 정기 보고는 켜지 않은 채 주기를 제안한다.
        # 사장님이 '응' 하면 그 주기로 켠다 (pending_suggestion)
        if changed:
            cur = {**cur, **changed, "updated_at": now.isoformat(timespec="minutes")}
            settings_store.save(store_id, cur)
        return {
            "action": "suggest",
            "suggested": suggest_frequency(repo, store_id, today),
            "changed": changed,
            "settings": cur,
        }
    new = {
        **cur,
        **changed,
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
        store_id=store_id,
        today=today,
        repo=repo,
        sentiment=sentiment or RuleSentiment(),
        priority="negative_first",
    )
    return {
        "frequency": freq,
        "analysis": analysis,
        "unanswered": {
            "total": unanswered["total"],
            "negative_count": unanswered["negative_count"],
            # 급한 순서의 답글 안 단 불만 리뷰 (보고 메시지의 [불만 리뷰 답글 초안] 칩 대상)
            "negative_ids": [
                r["review_id"]
                for r in unanswered["reviews"]
                if r["sentiment"] in ("negative", "mixed")
            ],
        },
        "suggest_increase_reviews": bool(analysis.get("low_review")),
    }


FREQ_KO = {"daily": "매일", "weekly": "매주", "biweekly": "격주", "monthly": "매달 1일"}
WEEKDAY_KO = dict(zip(WEEKDAYS, "월화수목금토일", strict=True))


def schedule_text(settings: dict) -> str | None:
    """'매일 09:00', '매주 월요일 09:00'. 꺼져 있거나 주기가 없으면 None."""
    freq = settings.get("frequency")
    if not settings.get("enabled") or freq not in FREQ_KO:
        return None
    day = f" {WEEKDAY_KO.get(settings.get('weekday'), '월')}요일" if "weekly" in freq else ""
    return f"{FREQ_KO[freq]}{day} {settings.get('time') or DEFAULTS['time']}"


def report_digest(result: dict, title: str = "리뷰 정기 보고") -> str:
    """정기 보고 한 회분(run_report 결과) → 대화에 넣을 짧은 요약. 숫자는 결과 그대로 쓴다."""
    a = result.get("analysis") or {}
    p = a.get("period") or {}
    head = title + (f" ({p.get('start') or '…'} ~ {p.get('end')})" if p.get("end") else "")
    names = {"positive": "긍정", "negative": "부정", "mixed": "혼합", "neutral": "중립"}
    count = f"리뷰 {a.get('review_count', 0)}건"
    mood = [f"{names.get(k, k)} {v}" for k, v in (a.get("sentiment") or {}).items() if v]
    ideas = a.get("report_ideas") or {}
    top = [x["display"] for x in ideas.get("complaints_top", [])][:2] or [
        x["display"]
        for x in sorted(a.get("aspects", []), key=lambda x: -x.get("negative", 0))
        if x.get("negative")
    ][:2]
    if a.get("review_count"):
        body = " · ".join([count, *mood]) + (f". 많이 나온 불만: {', '.join(top)}" if top else "")
    else:  # '리뷰 0건' 옆에 불만 N건이 나오면 숫자가 어긋나 보인다
        body = "이번 기간에는 새 리뷰가 없어요."
    # 답글 안 단 불만은 보고 기간이 아니라 전체 기간 기준이다
    n = (result.get("unanswered") or {}).get("negative_count", 0)
    todo = (
        f"아직 답글을 안 단 불만 리뷰가 모두 {n}건 있어요."
        if n
        else "아직 답글을 안 단 불만 리뷰는 없어요."
    )
    return "\n".join([head, body, todo])


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
