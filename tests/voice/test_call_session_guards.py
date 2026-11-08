"""통화 세션 안전망 (리뷰 4·10·11·15·17번).

클라이언트 보고가 안 오거나 틀려도, 어댑터가 실패해도 통화가 멈춰 있지 않고 손님에게는
내부 문구가 나가지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from call_harness import Call

from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import PortError
from agenthan.voice.call.session import CUSTOMER_RETRY, SessionSettings

GREETING = ("네, 샘플 러시안 베이커리입니다.", "주문 도와드릴게요.")


def run(coro):
    return asyncio.run(coro)


def _lines(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


async def _phase(c: Call, turn: int, phase: str) -> None:
    async def seen() -> None:
        while not any(e["phase"] == phase for e in c.out.of("state", turn)):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(seen(), 3)


# ---------------------------------------------------------------- 4: playback.done이 안 온다


def test_half_duplex_is_released_by_the_server_if_playback_done_never_comes(tmp_path) -> None:
    tts = FakeTts(bytes(3_200))  # 문장마다 100ms
    settings = SessionSettings(play_done_grace_s=0.1)

    async def call() -> Call:
        async with Call(tmp_path, tts=tts, settings=settings) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("bot.done", turn=0)
            await _phase(c, 0, "listening")  # 소리 200ms + 0.1초 뒤 서버가 스스로 푼다
            return c

    c = run(call())
    assert _lines(c)[0]["play_done_timeout"] is True


# ---------------------------------------------------------------- 11: seq 없는 playback.done


def test_playback_done_without_seq_waits_until_every_sentence_was_sent(tmp_path) -> None:
    tts = FakeTts(delays={GREETING[1]: 0.3})

    async def call() -> list[str]:
        async with Call(tmp_path, tts=tts) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("bot.say", turn=0)
            await c.session.feed_event({"type": "playback.done", "turn": 0})  # 둘째 문장 전
            await c.out.wait_for("bot.done", turn=0)
            await asyncio.sleep(0.05)
            before = [e["phase"] for e in c.out.of("state", 0)]
            await c.session.feed_event({"type": "playback.done", "turn": 0})
            await _phase(c, 0, "listening")
            return before

    assert "listening" not in run(call())


# ---------------------------------------------------------------- 10: 틀린 JSON 값


def test_events_with_wrong_value_types_are_ignored_and_the_call_goes_on(tmp_path) -> None:
    async def call() -> None:
        async with Call(tmp_path) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("bot.done", turn=0)
            for bad in (
                {"type": "playback.done", "turn": 0, "seq": "1"},
                {"type": "playback.done", "turn": [0], "seq": 1},
                {"type": "client.metrics", "turn": [0]},
                {"type": "playback.started", "turn": {"a": 1}},
                {"type": "playback.done", "turn": True, "seq": 1},
            ):
                await c.session.feed_event(bad)
            assert not any(e["phase"] == "listening" for e in c.out.of("state", 0))
            await c.played(0)
            await _phase(c, 0, "listening")

    run(call())


# ---------------------------------------------------------------- 15: 손님에게 나가는 문구


def test_the_guest_gets_a_fixed_message_and_the_detail_stays_in_the_log(tmp_path) -> None:
    stt = FakeSttStream()

    async def call() -> tuple[dict, Call]:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            stt.fail(PortError("CLOVA Speech 연결이 끊겼어요 (UNAVAILABLE: upstream reset)",
                               code="stt_disconnected"))
            error = await c.out.wait_for("error")
            return error, c

    error, c = run(call())
    assert error == {"type": "error", "turn": 0, "code": "stt_disconnected",
                     "message": CUSTOMER_RETRY, "retry": True}
    line = _lines(c)[-1]
    assert line["error"] == "stt_disconnected"
    assert "UNAVAILABLE" in line["error_detail"]


def test_engine_timeout_tells_the_guest_to_call_again(tmp_path) -> None:
    from test_call_session import GatedNlu

    nlu = GatedNlu("메도빅 하나요")
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> dict:
        async with Call(tmp_path, stt=stt, nlu=nlu,
                        settings=SessionSettings(turn_timeout_s=0.1)) as c:
            await c.start()
            await c.say()
            error = await c.out.wait_for("error")
            nlu.release.set()
            return error

    error = run(call())
    assert (error["code"], error["message"], error["retry"]) == (
        "engine_timeout", CUSTOMER_RETRY, True
    )
    assert SessionSettings().turn_timeout_s <= 20


# ---------------------------------------------------------------- 17: 통화 한도


def test_a_socket_that_never_sends_call_start_is_closed(tmp_path) -> None:
    async def call() -> dict:
        async with Call(tmp_path, settings=SessionSettings(start_timeout_s=0.1)) as c:
            await asyncio.wait_for(c.session.wait_done(), 2)
            return await c.out.wait_for("error")

    assert run(call())["code"] == "start_timeout"


def test_a_call_longer_than_the_limit_ends_with_an_error(tmp_path) -> None:
    async def call() -> dict:
        async with Call(tmp_path, settings=SessionSettings(max_call_s=0.3)) as c:
            await c.start()
            await asyncio.wait_for(c.session.wait_done(), 2)
            return await c.out.wait_for("error")

    error = run(call())
    assert (error["code"], error["retry"]) == ("call_too_long", True)


# ---------------------------------------------------------------- 17·09: 엔진 전용 스레드 풀


class _ThreadNames:
    """엔진이 부르는 동기 코드(가게 지식·NLU)가 어느 스레드에서 돌았는지 적는다."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def mark(self) -> None:
        self.names.append(threading.current_thread().name)


def test_engine_work_runs_on_the_executor_the_caller_gives(tmp_path) -> None:
    from agenthan.core.fakes import FakeStoreKnowledge
    from agenthan.voice.nlu.fake import FakeNlu

    seen = _ThreadNames()

    class Store(FakeStoreKnowledge):
        def list_menu(self):
            seen.mark()  # 엔진 생성
            return super().list_menu()

    class Nlu(FakeNlu):
        def parse(self, text, ctx):
            seen.mark()  # 손님 말 한 턴
            return super().parse(text, ctx)

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-engine")

    async def call() -> None:
        stt = FakeSttStream(replies=["메도빅 하나요"])
        async with Call(tmp_path, stt=stt, nlu=Nlu(), store_factory=Store.from_fixture,
                        executor=pool) as c:
            await c.start()
            await c.turn(1)

    try:
        run(call())
    finally:
        pool.shutdown()
    assert len(seen.names) >= 2
    assert all(name.startswith("test-engine") for name in seen.names), seen.names
