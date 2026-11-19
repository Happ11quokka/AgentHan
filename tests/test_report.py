"""정기 보고 루틴 테스트 (API 키 불필요)."""

from datetime import date, datetime
from pathlib import Path

from agenthan.agent.review import MenuCatalog, SnapshotRepo
from agenthan.agent.review.report import (
    ReportSettingsStore,
    check_alerts,
    next_run,
    run_report,
    set_review_report,
    suggest_frequency,
)
from agenthan.agent.review.report_parse import parse_report_request

ROOT = Path(__file__).resolve().parents[1]

TODAY = date(2026, 11, 7)  # 토요일
NOW = datetime(2026, 11, 7, 18, 0)
REPO = SnapshotRepo(ROOT / "fixtures" / "review_snapshot.jsonl")


def test_suggestion_differs_by_store():
    h = suggest_frequency(REPO, "달빛모찌", TODAY)
    b = suggest_frequency(REPO, "황금반점", TODAY)
    assert h["frequency"] == "biweekly" and h["reviews_28d"] == 4  # 최근 4주 4건 → 주 1건
    assert b["frequency"] == "daily"  # 리뷰가 많은 매장


def test_set_change_off_and_next_run():
    st = ReportSettingsStore()
    r = set_review_report(
        store_id="달빛모찌",
        today=TODAY,
        repo=REPO,
        settings_store=st,
        frequency="weekly",
        weekday="mon",
        time="9",
        now=NOW,
    )
    assert r["settings"]["time"] == "09:00" and r["next_run"] == "2026-11-09T09:00"  # 다음 월요일
    r = set_review_report(
        store_id="달빛모찌", today=TODAY, repo=REPO, settings_store=st, time="08:30", now=NOW
    )
    assert (
        r["settings"]["frequency"] == "weekly" and r["settings"]["time"] == "08:30"
    )  # 바꾼 것만 바뀜
    assert (
        set_review_report(
            store_id="달빛모찌", today=TODAY, repo=REPO, settings_store=st, action="off"
        )["next_run"]
        is None
    )
    # 주기를 처음 정하는데 말하지 않으면 채워서 저장하지 않고 제안만 한다
    first = set_review_report(
        store_id="황금반점", today=TODAY, repo=REPO, settings_store=st, now=NOW
    )
    assert first["action"] == "suggest" and first["suggested"]["frequency"] == "daily"
    assert "황금반점" not in st.data  # 아무것도 저장하지 않았다
    assert next_run({"enabled": True, "frequency": "monthly", "time": "09:00"}, NOW) == datetime(
        2026, 12, 1, 9, 0
    )


def test_parse_requests():
    P = parse_report_request
    assert P("매주 월요일 아침에 리뷰 정리해서 보내줘") == (
        "set",
        {"frequency": "weekly", "weekday": "mon", "time": "09:00"},
    )
    assert P("매일 저녁 8시에 리뷰 요약해서 보내줘") == (
        "set",
        {"frequency": "daily", "time": "20:00"},
    )
    assert P("나쁜 리뷰 올라오면 바로 알려줘") == ("set", {"alert": "negative"})
    assert P("보고할 때 불만만 알려줘") == ("set", {"focus": "complaint"})
    assert P("보고 시간 오전 8시로 바꿔줘") == ("set", {"time": "08:00"})
    assert P("리뷰 보고 그만 보내") == ("off", {})
    assert P("보고 얼마나 자주 받는 게 좋아?") == ("suggest", {})
    assert P("지금 리뷰 보고서 보여줘") == ("now", {})
    assert P("Send me a review report every Monday at 9am") == (
        "set",
        {"frequency": "weekly", "weekday": "mon", "time": "09:00"},
    )
    assert P("Присылай отчёт каждую неделю в понедельник") == (
        "set",
        {"frequency": "weekly", "weekday": "mon"},
    )
    assert P("요즘 손님들 불만 뭐야?") is None and P("최근 리뷰 어때?") is None
    assert P("응 그렇게 해줘", pending_suggestion={"frequency": "biweekly"}) == (
        "set",
        {"frequency": "biweekly"},
    )


def test_run_report_and_alerts():
    r = run_report(
        store_id="황금반점",
        today=TODAY,
        repo=REPO,
        settings={"frequency": "weekly", "focus": "all"},
    )
    assert r["analysis"]["format"] == "report" and r["analysis"]["period"]["start"] == "2026-10-31"
    assert "previous_period" in r["analysis"] and "unanswered" in r
    a = check_alerts(
        store_id="달빛모찌",
        today=TODAY,
        repo=REPO,
        settings={"alert": "negative"},
        since=date(2026, 10, 24),
    )
    assert [x["visit_date"] for x in a["alerts"]] == ["2026-10-25"]  # '맛이 없어요' 리뷰


