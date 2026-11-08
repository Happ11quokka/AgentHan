"""정기 보고 실행기: 스케줄러(cron 등)가 주기적으로 부르는 스크립트.

데모에서는 시각을 지정해 흉내 낸다.

    # 지금 보낼 보고·알림이 있는지 확인
    python scripts/run_report.py --store 홍만당 --check
    # 설정과 상관없이 보고서 바로 생성
    python scripts/run_report.py --store 홍만당 --now
    python scripts/run_report.py --store 홍만당 --check \
        --at "2026-11-09 09:00" --alerts-since 2026-10-24

실서비스: 이 스크립트를 짧은 간격(예: 10분)으로 실행하고, 결과를 채팅창 메시지로 보낸다.
"""

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from agenthan.agent.review import MenuCatalog, SnapshotRepo
from agenthan.agent.review.replies import MockReplyStore, ReplyAwareRepo
from agenthan.agent.review.report import ReportSettingsStore, check_alerts, next_run, run_report

ROOT = Path(__file__).resolve().parents[1]
SETTING_KEYS = ["enabled", "frequency", "weekday", "time", "focus", "alert"]

ap = argparse.ArgumentParser()
ap.add_argument("--store", default="홍만당")
ap.add_argument("--today", default="2026-11-07")
ap.add_argument(
    "--at", default=None, help="실행 시각 흉내 (YYYY-MM-DD HH:MM). 기본: 오늘 + 현재 시각"
)
ap.add_argument("--now", action="store_true", help="보고서 바로 생성")
ap.add_argument("--check", action="store_true", help="보낼 때가 된 보고·알림 확인")
ap.add_argument(
    "--alerts-since", default=None, help="이 날짜 이후 리뷰로 알림 확인 (기본: 마지막 확인일)"
)
ap.add_argument("--analysis", choices=["rule", "hcx"], default="rule")
ap.add_argument("--model", default="HCX-DASH-002")
a = ap.parse_args()

today = date.fromisoformat(a.today)
at = (
    datetime.strptime(a.at, "%Y-%m-%d %H:%M")
    if a.at
    else datetime.combine(today, datetime.now().time())
)
store = ReportSettingsStore(ROOT / "models" / "report_settings.json")
repo = ReplyAwareRepo(
    SnapshotRepo(ROOT / "data" / "snapshots" / "test_baseline.jsonl"),
    MockReplyStore(ROOT / "models" / "replies_mock.json", today=today),
)
st = store.get(a.store)
extra = {}
if a.analysis == "hcx":
    from agenthan.agent.review.hcx_llm import HCXReviewLLM, HCXSentiment

    llm = HCXReviewLLM(a.model, cache_path=ROOT / "models" / f"hcx_review_cache_{a.model}.jsonl")
    extra = {"llm": llm, "sentiment": HCXSentiment(llm)}
ctx = dict(
    store_id=a.store,
    today=at.date(),
    repo=repo,
    catalog=MenuCatalog.load(ROOT / "data" / "menus_sample.json"),
    **extra,
)

print(f"[{a.store}] 설정: {json.dumps({k: st.get(k) for k in SETTING_KEYS}, ensure_ascii=False)}")
if a.now:
    print(json.dumps(run_report(**ctx, settings=st), ensure_ascii=False, indent=2))
if a.check and not st.get("enabled"):
    print(
        "정기 보고·알림이 꺼져 있어 확인하지 않음 (대화에서 '매주 월요일 아침에 보고해줘'처럼 설정)"
    )
elif a.check:
    last = st.get("last_report_at")
    due = (
        next_run(st, datetime.fromisoformat(last))
        if last
        else next_run(st, at - timedelta(minutes=10))
    )
    if due and due <= at:
        print(f"보고 시각({due}) 도래 → 보고서 생성")
        print(
            json.dumps(
                run_report(**ctx, settings=st)["analysis"].get("insights"), ensure_ascii=False
            )
        )
        st["last_report_at"] = at.isoformat(timespec="minutes")
    else:
        print(f"보낼 보고 없음 (다음 보고: {next_run(st, at)})")
    since = date.fromisoformat(
        a.alerts_since or st.get("alerts_checked_on") or (at.date() - timedelta(days=1)).isoformat()
    )
    al = check_alerts(**ctx, settings=st, since=since)
    print(f"알림({al['mode']}, {since} 이후): {len(al['alerts'])}건")
    for x in al["alerts"]:
        mark = f"🚨 고위험({', '.join(x['risk']['words'])})" if x.get("risk") else "🔔"
        print(f"  {mark} {x['visit_date']} {x['sentiment']}: {x['quote']}")
    st["alerts_checked_on"] = at.date().isoformat()
    store.save(a.store, st)
