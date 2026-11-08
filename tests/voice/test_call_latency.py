"""지연 개선 묶음 (3단계 티켓 01): 첫 소리 전 말, 조기 epFlag, TTS 앞 무음, 생각 중 효과음, 지표.

통화 세션의 바깥 행동만 본다. 가짜 STT·TTS·시계와 합성 PCM으로 결정적으로 만든다.
"""

from __future__ import annotations

import asyncio

from call_harness import Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream, FakeTts

GREETING = "네, 샘플 러시안 베이커리입니다."


def run(coro):
    return asyncio.run(coro)


def slow_after_greeting(seconds: float) -> FakeTts:
    """인사 문장은 바로, 그 뒤 문장은 `seconds` 걸려 합성하는 TTS."""
    return FakeTts(delays=lambda text: 0.0 if text in (GREETING, "주문 도와드릴게요.") else seconds)


def test_speech_after_the_engine_but_before_the_first_sound_becomes_the_next_turn(
    tmp_path,
) -> None:
    # 2단계 버그: 엔진이 끝나면 TTS 합성 전에 "봇 말하는 중"이 돼서, 첫 소리 전에 한 말이 버려졌다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    tts = slow_after_greeting(0.8)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await asyncio.sleep(0.05)  # 엔진이 끝나고 TTS가 합성하는 중
            assert not c.out.of("bot.say", 1)
            await c.say()  # 첫 소리 전에 손님이 또 말한다. 그 결과도 첫 소리 전에 온다
            await c.out.wait_for("bot.done", turn=1)
            await c.played(1)
            await c.out.wait_for("stt.final", turn=2)
            return c

    c = run(call())
    assert [(e["turn"], e["text"]) for e in c.out.of("stt.final")] == [
        (1, "메도빅 하나요"), (2, "그게 다예요")
    ]


def test_speech_before_the_first_sound_whose_result_comes_after_it_is_kept(tmp_path) -> None:
    # 첫 소리 전에 말을 마쳤는데 STT 결과는 봇 소리가 나간 뒤에 왔다. 말한 시각으로 본다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"], lag_ms=1_500)
    tts = slow_after_greeting(0.3)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.feed(tone(800) + silence(2_000))
            await c.out.wait_for("stt.final", turn=1)
            await c.feed(tone(800) + silence(400))  # 첫 소리 전에 말하고 끝냈다
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(silence(1_500))  # 봇이 말하는 동안 0 프레임. 이때 결과가 온다
            await c.played(1)
            await c.out.wait_for("stt.final", turn=2)
            return c

    c = run(call())
    assert [(e["turn"], e["text"]) for e in c.out.of("stt.final")] == [
        (1, "메도빅 하나요"), (2, "그게 다예요")
    ]


def test_the_phase_stays_thinking_while_the_first_sentence_is_synthesized(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])
    tts = slow_after_greeting(0.4)

    async def call() -> tuple[list[str], list[str]]:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await asyncio.sleep(0.2)  # 엔진은 끝났고 첫 문장을 합성하는 중
            during = [e["phase"] for e in c.out.of("state", 1)]
            await c.out.wait_for("bot.done", turn=1)
            return during, [e["phase"] for e in c.out.of("state", 1)]

    during, after = run(call())
    assert during == ["thinking"]
    assert after == ["thinking", "speaking"]
