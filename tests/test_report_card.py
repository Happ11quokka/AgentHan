import json
import os
from dataclasses import asdict, replace
from unittest.mock import Mock

import pytest

from agenthan.agent import report_explainer as report
from agenthan.agent.clova_chat import chat as hcx_chat
from agenthan.agent.owner_assistant import OwnerAssistant


def failing_chat(*args, **kwargs):
    raise RuntimeError("offline")


def search_chat(messages, *, tools):
    """The model picks the read-only diagnosis tool that search_diagnosis exposes."""
    if any(t["function"]["name"] == "diagnose_search_visibility" for t in tools):
        call = {"name": "diagnose_search_visibility", "arguments": "{}"}
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "d", "function": call}]}
    return failing_chat(messages, tools=tools)


def test_card_rejects_fabrication_and_has_comparisons():
    answer = report.explain("통계", chat=failing_chat)
    fake = Mock(return_value={"content": json.dumps(["지난주보다 999% 늘었어요", "문제", "행동"])})
    card = report.make_card(answer, chat=fake)
    lines = card.render().splitlines()
    assert len(lines) == 3
    for line in lines:
        assert len(report.NUMBER.findall(line)) <= 1
        assert not report._numbers(line) or "지난주보다" in line
    assert "999" not in card.render()


def test_card_rejects_changed_number_and_checks_grounding():
    answer = report.explain("통계", chat=failing_chat)
    template = report.make_card(answer, chat=failing_chat)

    def change_number(messages, **kwargs):
        lines = json.loads(messages[-1]["content"])
        assert report.NUMBER.search(lines[0])
        lines[0] = report.NUMBER.sub("999", lines[0], count=1)
        return {"content": json.dumps(lines)}

    assert report.make_card(answer, chat=change_number) == template
    assert not report._grounded_numbers("조회 수가 지난주보다 999% 늘었어요", asdict(answer.facts))


def test_card_subject_particles():
    answer = report.explain("통계", chat=failing_chat)
    facts = replace(
        answer.facts,
        place_views=replace(answer.facts.place_views, change_percent=0),
        booking_order_requests=replace(answer.facts.booking_order_requests, change_percent=0),
        smartcall_calls=replace(answer.facts.smartcall_calls, change_percent=10),
    )
    card = report.make_card(
        replace(answer, facts=facts), chat=failing_chat, missing_keyword="메도빅"
    )
    assert card.good.startswith("스마트콜이 지난주보다")
    assert "‘메도빅’이 실제 메뉴와" in card.todo

    facts = replace(facts, smartcall_calls=replace(facts.smartcall_calls, change_percent=-10))
    assert report.make_card(replace(answer, facts=facts), chat=failing_chat).watch.startswith(
        "스마트콜이 지난주보다"
    )
    assert report.subject_particle("가게") == "가"
    assert report.subject_particle("cafe") == "가"


def test_card_no_improvement():
    answer = report.explain("통계", chat=failing_chat)

    def unchanged(metric):
        return replace(metric, change=0, change_percent=0)

    facts = replace(
        answer.facts,
        place_views=unchanged(answer.facts.place_views),
        booking_order_requests=unchanged(answer.facts.booking_order_requests),
        smartcall_calls=unchanged(answer.facts.smartcall_calls),
    )
    assert (
        report.make_card(replace(answer, facts=facts), chat=failing_chat).good
        == "크게 달라진 점은 없어요"
    )


def test_daily_card_compares_with_previous_day():
    answer = report.explain("통계", chat=failing_chat)
    facts = replace(
        answer.facts,
        period="day",
        place_views=replace(answer.facts.place_views, change_percent=18),
    )
    card = report.make_card(replace(answer, facts=facts), chat=failing_chat)
    assert "전날보다" in card.good


def test_glossary_without_chat_or_embedding(tmp_path):
    glossary = json.loads(report.GLOSSARY_PATH.read_text())
    assert len(glossary) == 7
    assert all(entry["meaning"] and entry["where"] for entry in glossary.values())
    chat = Mock(side_effect=AssertionError("HCX 호출 금지"))
    assistant = OwnerAssistant(tmp_path / "store.json", chat=chat, embed=chat)
    assert "비율" in assistant.ask("전환율이 뭐야?")
    chat.assert_not_called()


@pytest.mark.parametrize(
    "question",
    ["대표키워드 뭐 추가하면 좋아?", "대표 키워드 설명 바꿔줘", "전환율이 뭐야? 수정해줘"],
)
def test_glossary_does_not_intercept_edit_requests(question):
    assert report.glossary_lookup(question) is None


def test_seed_search_and_card_action(tmp_path):
    assistant = OwnerAssistant(tmp_path / "store.json", chat=search_chat)
    summary = assistant.ask("우리 가게 검색하면 어떻게 나와?")
    assert "샘플 러시안 베이커리: 1위" in summary
    assert "이태원동 메도빅: 3위" in summary
    assert "안 나오는 검색어" in summary
    assert "이태원동 베이커리" in assistant.ask("자세히")
    assert "대표키워드에 추가" in summary
    answer = report.explain("통계", chat=failing_chat)
    card = report.make_card(
        answer, chat=failing_chat, missing_keyword=assistant._missing_search_keyword
    )
    assert card.todo_feature == "search_keywords"
    assert assistant._missing_search_keyword in card.todo


