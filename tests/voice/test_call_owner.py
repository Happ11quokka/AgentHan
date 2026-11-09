"""통화 세션 → 사장님 쪽 알림(`OwnerChannel`, 4단계 티켓 02). 세션 포트 이음매(spec 이음매 2).

가짜 사장님 쪽(`RecordingOwnerChannel`)이 받은 알림만 본다: 통화 시작, 장바구니 변경, 손님 말·AI 말,
통화 끝과 이유. 끝내는 길이 어디든 "통화 끝"은 이유와 함께 한 번만 나간다(V1 사실 교정 5).
"""

from __future__ import annotations

import asyncio

from call_harness import WAIT, Call
from test_call_session import GatedNlu

from agenthan.core.contracts import OrderLine
from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.call.ports import ClientGone, PortError
from agenthan.voice.call.session import SessionSettings

ORDER_SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]
MEDOVIK = OrderLine("m01", 1, name="메도빅", unit_price=7_500)


def run(coro):
    return asyncio.run(coro)


async def _talk(c: Call, turns: int) -> None:
    """인사 뒤 손님이 `turns`번 말하고 봇 대답을 다 듣는다. 통화가 끝날 때까지 기다린다."""
    await c.start()
    for turn in range(1, turns + 1):
        await c.turn(turn)
    await asyncio.wait_for(c.session.wait_done(), WAIT)


def test_the_owner_hears_the_call_start_the_cart_and_a_confirmed_end(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=ORDER_SCRIPT)) as c:
            await _talk(c, 4)
            return c

    owner = run(call()).owner
    assert owner.notes[0] == ("started", "test-call")
    assert owner.customer_lines == ORDER_SCRIPT
    assert owner.bot_lines[0] == "네, 샘플 러시안 베이커리입니다. 주문 도와드릴게요."
    assert owner.carts[0] == ()
    assert owner.carts[-1] == (MEDOVIK,)
    assert owner.ended == ["confirmed"]
    assert owner.notes[-1] == ("ended", "test-call", "confirmed")


# ---------------------------------------------------------------- 끝내는 길마다 "통화 끝" 한 번


def test_a_handoff_call_ends_with_the_handoff_reason(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "사장님 바꿔 주세요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await _talk(c, 2)
            return c

    c = run(call())
    assert c.owner.ended == ["handoff"]
    assert c.owner.customer_lines == ["메도빅 하나요", "사장님 바꿔 주세요"]


def test_a_cancelled_order_ends_with_the_cancelled_reason(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "주문 취소할게요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await _talk(c, 3)
            return c

    assert run(call()).owner.ended == ["cancelled"]


def test_silence_until_the_goodbye_ends_with_the_no_input_reason(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path, settings=SessionSettings(no_input_s=0.2)) as c:
            await c.start()
            for turn in (1, 2, 3):  # 되묻기 두 번 + 마지막 인사
                await c.out.wait_for("bot.done", turn=turn)
                await c.played(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    assert run(call()).owner.ended == ["no_input"]


def test_the_customer_hanging_up_mid_order_ends_with_the_hangup_reason(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=["메도빅 하나요"])) as c:
            await c.start()
            await c.turn(1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())  # 하네스가 나가며 `close()`로 한 번 더 끝내도 알림은 한 번
    assert c.owner.ended == ["hangup"]
    assert c.owner.carts[-1] == (MEDOVIK,)


def test_a_socket_that_just_goes_away_ends_with_the_hangup_reason(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path) as c:
            await c.start()
            return c  # 손님 화면이 `call.end` 없이 사라졌다: 라우터는 `close()`만 부른다

    assert run(call()).owner.ended == ["hangup"]


def test_a_connection_that_breaks_while_sending_ends_with_the_hangup_reason(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path) as c:

            async def gone(event):
                raise ClientGone("code 1006")

            c.out.send_event = gone  # type: ignore[method-assign]
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    assert run(call()).owner.ended == ["hangup"]


def test_an_stt_failure_ends_with_the_error_reason(tmp_path) -> None:
    stt = FakeSttStream()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            stt.fail(PortError("연결이 끊겼어요", code="stt_disconnected"))
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    assert run(call()).owner.ended == ["error"]


def test_an_engine_timeout_ends_with_the_error_reason(tmp_path) -> None:
    nlu = GatedNlu("메도빅 하나요")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=["메도빅 하나요"]), nlu=nlu,
                        settings=SessionSettings(turn_timeout_s=0.2)) as c:
            await c.start()
            await c.say()
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            nlu.release.set()  # 붙잡힌 엔진 스레드를 풀어 준다 (테스트 정리)
            return c

    c = run(call())
    assert c.out.of("error")[0]["code"] == "engine_timeout"
    assert c.owner.ended == ["error"]
    assert c.owner.customer_lines == ["메도빅 하나요"]


def test_a_call_that_never_starts_still_tells_the_owner_it_ended(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path, settings=SessionSettings(start_timeout_s=0.05)) as c:
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    assert run(call()).owner.notes == [("ended", "test-call", "error")]


def test_a_broken_owner_channel_does_not_break_the_call(tmp_path) -> None:
    class Broken(RecordingOwnerChannel):
        def cart_changed(self, call_id, lines):
            raise RuntimeError("허브가 죽었다")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=ORDER_SCRIPT), owner=Broken()) as c:
            await _talk(c, 4)
            return c

    c = run(call())
    assert [o.status for o in c.sink.orders] == ["confirmed"]
    assert c.owner.ended == ["confirmed"]
