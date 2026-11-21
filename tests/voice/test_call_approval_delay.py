"""통화 세션 → 늦춘 수락 (주문 승인 티켓 04, spec "세션", 이음매 2).

가짜 사장님이 [+15분]·[+30분]을 누르면 세션이 봇 턴으로 바뀐 시각을 묻는다. 손님 "네"면 그 시각의
확정 주문서가 넘어가고, "아니요"면 요청을 취소로 거두고 주문서는 없다. 답 없이 끊으면 원래 주문서를
넘긴다(허브가 통화 뒤 늦춘 수락 문자를 보낸다). 손님 화면 `state.approval`도 따라 바뀐다.
"""

from __future__ import annotations

import asyncio
import json

from call_harness import WAIT, Call

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.call.session import SessionSettings

SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]
OFFER = "사장님이 6시 45분에 준비 가능하시대요. 괜찮으세요?"


def run(coro):
    return asyncio.run(coro)


def said(c: Call, turn: int) -> str:
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


def records(c: Call) -> dict[int, dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return {line["turn"]: line for line in lines}


def views(c: Call) -> list[dict | None]:
    """`state` 이벤트에 실린 확인 요청 상태가 바뀐 차례 (손님 화면 배지, 티켓 09)."""
    steps: list[dict | None] = []
    for event in c.out.of("state"):
        if not steps or steps[-1] != event["approval"]:
            steps.append(event["approval"])
    return steps


def view(status: str, delay_min: int = 0, reason: str = "") -> dict:
    return {"status": status, "number": 1, "delay_min": delay_min, "reason": reason}


async def offered(c: Call, owner: RecordingOwnerChannel, minutes: int = 15) -> None:
    """복창 "네"(확인 중) → 사장님이 늦춰 수락 → 바뀐 시각을 묻는 봇 턴 5 재생 끝."""
    await c.start()
    for turn in (1, 2, 3, 4):
        await c.turn(turn)
    owner.decide(1, "delay", delay_min=minutes)
    await c.out.wait_for("bot.done", turn=5)
    await c.played(5)


def test_the_customer_hears_the_new_time_and_yes_confirms_at_that_time(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=SessionSettings(approval_wait_s=5.0)) as c:
            await offered(c, owner)
            await c.turn(6)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == OFFER
    assert said(c, 6) == "네, 6시 45분까지 준비해 둘게요. 감사합니다."
    (order,) = c.sink.orders
    assert (order.status, order.pickup_time) == ("confirmed", "2026-11-08T18:45:00+09:00")
    assert owner.withdrawn == []
    assert owner.ended == ["confirmed"]
    assert records(c)[5]["approval_decision"]["kind"] == "delay"
    assert records(c)[5]["approval_decision"]["delay_min"] == 15
    assert views(c)[-3:] == [view("waiting"), view("delayed", 15), view("accepted", 15)]


def test_no_to_the_new_time_cancels_and_withdraws_the_request(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    stt = FakeSttStream(replies=[*SCRIPT, "아니요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner,
                        settings=SessionSettings(approval_wait_s=5.0)) as c:
            await offered(c, owner, 30)
            await c.turn(6)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 5) == "사장님이 7시에 준비 가능하시대요. 괜찮으세요?"
    assert said(c, 6) == "알겠어요, 이번 주문은 취소할게요. 다음에 또 주문해 주세요."
    assert c.sink.orders == []
    assert owner.withdrawn == [(1, "cancelled")]  # 사장님 카드는 취소로
    assert owner.ended == ["cancelled"]
    assert views(c)[-1] == view("withdrawn", reason="cancelled")


def test_hanging_up_on_the_new_time_hands_the_original_order_over(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=SessionSettings(approval_wait_s=5.0)) as c:
            await offered(c, owner)
            await c.session.feed_event({"type": "call.end", "turn": 5})
            await asyncio.sleep(0.1)
            return c

    c = run(call())  # 하네스가 나가며 한 번 더 끝내도 주문서는 한 번
    assert owner.ended == ["hangup"]
    assert owner.withdrawn == []
    (order,) = c.sink.orders  # 원래 시각의 주문서. 허브가 늦춘 수락 문자를 보낸다
    assert order == owner.approvals[0].order


def test_the_wait_timer_does_not_cut_in_after_the_delay_was_heard(tmp_path) -> None:
    """결정을 말한 뒤에는 통화 중 대기 시간이 지나도 문자 약속으로 끝내지 않는다."""
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> tuple[list, list]:
        async with Call(tmp_path, stt=FakeSttStream(replies=SCRIPT), owner=owner,
                        settings=SessionSettings(approval_wait_s=1.0, no_input_s=5.0)) as c:
            await offered(c, owner)
            await asyncio.sleep(1.2)
            return c.out.of("bot.say", 6), list(c.sink.orders)  # 하네스가 끊기 전

    assert run(call()) == ([], [])

