"""사장님 화면 점검(2026-11-21)에서 틀린 답이 나온 문장들.

기록 찾기, 시연용 연동 상태, 기능 연결 규칙."""

import json
import re
from datetime import date

import pytest

from agenthan.agent import owner_assistant as owner
from agenthan.agent import value_source
from agenthan.app import pilot
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import execute_feature_tool

PILOT = json.loads(pilot.PILOT_SEED.read_text(encoding="utf-8"))
TODAY = date(2026, 11, 9)


def ids(records):
    return [r["id"] for r in records]


def test_minutes_after_hour_are_not_people():
    fits, stated = value_source.candidates(
        PILOT["reservations"], "11월 10일 12시 30분 예약 확정해 줘", TODAY
    )
    assert stated and ids(fits) == ["resv-2"]
    # 인원은 그대로 읽는다.
    fits, _ = value_source.candidates(PILOT["reservations"], "4명 예약 확정해 줘", TODAY)
    assert "resv-2" in ids(fits) and all(r["people"] == 4 for r in fits)


def test_order_words_prefer_the_record_that_matches_more():
    fits, _ = value_source.candidates(PILOT["orders"], "메도빅 홀케이크 주문 접수해 줘", TODAY)
    assert ids(fits) == ["order-3"]
    fits, _ = value_source.candidates(PILOT["orders"], "메도빅 주문 접수해 줘", TODAY)
    assert ids(fits) == ["order-1", "order-3"]  # 하나씩만 맞으면 둘 다 후보


@pytest.mark.parametrize(
    "text,feature",
    [
        ("어제 손님은 어땠어?", "statistics"),
        ("오늘 방문 몇 명이야", "statistics"),
        ("쿠폰 만드는 법 알려 줘", "learning_support"),
        ("메뉴 등록하는 방법 알려 줘", "learning_support"),
        ("어떻게 하면 더 위로 올라가?", "search_diagnosis"),
    ],
)
def test_keyword_routes(text, feature):
    assert owner.keyword_feature(text) == feature


@pytest.mark.parametrize(
    "text",
    [
        "메뉴 등록해 줘",
        "어제 들어온 주문 보여 줘",
        "리뷰 답글 써 줘",
        "네이버페이 가입하려면 어떻게 해?",
        "가게에서 네이버페이 받으려면 서류 뭐 필요한지 가입하는 법 알려줘요",
        "요즘 손님들 불만 뭐야?",
        "오늘 저녁에 장사 끝나고 맥주 마시면서 볼만한 재밌는 한국 영화 뭐 있어요?",
        "이번 주 통계",
    ],
)
def test_keyword_routes_leave_other_requests(text):
    assert owner.keyword_feature(text) is None


def test_keyword_routes_touch_no_eval_sentence():
    """평가셋 문장은 하나도 가로채지 않는다(모델 기능 선택 평가를 바꾸지 않게)."""
    from pathlib import Path

    hits = []
    for path in Path(__file__).parent.joinpath("data").rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            for text in re.findall(r'"([^"\\]{2,200})"', line):
                if owner.keyword_feature(text):
                    hits.append((path.name, text))
    assert hits == []


def test_help_finds_feature_how_to_and_general_education(tmp_path):
    store = DummyStore(tmp_path / "s.json")
    store.write(PILOT)
    coupon = execute_feature_tool(store, "search_help_articles", {"query": "쿠폰 만드는 법"})
    assert coupon["found"] and "플레이스 쿠폰" in coupon["records"][0]["how"]
    edu = execute_feature_tool(
        store, "search_help_articles", {"query": "스마트플레이스 쓰는 법을 배울 수 있는 교육"}
    )
    assert edu["found"] and edu["records"][0]["id"] == "help-5"
    none = execute_feature_tool(store, "search_help_articles", {"query": "택배 보내기"})
    assert not none["found"]
    from agenthan.agent.planner_loop import help_line

    assert "https://help.naver.com" in help_line(none["fallback"][0])


def no_model(*_args, **_kwargs):
    raise AssertionError("모델을 부르면 안 된다")


@pytest.mark.parametrize(
    "feature,said,expect",
    [
        (
            "place_plus",
            "우리 가게 POS가 네이버 플레이스랑 연동되는지 확인하고 싶어",
            "연습POS 베이직",
        ),
        ("naverpay_signup", "네이버페이 가입하려면 어떻게 해?", "가입되어 있어요"),
    ],
)
def test_pilot_shop_is_already_connected(tmp_path, feature, said, expect):
    a = pilot.assistant(tmp_path / "store.json", chat=no_model)
    a._begin_turn()
    a.trace = owner.new_trace(said, "test")
    reply = a.respond(said, owner.Route(feature, "test"))
    assert expect in reply
