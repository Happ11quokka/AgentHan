"""통화 세션 → 사장님 주문 확인 (주문 승인 티켓 02, spec "세션", 이음매 2).

엔진 턴 결과에 확인 요청이 실리면 세션이 `OwnerChannel.open_approval`로 연다(바로 돌아옴). 사장님
결정은 세션이 준 받는 곳으로 들어오고, 세션은 듣는 중이 되면 봇이 먼저 말하는 턴으로 엔진에
넣는다. 질문 카드와 같은 대기 장치(01)를 쓴다. 가짜 사장님: 즉시 수락(accept) · 결정 안 함(hold,
시험이 `decide`로 늦은 결정을 흉내 냄) · 화면 없음(off, 기본).
"""

from __future__ import annotations

import asyncio
import json
import time

from call_harness import WAIT, Call, tone

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.call.session import SessionSettings

SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]
HOLD = "사장님께 주문 확인 중이에요. 잠시만요."
CLOSING = "네, 6시 반까지 준비해 둘게요. 감사합니다."
LATER = ("가게에서 아직 확인 중이에요. 확인되는 대로 문자로 확정 알려 드릴게요. "
         "확정 문자를 받으시면 주문 완료예요. 감사합니다.")
QUIET = 0.3  # 시험용 무응답 대기. 확인 대기보다 짧아서 안 멈췄으면 "여보세요?"가 먼저 나간다
APPROVAL_WAIT = 0.9  # 시험용 통화 중 대기 (기본 20초)


def run(coro):
    return asyncio.run(coro)


def said(c: Call, turn: int) -> str:
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


def waiting(**kw) -> SessionSettings:
    return SessionSettings(**{"no_input_s": QUIET, "approval_wait_s": APPROVAL_WAIT, **kw})


def records(c: Call) -> dict[int, dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return {line["turn"]: line for line in lines}


async def bot_turn(c: Call, turn: int) -> None:
    await c.out.wait_for("bot.done", turn=turn)
    await c.played(turn)


async def to_yes(c: Call) -> float:
    """인사 → 메도빅 → 그게 다예요 → 6시 반 → "네"("확인 중" 재생 끝까지). 요청을 연 때."""
    await c.start()
    for turn in (1, 2, 3):
        await c.turn(turn)
    await c.say()
    await c.out.wait_for("bot.say", turn=4)
    opened = time.monotonic()
    await bot_turn(c, 4)
    return opened


def test_an_owner_who_accepts_at_once_is_heard_after_the_hold_line(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="accept")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner) as c:
            await to_yes(c)
            await bot_turn(c, 5)  # 수락을 말하는 봇 턴
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 4) == HOLD
    assert said(c, 5) == CLOSING
    (request,) = owner.approvals
    assert request.number == 1
    assert [(line.menu_id, line.quantity) for line in request.order.lines] == [("m01", 1)]
    assert [o.status for o in c.sink.orders] == ["confirmed"]  # 수락 뒤에 주문서가 넘어간다
    assert c.out.of("order", 4) == [] and len(c.out.of("order", 5)) == 1
    assert owner.ended == ["confirmed"]
    assert c.out.of("call.ended")[0]["reason"] == "confirmed"
    turns = records(c)
    assert turns[4]["approval_request"]["number"] == 1
    assert turns[5]["bot_turn"] == "owner_decision"
    assert turns[5]["approval_decision"]["kind"] == "accept"


def test_without_an_owner_screen_the_call_confirms_at_once(tmp_path) -> None:
    owner = RecordingOwnerChannel()  # 화면 없음 (기본)

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 4) == CLOSING
    assert owner.approvals == []
    assert [o.status for o in c.sink.orders] == ["confirmed"]


def test_with_approval_switched_off_an_owner_screen_does_not_hold_the_call(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    settings = SessionSettings(owner_approval=False)

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=settings) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 4) == CLOSING
    assert owner.approvals == []