def test_search_summary_is_short_and_details_reuse_results(tmp_path, monkeypatch):
    from agenthan.agent import owner_assistant
    from agenthan.tools.search_visibility import summarize

    factory = owner_assistant.FixtureSearch
    client = factory(
        owner_assistant.Path(owner_assistant.__file__).parent.parent
        / "tools"
        / "search_fixture_sample.json"
    )
    client.local = Mock(wraps=client.local)
    client.blog = Mock(wraps=client.blog)
    monkeypatch.setattr(owner_assistant, "FixtureSearch", Mock(return_value=client))
    monkeypatch.setenv("AGENTHAN_SEARCH_LIVE", "0")
    assistant = OwnerAssistant(tmp_path / "store.json", chat=search_chat)
    summary = assistant.ask("우리 가게 검색하면 어떻게 나와?")
    lines = summary.splitlines()
    assert len(lines) <= 6
    assert "나오는 검색어:" in lines[0] and "위" in lines[0]
    assert "안 나오는 검색어:" in lines[1]
    assert len(lines[0].split(": ", 1)[1].split(", ")) <= 3
    assert len(lines[1].split(": ", 1)[1].split(", ")) <= 3
    assert lines[1].startswith("안 나오는 검색어: 메도빅, 이태원 케이크")
    assert assistant._missing_search_keyword in lines[1]
    assert lines[2].startswith("사장님의 ‘Наполеон’, 손님은 ‘나폴레옹’으로 찾아요")
    assert "먼저 나오는 가게: 가상 구름 빵집, 가상 달빛 빵집" in lines[3]
    assert "‘나폴레옹’을 대표키워드에 추가할까요?" in lines[4]
    assert lines[-1].startswith("개인화 없는 검색 기준")
    counts = client.local.call_count, client.blog.call_count
    # The keyword proposal stays held, so a reminder follows the details.
    assert assistant.ask("자세히").startswith(summarize(assistant.last_search))
    assert (client.local.call_count, client.blog.call_count) == counts
    assistant.ask("통계")
    assert assistant.ask("자세히").startswith(assistant.last_report.text)


def test_search_summary_without_results(tmp_path, monkeypatch):
    from agenthan.agent import owner_assistant
    from agenthan.tools.search_visibility import VisibilityReport

    monkeypatch.setattr(
        owner_assistant, "diagnose", lambda profile, client, queries: VisibilityReport(profile)
    )
    monkeypatch.setenv("AGENTHAN_SEARCH_LIVE", "0")
    assistant = OwnerAssistant(tmp_path / "store.json", chat=search_chat)
    answer = assistant.ask("우리 가게 검색하면 어떻게 나와?")
    assert "상위 5개에 나오는 검색어가 없어요" in answer
    assert "안 나오는 검색어: 확인된 검색어가 없어요" in answer
    assert "이번 주 할 일" in answer


def test_search_action_subject_particle(tmp_path):
    assistant = OwnerAssistant(tmp_path / "store.json", chat=search_chat)
    store = assistant.store.read()
    store["shop"]["keywords"] = []
    for menu in store["menus"]:  # No native names: the inflow-keyword suggestion speaks.
        menu.pop("name_native", None)
    assistant.store.read = Mock(return_value=store)
    answer = report.explain("통계", chat=failing_chat)
    keywords = [keyword for keyword in answer.facts.top_keywords if keyword.name == "메도빅"]
    assert keywords
    assistant.last_report = replace(answer, facts=replace(answer.facts, top_keywords=keywords))
    assert "‘메도빅’이 실제 메뉴와" in assistant.ask("우리 가게 검색하면 어떻게 나와?")


@pytest.mark.network
def test_live_hcx_card():
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        pytest.skip("CLOVA 환경 키 없음")
    answer = report.explain("통계", chat=hcx_chat)
    calls = []

    def live_chat(*args, **kwargs):
        response = hcx_chat(*args, **kwargs)
        calls.append(response)
        return response

    card = report.make_card(answer, chat=live_chat)
    assert calls and calls[0].get("content")
    assert len(card.render().splitlines()) == 3
    assert all(len(report.NUMBER.findall(line)) <= 1 for line in card.render().splitlines())


@pytest.mark.network
def test_live_search_visibility(monkeypatch):
    from agenthan.tools.naver_search import ApiHubSearch
    from agenthan.tools.search_visibility import Query, StoreProfile, diagnose, summarize

    keys = ("NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET")
    if os.environ.get("AGENTHAN_SEARCH_LIVE") != "1" or not all(os.environ.get(k) for k in keys):
        pytest.skip("실제 검색 설정 또는 환경 키 없음")
    client = ApiHubSearch()
    query = "스타벅스 강남"
    items = client.local(query)
    assert items and items[0].title and items[0].roadAddress
    profile = StoreProfile(items[0].title, "", "", [], [], items[0].roadAddress)
    result = diagnose(profile, client, [Query(query, "store_name")])
    assert result.local_results[0].rank == 1
    assert f"{query}: 1위" in summarize(result)


