"""티켓 05: 가짜 NLU의 메뉴 질문, "아니요, ~" 정정 머리말, 메뉴 없는 수량."""

from dataclasses import replace

import pytest

from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu

CTX = NluContext(
    menu_names=("메도빅", "나폴레옹", "아메리카노", "블린"),
    cart_summary="",
    last_question="",
    state="ordering",
)
WITH_CART = replace(CTX, cart_summary="메도빅 하나", state="confirm")


def parse(text: str, ctx: NluContext = CTX) -> list[Act]:
    return FakeNlu().parse(text, ctx)


@pytest.mark.parametrize(
    "text", ["메도빅 많이 달아요?", "메도빅 얼마예요", "메도빅 알레르기 있어요"]
)
def test_menu_question(text: str) -> None:
    assert parse(text) == [
        Act("ask_menu_info", menu_text="메도빅", menu_guess="메도빅", free_text=text)
    ]


def test_question_without_menu() -> None:
    assert parse("글루텐 프리예요?") == [Act("ask_menu_info", free_text="글루텐 프리예요?")]


def test_plain_question_mark_is_not_menu_question() -> None:
    assert parse("여보세요?") == [Act("unknown", free_text="여보세요?")]


def test_no_prefix_then_correction() -> None:
    assert parse("아니요, 아메리카노 두 잔이요", WITH_CART) == [
        Act("no"),
        Act("add_item", menu_text="아메리카노", menu_guess="아메리카노", qty=2),
    ]


@pytest.mark.parametrize("text", ["아니요, 두 개요", "아니 두 개요", "두 개요"])
def test_bare_qty_with_cart_is_change_qty(text: str) -> None:
    acts = parse(text, WITH_CART)
    assert acts[-1] == Act("change_qty", qty=2)


def test_bare_qty_with_empty_cart_is_unknown() -> None:
    assert parse("두 개요") == [Act("unknown", free_text="두 개요")]


def test_ne_gae_is_four_not_yes() -> None:
    assert parse("네 개요", WITH_CART) == [Act("change_qty", qty=4)]


def test_no_prefix_then_time() -> None:
    assert parse("아니요, 7시요", WITH_CART) == [
        Act("no"),
        Act("set_pickup_time", time_text="7시"),
    ]
