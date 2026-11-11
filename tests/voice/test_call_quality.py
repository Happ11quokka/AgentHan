"""6단계 품질: 통화 세션이 남기는 품질 지표와 바깥 행동.

- 턴 기록에 엔진 경로(`route`), 되묻기(`reask`), 넘긴 이유(`handoff`)
- "잘 못 들었어요"가 통화 되묻기 상한에 닿으면 사장님께 넘기고 `call.ended {reason: handoff}`
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from call_harness import Call

from agenthan.voice.call.fakes import FakeSttStream
from agenthan.voice.store_config import StoreConfig

GIVE_UP = "죄송해요, 제가 잘 못 알아듣고 있어요. 사장님께 메모 남겨 드릴게요."


def run(coro):
    return asyncio.run(coro)


def records(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def said(c: Call, turn: int) -> str:
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


def test_turn_records_carry_the_route_reasks_and_the_handoff_reason(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "가나다라요", "마바사요", "아자차요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await c.session.wait_done()
            return c

    c = run(call())
    lines = records(c)
    assert "route" not in lines[0]  # 인사 턴은 손님 말이 아니다
    assert [line["route"] for line in lines[1:]] == ["nlu", "nlu", "nlu", "nlu"]
    assert "reask" not in lines[1]
    assert lines[2]["reask"] == {"slot": "order", "n": 1}
    assert lines[3]["reask"] == {"slot": "order", "n": 2}
    assert "handoff" not in lines[3]
    assert lines[4]["handoff"] == {"reason": "slot_failures", "slot": "order", "reasks": 3}
    assert lines[4]["end_reason"] == "handoff"
    assert said(c, 4) == GIVE_UP
    assert c.out.of("call.ended")[-1]["reason"] == "handoff"
    (order,) = c.sink.orders
    assert order.status == "handoff"


def test_rule_turns_are_marked_as_rule(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요 그게 다예요", "6시 반이요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await c.session.wait_done()
            return c

    lines = records(run(call()))
    assert [line["route"] for line in lines[1:]] == ["nlu", "rule", "nlu", "rule"]
    assert all("reask" not in line and "handoff" not in line for line in lines)


def test_not_heard_at_the_call_limit_hands_off_and_ends_the_call(tmp_path) -> None:
    stt = FakeSttStream(replies=["", ""])
    config = replace(StoreConfig.from_fixture(), max_reasks=2)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, config=config) as c:
            await c.start()
            await c.say(speech_ms=800)
            await c.out.wait_for("bot.done", turn=1)
            await c.played(1)
            await c.say(speech_ms=800)
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            await c.session.wait_done()
            return c

    c = run(call())
    assert said(c, 2) == GIVE_UP
    assert c.out.of("call.ended")[-1]["reason"] == "handoff"
    lines = records(c)
    assert lines[1]["reask"] == {"slot": "not_heard", "n": 1}
    assert lines[2]["handoff"] == {"reason": "call_failures", "slot": "not_heard", "reasks": 2}
    assert lines[2]["end_reason"] == "handoff"
    (order,) = c.sink.orders
    assert order.status == "handoff"
