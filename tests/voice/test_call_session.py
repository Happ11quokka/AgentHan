"""통화 세션: 가짜 STT·TTS·시계로 바깥 행동만 본다 (spec Testing Decisions · 통화 세션 테스트).

들어간 오디오·이벤트에 대해 나간 이벤트·오디오·주문서·기록을 본다. 내부 필드는 보지 않는다.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

from call_harness import Call, silence, tone

from agenthan.voice.call.fakes import FakeClock, FakeSttStream, FakeTts
from agenthan.voice.call.frames import CHUNK_BYTES
from agenthan.voice.call.ports import SystemClock
from agenthan.voice.nlu.fake import FakeNlu

ORDER_SCRIPT = ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]


def run(coro):
    return asyncio.run(coro)


async def settle(seconds: float = 0.05) -> None:
    """STT 읽기 작업이 결과를 처리할 틈을 준다."""
    await asyncio.sleep(seconds)


def _full_call(tmp_path, **kw) -> Call:
    """인사 → 메도빅 하나요 → 그게 다예요 → 6시 반이요 → 네. 턴마다 `client.metrics`도 보낸다."""
    stt = FakeSttStream(replies=ORDER_SCRIPT)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, **kw) as c:
            await c.start(mic={"echoCancellation": "all", "sampleRate": 16000})
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
                await c.session.feed_event({"type": "client.metrics", "turn": turn,
                                            "first_audio_ms": 12.5})
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    return run(call())


def test_one_call_from_greeting_to_confirmed_order(tmp_path) -> None:
    c = _full_call(tmp_path)
    says = [e["text"] for e in c.out.of("bot.say")]
    assert says[0] == "네, 샘플 러시안 베이커리입니다."
    assert "메도빅 하나, 6시 반 픽업, 총 7,500원이에요." in says
    assert [e["text"] for e in c.out.of("stt.final")] == ORDER_SCRIPT
    (order_event,) = c.out.of("order")
    assert order_event["turn"] == 4
    assert order_event["order"]["status"] == "confirmed"
    assert order_event["order"]["pickup_time"] == "2026-11-08T18:30:00+09:00"
    assert order_event["total"] == 7_500
    (order,) = c.sink.orders
    assert order.status == "confirmed"
    assert [(line.menu_id, line.quantity) for line in order.lines] == [("m01", 1)]
    assert c.out.frames, "봇 음성 프레임이 나가야 한다"


def test_half_duplex_drops_results_while_bot_speaks_and_resumes_after_playback(tmp_path) -> None:
    stt = FakeSttStream(replies=["봇 목소리 메아리", "메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("bot.done", turn=0)
            await c.say()  # 인사를 재생하는 중 (playback.done 전)
            await settle()
            assert c.out.of("stt.final") == []
            await c.played(0)
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert [(e["turn"], e["text"]) for e in c.out.of("stt.final")] == [(1, "메도빅 하나요")]
    phases = [(e["turn"], e["phase"]) for e in c.out.of("state")]
    assert phases.index((0, "listening")) > phases.index((0, "speaking"))


def test_silence_after_speech_sends_ep_flag_on_that_frame(tmp_path) -> None:
    stt = FakeSttStream()

    async def call() -> None:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            before = stt.audio_ms
            await c.feed(tone(800) + silence(1_000))
            eps = [w for w in stt.writes if w.ep_flag]
            # 말소리 0~800ms, 무음 250ms(기본 `ep_silence_ms`)를 넘기는 프레임(1,000~1,100ms)에
            # epFlag 하나. 턴 판정(무음 400ms)을 기다리지 않는다 (3단계 티켓 01)
            assert [(w.at_ms - before, w.seq_id) for w in eps] == [(1_000, 1)]
            await c.feed(silence(1_000) + tone(100) + silence(1_000))  # 짧은 소리는 말이 아니다
            assert len([w for w in stt.writes if w.ep_flag]) == 1

    run(call())


def test_end_of_speech_button_sends_ep_flag_on_next_frame(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.feed(tone(800))
            await c.session.feed_event({"type": "user.end_of_speech", "turn": 0})
            before = len(stt.writes)
            await c.feed(silence(1_000))
            assert [w.ep_flag for w in stt.writes[before:]] == [True] + [False] * 9
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    (final,) = c.out.of("stt.final")
    assert final["text"] == "메도빅 하나요" and final["end_by"] == "button"


def test_fragments_are_merged_when_speech_continues_after_a_result(tmp_path) -> None:
    # "메도빅 하나랑" … 0.8초 쉼 … "나폴레옹 하나요". 첫 결과가 올 때 손님은 이미 다시 말하는 중
    stt = FakeSttStream(replies=["메도빅 하나랑", "나폴레옹 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.feed(tone(1_000) + silence(800) + tone(1_000) + silence(1_500))
            await c.out.wait_for("bot.done", turn=1)
            await settle()
            return c

    c = run(call())
    (final,) = c.out.of("stt.final")
    assert final["text"] == "메도빅 하나랑 나폴레옹 하나요"
    assert final["fragments"] == 2
    assert c.out.of("bot.say", 1)[0]["text"] == "메도빅 하나, 나폴레옹 하나요."


class GatedNlu:
    """`gate_text`를 받으면 `release`가 열릴 때까지 엔진 스레드를 붙잡는 NLU."""

    def __init__(self, gate_text: str) -> None:
        self.inner = FakeNlu()
        self.gate_text = gate_text
        self.entered = threading.Event()
        self.release = threading.Event()

    def parse(self, text, ctx):
        if text == self.gate_text:
            self.entered.set()
            self.release.wait(5)
        return self.inner.parse(text, ctx)


def test_results_arriving_while_engine_runs_become_one_next_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "나폴레옹 하나요", "그게 다예요"])
    nlu = GatedNlu("메도빅 하나요")

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu) as c:
            await c.start()
            await c.say()
            await asyncio.to_thread(nlu.entered.wait, 2)
            await c.say()
            await c.say()  # 엔진이 도는 동안 두 번 더 말함
            await settle()
            nlu.release.set()
            await c.out.wait_for("bot.done", turn=1)
            await c.played(1)
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            await settle()
            return c

    c = run(call())
    assert [(e["turn"], e["text"]) for e in c.out.of("stt.final")] == [
        (1, "메도빅 하나요"), (2, "나폴레옹 하나요 그게 다예요")
    ]
    assert [e["text"] for e in c.out.of("bot.say", 2)] == ["나폴레옹 하나요.", "몇 시쯤 오세요?"]


def test_turn_numbers_increase_and_every_message_has_a_turn(tmp_path) -> None:
    c = _full_call(tmp_path)
    turns = [m["turn"] if isinstance(m, dict) else m.turn for m in c.out.sent]
    assert all(isinstance(t, int) for t in turns)
    assert turns == sorted(turns)
    assert [e["turn"] for e in c.out.of("bot.done")] == [0, 1, 2, 3, 4]
    assert c.out.events[0]["type"] == "call.ready"


def test_tts_sentences_go_out_in_order_even_if_synthesis_finishes_out_of_order(tmp_path) -> None:
    tts = FakeTts(bytes(CHUNK_BYTES * 2 + 100), delays={"네, 샘플 러시안 베이커리입니다.": 0.1})

    async def call() -> Call:
        async with Call(tmp_path, tts=tts) as c:
            await c.start()
            return c

    c = run(call())
    assert tts.max_active == 2
    sent = [
        ("audio", m.seq, len(m.pcm), m.last) if not isinstance(m, dict) else (m["type"], m["seq"])
        for m in c.out.sent
        if not isinstance(m, dict) or m["type"] in ("bot.say", "bot.audio.end")
    ]
    assert sent == [
        ("bot.say", 0), ("audio", 0, CHUNK_BYTES, False), ("audio", 0, CHUNK_BYTES, False),
        ("audio", 0, 100, True), ("bot.audio.end", 0),
        ("bot.say", 1), ("audio", 1, CHUNK_BYTES, False), ("audio", 1, CHUNK_BYTES, False),
        ("audio", 1, 100, True), ("bot.audio.end", 1),
    ]
    assert [e["text"] for e in c.out.of("bot.say", 0)] == [
        "네, 샘플 러시안 베이커리입니다.", "주문 도와드릴게요."
    ]


def test_handoff_order_goes_to_sink_and_call_ends_after_playback(tmp_path) -> None:
    stt = FakeSttStream(replies=["사장님 바꿔 주세요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            assert not c.session.done
            await c.played(1)
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    c = run(call())
    (order,) = c.sink.orders
    assert order.status == "handoff"
    assert c.out.of("order", 1)[0]["order"]["status"] == "handoff"


def test_latency_jsonl_has_one_line_per_turn_with_marks_and_client_values(tmp_path) -> None:
    import json

    c = _full_call(tmp_path)
    lines = [json.loads(s) for s in (c.log_dir / "phone-20261108.jsonl").read_text().splitlines()]
    assert [(r["call"], r["turn"]) for r in lines] == [("test-call", t) for t in range(5)]
    greeting, first = lines[0], lines[1]
    assert greeting["client"]["mic"] == {"echoCancellation": "all", "sampleRate": 16000}
    assert first["text"] == "메도빅 하나요"
    assert first["at"] == "2026-11-08T16:00:00+09:00"
    assert (first["epd_type"], first["fragments"], first["splits"], first["end_by"]) == (
        "endPoint", 1, 0, "vad"
    )
    assert first["output_latency"] == 0.02
    assert first["client"] == {"first_audio_ms": 12.5}
    order = ["speech_end", "ep_sent", "stt_final", "engine_start", "engine_end", "tts_start",
             "tts_first", "audio_first", "play_start", "play_done"]
    marks = first["marks"]
    assert [m for m in order if m in marks] == order
    assert [marks[m] for m in order] == sorted(marks[m] for m in order)
    assert [e["turn"] for e in c.out.of("metrics")][:4] == [0, 1, 2, 3]


def test_engine_gets_store_time_even_if_clock_is_given_in_utc(tmp_path) -> None:
    c = _full_call(tmp_path, clock=FakeClock(datetime(2026, 11, 8, 7, 0, tzinfo=UTC)))
    assert c.sink.orders[0].pickup_time == "2026-11-08T18:30:00+09:00"
    assert SystemClock().now().utcoffset().total_seconds() == 9 * 3600
