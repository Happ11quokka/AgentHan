"""마이크를 닫는 때 = 실제 재생 시작 (6단계 티켓 02, `research/s6-turn-split.md` 차선 2).

전에는 브라우저가 `state: speaking`(첫 오디오 송신 직전)을 받으면 0 프레임으로 바꿨고, 서버도
그 오디오 시각 뒤에 시작한 말을 버렸다. 손님 귀에 봇 소리가 들리는 것은 그보다 망 지연 + 재생
예약만큼 뒤다. 그 사이 마이크에는 봇 소리가 없으니 열어 둔다.

- 서버는 그 턴 첫 문장의 `playback.started`를 받은 오디오 시각을 반이중 경계로 쓴다
- 보고가 없으면 지금처럼 첫 소리를 보낸 때가 경계다. 보고가 늦어도 `play_start_cap_ms`를 넘지 않는다
- 재생이 시작된 뒤 시작한 말은 그대로 버린다(에코가 섞일 수 있는 구간)
"""

from __future__ import annotations

import asyncio
import json

from call_harness import Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream
from agenthan.voice.call.session import SessionSettings


def run(coro):
    return asyncio.run(coro)


def records(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


async def started(c: Call, turn: int) -> None:
    await c.session.feed_event({"type": "playback.started", "turn": turn, "seq": 0})


async def speak_between_send_and_playback(c: Call) -> None:
    """턴 1의 봇 소리를 서버가 보낸 뒤, 브라우저가 재생을 시작하기 전에 손님이 말한다."""
    await c.start()
    await c.say()
    await c.out.wait_for("bot.done", turn=1)  # 서버는 이미 speaking
    await c.feed(tone(800))  # 아직 스피커로 소리가 안 나갔다. 진짜 마이크 소리
    await started(c, 1)
    await c.feed(silence(1_500))  # 재생 중: 브라우저가 0 프레임. 이때 STT 글이 온다
    await c.played(1)


def test_speech_before_playback_starts_is_kept_as_the_next_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await speak_between_send_and_playback(c)
            await c.out.wait_for("stt.final", turn=2)
            return c

    c = run(call())
    assert [(e["turn"], e["text"]) for e in c.out.of("stt.final")] == [
        (1, "메도빅 하나요"), (2, "그게 다예요")
    ]
    turn1 = next(r for r in records(c) if r["turn"] == 1)
    assert "dropped" not in turn1
    assert turn1["mic_open_extra_ms"] == 800  # 첫 소리 송신 → 재생 시작 사이에 더 연 시간


def test_the_old_boundary_drops_that_speech_when_the_switch_is_off(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=SessionSettings(mic_close="state")) as c:
            await speak_between_send_and_playback(c)
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]
    turn1 = next(r for r in records(c) if r["turn"] == 1)
    assert [(d["text"], d["reason"]) for d in turn1["dropped"]] == [("그게 다예요", "speaking")]
    assert "mic_open_extra_ms" not in turn1


def test_speech_after_playback_started_is_still_dropped(tmp_path) -> None:
    # 재생이 시작된 뒤의 소리에는 봇 목소리가 섞일 수 있다. 0 프레임을 안 보내는 쪽이어도 버린다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(silence(200))
            await started(c, 1)
            await c.feed(tone(800) + silence(1_500))
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]
    turn1 = next(r for r in records(c) if r["turn"] == 1)
    assert [(d["text"], d["reason"]) for d in turn1["dropped"]] == [("그게 다예요", "speaking")]


def test_without_a_playback_report_the_boundary_stays_at_the_first_sound(tmp_path) -> None:
    # 재생 시작을 알리지 않는 손님 쪽(낡은 화면·멈춘 AudioContext)은 지금처럼 첫 소리부터 막는다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(tone(800) + silence(1_500))
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]
    turn1 = next(r for r in records(c) if r["turn"] == 1)
    assert [d["reason"] for d in turn1["dropped"]] == ["speaking"]


def test_a_late_playback_report_cannot_open_more_than_the_cap(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    capped = SessionSettings(play_start_cap_ms=300)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=capped) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(silence(500))
            await c.feed(tone(800))  # 첫 소리 송신 + 500ms에 시작: 상한(300ms) 밖
            await started(c, 1)  # 보고가 아주 늦게 왔다
            await c.feed(silence(1_500))
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]
    turn1 = next(r for r in records(c) if r["turn"] == 1)
    assert [d["reason"] for d in turn1["dropped"]] == ["speaking"]
    assert turn1["mic_open_extra_ms"] == 300


def test_only_the_first_report_of_the_speaking_turn_moves_the_boundary(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await started(c, 1)
            await c.feed(tone(800))
            await started(c, 1)  # 같은 보고가 또 왔다. 경계를 뒤로 밀지 않는다
            await c.session.feed_event({"type": "playback.started", "turn": 1, "seq": 1})
            await c.session.feed_event({"type": "playback.started", "turn": 0, "seq": 0})
            await c.feed(silence(1_500))
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert [e["turn"] for e in c.out.of("stt.final")] == [1]


def test_call_ready_tells_the_browser_when_to_close_the_mic(tmp_path) -> None:
    async def ready(settings: SessionSettings, **client) -> dict:
        async with Call(tmp_path, settings=settings) as c:
            await c.start(**client)
            return c.out.of("call.ready")[0]

    assert run(ready(SessionSettings()))["mic_close"] == "playback"
    assert "mic_close" not in run(ready(SessionSettings(mic_close="state")))


def test_a_yes_before_the_readback_is_heard_is_still_not_a_confirmation(tmp_path) -> None:
    # 복창 소리가 나가기 전(송신 → 재생 시작 사이)에 한 "네"는 금액을 듣기 전이다. 확정이 아니다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요", "6시 반이요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2):
                await c.turn(turn)
            await c.say()
            await c.out.wait_for("bot.done", turn=3)  # 복창을 보냈다
            await c.feed(tone(300))
            await started(c, 3)
            await c.feed(silence(1_500))
            await c.played(3)
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert c.sink.orders == []
    assert c.out.of("state")[-1]["dialog"] == "confirm"
    turn3 = next(r for r in records(c) if r["turn"] == 3)
    assert [(d["text"], d["reason"]) for d in turn3["dropped"]] == [("네", "early_yes")]
