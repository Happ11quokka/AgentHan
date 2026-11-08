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


# ---------------------------------------------------------------- 티켓 03: 수량·변경·삭제·옵션·요청


def parse_with_cart(text: str, cart_summary: str) -> list[Act]:
    ctx = NluContext(CTX.menu_names, cart_summary, "", "ordering")
    return FakeNlu().parse(text, ctx)


def add(menu: str, qty: int | None = None, option: str = "") -> Act:
    return Act("add_item", menu_text=menu, menu_guess=menu, qty=qty, option_text=option)


@pytest.mark.parametrize(
    ("text", "qty"),
    [
        ("아메리카노 하나요", 1),
        ("아메리카노 한 잔이요", 1),
        ("아메리카노 둘이요", 2),
        ("아메리카노 두 잔 주세요", 2),
        ("아메리카노 세 잔이요", 3),
        ("아메리카노 3잔이요", 3),
        ("아메리카노 넷이요", 4),
        ("메도빅 다섯 개요", 5),
        ("메도빅 열 개요", 10),
    ],
)
def test_qty_expressions(text: str, qty: int) -> None:
    assert parse(text)[0].qty == qty


def test_multi_items_one_utterance() -> None:
    assert parse("메도빅 둘이랑 아메리카노 하나요") == [add("메도빅", 2), add("아메리카노", 1)]


def test_add_then_remove_in_one_utterance() -> None:
    assert parse("아메리카노 둘이랑 블린 하나, 아 블린 빼 주세요") == [
        add("아메리카노", 2),
        add("블린", 1),
        Act("remove_item", menu_text="블린", menu_guess="블린"),
    ]


@pytest.mark.parametrize("text", ["블린 빼 주세요", "블린은 취소해 주세요", "블린 없애 주세요"])
def test_remove_item(text: str) -> None:
    assert parse(text) == [Act("remove_item", menu_text="블린", menu_guess="블린")]


@pytest.mark.parametrize("text", ["그거 빼 주세요", "방금 거 취소해 주세요"])
def test_remove_without_menu_means_last_item(text: str) -> None:
    assert parse(text) == [Act("remove_item")]


def test_remove_unlisted_menu_keeps_raw_text() -> None:
    assert parse("짜장면 빼 주세요") == [Act("remove_item", menu_text="짜장면")]


@pytest.mark.parametrize(
    ("text", "qty"),
    [("아메리카노 세 잔으로 해 주세요", 3), ("아메리카노 하나로 바꿔 주세요", 1),
     ("아메리카노 2잔으로 할게요", 2)],
)
def test_change_qty_with_menu(text: str, qty: int) -> None:
    assert parse(text) == [
        Act("change_qty", menu_text="아메리카노", menu_guess="아메리카노", qty=qty)
    ]


@pytest.mark.parametrize(("text", "qty"), [("두 개로 바꿔 주세요", 2), ("세 잔으로 해 주세요", 3)])
def test_change_qty_without_menu(text: str, qty: int) -> None:
    assert parse(text) == [Act("change_qty", qty=qty)]


@pytest.mark.parametrize(
    ("text", "option"),
    [("차갑게요", "차갑게"), ("아이스로 주세요", "아이스"), ("따뜻한 걸로요", "따뜻한"),
     ("디카페인으로요", "디카페인"), ("사워크림 빼 주세요", "사워크림 빼"),
     ("샷 추가해 주세요", "샷 추가"), ("차갑게, 샷 추가요", "차갑게 샷 추가")],
)
def test_set_option_without_menu(text: str, option: str) -> None:
    assert parse(text) == [Act("set_option", option_text=option)]


@pytest.mark.parametrize(
    ("text", "act"),
    [
        ("아이스 아메리카노 하나요", add("아메리카노", 1, "아이스")),
        ("아메리카노 차갑게 두 잔이요", add("아메리카노", 2, "차갑게")),
        ("메도빅 홀케이크로 하나요", add("메도빅", 1, "홀케이크")),
    ],
)
def test_add_item_with_option(text: str, act: Act) -> None:
    assert parse(text) == [act]


def test_options_stay_with_their_menu() -> None:
    assert parse("아이스 아메리카노 하나랑 따뜻한 아메리카노 하나요") == [
        add("아메리카노", 1, "아이스"),
        add("아메리카노", 1, "따뜻한"),
    ]


def test_menu_with_option_already_in_cart_is_set_option() -> None:
    acts = parse_with_cart("아메리카노는 디카페인으로요", "아메리카노 한 잔")
    assert acts == [
        Act("set_option", menu_text="아메리카노", menu_guess="아메리카노", option_text="디카페인")
    ]


def test_menu_with_option_not_in_cart_is_add() -> None:
    assert parse("아메리카노는 디카페인으로요") == [add("아메리카노", None, "디카페인")]


def test_request_for_menu() -> None:
    assert parse("블린은 소스 따로 주세요") == [
        Act("set_request", menu_text="블린", menu_guess="블린", free_text="소스 따로 주세요")
    ]


def test_request_without_menu() -> None:
    assert parse("포크 두 개 넣어 주세요") == [
        Act("set_request", free_text="포크 두 개 넣어 주세요")
    ]


@pytest.mark.parametrize(
    ("text", "act"),
    [
        ("아 그거 빼 주세요", Act("remove_item")),
        ("어 메도빅 하나요", Act("add_item", menu_text="메도빅", menu_guess="메도빅", qty=1)),
        ("음, 저기 블린 두 개요", Act("add_item", menu_text="블린", menu_guess="블린", qty=2)),
        ("아니요", Act("no")),  # "아니요"의 "아"는 군말이 아니다
    ],
)
def test_leading_filler_is_dropped(text: str, act: Act) -> None:
    """티켓 06: STT가 넘기는 말 앞 군말("아", "어", "음")은 떼고 읽는다."""
    assert parse(text) == [act]
