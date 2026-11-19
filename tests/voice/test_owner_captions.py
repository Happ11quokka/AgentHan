"""통화 자막 (4단계 티켓 10): HTTP 없이 허브 공개 인터페이스만 본다 (spec 이음매 3).

통화 세션이 `OwnerChannel`로 알리는 손님 말(`customer_said`)·AI 말(`bot_said`)이 사장님 화면에
`call.caption`(통화 하나의 자막 전체, 최근 4줄)으로 가는지 본다. 손님 말은 원문으로 먼저 오고
번역이 오면 같은 줄을 덮어쓴다. AI 말은 번역하지 않는다(한국어 원문 회색 한 줄은 화면 몫).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.owner.fakes import FakeTranslator
from agenthan.voice.owner.hub import OwnerEvent, OwnerHub

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
WAIT = 2.0


def _hub(**kw) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW), **kw)


class Screen:
    """사장님 화면 하나: 구독해서 받은 이벤트를 모은다 (`test_owner_ru_card.Screen`과 같다)."""

    QUIET = 0.1

    def __init__(self, hub: OwnerHub) -> None:
        self._stream = hub.events()
        self._next: asyncio.Future[OwnerEvent] | None = None

    async def _get(self, timeout: float) -> OwnerEvent:
        if self._next is None:
            self._next = asyncio.ensure_future(anext(self._stream))
        done, _ = await asyncio.wait({self._next}, timeout=timeout)
        if not done:
            raise TimeoutError
        event, self._next = self._next.result(), None
        return event

    async def one(self) -> OwnerEvent:
        return await self._get(WAIT)

    async def rest(self) -> list[OwnerEvent]:
        """더 오지 않을 때까지(`QUIET`초 조용할 때까지) 받는다."""
        got = []
        while True:
            try:
                got.append(await self._get(self.QUIET))
            except TimeoutError:
                return got

    async def close(self) -> None:
        if self._next is not None:
            self._next.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await self._next
        await self._stream.aclose()


def _captions(events: list[OwnerEvent]) -> list[dict]:
    return [e.data for e in events if e.name == "call.caption"]


def test_customer_and_bot_lines_come_in_the_order_they_were_said() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.one()  # snapshot
        hub.call_started("call-1")
        hub.bot_said("call-1", "안녕하세요, 루스키 베이커리입니다.")
        hub.customer_said("call-1", "메도빅 하나요")
        hub.bot_said("call-1", "메도빅 하나 담았어요.")
        events = await screen.rest()
        await screen.close()
        return events

    captions = _captions(asyncio.run(scenario()))
    assert captions[-1] == {"call_id": "call-1", "lines": [
        {"n": 1, "who": "bot", "text": "안녕하세요, 루스키 베이커리입니다.", "text_owner": ""},
        {"n": 2, "who": "customer", "text": "메도빅 하나요", "text_owner": ""},
        {"n": 3, "who": "bot", "text": "메도빅 하나 담았어요.", "text_owner": ""},
    ]}
    assert [len(c["lines"]) for c in captions] == [1, 2, 3]  # 줄마다 자막 전체를 다시 보낸다


def test_only_the_last_four_lines_are_kept() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.one()
        hub.call_started("call-1")
        for i in range(1, 4):
            hub.customer_said("call-1", f"손님 {i}")
            hub.bot_said("call-1", f"AI {i}")
        events = await screen.rest()
        await screen.close()
        return events

    last = _captions(asyncio.run(scenario()))[-1]
    assert [(line["n"], line["text"]) for line in last["lines"]] == [
        (3, "손님 2"), (4, "AI 2"), (5, "손님 3"), (6, "AI 3")]


def test_a_customer_line_comes_first_in_korean_and_its_translation_overwrites_it() -> None:
    gate = asyncio.Event()
    translator = FakeTranslator({"[1] 하나요": "[1] одну"}, gate=gate)

    async def scenario():
        hub = _hub(translator=translator)
        screen = Screen(hub)
        await screen.one()
        hub.call_started("call-1")
        hub.customer_said("call-1", "메도빅 하나요")
        hub.bot_said("call-1", "메도빅 하나 담았어요.")
        before = await screen.rest()
        gate.set()
        after = await screen.rest()
        await screen.close()
        return before, after

    before, after = asyncio.run(scenario())
    assert _captions(before)[-1]["lines"] == [
        {"n": 1, "who": "customer", "text": "메도빅 하나요", "text_owner": ""},
        {"n": 2, "who": "bot", "text": "메도빅 하나 담았어요.", "text_owner": ""},
    ]
    assert _captions(after) == [{"call_id": "call-1", "lines": [
        {"n": 1, "who": "customer", "text": "메도빅 하나요", "text_owner": "Медовик одну"},
        {"n": 2, "who": "bot", "text": "메도빅 하나 담았어요.", "text_owner": ""},
    ]}]
    assert translator.calls == [(("[1] 하나요",), "ko", "ru")]  # AI 말은 번역하지 않는다


@pytest.mark.parametrize("translator", [FakeTranslator(fail=True), None],
                         ids=["translate-fails", "no-key"])
def test_a_customer_line_stays_in_korean_when_translation_fails(translator) -> None:
    async def scenario():
        hub = _hub(translator=translator)
        screen = Screen(hub)
        await screen.one()
        hub.call_started("call-1")
        hub.customer_said("call-1", "고수 빼 주세요")
        events = await screen.rest()
        await screen.close()
        return events

    assert _captions(asyncio.run(scenario())) == [{"call_id": "call-1", "lines": [
        {"n": 1, "who": "customer", "text": "고수 빼 주세요", "text_owner": ""}]}]


def test_a_new_screen_gets_the_captions_of_calls_still_on_the_line() -> None:
    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        hub.call_started("call-2")
        hub.call_started("call-3")
        for call_id in ("call-1", "call-2", "call-3"):
            hub.customer_said(call_id, f"{call_id} 손님")
        hub.call_ended("call-2", "hangup")  # 미완료 카드
        hub.submit(Order(id="call-3", lines=[OrderLine("m01", 1, name="메도빅", unit_price=7_500)],
                         pickup_time="2026-11-08T18:30:00+09:00", status="confirmed"))
        screen = Screen(hub)
        snapshot = await screen.one()
        await screen.close()
        return snapshot

    snapshot = asyncio.run(scenario())
    assert snapshot.data["captions"] == [{"call_id": "call-1", "lines": [
        {"n": 1, "who": "customer", "text": "call-1 손님", "text_owner": ""}]}]


def test_lines_after_the_call_left_the_line_are_not_captioned() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.one()
        hub.customer_said("call-0", "카드 없는 통화")  # `call_started` 없이 온 말
        hub.call_started("call-1")
        hub.call_ended("call-1", "hangup")
        hub.bot_said("call-1", "끝난 뒤 대사")
        events = await screen.rest()
        await screen.close()
        return events

    assert _captions(asyncio.run(scenario())) == []
