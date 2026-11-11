from dataclasses import replace
from unittest.mock import Mock

import pytest

from agenthan.agent import owner_assistant
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.tools.report_insights import Insight


def pick(feature_id):
    return owner_assistant.feature_selector.Selection(feature_id, "feature", (feature_id,))


def offline(*args, **kwargs):
    raise RuntimeError("offline")


def test_followup_before_and_after_report(tmp_path):
    assistant = OwnerAssistant(tmp_path / "store.json", chat=offline)
    assert "좋아진 점" in assistant.ask("왜 줄었어?")
    assert assistant.last_report is not None
    facts = assistant.last_report.facts
    reduced = replace(facts.place_views, change=-300, change_percent=-20)
    assistant.last_report = replace(
        assistant.last_report, facts=replace(facts, place_views=reduced)
    )
    text = assistant.ask("왜 줄었어?")
    assert "조회 수가 지난 기간보다 300회 줄었어요" in text
    assert facts.channels[0].name in text
    day = next((day, item) for day, item in facts.weekday_changes.items() if item.change)
    hour = next((hour, item) for hour, item in enumerate(facts.hourly_changes) if item.change)
    assert f"{day[1].previous:,}회→{day[1].current:,}회 ({day[1].change:+,}회)" in text
    assert f"{hour[0]}시 {hour[1].previous:,}회→{hour[1].current:,}회" in text
    assert "요일" in text and "시간대" in text and "데이터로는 알 수 없어요" in text
    assert "검색어" in assistant.ask("어디서 왔어?")
    assert "요일" in assistant.ask("언제?")
    assert "성별" in assistant.ask("누가?")
    assert "데이터로는 알 수 없어요" in assistant.ask("비 와서 그래?")
    assert assistant.last_report.text == assistant.ask("자세히")


def test_non_view_metric_is_not_misrepresented_as_view_breakdown(tmp_path):
    assistant = OwnerAssistant(tmp_path / "store.json", chat=offline)
    assistant.ask("왜 줄었어?")
    facts = assistant.last_report.facts
    requests = replace(facts.booking_order_requests, change=-999)
    assistant.last_report = replace(
        assistant.last_report, facts=replace(facts, booking_order_requests=requests)
    )
    answer = assistant.ask("왜 줄었어?")
    assert "예약·주문 신청 수" in answer
    assert "경로·요일·시간대별 변화와 원인은 데이터로는 알 수 없어요" in answer
    assert "조회 경로별 변화" not in answer


def test_non_view_decline_connects_views_conversion_and_action(tmp_path):
    assistant = OwnerAssistant(tmp_path / "store.json", chat=offline)
    assistant.ask("통계")
    last = assistant.last_report
    facts = last.facts
    channels = tuple(replace(c, change=5) for c in facts.channels)
    days = {day: replace(item, change=4) for day, item in facts.weekday_changes.items()}
    hours = tuple(replace(item, change=3) for item in facts.hourly_changes)
    days["mon"] = replace(days["mon"], change=42)
    facts = replace(
        facts,
        place_views=replace(facts.place_views, change=50),
        booking_order_requests=replace(facts.booking_order_requests, change=-100),
        smartcall_calls=replace(facts.smartcall_calls, change=0),
        previous_conversion_percent=12.3,
        conversion_percent=8.2,
        channels=channels,
        weekday_changes=days,
        hourly_changes=hours,
    )
    insight = Insight("conversion_drop", "전환 감소", {}, "menu", 10)
    assistant.last_report = replace(last, facts=facts, insights=[insight])
    answer = assistant.ask("왜 줄었어?")
    assert "조회 수는 지난 기간보다 늘었어요" in answer
    assert "예약·주문 전환율은 12.3% → 8.2%" in answer
    assert "조회 월요일 기준으로 지난 기간보다 +42회" in answer
    assert "할 일: 대표 메뉴의 사진과 설명을 보강해 보세요" in answer
    assert "일 수 있어요" in answer


def test_non_view_without_comparison_or_action_uses_available_facts(tmp_path):
    assistant = OwnerAssistant(tmp_path / "store.json", chat=offline)
    assistant.ask("통계")
    last = assistant.last_report
    facts = replace(
        last.facts,
        place_views=replace(last.facts.place_views, change=None),
        booking_order_requests=replace(last.facts.booking_order_requests, change=-999),
        previous_conversion_percent=None,
    )
    assistant.last_report = replace(last, facts=facts, insights=[])
    answer = assistant.ask("왜 줄었어?")
    assert "조회 수의 지난 기간 비교 자료가 없어요" in answer
    assert "전환율" not in answer
    assert "할 일: 다음 리포트에서 변화를 확인해 보세요" in answer


def test_smartcall_followup_subject_particle(tmp_path):
    assistant = OwnerAssistant(tmp_path / "store.json", chat=offline)
    assistant.ask("통계")
    facts = assistant.last_report.facts
    facts = replace(facts, smartcall_calls=replace(facts.smartcall_calls, change=-999))
    assistant.last_report = replace(assistant.last_report, facts=facts)
    assert assistant.ask("왜 줄었어?").startswith("스마트콜이 지난 기간보다")


@pytest.mark.parametrize(
    "question",
    ["검색어 어떻게 바꿔?", "검색 키워드 어떻게 등록해?", "대표키워드 뭐 추가하면 좋아?"],
)
def test_keyword_edit_routes_to_feature_not_search(tmp_path, monkeypatch, question):
    match = Mock(return_value=pick("search_keywords"))
    monkeypatch.setattr(owner_assistant.feature_selector, "select", match)
    chat = Mock(return_value={"role": "assistant", "content": "어떤 키워드로 바꿀까요?"})
    assistant = OwnerAssistant(tmp_path / "store.json", chat=chat)
    assistant._search_visibility = Mock(side_effect=AssertionError("검색 진단 호출 금지"))
    assert assistant.ask(question) == "어떤 키워드로 바꿀까요?"
    tools = chat.call_args.kwargs["tools"]
    assert [t["function"]["name"] for t in tools] == ["set_representative_keywords"]
    match.assert_called_once()
    assistant._search_visibility.assert_not_called()


def test_review_question_routes_to_review_not_followup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        owner_assistant.feature_selector, "select", lambda *a, **k: pick("review_reply")
    )
    follow = Mock(side_effect=AssertionError("통계 후속 질문 호출 금지"))
    monkeypatch.setattr(owner_assistant.report_explainer, "follow_up", follow)
    assistant = OwnerAssistant(tmp_path / "store.json", chat=offline)
    assistant.last_report = owner_assistant.report_explainer.explain("통계", chat=offline)
    assistant._delegate_review = Mock(return_value="리뷰 위임")
    question = "누가 리뷰 남겼는지 보여줘"
    assert assistant.ask(question) == "리뷰 위임"
    assistant._delegate_review.assert_called_once_with("review_reply", question)
    follow.assert_not_called()
