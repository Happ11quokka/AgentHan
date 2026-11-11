"""5단계 티켓 04: 2026-11-11 아이폰 실기기 통화에서 나온 엔진 버그.

act는 그 통화에서 HCX가 실제로 낸 것 그대로다(진단 재현 `engine_replay`, 티켓 04 Comments).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=KST)
TEMP_Q = "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"


class ScriptedNlu:
    def __init__(self, script: dict[str, Sequence[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if text in self._script:
            return list(self._script[text])
        return FakeNlu().parse(text, ctx)


def _engine(script: dict[str, Sequence[Act]]) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), ScriptedNlu(script),
                          StoreConfig.from_fixture(), order_id="o-s5-04")
    engine.start(NOW)
    return engine


def _lines(r: TurnResult) -> list[tuple[str, int, tuple[str, ...]]]:
    return [(line.item.id, line.qty, tuple(o.name for o in line.options)) for line in r.cart.lines]


ICE_ORDER = "음맥도미 두 개랑 아이스아메리카노 하나요"
ICE_ACTS = [
    Act("add_item", menu_text="음맥도미", menu_guess="메도빅", qty=2),
    Act("add_item", menu_text="아이스 아메리카노", menu_guess="아메리카노", qty=1),
]


def test_temperature_inside_the_menu_name_picks_the_option() -> None:
    """HCX가 "아이스"를 메뉴명 쪽에만 넣었다. 다시 묻지 않고 차갑게로 담는다."""
    r = _engine({ICE_ORDER: ICE_ACTS}).handle(ICE_ORDER, NOW)
    assert _lines(r) == [("m01", 2, ()), ("m03", 1, ("차갑게",))]
    assert TEMP_Q not in r.utterances
    assert r.utterances[0].startswith("메도빅 두 개, 아이스 아메리카노 한 잔이요.")


def test_hot_word_inside_the_menu_name_picks_hot() -> None:
    said = "뜨거운 아메리카노 한 잔이요"
    r = _engine({said: [Act("add_item", menu_text="뜨거운 아메리카노", menu_guess="아메리카노",
                            qty=1)]}).handle(said, NOW)
    assert _lines(r) == [("m03", 1, ("따뜻하게",))]


def test_menu_name_without_an_option_word_still_asks_the_temperature() -> None:
    said = "아메리카노 하나요"
    r = _engine({said: [Act("add_item", menu_text="아메리카노", menu_guess="아메리카노", qty=1)]}
                ).handle(said, NOW)
    assert _lines(r) == [("m03", 1, ())]
    assert TEMP_Q in r.utterances


def test_option_answer_whose_menu_text_is_not_a_menu_answers_the_question() -> None:
    """"아이스로 주세요"를 HCX가 menu_text "아이스로"로 냈다. "그 메뉴는 없어요"가 아니라 차갑게."""
    first = "아메리카노 하나요"
    answer = "아이스로 주세요"
    engine = _engine({
        first: [Act("add_item", menu_text="아메리카노", menu_guess="아메리카노", qty=1)],
        answer: [Act("set_option", menu_text="아이스로", menu_guess="아메리카노",
                     option_text="차가운")],
    })
    r = engine.handle(first, NOW)
    assert r.state is State.CLARIFY and TEMP_Q in r.utterances
    r = engine.handle(answer, NOW)
    assert _lines(r) == [("m03", 1, ("차갑게",))]
    assert not any("메뉴는 없어요" in u for u in r.utterances)
    assert r.state is State.ORDERING


def test_option_with_a_non_menu_name_goes_to_the_last_item_while_ordering() -> None:
    first = "아메리카노 따뜻하게 하나요"
    change = "아이스로 바꿔 주세요"
    engine = _engine({
        first: [Act("add_item", menu_text="아메리카노", menu_guess="아메리카노", qty=1,
                    option_text="따뜻하게")],
        change: [Act("set_option", menu_text="아이스로", option_text="아이스")],
    })
    engine.handle(first, NOW)
    r = engine.handle(change, NOW)
    assert _lines(r) == [("m03", 1, ("차갑게",))]
    assert not any("메뉴는 없어요" in u for u in r.utterances)


def test_removing_an_option_word_is_not_told_it_is_missing_from_the_cart() -> None:
    """HCX가 "블린 하나요 샤워 크림 빼주세요"를 remove_item 샤워 크림 하나로 냈다.

    샤워 크림은 메뉴가 아니라 블린의 옵션 대상어(사워크림)다. "주문하신 것 중에 샤워 크림은
    없어요"라고 하지 않고, 담긴 블린이 있으면 옵션으로 붙인다.
    """
    first = "블린 하나요"
    fix = "샤워 크림 빼주세요"
    engine = _engine({
        first: [Act("add_item", menu_text="블린", menu_guess="블린", qty=1)],
        fix: [Act("remove_item", menu_text="샤워 크림", qty=1)],
    })
    engine.handle(first, NOW)
    r = engine.handle(fix, NOW)
    assert _lines(r) == [("m04", 1, ("사워크림 빼기",))]
    assert not any("없어요" in u for u in r.utterances)


def test_removing_an_option_word_with_an_empty_cart_orders_the_only_menu_with_it() -> None:
    """HCX가 블린을 놓치고 remove_item "샤워 크림"만 냈다. 사워크림 빼기는 블린에만 있어 블린으로
    담는다(티켓 10). 손님이 실제로 원한 주문이다."""
    said = "음 블린 하나요 샤워 크림 빼주세요."
    r = _engine({said: [Act("remove_item", menu_text="샤워 크림", qty=1)]}).handle(said, NOW)
    assert not any("주문하신 것 중" in u for u in r.utterances)
    assert _lines(r) == [("m04", 1, ("사워크림 빼기",))]


def test_a_bare_misheard_menu_name_that_nlu_gave_up_on_is_confirmed() -> None:
    """STT "맥도빅" 한 마디를 HCX가 unknown으로 냈다. 짧은 말이 메뉴와 비슷하면 되묻는다."""
    engine = _engine({"맥도빅": [Act("unknown", free_text="맥도빅")]})
    r = engine.handle("맥도빅", NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == ["혹시 메도빅 말씀하신 거 맞으세요?"]
    r = engine.handle("네", NOW)
    assert _lines(r) == [("m01", 1, ())]


def test_a_long_sentence_nlu_gave_up_on_is_not_guessed_as_a_menu() -> None:
    said = "아니 블리크림에 빼달라고"
    r = _engine({said: [Act("unknown", free_text=said)]}).handle(said, NOW)
    assert r.cart.is_empty()
    assert r.state is not State.CLARIFY


def test_saying_the_option_that_is_already_set_still_gets_an_answer() -> None:
    """이미 차갑게인 아메리카노에 "아이스로 주세요": 말없이 넘어가지 않는다."""
    first = "아메리카노 차갑게 하나요"
    again = "아이스로 주세요"
    engine = _engine({
        first: [Act("add_item", menu_text="아메리카노", menu_guess="아메리카노", qty=1,
                    option_text="차갑게")],
        again: [Act("set_option", menu_text="아이스로", menu_guess="아메리카노",
                    option_text="아이스")],
    })
    engine.handle(first, NOW)
    r = engine.handle(again, NOW)
    assert _lines(r) == [("m03", 1, ("차갑게",))]
    assert r.utterances, "봇이 아무 말도 안 하면 손님은 끊긴 줄 안다"
    assert "아이스" in r.utterances[0]


def test_an_option_only_one_menu_has_orders_that_menu_when_nothing_is_in_the_cart() -> None:
    """2026-11-11 실기기: 메도빅을 물어본 뒤 "홀케이크로 줘." → "어떤 메뉴에 해 드릴까요?".
    홀케이크는 메도빅에만 있는 옵션이라 메도빅으로 담는다(가게 데이터로 판정, 티켓 10)."""
    said = "홀케이크로 줘."
    r = _engine({said: [Act("set_option", option_text="홀케이크")]}).handle(said, NOW)
    assert _lines(r) == [("m01", 1, ("홀케이크 변경",))]
    assert r.utterances[0].startswith("메도빅 홀케이크로 하나요.")


def test_an_option_several_menus_have_still_asks_which_menu() -> None:
    from pathlib import Path

    pho = Path(__file__).parents[2] / "fixtures" / "phone" / "sample_pho_store.json"
    said = "고수 빼 주세요"
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(pho),
                          ScriptedNlu({said: [Act("set_option", option_text="고수 빼")]}),
                          StoreConfig.from_fixture(pho), order_id="o-pho")
    engine.start(NOW)
    r = engine.handle(said, NOW)
    assert r.cart.is_empty()
    assert r.utterances == ["어떤 메뉴에 해 드릴까요?"]
