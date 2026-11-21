"""대기 없는 주문: 통화 세션 (전화 메뉴·주문 현황·쿠폰 티켓 04, spec "주문 현황").

기본 설정(통화 중 대기 0)에서 복창에 "네"면 그 턴에 "주문 넣어 드렸어요. …"를 말하고 통화가 끝난다.
대기 대사·대기음 신호(`bot.hold`)·10초 재촉이 없다. 확인 요청은 통화 뒤 단계로 열리고(자동 취소 =
요청 시각 + 5분), 사장님이 통화 뒤 [수락]하면 주문 현황이 "확정"이 된다. 사장님 화면이 꺼져 있으면
지금처럼 자동 확정이고 주문 현황은 처음부터 "확정"이다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from call_harness import WAIT, Call
from test_call_approval import CLOSING, SCRIPT, bot_turn, records, run, said
from test_call_approval_hub import _card

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock, FakeSttStream, RecordingOwnerChannel
from agenthan.voice.owner.hub import OwnerHub

PLACED = "주문 넣어 드렸어요. 가게에서 확인하면 화면으로 알려 드릴게요. 감사합니다."
NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
CALL = "test-call"


async def to_end(c: Call) -> None:
    """인사 → 메도빅 → 그게 다예요 → 6시 반 → "네"(마무리 재생 끝) → 통화 끝."""
    await c.start()
    for turn in (1, 2, 3):
        await c.turn(turn)
    await c.say()
    await bot_turn(c, 4)
    await asyncio.wait_for(c.session.wait_done(), WAIT)


def test_yes_ends_the_call_at_once_with_no_hold_and_no_nudge(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")  # 사장님 화면은 켜져 있다

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner) as c:
            await to_end(c)
            await asyncio.sleep(0.3)  # 끝난 통화에는 재촉·결정 턴이 없다
            return c

    c = run(call())
    assert said(c, 4) == PLACED
    assert c.out.of("bot.say", 5) == []
    assert c.out.of("bot.hold") == []  # 대기음 신호 없음
    (request,) = owner.approvals
    assert request.in_call is False
    assert [o.status for o in c.sink.orders] == ["confirmed"]  # 주문서는 "네" 턴에 넘어간다
    assert len(c.out.of("order", 4)) == 1
    assert c.out.of("call.ended")[0]["reason"] == "confirmed"
    assert c.out.of("state")[-1]["approval"]["status"] == "after_call"
    assert records(c)[4]["approval_request"]["in_call"] is False


async def _hub() -> tuple[OwnerHub, FakeClock, Any]:
    clock = FakeClock(NOW)
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock)
    screen = hub.events()
    await anext(screen)  # 사장님 화면 하나 (승인을 받는다)
    return hub, clock, screen


def test_the_request_opens_after_the_call_and_an_accept_confirms_the_status(tmp_path) -> None:
    async def call() -> tuple[Call, dict, dict, dict]:
        hub, clock, screen = await _hub()
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), hub=hub) as c:
            await to_end(c)
        card = await _card(hub)  # 사장님 화면이 받는 카드
        before = hub.order_status(CALL)
        clock.set(NOW + timedelta(minutes=2))
        hub.accept(CALL)
        after = hub.order_status(CALL)
        await screen.aclose()
        return c, card, before, after

    c, card, before, after = run(call())
    assert said(c, 4) == PLACED
    assert card["stage"] == "after_call"  # 처음부터 통화 뒤 확인
    requested = datetime.fromisoformat(card["requested_at"])
    assert datetime.fromisoformat(card["deadline_at"]) == requested + timedelta(minutes=5)
    assert before["stage"] == "checking"
    assert after["stage"] == "confirmed"
    assert after["pickup"]["label"] == "18:30"


def test_with_the_owner_screen_off_the_status_is_confirmed_from_the_start(tmp_path) -> None:
    async def call() -> tuple[Call, dict]:
        hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), hub=hub) as c:
            await to_end(c)
        return c, hub.order_status(CALL)

    c, status = run(call())
    assert said(c, 4) == CLOSING  # 자동 확정: 픽업 시각이 든 예전 마무리
    assert status["stage"] == "confirmed"
