"""통화 세션 → 재료 소진 거절과 오늘 품절 (주문 승인 티켓 06, spec "세션", 이음매 2).

세션은 통화를 시작할 때(엔진 생성)와 사장님 결정을 말하기 직전에 사장님 쪽의 오늘 품절 목록
(`OwnerChannel.sold_out_today`)을 엔진 메뉴에 덮어 쓴다. 재료 소진 거절이면 손님은 품절 안내와
"빼고 주문할까요, 다른 메뉴로 바꾸실까요?"를 듣고, 빼기만 하면 재승인 없이 확정으로 끝난다
(손님 화면 상태는 rejected → accepted).
"""

from __future__ import annotations

import asyncio

from call_harness import WAIT, Call
from test_call_approval import CLOSING, HOLD, approval_steps, bot_turn, run, said, view, waiting

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel

TWO = ["메도빅 하나랑 나폴레옹 하나요", "그게 다예요", "6시 반이요", "네"]
NOTICE = "메도빅은 지금 재료가 떨어졌대요. 빼고 주문할까요, 다른 메뉴로 바꾸실까요?"


async def to_yes(c: Call) -> None:
    """인사 → 두 메뉴 → 그게 다예요 → 6시 반 → "네"("확인 중" 재생 끝까지)."""
    await c.start()
    for turn in (1, 2, 3):
        await c.turn(turn)
    await c.say()
    await bot_turn(c, 4)


def test_dropping_the_sold_out_menu_confirms_the_rest_without_a_second_request(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=[*TWO, "빼 주세요"]), owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            owner.reject(1, "out_of_stock", menu_ids=("m01",))
            await bot_turn(c, 5)
            await c.turn(6)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 4) == HOLD
    assert said(c, 5) == NOTICE
    assert said(c, 6) == CLOSING
    assert len(owner.approvals) == 1  # 재승인 없음
    (order,) = c.sink.orders
    assert order.status == "confirmed"
    assert [(line.menu_id, line.quantity) for line in order.lines] == [("m02", 1)]
    assert owner.ended == ["confirmed"]
    steps = [step for _, step in approval_steps(c)]
    assert steps[-2:] == [view("rejected", 1, reason="out_of_stock"), view("accepted", 1)]


def test_the_sold_out_list_is_applied_when_the_call_starts(tmp_path) -> None:
    owner = RecordingOwnerChannel(sold_out=("m01",))

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=["메도빅 하나요"]),
                        owner=owner) as c:
            await c.start()
            await c.turn(1)
            return c

    c = run(call())
    assert said(c, 1).startswith("죄송해요, 메도빅은 오늘 다 떨어졌어요.")


def test_the_sold_out_list_is_applied_again_when_the_decision_arrives(tmp_path) -> None:
    owner = RecordingOwnerChannel(approval="hold")
    script = [*TWO, "블린으로 바꿔 주세요"]

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=script), owner=owner,
                        settings=waiting(approval_wait_s=5.0)) as c:
            await to_yes(c)
            owner.sold_out.add("m04")  # 통화 중에 다른 카드에서 블린을 품절로 걸었다
            owner.reject(1, "out_of_stock", menu_ids=("m01",))
            await bot_turn(c, 5)
            await c.turn(6)
            return c

    c = run(call())
    assert said(c, 5) == NOTICE
    assert said(c, 6).startswith("죄송해요, 블린은 오늘 다 떨어졌어요.")
