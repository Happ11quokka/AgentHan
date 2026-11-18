"""시각만 있는 손님 말은 Papago 없이 "H:MM"으로 (11/16 실기기).

Papago ko→ru가 "6시" → "э.", "6시 반" → "Шесть с половиной минут."으로 옮겨 숫자 확인이 버렸고,
사장님 화면에는 한국어만 남았다.
"""

from __future__ import annotations

import asyncio

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.owner.card_texts import CardTexts, time_only
from agenthan.voice.owner.lexicon import RU


class EchoTranslator:
    """보낸 글을 기록하고 "[ru] 글"을 돌려준다."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        self.sent.extend(texts)
        return [f"[ru] {t}" for t in texts]


@pytest.mark.parametrize(("text", "shown"), [
    ("6시", "6:00"),
    ("6시 반", "6:30"),
    ("6시 반이요", "6:30"),
    ("6시 30분에요.", "6:30"),
    ("오후 6시", "18:00"),
    ("저녁 7시 반쯤", "19:30"),
    ("오전 11시", "11:00"),
    ("12시", "12:00"),
])
def test_a_time_alone_becomes_a_clock(text: str, shown: str) -> None:
    assert time_only(text) == shown


@pytest.mark.parametrize(
    "text", ["6시 반에 찾으러 갈게요", "메도빅 6개", "오케이 6시", "6시간", "30시"])
def test_other_sentences_are_not_times(text: str) -> None:
    assert time_only(text) is None


def test_time_captions_skip_the_translator_and_others_still_go() -> None:
    tr = EchoTranslator()
    texts = CardTexts(tr, store=FakeStoreKnowledge.from_fixture(), lexicon=RU)

    async def run() -> bool:
        todo = texts.claim("call-1", ["6시 반", "고수 빼 주세요"])
        return await texts.fetch("call-1", todo)

    assert asyncio.run(run())
    assert tr.sent == ["[1] 빼 주세요"]
    assert texts.known("call-1", "6시 반") == "6:30"
    assert texts.claim("call-1", ["6시 반"]) == []  # 다시 맡지 않는다


def test_only_time_captions_need_no_translator_call() -> None:
    tr = EchoTranslator()
    texts = CardTexts(tr, store=FakeStoreKnowledge.from_fixture(), lexicon=RU)
    assert asyncio.run(texts.fetch("call-1", texts.claim("call-1", ["6시"])))
    assert tr.sent == []
    assert texts.known("call-1", "6시") == "6:00"
