"""통화 녹음과 봇 말 기록 (5단계 티켓 01): 세션 바깥에서 본다."""

from __future__ import annotations

import array
import asyncio
import json
import wave

from call_harness import Call, tone
from test_call_interrupt import interrupt

from agenthan.voice.call.fakes import FakeSttStream, FakeTts

ORDER_SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]


def _call(tmp_path, record_dir=None) -> Call:
    async def go() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=ORDER_SCRIPT), record_dir=record_dir,
                        tts=FakeTts(pcm=array.array("h", [3_000] * 6_400).tobytes())) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    return asyncio.run(go())


def _lines(c: Call) -> list[dict]:
    return [json.loads(s) for s in (c.log_dir / "phone-20261108.jsonl").read_text().splitlines()]


def _stereo(path) -> tuple[array.array, array.array]:
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getframerate()) == (2, 16_000)
        data = array.array("h", w.readframes(w.getnframes()))
    return data[0::2], data[1::2]


def test_recording_has_customer_left_and_bot_right_on_the_marks_timeline(tmp_path) -> None:
    c = _call(tmp_path, record_dir=tmp_path / "rec")
    lines = _lines(c)
    rel = lines[0]["recording"]
    assert rel == "20261108/test-call.wav"
    left, right = _stereo(tmp_path / "rec" / rel)
    assert max(left) > 1_000, "손님 말소리(tone)가 왼쪽에 있어야 한다"
    assert max(right) == 3_000, "봇 목소리가 오른쪽에 있어야 한다"
    # 턴 1 봇 첫 소리를 보낸 때(audio_first) 오른쪽에 봇 소리가 있다
    first = lines[1]["marks"]["audio_first"]
    assert right[first * 16 + 160] == 3_000
    # 넣은 손님 말소리(800ms × 4턴)가 빠지거나 겹치지 않고 모두 있다. 테스트는 프레임을 한꺼번에
    # 넣어 손님 쪽 위치는 실제 시각과 다르다(실제로는 프레임이 실시간으로 온다)
    fed = array.array("h", b"".join(tone(800)))
    loud = sum(1 for x in left if abs(x) > 1_000)
    assert loud == 4 * sum(1 for x in fed if abs(x) > 1_000)


def test_no_record_dir_means_no_recording_field_and_no_file(tmp_path) -> None:
    c = _call(tmp_path)
    assert "recording" not in _lines(c)[0]
    assert not list(tmp_path.rglob("*.wav"))


def test_every_turn_line_keeps_what_the_bot_said(tmp_path) -> None:
    c = _call(tmp_path)
    lines = _lines(c)
    said = [e["text"] for e in c.out.of("bot.say")]
    assert [s for r in lines for s in r.get("bot", [])] == said
    assert lines[0]["bot"][0] == "네, 샘플 러시안 베이커리 AI 전화 주문입니다."
    assert "메도빅 하나, 6시 반에 찾으러 오시고, 모두 7,500원이에요." in lines[3]["bot"]


def test_interrupted_bot_audio_after_the_cut_is_not_in_the_recording(tmp_path) -> None:
    long_pcm = array.array("h", [3_000] * 16_000 * 3).tobytes()  # 문장마다 3초

    async def go() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=ORDER_SCRIPT), tts=FakeTts(long_pcm),
                        record_dir=tmp_path / "rec") as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await interrupt(c, 1)
            await c.out.wait_for("state", turn=1)
            return c

    c = asyncio.run(go())
    lines = _lines(c)
    start = lines[1]["marks"]["audio_first"]
    _, right = _stereo(tmp_path / "rec" / lines[0]["recording"])
    sent = sum(len(f.pcm) for f in c.out.frames if f.turn == 1) // 32
    assert sent >= 3_000, "끊기 전에 턴 1 소리를 여러 문장 보냈다"
    assert len(right) < (start + sent) * 16, "끊은 뒤의 봇 소리는 지운다"


def test_numbers_are_spoken_as_words_but_captions_keep_digits(tmp_path) -> None:
    """5단계 티켓 10: TTS에는 말로("여섯 시 반", "칠천오백 원"), 자막(`bot.say`)은 숫자 그대로."""
    tts = FakeTts()
    stt = FakeSttStream(replies=ORDER_SCRIPT)

    async def go() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    c = asyncio.run(go())
    said = " ".join(e["text"] for e in c.out.of("bot.say"))
    assert "6시 반" in said and "7,500원" in said
    spoken = " ".join(tts.calls)
    assert "여섯 시 반" in spoken and "칠천오백 원" in spoken
    assert not any(ch.isdigit() for ch in spoken)