def test_a_late_accept_is_said_and_there_is_no_hello_while_waiting(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await asyncio.sleep(QUIET * 2)  # 기다리는 중: 무응답 되묻기 없음
            assert c.out.of("bot.say", 5) == []
            owner.decide(1, "accept")
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == CLOSING
    assert records(c)[5]["approval_decision"]["wait_ms"] >= QUIET * 2000 - 50


def test_no_decision_in_time_promises_a_text_and_ends_the_call(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> tuple[Call, float, float]:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting()) as c:
            opened = await to_yes(c)
            await c.out.wait_for("bot.say", turn=5)
            spoke = time.monotonic()
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            owner.decide(1, "accept")  # 통화 뒤 결정: 세션은 버린다(문자는 허브 몫)
            await asyncio.sleep(0.1)
            return c, opened, spoke

    c, opened, spoke = run(call())
    assert spoke - opened >= APPROVAL_WAIT - 0.05
    assert said(c, 5) == LATER
    assert [o.status for o in c.sink.orders] == ["confirmed"]  # 주문서는 넘어간다
    assert owner.ended == ["confirmed"]
    assert c.out.of("bot.say", 6) == []
    assert records(c)[5]["approval_decision"]["kind"] == "timeout"


def test_a_decision_while_the_customer_is_speaking_waits_for_that_turn(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "네네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await c.feed(tone(500))  # 손님이 말하기 시작했다(아직 글 없음)
            owner.decide(1, "accept")
            await asyncio.sleep(0.3)
            assert c.out.of("bot.say", 5) == []  # 손님 말을 자르지 않는다
            await c.turn(5)  # 그 말이 턴이 된다 ("확인 중이에요")
            await bot_turn(c, 6)  # 그 턴 뒤에 수락
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == HOLD
    assert said(c, 6) == CLOSING
    assert records(c)[6]["approval_decision"]["deferred_ms"] >= 300


def test_changing_the_order_while_waiting_withdraws_the_request_and_drops_its_decision(
        tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "메도빅 두 개로요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await c.turn(5)  # 고친 항목 되받기 + 다시 복창
            owner.decide(1, "accept")  # 거둔 요청의 결정: 버린다
            await asyncio.sleep(0.2)
            assert c.out.of("bot.say", 6) == []
            await c.turn(6)  # "네" → 새 번호로 다시 요청
            owner.decide(2, "accept")
            await bot_turn(c, 7)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert owner.withdrawn == [(1, "changed")]
    assert [r.number for r in owner.approvals] == [1, 2]
    assert said(c, 6) == HOLD
    assert said(c, 7) == "네, 6시 반까지 준비해 둘게요. 감사합니다."
    (order,) = c.sink.orders
    assert [(line.menu_id, line.quantity) for line in order.lines] == [("m01", 2)]


def test_a_smoke_call_never_asks_for_approval(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    settings = SessionSettings(smoke_key="smoke-key")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=settings) as c:
            await c.start(smoke="smoke-key")
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 4) == CLOSING
    assert owner.approvals == []


# 손님 화면의 확인 요청 상태 (주문 승인 티켓 09): `state` 이벤트의 `approval`


def approval_steps(c: Call) -> list[tuple[int, dict | None]]:
    """`state` 이벤트에 실린 확인 요청 상태가 바뀐 곳(턴, 값)을 차례로."""
    steps: list[tuple[int, dict | None]] = []
    for event in c.out.of("state"):
        if not steps or steps[-1][1] != event["approval"]:
            steps.append((event["turn"], event["approval"]))
    return steps


def view(status: str, number: int, delay_min: int = 0, reason: str = "") -> dict:
    return {"status": status, "number": number, "delay_min": delay_min, "reason": reason}


def test_the_customer_screen_sees_the_wait_and_then_the_accept(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="accept")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner) as c:
            await to_yes(c)
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert approval_steps(c) == [(0, None), (4, view("waiting", 1)), (5, view("accepted", 1))]


def test_the_customer_screen_sees_a_timeout_handed_over_to_texts(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=waiting()) as c:
            await to_yes(c)
            await bot_turn(c, 5)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert approval_steps(c)[-2:] == [(4, view("waiting", 1)), (5, view("timeout", 1))]


def test_the_customer_screen_sees_a_withdrawn_request_and_the_new_one(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "메도빅 두 개로요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            await c.turn(5)
            await c.turn(6)
            owner.decide(2, "accept")
            await bot_turn(c, 7)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert [v for _, v in approval_steps(c)] == [
        None, view("waiting", 1), view("withdrawn", 1, reason="changed"),
        view("waiting", 2), view("accepted", 2)]


def test_a_call_that_skips_approval_shows_the_order_was_confirmed_at_once(tmp_path) -> None:
    owner = RecordingOwnerChannel()  # 화면 없음

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert approval_steps(c) == [(0, None), (4, view("skipped", 0))]
