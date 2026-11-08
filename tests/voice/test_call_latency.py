"""지연 개선 묶음 (3단계 티켓 01): 첫 소리 전 말, 조기 epFlag, TTS 앞 무음, 생각 중 효과음, 지표.

통화 세션의 바깥 행동만 본다. 가짜 STT·TTS·시계와 합성 PCM으로 결정적으로 만든다.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct

from call_harness import Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import SttResult
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


# ------------------------------------------------------ 생각 중 효과음 (bot.thinking)


def _turn_items(c: Call, turn: int) -> list[str]:
    return [m["type"] if isinstance(m, dict) else "audio" for m in c.out.sent
            if (m["turn"] if isinstance(m, dict) else m.turn) == turn]


def test_a_slow_answer_sends_bot_thinking_once_at_speech_end_plus_the_delay(tmp_path) -> None:
    # 말 끝 + `thinking_after_s`까지 봇 목소리가 없으면 효과음 신호를 한 번 보낸다
    stt = FakeSttStream(replies=["메도빅 하나요"])
    settings = SessionSettings(thinking_after_s=0.3)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow_after_greeting(0.8),
                        settings=settings) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await c.played(1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
        return c

    c = run(call())
    items = _turn_items(c, 1)
    assert items.count("bot.thinking") == 1
    assert items.index("bot.thinking") < items.index("bot.say") < items.index("audio")
    (metrics,) = c.out.of("metrics", 1)
    marks = metrics["marks"]
    assert 300 <= marks["thinking"] - marks["speech_end"] < 300 + 250
    assert marks["thinking"] < marks["audio_first"]


def test_a_quick_answer_sends_no_bot_thinking(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.turn(1)
            await asyncio.sleep(0.05)
        return c

    c = run(call())
    assert c.out.of("bot.thinking") == []


# ------------------------------------------------------ 봇이 먼저 말하는 턴 (04·05가 쓴다)


def _greet_again(engine, now):
    return engine.start(now)


def test_a_bot_initiated_turn_gets_a_new_turn_number_and_the_next_guest_turn_follows(
    tmp_path,
) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> tuple[Call, int | None]:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            turn = c.session.bot_turn("test", _greet_again)
            await c.out.wait_for("bot.done", turn=1)
            # 지난 턴(0)의 늦은 재생 끝 보고로 듣기에 돌아가지 않는다
            await c.session.feed_event({"type": "playback.done", "turn": 0, "seq": 1})
            assert c.out.of("state")[-1]["phase"] == "speaking"
            await c.played(1)
            await c.say()
            await c.out.wait_for("bot.done", turn=2)
            await c.session.feed_event({"type": "call.end", "turn": 2})
        return c, turn

    c, turn = run(call())
    assert turn == 1
    assert [e["text"] for e in c.out.of("bot.say", 1)][0] == GREETING
    assert {f.turn for f in c.out.frames} == {0, 1, 2}
    assert [(e["turn"], e["text"]) for e in c.out.of("stt.final")] == [(2, "메도빅 하나요")]
    (metrics,) = c.out.of("metrics", 1)
    assert metrics["bot_turn"] == "test"
    assert "speech_end" not in metrics["marks"]


def test_a_bot_initiated_turn_is_refused_unless_the_call_is_listening(tmp_path) -> None:
    tts = slow_after_greeting(0.3)
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> tuple[Call, int | None]:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            refused = c.session.bot_turn("test", _greet_again)  # 손님 턴을 처리하는 중
            await c.out.wait_for("bot.done", turn=1)
        return c, refused

    c, refused = run(call())
    assert refused is None
    assert c.out.of("bot.done", 2) == []


# ------------------------------------------------------ 턴 기록 지표


def _lines(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(s) for s in path.read_text().splitlines()]


def test_each_guest_turn_records_speech_end_to_first_voice_and_thinking(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    settings = SessionSettings(thinking_after_s=0.2)
    tts = FakeTts(delays=lambda text: 0.4 if text == "몇 시쯤 오세요?" else 0.0)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts, settings=settings) as c:
            await c.start()
            await c.turn(1)
            await c.turn(2)
            await c.session.feed_event({"type": "call.end", "turn": 2})
        return c

    greeting, first, second = _lines(run(call()))
    assert "end_to_voice_ms" not in greeting
    for line in (first, second):
        marks = line["marks"]
        assert line["end_to_voice_ms"] == marks["play_start"] - marks["speech_end"]
    assert "end_to_thinking_ms" not in first
    marks = second["marks"]
    assert second["end_to_thinking_ms"] == marks["thinking"] - marks["speech_end"]


def test_a_turn_queued_during_playback_has_no_speech_end_to_voice_time(tmp_path) -> None:
    # 봇 재생 전에 끝난 말이라 "말 끝 → 첫 목소리"가 재생 시간까지 잰다. 지표에서 뺀다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    tts = slow_after_greeting(0.6)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await c.played(1)
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            await c.session.feed_event({"type": "call.end", "turn": 2})
        return c

    lines = _lines(run(call()))
    assert lines[2]["queued"] is True
    assert "end_to_voice_ms" not in lines[2]
    assert "queued" not in lines[1] and "end_to_voice_ms" in lines[1]


def test_speech_with_an_empty_ep_flag_reply_is_logged_as_an_empty_turn(tmp_path) -> None:
    # 말소리는 있었는데 STT 글이 비었다(빈 턴). 재연결 때 어댑터가 만든 빈 결과(eof)는 세지 않는다
    stt = FakeSttStream(replies=[""])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say()
            stt.push(SttResult("", 5_000, 5_000, "eof", True))
            await asyncio.sleep(0.05)
            await c.session.feed_event({"type": "call.end", "turn": 0})
        return c

    (greeting,) = _lines(run(call()))
    (empty,) = greeting["empty"]
    assert (empty["epd_type"], empty["ep_flag"]) == ("endPoint", True)
    assert empty["start_ms"] <= empty["end_ms"]
