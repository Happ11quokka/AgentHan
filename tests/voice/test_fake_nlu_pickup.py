"""가짜 NLU의 픽업 시간 조각(`time_text`) 뽑기 (티켓 04). 시각 계산은 `timeparse`가 한다."""

import pytest

from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu

CTX = NluContext(
    menu_names=("메도빅", "나폴레옹", "아메리카노", "블린"),
    cart_summary="",
    last_question="몇 시쯤 오세요?",
    state="ask_pickup",
)


def parse(text: str) -> list[Act]:
    return FakeNlu().parse(text, CTX)


@pytest.mark.parametrize(
    ("text", "time_text"),
    [
        ("여섯 시 반이요", "여섯 시 반"),
        ("이따 7시에 갈게요", "이따 7시"),
        ("저녁 일곱 시요", "저녁 일곱 시"),
        ("20분 뒤에 갈게요", "20분 뒤"),
        ("30분 있다가 갈게요", "30분 있다가"),
        ("15분 있다 갈게요", "15분 있다"),
        ("한 시간 후에요", "한 시간 후"),
        ("1시간 반 뒤요", "1시간 반 뒤"),
        ("다섯 시 십오 분이요", "다섯 시 십오 분"),
        ("오후 두 시", "오후 두 시"),
        ("네 시 사십오 분", "네 시 사십오 분"),
        ("다섯시반", "다섯시반"),
        ("열한 시 반", "열한 시 반"),
        ("6:30", "6:30"),
        ("지금 갈게요", "지금"),
        ("곧 갈게요", "곧"),
        ("지금 바로 갈게요", "지금 바로"),
        ("이따 갈게요", "이따"),
        ("좀 이따가요", "좀 이따가"),
    ],
)
def test_time_text(text: str, time_text: str) -> None:
    assert parse(text) == [Act("set_pickup_time", time_text=time_text)]


def test_menu_and_native_numeral_time() -> None:
    acts = parse("메도빅 하나요, 여섯 시에 갈게요")
    assert acts == [
        Act("add_item", menu_text="메도빅", menu_guess="메도빅", qty=1),
        Act("set_pickup_time", time_text="여섯 시"),
    ]


def test_hour_count_is_not_clock() -> None:
    assert parse("두 시간 뒤요") == [Act("set_pickup_time", time_text="두 시간 뒤")]


@pytest.mark.parametrize("text", ["지금 메도빅 있어요?", "네", "메도빅 두 개요"])
def test_no_time(text: str) -> None:
    assert all(a.type != "set_pickup_time" for a in parse(text))
