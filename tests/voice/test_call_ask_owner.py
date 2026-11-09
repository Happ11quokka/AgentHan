"""통화 세션 → 사장님께 묻기 (4단계 티켓 05, spec "ASK_OWNER: 세션", 이음매 2).

엔진 턴 결과에 질문이 실리면 세션이 `OwnerChannel.open_question`으로 연다(바로 돌아옴). 사장님 답은
세션이 준 받는 곳(`reply`)으로 들어오고, 세션은 듣는 중이 되면 봇이 먼저 말하는 턴(`bot_turn`)으로
엔진에 넣는다. 가짜 사장님은 질문을 받자마자 답한다.

티켓 06: 답 대기 시간 초과, 답·열람을 말하는 조건(무응답 타이머와 같음)과 미루기, 기다리는 동안
무응답 타이머 멈춤, 대기 중 끊음·취소, 낡은 답 버리기.
"""

from __future__ import annotations

import asyncio
import json
import time

from call_harness import WAIT, Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream, RecordingOwnerChannel
from agenthan.voice.call.session import SessionSettings
from agenthan.voice.dialog.owner_question import OwnerQuestion, OwnerReply

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


def test_a_written_answer_is_promised_by_text_and_the_end_of_call_carries_the_texts(
        tmp_path) -> None:
    """티켓 07: 자유 입력 답은 읽지 않고 문자 약속. `call.ended`에 그때까지의 손님 문자."""
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS, "그게 다예요", "6시 반이요", "네"])
    texts = [{"id": "test-call-s1", "kind": "free_answer", "text": "메도빅 문의 답변이에요."}]
    owner = RecordingOwnerChannel(answer="free", texts=texts)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner) as c:
            await c.start()
            await c.turn(1)
            await c.turn(2)
            await bot_turn(c, 3)
            for turn in (4, 5, 6):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            return c

    c = run(call())
    assert said(c, 3) == "사장님이 답을 남기셨어요. 문자로 보내 드릴게요. 더 필요하신 거 있으세요?"
    assert said(c, 6) == "6시 반에 준비해 둘게요. 감사합니다."
    assert c.out.of("call.ended") == [
        {"type": "call.ended", "turn": 6, "reason": "confirmed", "sms": texts}]


# ---------------------------------------------------------------- 시간·끼어들기 (티켓 06)
# 가짜 사장님은 답하지 않고, 시험이 나중에 `owner.answer`·`owner.see`로 늦은 답·열람을 흉내 낸다.

QUIET = 0.3  # 시험용 무응답 대기. 사장님 대기보다 짧아서 꺼지지 않았으면 "여보세요?"가 먼저 나간다
OWNER_WAIT = 0.9  # 시험용 사장님 답 대기 (기본 20초)
LATER = "확인해서 문자로 알려 드릴게요."
SEEN = "사장님이 보고 계세요, 조금만요."
YES = "사장님께 여쭤보니 네, 된다고 하세요."


def waiting(**kw) -> SessionSettings:
    return SessionSettings(**{"no_input_s": QUIET, "owner_wait_s": OWNER_WAIT, **kw})


def records(c: Call) -> dict[int, dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return {line["turn"]: line for line in lines}


async def asked(c: Call) -> float:
    """인사 → 메도빅 하나 → 근거 없는 질문("잠시만요" 재생 끝까지). 질문을 연 때를 돌려준다."""
    await c.start()
    await c.turn(1)
    await c.say()
    await c.out.wait_for("bot.say", turn=2)
    opened = time.monotonic()
    await c.out.wait_for("bot.done", turn=2)
    await c.played(2)
    return opened


def test_no_answer_times_out_with_a_text_promise_and_no_hello_while_waiting(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS])
    owner = RecordingOwnerChannel()

    async def call() -> tuple[Call, float, float]:
        async with Call(tmp_path, stt=stt, owner=owner, settings=waiting()) as c:
            opened = await asked(c)
            await c.out.wait_for("bot.say", turn=3)
            spoke = time.monotonic()
            await bot_turn(c, 3)
            await bot_turn(c, 4)  # 질문이 닫혔으니 무응답 타이머가 다시 돈다
            owner.answer(1, "yes")  # 시간 초과 뒤의 늦은 답: 통화 중에는 말하지 않는다
            await asyncio.sleep(QUIET / 2)
            return c, opened, spoke

    c, opened, spoke = run(call())
    assert spoke - opened >= OWNER_WAIT - 0.05
    assert said(c, 3) == f"{LATER} 더 필요하신 거 있으세요?"
    assert said(c, 4) == "여보세요? 더 필요하신 거 있으세요?"
    assert c.out.of("bot.say", 5) == []
    assert owner.timeouts == [1]
    turns = records(c)
    assert turns[2]["owner_question"]["number"] == 1
    assert turns[3]["bot_turn"] == "owner_answer"
    assert turns[3]["owner_answer"]["answer"] == "timeout"
    assert turns[3]["owner_answer"]["wait_ms"] >= OWNER_WAIT * 1000 - 50