DEMO_STORE = report.Path(__file__).parent.parent / "fixtures" / "demo_store.json"


def demo_assistant(tmp_path, monkeypatch, local):
    """The fictional demo store, diagnosed against a made-up search result."""
    from agenthan.agent import owner_assistant

    fixture = tmp_path / "search.json"
    fixture.write_text(json.dumps({"local": local, "blog": {}}, ensure_ascii=False))
    factory = owner_assistant.FixtureSearch
    monkeypatch.setattr(owner_assistant, "FixtureSearch", lambda _path: factory(fixture))
    monkeypatch.setenv("AGENTHAN_SEARCH_LIVE", "0")
    store = tmp_path / "store.json"
    store.write_text(DEMO_STORE.read_text(encoding="utf-8"), encoding="utf-8")
    return OwnerAssistant(store, chat=search_chat)


DEMO_LOCAL = {
    "메도빅": [
        {"title": "가상 꿀빵집", "category": "베이커리"},
        {"title": "가상 별빵집", "category": "카페"},
        {"title": "가상 해빵집", "category": "케이크"},
        {"title": "가상 달빵집", "category": "케이크"},
    ],
}


def test_native_menu_gap_asks_to_add_keyword_and_saves_on_yes(tmp_path, monkeypatch):
    assistant = demo_assistant(tmp_path, monkeypatch, DEMO_LOCAL)
    answer = assistant.ask("손님이 검색하면 우리 가게 나와?")
    lines = answer.splitlines()
    assert len(lines) <= 6
    # Scene 1: the owner's own word and the customers' word for the same menu.
    assert "‘Медовик’" in answer and "손님은 ‘메도빅’으로 찾아요" in answer
    assert "‘이태원동 메도빅’에는 아직 나오는 가게가 없어요" in answer
    # Scene 2: who shows up instead, by name.
    assert "‘메도빅’에서 먼저 나오는 가게: 가상 꿀빵집, 가상 별빵집, 가상 해빵집" in answer
    assert "가상 달빵집" not in answer
    assert "‘메도빅’을 대표키워드에 추가할까요?" in answer
    assert "메도빅" not in assistant.store.read()["shop"]["keywords"]
    assert assistant.ask("네").startswith("저장했어요")
    assert assistant.store.read()["shop"]["keywords"] == [
        "러시안 베이커리",
        "디저트 카페",
        "메도빅",
    ]


def test_native_menu_gap_declined_saves_nothing(tmp_path, monkeypatch):
    assistant = demo_assistant(tmp_path, monkeypatch, DEMO_LOCAL)
    assistant.ask("손님이 검색하면 우리 가게 나와?")
    assert assistant.ask("취소").startswith("취소했어요")
    assert assistant.store.read()["shop"]["keywords"] == ["러시안 베이커리", "디저트 카페"]


def test_native_menu_gap_skips_registered_menu_and_full_keywords(tmp_path, monkeypatch):
    assistant = demo_assistant(tmp_path, monkeypatch, DEMO_LOCAL)
    data = assistant.store.read()
    data["shop"]["keywords"] = ["메도빅", "나폴레옹", "블린", "러시안 베이커리", "디저트 카페"]
    assistant.store.write(data)
    answer = assistant.ask("손님이 검색하면 우리 가게 나와?")
    assert "손님은 ‘메도빅’으로 찾아요" in answer
    assert "추가할까요" not in answer
    assert assistant.pending_action is None


def test_native_menu_gap_absent_without_native_names(tmp_path, monkeypatch):
    assistant = demo_assistant(tmp_path, monkeypatch, DEMO_LOCAL)
    data = assistant.store.read()
    for menu in data["menus"]:
        menu.pop("name_native", None)
    assistant.store.write(data)
    answer = assistant.ask("손님이 검색하면 우리 가게 나와?")
    assert "손님은" not in answer and "추가할까요" not in answer
    assert "‘메도빅’에서 먼저 나오는 가게: 가상 꿀빵집" in answer


def test_live_search_masks_real_competitor_names(tmp_path, monkeypatch):
    """Demo rule: slides and recordings show only fictional shops (fixtures/README.md)."""
    from agenthan.agent import owner_assistant

    assistant = demo_assistant(tmp_path, monkeypatch, {})
    from agenthan.tools.naver_search import FixtureSearch as factory

    real = {"메도빅": [{"title": "진짜 빵집", "category": "베이커리"}]}
    fixture = tmp_path / "real.json"
    fixture.write_text(json.dumps({"local": real, "blog": {}}, ensure_ascii=False))
    monkeypatch.setattr(owner_assistant, "ApiHubSearch", lambda: factory(fixture))
    monkeypatch.setenv("AGENTHAN_SEARCH_LIVE", "1")
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_ID", "test-id")
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_SECRET", "test-secret")
    answer = assistant.ask("손님이 검색하면 우리 가게 나와?")
    assert "‘메도빅’에서 먼저 나오는 가게: 가상 가게 A" in answer
    assert "진짜" not in answer and "진짜" not in assistant.ask("자세히")
