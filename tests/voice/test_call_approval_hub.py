"""통화 세션과 진짜 사장님 허브를 함께 돌린다: 결정이 손님 말·시간 초과와 겹치는 때 (승인 리뷰).

통화 중 결정은 통화가 그 결정을 손님에게 말해야(`approval_said`) 굳는다. 그 전에 손님이 주문을
고치거나 취소해 요청을 거두면 거둔 쪽이 이긴다: 카드는 "고치는 중"·손님 취소로 돌아가고, 확정
문자도 수락 기록도 없다. 세션이 아직 말하지 않은 시간 초과보다 사장님 결정이 먼저 와 있으면 결정을
말한다(재료 소진 메뉴가 든 주문을 자동 확정하지 않는다). 시간 초과 대사가 먼저 나간 뒤에 온 결정은
통화 뒤 결정처럼 문자로 간다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from call_harness import WAIT, Call, tone
from test_call_approval import CLOSING, HOLD, LATER, SCRIPT, bot_turn, run, said, to_yes, waiting
from test_call_approval_out_of_stock import NOTICE, TWO
from test_call_approval_out_of_stock import to_yes as to_yes_two

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock, FakeSttStream
from agenthan.voice.owner.decisions import DecisionRecord
from agenthan.voice.owner.hub import OwnerHub

NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
CALL = "test-call"


class Decisions:
    """결정 기록 받는 곳 가짜 (DB 대신)."""

    def __init__(self) -> None:
        self.records: list[DecisionRecord] = []

    def record(self, decision: DecisionRecord) -> None:
        self.records.append(decision)

    @property
    def statuses(self) -> list[str]:
        return [r.owner_status for r in self.records]


async def _hub() -> tuple[OwnerHub, Decisions, Any]:
    """허브 + 결정 기록 + 사장님 화면 하나(구독해 있어야 승인을 받는다)."""
    decisions = Decisions()
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW),
                   decision_sink=decisions)
    screen = hub.events()
    await anext(screen)  # snapshot
    return hub, decisions, screen


def _texts(hub: OwnerHub) -> list[str]:
    return [t["kind"] for t in hub.customer_texts(CALL)]


async def _card(hub: OwnerHub) -> dict[str, Any]:
    """새로 연 사장님 화면이 받는 그 통화의 주문 카드(snapshot)."""
    stream = hub.events()
    snapshot = await anext(stream)
    await stream.aclose()
    return next(card for card in snapshot.data["orders"] if card["id"] == CALL)


def test_an_accept_overtaken_by_a_change_is_not_confirmed(tmp_path) -> None:
    stt = FakeSttStream(replies=[*SCRIPT, "메도빅 두 개로요"])

    async def call() -> tuple[OwnerHub, Decisions, Call, dict, dict]:
        hub, decisions, screen = await _hub()
        async with Call(tmp_path, stt=stt, hub=hub, settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await c.feed(tone(500))  # 손님이 고치기 시작했다
            hub.accept(CALL)  # 그 사이 사장님이 수락한다. 손님이 말하는 중이라 아직 말하지 않는다
            await asyncio.sleep(0.2)
            await c.turn(5)  # 고친 주문 → 요청을 거둔다
            await asyncio.sleep(0.2)
            during = await _card(hub)
        after = await _card(hub)  # 통화 끝(고치는 중에 끊음)
        await screen.aclose()
        return hub, decisions, c, during, after

    hub, decisions, c, during, after = run(call())
    assert CLOSING not in said(c, 5)
    assert c.out.of("bot.say", 6) == []  # 거둔 요청의 수락은 말하지 않는다
    assert (during["status"], during["withdrawn"]) == ("new", "changed")  # "손님이 고치는 중"
    assert (after["status"], after["withdrawn"]) == ("new", "cancelled")
    assert _texts(hub) == []  # 확정 문자 없음
    assert decisions.statuses == []  # 수락 기록 없음
    assert c.sink.orders == []


def test_an_accept_overtaken_by_a_cancel_is_not_confirmed(tmp_path) -> None:
    stt = FakeSttStream(replies=[*SCRIPT, "취소할게요", "네"])

    async def call() -> tuple[OwnerHub, Decisions, Call, dict]:
        hub, decisions, screen = await _hub()
        async with Call(tmp_path, stt=stt, hub=hub, settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await c.feed(tone(500))
            hub.accept(CALL, delay_min=15)  # 늦춘 수락도 같다
            await asyncio.sleep(0.2)
            await c.turn(5)  # "주문 취소할까요?" (요청을 거둔다)
            await c.turn(6)  # "네" → 취소
            await asyncio.wait_for(c.session.wait_done(), WAIT)
        await screen.aclose()
        return hub, decisions, c, await _card(hub)

    hub, decisions, c, card = run(call())
    assert c.out.of("call.ended")[0]["reason"] == "cancelled"
    assert (card["status"], card["withdrawn"], card["delay_reply"]) == ("new", "cancelled", "")
    assert _texts(hub) == []  # 확정 문자도 늦춘 수락 문자도 없다
    assert decisions.statuses == []
    assert c.sink.orders == []


def test_an_out_of_stock_reject_beats_a_timeout_that_was_not_said_yet(tmp_path) -> None:
    stt = FakeSttStream(replies=[*TWO, "네네"])

    async def call() -> tuple[OwnerHub, Decisions, Call, dict]:
        hub, decisions, screen = await _hub()
        async with Call(tmp_path, stt=stt, hub=hub,
                        settings=waiting(approval_wait_s=0.6, approval_reprompt_s=0)) as c:
            await to_yes_two(c)
            await c.feed(tone(500))  # 손님이 말하는 중에 시간 초과가 줄 선다
            await asyncio.sleep(0.7)
            hub.reject(CALL, "out_of_stock", menu_ids=["m01"])  # 그 뒤 재료 소진
            await asyncio.sleep(0.1)
            await c.turn(5)  # 그 말("확인 중")
            await bot_turn(c, 6)  # 그 턴 뒤: 시간 초과가 아니라 재료 소진을 말한다
        await screen.aclose()  # 손님이 답하지 않고 끊었다
        return hub, decisions, c, await _card(hub)

    hub, decisions, c, card = run(call())
    assert said(c, 5) == HOLD
    assert said(c, 6) == NOTICE
    assert LATER not in " ".join(e["text"] for e in c.out.of("bot.say"))
    assert (card["status"], card["reject_reason"], card["auto_accepted"]) == (
        "rejected", "out_of_stock", False)
    assert _texts(hub) == ["rejected"]  # 끊은 뒤 품절 취소 문자. 확정 문자 없음
    assert decisions.statuses == ["rejected"]
    assert c.sink.orders == []


def test_an_out_of_stock_reject_after_the_timeout_line_is_texted_not_auto_accepted(
        tmp_path) -> None:
    async def call() -> tuple[OwnerHub, Decisions, Call, dict]:
        hub, decisions, screen = await _hub()
        async with Call(tmp_path, stt=FakeSttStream(replies=TWO), hub=hub,
                        settings=waiting(approval_wait_s=0.5, approval_reprompt_s=0)) as c:
            await to_yes_two(c)
            await c.out.wait_for("bot.say", turn=5)  # 시간 초과 대사가 나가는 중
            hub.reject(CALL, "out_of_stock", menu_ids=["m01"])
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            await asyncio.sleep(0.05)
        await screen.aclose()
        return hub, decisions, c, await _card(hub)

    hub, decisions, c, card = run(call())
    assert said(c, 5) == LATER
    assert [o.status for o in c.sink.orders] == ["confirmed"]  # 통화는 문자 약속으로 끝났다
    assert (card["status"], card["auto_accepted"]) == ("rejected", False)
    assert _texts(hub) == ["rejected"]  # 품절 메뉴가 든 주문을 확정하지 않는다
    assert decisions.statuses == ["rejected"]


def test_an_accept_after_the_timeout_line_is_texted_once(tmp_path) -> None:
    async def call() -> tuple[OwnerHub, Decisions, Call, dict]:
        hub, decisions, screen = await _hub()
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), hub=hub,
                        settings=waiting(approval_wait_s=0.5, approval_reprompt_s=0)) as c:
            await to_yes(c)
            await c.out.wait_for("bot.say", turn=5)
            hub.accept(CALL)
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            await asyncio.sleep(0.05)
        await screen.aclose()
        return hub, decisions, c, await _card(hub)

    hub, decisions, c, card = run(call())
    assert said(c, 5) == LATER
    assert card["status"] == "accepted"
    assert _texts(hub) == ["order"]
    assert decisions.statuses == ["accepted"]


def test_an_accept_said_in_the_call_texts_and_records_once(tmp_path) -> None:
    async def call() -> tuple[OwnerHub, Decisions, Call]:
        hub, decisions, screen = await _hub()
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), hub=hub,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            hub.accept(CALL)
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            await asyncio.sleep(0.05)
        await screen.aclose()
        return hub, decisions, c

    hub, decisions, c = run(call())
    assert said(c, 5) == CLOSING
    assert _texts(hub) == ["order"]
    assert decisions.statuses == ["accepted"]