def test_conversation_with_silent_model():
    """HCX가 도구를 전혀 안 불러도 보고 설정 대화가 규칙으로 끝까지 흐른다."""
    from types import SimpleNamespace as NS

    from agenthan.agent.review.agent import ReviewToolbox, run_turn

    silent = NS(
        chat=NS(
            completions=NS(
                create=lambda **kw: NS(
                    choices=[NS(message=NS(content="네 알겠습니다", tool_calls=None))]
                )
            )
        )
    )
    tb = ReviewToolbox(
        "달빛모찌", TODAY, REPO, MenuCatalog.load(ROOT / "fixtures" / "review_menus.json"), now=NOW
    )
    msgs = [{"role": "system", "content": "s"}]

    def ask(q):
        msgs.append({"role": "user", "content": q})
        return run_turn(silent, "HCX-005", msgs, tb)

    out = ask("보고 얼마나 자주 받는 게 좋아?")
    assert "격주" in out and "4건" in out and "설정할까요" in out
    out = ask("응 그렇게 해줘")
    assert out.startswith("격주 토요일") is False and "격주" in out and "다음 보고" in out
    assert tb.settings_store.get("달빛모찌")["frequency"] == "biweekly"
    out = ask("나쁜 리뷰 올라오면 바로 알려줘")
    assert "바로 알려드려요" in out
    out = ask("지금 리뷰 보고서 보여줘")
    assert out.startswith("리뷰 보고서예요.") and "**기간**" in out and "답글 안 단 리뷰" in out
    out = ask("리뷰 보고 그만 보내")
    assert out.startswith("정기 보고를 껐어요") and not tb.settings_store.get("달빛모찌")["enabled"]


def test_unstated_frequency_saves_only_what_was_said():
    """알림만 말하면 알림만 저장하고 정기 보고는 꺼 둔 채 주기를 제안한다."""
    st = ReportSettingsStore()
    r = set_review_report(
        store_id="달빛모찌", today=TODAY, repo=REPO, settings_store=st, alert="every_new", now=NOW
    )
    assert r["action"] == "suggest" and r["changed"] == {"alert": "every_new"}
    saved = st.get("달빛모찌")
    assert saved["alert"] == "every_new" and not saved["enabled"] and saved["frequency"] is None
    assert next_run(saved, NOW) is None
    r = set_review_report(
        store_id="달빛모찌", today=TODAY, repo=REPO, settings_store=st, frequency="weekly", now=NOW
    )
    assert r["action"] == "set" and r["settings"]["enabled"]
    assert r["settings"]["alert"] == "every_new"  # 앞서 저장한 알림은 그대로


def _toolbox():
    from agenthan.agent.review.agent import ReviewToolbox

    return ReviewToolbox(
        "달빛모찌", TODAY, REPO, MenuCatalog.load(ROOT / "fixtures" / "review_menus.json"), now=NOW
    )


def test_model_values_not_in_utterance_are_dropped():
    """HCX가 덧붙인 요일·시간은 사장님 말에 없으면 저장하지 않는다."""
    tb = _toolbox()
    r = tb.dispatch(
        "set_review_report",
        {"frequency": "weekly", "weekday": "fri", "time": "20:00"},
        "리뷰 보고 매주 받을래",
    )
    assert r["action"] == "set" and tb.last_args == {"frequency": "weekly"}
    st = tb.settings_store.get("달빛모찌")
    assert (st["frequency"], st["weekday"], st["time"]) == ("weekly", "mon", "09:00")  # 기본값
    # 말에 있는 값만 남아 아무것도 없으면 실행하지 않고 되묻게 한다
    tb = _toolbox()
    r = tb.dispatch("set_review_report", {"time": "09:00"}, "매주 리뷰 보고해줘")
    assert r["error"] == "not_requested" and "달빛모찌" not in tb.settings_store.data


def test_alert_only_conversation_asks_before_turning_reports_on():
    from types import SimpleNamespace as NS

    from agenthan.agent.review.agent import run_turn

    silent = NS(
        chat=NS(
            completions=NS(
                create=lambda **kw: NS(
                    choices=[NS(message=NS(content="네 알겠습니다", tool_calls=None))]
                )
            )
        )
    )
    tb = _toolbox()
    msgs = [{"role": "system", "content": "s"}]

    def ask(q):
        msgs.append({"role": "user", "content": q})
        return run_turn(silent, "HCX-005", msgs, tb)

    out = ask("새 리뷰 올라올 때마다 알려줘")
    assert "저장했어요" in out and "아직 꺼져" in out and "설정할까요" in out
    assert not tb.settings_store.get("달빛모찌")["enabled"]
    out = ask("응 그렇게 해줘")
    st = tb.settings_store.get("달빛모찌")
    assert st["enabled"] and st["frequency"] == "biweekly" and st["alert"] == "every_new"
    assert "다음 보고" in out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
