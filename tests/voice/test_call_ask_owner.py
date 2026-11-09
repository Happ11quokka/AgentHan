"""통화 세션 → 사장님께 묻기 (4단계 티켓 05, spec "ASK_OWNER: 세션", 이음매 2).

엔진 턴 결과에 질문이 실리면 세션이 `OwnerChannel.open_question`으로 연다(바로 돌아옴). 사장님 답은
세션이 준 받는 곳(`reply`)으로 들어오고, 세션은 듣는 중이 되면 봇이 먼저 말하는 턴(`bot_turn`)으로
엔진에 넣는다. 가짜 사장님은 질문을 받자마자 답한다.
"""

from __future__ import annotations

import asyncio

from call_harness import WAIT, Call

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.dialog.owner_question import OwnerQuestion

NUTS = "메도빅에 견과류 들어가요?"
HOLD_ON = "잠시만요, 사장님께 여쭤볼게요."


def run(coro):
    return asyncio.run(coro)


def said(c: Call, turn: int) -> str:
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


async def bot_turn(c: Call, turn: int) -> None:
    await c.out.wait_for("bot.done", turn=turn)
    await c.played(turn)


def test_an_owner_who_answers_at_once_is_heard_after_the_hold_line(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS, "그게 다예요", "6시 반이요", "네"])
    owner = RecordingOwnerChannel(answer="yes")

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner) as c:
            await c.start()
            await c.turn(1)
            await c.turn(2)  # "잠시만요" 재생이 끝나면 사장님 답을 말하는 봇 턴이 열린다
            await bot_turn(c, 3)
            for turn in (4, 5, 6):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 2) == HOLD_ON
    assert said(c, 3) == "사장님께 여쭤보니 네, 된다고 하세요. 더 필요하신 거 있으세요?"
    assert owner.questions == [
        ("test-call", OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅", text=NUTS)),
    ]
    assert len(c.sink.orders) == 1  # 답 뒤에도 주문은 평소처럼 확정된다
    assert owner.ended == ["confirmed"]
