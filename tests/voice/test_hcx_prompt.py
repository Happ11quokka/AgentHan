"""HCX 프롬프트가 가게에 묶이지 않는지 (5단계 티켓 05)."""

from __future__ import annotations

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.nlu.base import NluContext
from agenthan.voice.nlu.hcx import build_messages

SAMPLE_WORDS = ("메도빅", "나폴레옹", "블린", "아메리카노", "사워크림", "허니케", "빵집",
                "베이커리", "디카페인", "홀케이크")


def _system(ctx: NluContext) -> str:
    return build_messages("주세요", ctx)[0]["content"]


def test_the_fixed_prompt_names_no_menu_of_the_sample_store() -> None:
    empty = NluContext(menu_names=(), cart_summary="", last_question="", state="ordering")
    system = _system(empty)
    assert [w for w in SAMPLE_WORDS if w in system] == []


def test_the_store_menu_comes_only_from_the_context_with_its_options() -> None:
    menu = tuple(m.name_ko for m in FakeStoreKnowledge.from_fixture().list_menu())
    ctx = NluContext(menu_names=menu, cart_summary="", last_question="", state="ordering",
                     menu_catalog=("블린 | 옵션: 사워크림 빼기",
                                   "아메리카노 | 꼭 고를 것 온도: 따뜻하게/차갑게"))
    system = _system(ctx)
    header = "메뉴 목록 (이름 | 다른 이름 | 꼭 고를 것 | 옵션):"
    assert f"{header}\n- 블린 | 옵션: 사워크림 빼기" in system
    assert "- 아메리카노 | 꼭 고를 것 온도: 따뜻하게/차갑게" in system


def test_without_a_catalog_the_menu_names_are_still_given() -> None:
    ctx = NluContext(menu_names=("반미", "분짜"), cart_summary="", last_question="",
                     state="ordering")
    assert "반미, 분짜" in _system(ctx)