def test_the_owner_seeing_the_card_is_said_once_then_the_answer(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS])
    owner = RecordingOwnerChannel()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner, settings=waiting(owner_wait_s=5.0)) as c:
            await asked(c)
            owner.see(1)
            owner.see(1)  # 두 번 봐도 한 번만
            await bot_turn(c, 3)
            await asyncio.sleep(QUIET * 1.5)  # 아직 기다리는 중: "여보세요?" 없음
            owner.answer(1, "yes")
            await bot_turn(c, 4)
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert said(c, 3) == SEEN
    assert said(c, 4) == f"{YES} 더 필요하신 거 있으세요?"
    assert c.out.of("bot.say", 5) == []
    turns = records(c)
    assert turns[3]["bot_turn"] == "owner_seen"
    assert turns[4]["owner_answer"]["seen_ms"] is not None


def test_an_answer_while_the_customer_is_speaking_waits_for_that_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS, "블린 하나요"])
    owner = RecordingOwnerChannel()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner, settings=waiting(owner_wait_s=5.0)) as c:
            await asked(c)
            await c.feed(tone(500))  # 손님이 말하기 시작했다(VAD 말 구간, 아직 글 없음)
            owner.answer(1, "yes")
            await asyncio.sleep(0.3)
            assert c.out.of("bot.say", 3) == []  # 손님 말을 자르지 않는다
            await c.turn(3)  # 그 말이 턴이 된다
            await bot_turn(c, 4)  # 그 턴 뒤에 사장님 답
            return c

    c = run(call())
    assert said(c, 3).startswith("블린")
    assert said(c, 4).startswith(YES)
    turns = records(c)
    assert turns[3].get("bot_turn") is None
    assert turns[4]["owner_answer"]["deferred_ms"] >= 300


def test_an_answer_while_waiting_for_the_end_of_turn_text_waits_for_that_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS, "블린 하나요"])
    owner = RecordingOwnerChannel()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner, settings=waiting(owner_wait_s=5.0)) as c:
            await asked(c)
            # 말 끝(무음 400ms)은 지났고 epFlag 응답(글)을 기다리는 중
            await c.feed(tone(800) + silence(500))
            owner.answer(1, "yes")
            await asyncio.sleep(0.3)
            assert c.out.of("bot.say", 3) == []
            await c.feed(silence(700))
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await bot_turn(c, 4)
            return c

    c = run(call())
    assert said(c, 3).startswith("블린")
    assert said(c, 4).startswith(YES)


def test_hanging_up_while_waiting_ends_once_and_drops_the_late_answer(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS])
    owner = RecordingOwnerChannel()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner, settings=waiting()) as c:
            await asked(c)
            await c.session.feed_event({"type": "call.end", "turn": 2})
            owner.answer(1, "yes")
            await asyncio.sleep(OWNER_WAIT + 0.2)  # 시간 초과 타이머도 꺼졌다
            return c

    c = run(call())
    assert owner.ended == ["hangup"]
    assert owner.timeouts == []
    assert c.out.of("bot.say", 3) == []


def test_cancelling_while_waiting_ends_the_call_and_drops_the_late_answer(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS, "취소할게요", "네"])
    owner = RecordingOwnerChannel()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner, settings=waiting(owner_wait_s=5.0)) as c:
            await asked(c)
            await c.turn(3)
            await c.turn(4)
            await asyncio.wait_for(c.session.wait_done(), WAIT)
            owner.answer(1, "yes")
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert said(c, 3) == "주문 취소할까요?"
    assert owner.ended == ["cancelled"]
    assert c.out.of("bot.say", 5) == []


def test_a_stale_answer_for_another_question_is_dropped(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS])
    owner = RecordingOwnerChannel()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, owner=owner, settings=waiting(owner_wait_s=5.0)) as c:
            await asked(c)
            owner._replies[1](OwnerReply(2, "yes"))  # 이 통화에 없는 질문 번호
            await asyncio.sleep(QUIET * 1.5)
            owner.answer(1, "no")
            await bot_turn(c, 3)
            return c

    c = run(call())
    assert said(c, 3).startswith("사장님께 여쭤보니 안 된다고 하세요.")
    assert c.out.of("bot.say", 4) == []
