import json
from datetime import date

import pytest

from agenthan.agent import value_source as vs
from agenthan.agent.owner_assistant import tool_params
from agenthan.tools.dummy_store import SEED_PATH

TODAY = date(2026, 11, 10)
STORE = json.loads(SEED_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("만 오천 원", 15000),
        ("1만 2천원", 12000),
        ("삼천오백 원이요", 3500),
        ("12,000원", 12000),
        ("10% 할인", 10),
        ("가격은 5000원", 5000),
        ("할인 금액만 3000원으로", 3000),
        ("오늘만 일만 오천 원", 15000),
        ("가격 2만 원", 20000),
        ("이번만 2만원", 20000),
        ("3천5백원", 3500),
    ],
)
def test_korean_numbers(text, value):
    assert value in vs.numbers(text)


def test_bare_syllables_are_not_numbers():
    assert vs.numbers("일요일 이름 사진") == set()


def check(feature, args, said):
    return vs.check(tool_params(feature), args, said, STORE, today=TODAY)


def test_values_the_owner_said_pass():
    assert check("business_info", {"phone": "02-777-8888"}, "새 번호는 02-777-8888")[1] == []
    hours = {"days": ["sat"], "closed": False, "open_time": "10:00", "close_time": "20:00"}
    assert check("business_hours", hours, "토요일은 10시부터 저녁 8시까지 할게요")[1] == []
    coupon = {
        "title": "겨울 할인 쿠폰",
        "discount_type": "amount",
        "discount_value": 2000,
        "start_date": "2026-11-15",
        "end_date": "2026-11-30",
    }
    said = "이름은 겨울 할인 쿠폰, 2000원 할인이요\n11월 15일부터 30일까지요"
    assert check("coupon", coupon, said)[1] == []
    assert check("menu", {"name": "블린", "price": 15000}, "블린 만 오천 원으로")[1] == []
    sources, missing = check(
        "reservation",
        {"reservation_id": "resv-2", "status": "confirmed"},
        "예약 확정해 주세요\n내일 점심 4명 예약이요",
    )
    assert missing == [] and sources["reservation_id"] == "lookup"
    reply = {"inquiry": "talk-2", "reply_text": "안녕하세요. 10개 이상 단체 주문 가능합니다."}
    assert (
        check(
            "talktalk", reply, "단체 주문 물어본 거요\n답장은 '10개 이상 단체 주문 가능합니다'로"
        )[1]
        == []
    )


def test_values_the_owner_did_not_say_are_refused():
    # The store's own number or a neighbour of it is not the owner's new number.
    assert check("business_info", {"phone": "02-000-0001"}, "전화번호 바꿔 주세요")[1]
    assert check("menu", {"name": "피로시키", "price": 3000}, "피로시키 추가해 주세요")[1] == [
        "가격 (원)"
    ]
    # Two new orders and no description: the model must not pick one.
    assert check("order", {"order_id": "order-1", "status": "accepted"}, "주문 접수해 주세요")[1]
    assert check(
        "reservation", {"reservation_id": "resv-1", "status": "confirmed"}, "예약 처리해 줘"
    )[1]
    assert check("onsite_payment", {"enabled": True}, "매장결제 어떻게 해요?")[1]


def test_new_tool_inputs_need_the_owners_words():
    # Day groups and date ranges the owner gave by their ends.
    weekdays = {"days": ["mon", "tue", "wed", "thu", "fri"], "open_time": "10:00"}
    assert check("business_hours", weekdays, "평일은 10시에 열어요")[1] == []
    assert check("business_hours", {"days": ["weekend"], "closed": True}, "주말엔 쉬어요")[1] == []
    closed = {"days": ["2026-11-20", "2026-11-21", "2026-11-22"], "closed": True}
    assert check("business_hours", closed, "11월 20일부터 22일까지 쉬어요")[1] == []
    assert check("business_hours", {"days": ["sat"], "closed": True}, "평일은 쉬어요")[1]
    # A menu named by its alias; actions need the owner's words.
    sold = {"name": "메도빅", "action": "sold_out"}
    assert check("menu", sold, "꿀케이크 오늘 다 팔렸어요")[1] == []
    assert check("menu", {"name": "메도빅", "action": "delete"}, "메도빅 가격 알려줘")[1]
    # Keywords to add or remove must be said.
    keywords = {"add": ["디저트"], "remove": ["메도빅"]}
    assert check("search_keywords", keywords, "메도빅 빼고 디저트 넣어줘")[1] == []
    assert check("search_keywords", {"add": ["브런치"]}, "키워드 하나 추가해줘")[1]


def test_wrong_shapes_are_unconfirmed_not_errors():
    hours = {"days": ["fri"], "open_time": ["09:00"], "close_time": "23:00"}
    assert check("business_hours", hours, "금요일은 9시부터 밤 11시까지요")[1] == ["여는 시간"]
