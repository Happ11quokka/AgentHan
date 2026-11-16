"""통화 세션 → 사장님 보충 지식 (4단계 티켓 13).

세션은 엔진에 보충 지식 읽기 포트를 넣고(사장님 쪽이 붙은 통화만), 턴 결과의 보충 요청을
`OwnerChannel.knowledge_wanted`로 넘긴다.
"""

from __future__ import annotations

import asyncio
import json

from call_harness import WAIT, Call

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.dialog.knowledge import KnowledgeKey, KnowledgeRequest, KnowledgeStore

TASTE = "아메리카노 어떤 맛이에요?"
PASS_ON = "죄송해요, 그건 제가 정확히 모르겠어요. 사장님께 전해 둘게요."


def said(c: Call, turn: int) -> str:
    return " ".join(e["text"] for e in c.out.of("bot.say", turn))


def records(c: Call) -> dict[int, dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return {line["turn"]: line for line in lines}


async def _ask_and_hang_up(c: Call) -> None:
    await c.start()
    await c.turn(1)
    await c.session.feed_event({"type": "call.end", "turn": 1})
    await asyncio.wait_for(c.session.wait_done(), WAIT)


def test_an_unknown_menu_question_is_passed_on_to_the_owner_side(tmp_path) -> None:
    owner = RecordingOwnerChannel()

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=[TASTE]), owner=owner,
                        knowledge=KnowledgeStore()) as c:
            await _ask_and_hang_up(c)
            return c

    c = asyncio.run(call())
    assert said(c, 1) == f"{PASS_ON} 어떤 걸로 드릴까요?"
    request = KnowledgeRequest(KnowledgeKey("m03", "taste"), menu_name="아메리카노", text=TASTE)
    assert owner.knowledge == [("test-call", request)]
    assert records(c)[1]["knowledge_requests"] == [
        {"menu_id": "m03", "kind": "taste", "subject": ""}]


def test_a_saved_note_is_heard_in_the_next_call(tmp_path) -> None:
    store = KnowledgeStore()
    store.save_note(KnowledgeKey("m03", "taste"), "고소하고 쓴맛이 적어요.")

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=[TASTE]),
                        owner=RecordingOwnerChannel(), knowledge=store) as c:
            await _ask_and_hang_up(c)
            return c

    c = asyncio.run(call())
    assert said(c, 1) == "사장님 말씀으로는 고소하고 쓴맛이 적어요. 어떤 걸로 드릴까요?"
    assert c.owner.knowledge == []


def test_without_an_owner_side_the_knowledge_loop_stays_off(tmp_path) -> None:
    """사장님 쪽이 없는 통화(1~3단계·CLI)는 "사장님께 전해 둘게요"라고 하지 않는다."""

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=[TASTE]), owner=None,
                        knowledge=KnowledgeStore(), no_owner=True) as c:
            await _ask_and_hang_up(c)
            return c

    c = asyncio.run(call())
    assert said(c, 1) == "그건 제가 정확히 모르겠어요. 어떤 걸로 드릴까요?"
