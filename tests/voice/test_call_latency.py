"""지연 개선 묶음 (3단계 티켓 01): 첫 소리 전 말, 조기 epFlag, TTS 앞 무음, 생각 중 효과음, 지표.

통화 세션의 바깥 행동만 본다. 가짜 STT·TTS·시계와 합성 PCM으로 결정적으로 만든다.
"""

from __future__ import annotations

import asyncio
import math
import struct

from call_harness import Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.session import SessionSettings

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


# ------------------------------------------------------ 조기 epFlag · 턴 판정 (250 / 400ms)


def test_a_pause_shorter_than_the_turn_silence_merges_the_early_ep_flag_reply(tmp_path) -> None:
    # "메도빅 하나랑" … 300ms 쉼 … "나폴레옹 하나요". 쉼에서 epFlag가 먼저 나가 첫 글이 오지만,
    # 무음이 400ms가 안 됐고 말이 다시 시작됐으니 턴을 넘기지 않고 다음 글과 합친다
    stt = FakeSttStream(replies=["메도빅 하나랑", "나폴레옹 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.feed(tone(1_000) + silence(300) + tone(1_000) + silence(1_500))
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    (final,) = c.out.of("stt.final")
    assert (final["text"], final["fragments"], final["end_by"]) == (
        "메도빅 하나랑 나폴레옹 하나요", 2, "vad"
    )


def test_an_early_ep_flag_reply_waits_for_the_400ms_turn_silence(tmp_path) -> None:
    # epFlag 응답이 무음 400ms 전에 왔다. 턴은 무음 400ms가 되는 프레임에서 넘긴다(붙잡기 시간
    # 제한까지 기다리지 않는다)
    stt = FakeSttStream(replies=["메도빅 하나요"], lag_ms=0)
    settings = SessionSettings(hold_max_s=5.0)

    async def call() -> tuple[list[dict], dict]:
        async with Call(tmp_path, stt=stt, settings=settings) as c:
            await c.start()
            await c.feed(tone(800) + silence(300))
            await asyncio.sleep(0.05)
            early = c.out.of("stt.final")
            await c.feed(silence(100))
            final = await c.out.wait_for("stt.final", turn=1)
            return early, final

    early, final = run(call())
    assert early == []
    assert (final["text"], final["end_by"]) == ("메도빅 하나요", "vad")


# ------------------------------------------------------ TTS 첫 문장 앞 무음 자르기


def _pcm(*parts: tuple[int, int]) -> bytes:
    """(ms, 진폭) 구간들 → PCM16LE 16kHz. 진폭 0은 무음, 나머지는 440Hz 사인."""
    out = b""
    for ms, amplitude in parts:
        n = ms * 16
        out += struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 440 * i / 16_000))
                                       for i in range(n)))
    return out


async def _greeting_only(c: Call) -> None:
    await c.start()
    await c.session.feed_event({"type": "call.end", "turn": 0})


def test_leading_silence_of_the_first_sentence_is_cut_before_it_goes_out(tmp_path) -> None:
    # 앞 300ms: 무음 200ms + 문턱(RMS 0.01 FS ≈ 328) 아래 잡음 100ms. 그 뒤 말소리 200ms.
    # 첫 가청 소리 20ms 앞까지 자른다. 둘째 문장은 그대로
    pcm = _pcm((200, 0), (100, 200), (200, 8_000))

    async def call() -> Call:
        async with Call(tmp_path, tts=FakeTts(pcm)) as c:
            await _greeting_only(c)
        return c

    c = run(call())
    sent = {seq: sum(len(f.pcm) for f in c.out.frames if f.seq == seq) for seq in (0, 1)}
    assert sent == {0: len(pcm) - 280 * 32, 1: len(pcm)}
    first = next(f for f in c.out.frames if f.seq == 0)
    assert first.pcm == pcm[280 * 32:280 * 32 + len(first.pcm)]
    (metrics,) = c.out.of("metrics", 0)
    assert metrics["tts_trim_ms"] == 280


def test_a_sentence_that_starts_loud_is_not_cut(tmp_path) -> None:
    pcm = _pcm((300, 8_000))

    async def call() -> Call:
        async with Call(tmp_path, tts=FakeTts(pcm)) as c:
            await _greeting_only(c)
        return c

    c = run(call())
    assert sum(len(f.pcm) for f in c.out.frames if f.seq == 0) == len(pcm)
    (metrics,) = c.out.of("metrics", 0)
    assert metrics["tts_trim_ms"] == 0
