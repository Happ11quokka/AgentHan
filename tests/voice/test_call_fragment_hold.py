"""조각 붙잡기 (spec 조각 합치기, 리뷰 2·3번).

- 붙잡는 근거는 "결과 끝 뒤에 새로 시작한 말 구간"뿐이다. 결과 전부터 이어진 소리(잡음)는 아니다
- 붙잡은 턴은 시간 제한(`hold_max_s`, 이어지는 말이 있으면 `hold_cap_s`까지) 안에 풀린다
- 봇이 말하기 시작하면 붙잡아 둔 조각과 "말 끝" 표시를 지운다
"""

from __future__ import annotations

import asyncio

from call_harness import Call, silence, tone
from test_call_session import GatedNlu

from agenthan.voice.call.fakes import FakeSttStream, Scripted
from agenthan.voice.call.ports import SttResult
from agenthan.voice.call.session import SessionSettings


def run(coro):
    return asyncio.run(coro)


def test_noise_that_never_stops_after_speech_does_not_hold_the_turn(tmp_path) -> None:
    # 말(0~1,000ms) 뒤로 배경 소리가 임계값 위에서 계속된다. CLOVA는 1,000ms에서 gap으로 끊었다
    gap = SttResult("메도빅 하나요", 0, 1_000, "gap")
    stt = FakeSttStream(script=[Scripted(1_500, gap)])

    async def call() -> dict:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.feed(tone(1_500))
            return await c.out.wait_for("stt.final", turn=1)

    final = run(call())
    assert (final["text"], final["end_by"]) == ("메도빅 하나요", "gap")


def test_a_held_turn_is_released_when_no_result_follows(tmp_path) -> None:
    # 말 뒤 0.5초 쉬고 다시 소리가 시작돼 붙잡았는데, 그 소리가 끝나지 않는다(잡음·자동 음량)
    gap = SttResult("메도빅 하나요", 0, 1_000, "gap")
    stt = FakeSttStream(script=[Scripted(1_700, gap)])
    settings = SessionSettings(hold_max_s=0.1, hold_cap_s=0.3)

    async def call() -> dict:
        async with Call(tmp_path, stt=stt, settings=settings) as c:
            await c.start()
            await c.feed(tone(1_000) + silence(500) + tone(1_000))
            return await c.out.wait_for("stt.final", turn=1)

    final = run(call())
    assert (final["text"], final["fragments"], final["end_by"]) == (
        "메도빅 하나요", 1, "hold_timeout"
    )


def test_a_fragment_held_while_thinking_does_not_stick_to_the_next_utterance(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    nlu = GatedNlu("메도빅 하나요")

    async def call() -> list[str]:
        async with Call(tmp_path, stt=stt, nlu=nlu) as c:
            await c.start()
            await c.say()
            await asyncio.to_thread(nlu.entered.wait, 2)
            # 엔진이 도는 동안 손님이 다시 말하기 시작했고, 그 앞 조각 결과가 붙잡혔다
            at = stt.audio_ms
            await c.feed(tone(600))
            stt.push(SttResult("나폴레옹", at - 300, at - 100, "gap"))
            await asyncio.sleep(0.05)
            nlu.release.set()
            await c.out.wait_for("bot.done", turn=1)
            await c.played(1)
            await c.say()
            await c.out.wait_for("stt.final", turn=2)
            return [e["text"] for e in c.out.of("stt.final")]

    assert run(call()) == ["메도빅 하나요", "그게 다예요"]


def test_a_button_pressed_while_thinking_does_not_mark_the_next_utterance(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "", "나폴레옹 하나랑", "블린 하나요"])
    nlu = GatedNlu("메도빅 하나요")

    async def call() -> list[dict]:
        async with Call(tmp_path, stt=stt, nlu=nlu) as c:
            await c.start()
            await c.say()
            await asyncio.to_thread(nlu.entered.wait, 2)
            await c.session.feed_event({"type": "user.end_of_speech", "turn": 1})
            await c.feed(silence(100))  # epFlag가 나간다. 그 응답은 봇이 말할 때 온다
            nlu.release.set()
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(silence(1_000))  # 재생 중 0 프레임. 버튼 응답이 여기서 와서 버려진다
            await c.played(1)
            # "나폴레옹 하나랑" … 0.8초 쉼 … "블린 하나요": 한 말로 합쳐져야 한다
            await c.feed(tone(1_000) + silence(800) + tone(1_000) + silence(1_500))
            await c.out.wait_for("stt.final", turn=2)
            return c.out.of("stt.final")

    finals = run(call())
    assert [(f["text"], f["end_by"]) for f in finals[1:]] == [
        ("나폴레옹 하나랑 블린 하나요", "vad")
    ]
