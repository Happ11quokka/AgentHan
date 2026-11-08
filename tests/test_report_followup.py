from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agenthan.agent import owner_assistant
from agenthan.agent.owner_assistant import OwnerAssistant


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
    assert "채널·요일·시간대별 변화와 원인은 데이터로는 알 수 없어요" in answer
    assert "조회 경로별 변화" not in answer


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
    match = Mock(return_value=[SimpleNamespace(feature_id="search_keywords")])
    monkeypatch.setattr(owner_assistant, "match_features", match)
    assistant = OwnerAssistant(tmp_path / "store.json", chat=offline)
    assistant._search_visibility = Mock(side_effect=AssertionError("검색 진단 호출 금지"))
    assert "검색 노출·대표키워드" in assistant.ask(question)
    match.assert_called_once()
    assistant._search_visibility.assert_not_called()


def test_review_question_routes_to_review_not_followup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        owner_assistant,
        "match_features",
        lambda *args, **kwargs: [SimpleNamespace(feature_id="review_reply")],
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
