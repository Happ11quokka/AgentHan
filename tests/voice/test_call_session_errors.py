"""통화 세션 오류 (spec Testing Decisions).

어댑터·엔진이 실패하면 `error` 이벤트를 내고 통화를 끝낸다.
"""

from __future__ import annotations

import asyncio

from call_harness import Call
from test_call_session import GatedNlu, settle

from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import PortError
from agenthan.voice.call.session import SessionSettings


def run(coro):
    return asyncio.run(coro)


async def _start_and_wait_error(c: Call) -> dict:
    await c.session.feed_event({"type": "call.start", "turn": 0})
    error = await c.out.wait_for("error")
    await asyncio.wait_for(c.session.wait_done(), 3)
    return error


def test_stt_connect_failure_sends_error_without_call_ready(tmp_path) -> None:
    stt = FakeSttStream(fail_start=PortError("인증 실패", code="stt_auth", retry=False))

    async def call() -> tuple[Call, dict]:
        async with Call(tmp_path, stt=stt) as c:
            return c, await _start_and_wait_error(c)

    c, error = run(call())
    assert error == {"type": "error", "turn": 0, "code": "stt_auth", "message": "인증 실패",
                     "retry": False}
    assert c.out.of("call.ready") == []


def test_stt_stream_break_mid_call_sends_error(tmp_path) -> None:
    stt = FakeSttStream()

    async def call() -> dict:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            stt.fail(PortError("연결이 끊겼어요", code="stt_disconnected"))
            error = await c.out.wait_for("error")
            await asyncio.wait_for(c.session.wait_done(), 3)
            return error

    error = run(call())
    assert (error["code"], error["retry"]) == ("stt_disconnected", True)


def test_tts_failure_sends_error(tmp_path) -> None:
    tts = FakeTts(failures={"주문 도와드릴게요.": PortError("요청이 많아요", code="tts_429")})

    async def call() -> dict:
        async with Call(tmp_path, tts=tts) as c:
            return await _start_and_wait_error(c)

    error = run(call())
    assert (error["turn"], error["code"]) == (0, "tts_429")


def test_unexpected_tts_exception_is_reported_without_its_message(tmp_path) -> None:
    tts = FakeTts(failures={"주문 도와드릴게요.": RuntimeError("secret-key-123")})

    async def call() -> dict:
        async with Call(tmp_path, tts=tts) as c:
            return await _start_and_wait_error(c)

    error = run(call())
    assert error["code"] == "tts"
    assert "secret" not in error["message"]


def test_hcx_without_key_sends_config_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # .env 없음

    async def call() -> dict:
        async with Call(tmp_path, nlu="hcx") as c:
            return await _start_and_wait_error(c)

    error = run(call())
    assert (error["code"], error["retry"]) == ("nlu_config", False)


def test_engine_timeout_ends_call_and_never_starts_the_next_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    nlu = GatedNlu("메도빅 하나요")

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu,
                        settings=SessionSettings(turn_timeout_s=0.2)) as c:
            await c.start()
            await c.say()
            error = await c.out.wait_for("error")
            assert (error["turn"], error["code"]) == (1, "engine_timeout")
            await c.say()  # 끝난 통화: 더 듣지 않는다
            await settle()
            nlu.release.set()  # 붙잡힌 엔진 스레드를 풀어 준다 (테스트 정리)
            await settle()
            return c

    c = run(call())
    assert [e["text"] for e in c.out.of("stt.final")] == ["메도빅 하나요"]
    assert c.out.of("bot.say", 1) == []
    assert c.session.done


def test_outbound_failure_ends_the_call(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path) as c:

            async def broken(event):
                raise ConnectionError("socket closed")

            c.out.send_event = broken  # type: ignore[method-assign]
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    assert run(call()).session.done
