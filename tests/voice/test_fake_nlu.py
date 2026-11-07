import pytest

from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu

CTX = NluContext(
    menu_names=("메도빅", "나폴레옹", "아메리카노", "블린"),
    cart_summary="",
    last_question="",
    state="ordering",
)


def parse(text: str) -> list[Act]:
    return FakeNlu().parse(text, CTX)


@pytest.mark.parametrize(
    ("text", "qty"),
    [("메도빅 하나요", 1), ("메도빅 두 개요", 2), ("메도빅 2개 주세요", 2), ("메도빅요", None)],
)
def test_menu_with_qty(text: str, qty: int | None) -> None:
    assert parse(text) == [Act("add_item", menu_text="메도빅", menu_guess="메도빅", qty=qty)]


def test_two_menus_in_order() -> None:
    acts = parse("메도빅 하나랑 블린 두 개요")
    assert [(a.menu_guess, a.qty) for a in acts] == [("메도빅", 1), ("블린", 2)]


def test_unlisted_menu_keeps_raw_text() -> None:
    assert parse("꿀케이크 하나요") == [Act("add_item", menu_text="꿀케이크", qty=1)]


@pytest.mark.parametrize("text", ["그게 다예요", "이게 다예요.", "없어요"])
def test_done_ordering(text: str) -> None:
    assert parse(text) == [Act("done_ordering")]


@pytest.mark.parametrize(("text", "kind"), [("네", "yes"), ("네.", "yes"), ("맞아요", "yes"),
                                            ("아니요", "no"), ("아뇨", "no")])
def test_yes_no(text: str, kind: str) -> None:
    assert parse(text) == [Act(kind)]  # type: ignore[arg-type]


@pytest.mark.parametrize(("text", "time_text"), [("6시 반이요", "6시 반"), ("6시에 갈게요", "6시"),
                                                 ("5시 15분", "5시 15분")])
def test_pickup_time(text: str, time_text: str) -> None:
    assert parse(text) == [Act("set_pickup_time", time_text=time_text)]


def test_time_digit_is_not_qty() -> None:
    acts = parse("메도빅 6시에 갈게요")
    assert acts[0] == Act("add_item", menu_text="메도빅", menu_guess="메도빅", qty=None)
    assert acts[1].type == "set_pickup_time"


def test_unknown() -> None:
    assert parse("음") == [Act("unknown", free_text="음")]
