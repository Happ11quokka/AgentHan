"""말 끝 판정 국면 (3단계 spec "전체 흐름", 티켓 02). 통화 세션 바깥 행동만 본다.

- 이어짐 글이면 마지막 말 끝 + `cont_wait_s` 안에 이어진 말과 한 턴. 안 이어지면 상한에 넘김
- 상한은 말이 다시 시작되면 다시 센다. 조각 붙잡기(티켓 10) 타이머가 이 마감을 덮지 않는다
- 확인 질문 뒤 "네"·기다림 요청은 기다리지 않는다. 필러만 있는 말은 엔진에 넘기지 않는다
- 이어짐 대기 중 붙잡은 글로 NLU를 미리 부른다. `(글, NluContext)`가 같으면 한 번만 부른다
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from call_harness import Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream
from agenthan.voice.call.ports import SttResult
from agenthan.voice.call.session import SessionSettings
from agenthan.voice.nlu.fake import FakeNlu

CAP = 0.5  # 시험용 이어짐 상한(초). 기본값은 2.0


def run(coro):
    return asyncio.run(coro)


def settings(**kw) -> SessionSettings:
    return SessionSettings(**{"cont_wait_s": CAP, **kw})


def records(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


class CountingNlu:
    """부른 글을 센다. `block`에 든 글은 처음 한 번 `release`가 열릴 때까지 붙잡는다."""

    def __init__(self, block: str | None = None) -> None:
        self.inner = FakeNlu()
        self.calls: list[str] = []
        self.block = block
        self.release = threading.Event()
        self._lock = threading.Lock()

    def parse(self, text, ctx):
        with self._lock:
            self.calls.append(text)
            first = self.calls.count(text) == 1
        if text == self.block and first:
            self.release.wait(5)
        return self.inner.parse(text, ctx)


def test_a_continuing_utterance_waits_and_merges_with_the_next_one(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑", "아메리카노 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=settings()) as c:
            await c.start()
            await c.say()
            await asyncio.sleep(0.2)  # 상한 안에 다시 말한다
            assert c.out.of("stt.final") == []
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    (final,) = c.out.of("stt.final")
    assert (final["text"], final["fragments"]) == ("메도빅 두 개랑 아메리카노 하나요", 2)
    turn1 = records(c)[1]
    assert turn1["turn_end"]["label"] == "end"
    assert turn1["turn_end"]["path"] == "rule"
    assert [s["label"] for s in turn1["turn_end_steps"]] == ["continue"]
    assert turn1["turn_end_steps"][0]["reason"] == "connective:랑"
    assert turn1["cont_wait_ms"] >= 150


def test_a_continuing_utterance_goes_to_the_engine_at_the_cap_when_nothing_follows(
    tmp_path,
) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑"])

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, settings=settings()) as c:
            await c.start()
            await c.feed(tone(800))
            spoke = time.monotonic()  # 마지막 유성 프레임 도착
            await c.feed(silence(1_200))
            await c.out.wait_for("stt.final", turn=1)
            waited = time.monotonic() - spoke
            await c.out.wait_for("bot.done", turn=1)
            return c, waited

    c, waited = run(call())
    assert CAP - 0.05 <= waited < CAP + 0.3
    turn1 = records(c)[1]
    assert turn1["text"] == "메도빅 두 개랑"
    assert (turn1["turn_end"]["label"], turn1["turn_end"]["path"]) == ("end", "cap")
    assert turn1["turn_end"]["reason"] == "cap:connective:랑"
    assert "turn_end_steps" not in turn1  # 판정은 한 번(이어짐 → 상한)


def test_the_cap_restarts_from_the_end_of_renewed_speech(tmp_path) -> None:
    # 이어짐 대기 중 손님이 다시 소리를 냈는데 글은 없다(빈 epFlag 응답).
    # 마감은 그 소리 끝부터 다시 센다
    stt = FakeSttStream(replies=["메도빅 두 개랑"])

    async def call() -> tuple[float, float]:
        async with Call(tmp_path, stt=stt, settings=settings()) as c:
            await c.start()
            await c.feed(tone(800) + silence(1_200))
            first_end = time.monotonic()
            await asyncio.sleep(0.3)
            await c.feed(tone(600))
            second_end = time.monotonic()
            await c.feed(silence(1_000))
            await c.out.wait_for("stt.final", turn=1)
            now = time.monotonic()
            return now - first_end, now - second_end

    since_first, since_second = run(call())
    assert since_second >= CAP - 0.05
    assert since_first >= 0.3 + CAP - 0.05


def test_a_fragment_hold_inside_the_wait_merges_into_one_turn(tmp_path) -> None:
    # 이어짐 대기 중 손님이 다시 말하고, CLOVA가 그 말 중간에서 gap 조각을 낸다(티켓 10 A1 모양).
    # 조각 붙잡기 타이머(`hold_max_s`)가 짧아도 이어짐 마감을 덮지 않고 세 조각이 한 턴이 된다
    stt = FakeSttStream(replies=["메도빅 두 개랑", "하나요"])
    hold = settings(hold_max_s=0.1, hold_cap_s=0.3)

    async def call() -> list[dict]:
        async with Call(tmp_path, stt=stt, settings=hold) as c:
            await c.start()
            await c.say()
            at = stt.audio_ms
            await c.feed(tone(500))
            stt.push(SttResult("아메리카노", at, at + 400, "gap"))
            await asyncio.sleep(0.2)  # 붙잡기 타이머가 돌 시간. 손님은 아직 말하는 중
            await c.feed(tone(300) + silence(1_200))
            await c.out.wait_for("stt.final", turn=1)
            await asyncio.sleep(0.05)
            return c.out.of("stt.final")

    (final,) = run(call())
    assert (final["text"], final["fragments"]) == ("메도빅 두 개랑 아메리카노 하나요", 3)


def test_yes_after_the_readback_is_not_held(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요", "6시 반이요", "네"])
    slow = settings(cont_wait_s=2.0)

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, settings=slow) as c:
            await c.start()
            for turn in (1, 2, 3):
                await c.turn(turn)
            began = time.monotonic()
            await c.say()
            await c.out.wait_for("stt.final", turn=4)
            return c, time.monotonic() - began

    c, waited = run(call())
    assert waited < 1.0
    turn4 = next(r for r in records(c) if r["turn"] == 4)
    assert turn4["turn_end"] == {"label": "end", "reason": "yes_no_after_confirm", "path": "rule",
                                 "score": None, "backchannel": True}


def test_a_wait_request_goes_to_the_engine_without_waiting(tmp_path) -> None:
    stt = FakeSttStream(replies=["아메리카노 한 잔이랑 잠깐만요"])

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, settings=settings(cont_wait_s=2.0)) as c:
            await c.start()
            began = time.monotonic()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            waited = time.monotonic() - began
            await c.out.wait_for("bot.done", turn=1)
            return c, waited

    c, waited = run(call())
    assert waited < 1.0
    assert records(c)[1]["turn_end"]["label"] == "wait_request"


def test_filler_only_speech_never_reaches_the_engine_and_is_not_a_failure(tmp_path) -> None:
    # "음…"만 세 번. 엔진에 넘겼다면 unknown 실패 3회로 사장님 연결(FALLBACK)이 됐다
    stt = FakeSttStream(replies=["음", "어", "음…", "메도빅 하나요"])
    nlu = CountingNlu()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu, settings=settings(cont_wait_s=0.2)) as c:
            await c.start()
            for _ in range(3):
                await c.say()
                await asyncio.sleep(0.3)
            assert c.out.of("stt.final") == []
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert [e["text"] for e in c.out.of("stt.final")] == ["메도빅 하나요"]
    assert c.out.of("state")[-1]["dialog"] == "ordering"
    assert not any(t.startswith(("음", "어")) for t in nlu.calls)
    turn0 = records(c)[0]
    assert [f["text"] for f in turn0["fillers"]] == ["음", "어", "음…"]
    assert turn0["fillers"][0]["reason"] == "cap:filler_only"


def test_a_filler_followed_by_an_order_is_one_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["음", "메도빅 하나요"])

    async def call() -> list[dict]:
        async with Call(tmp_path, stt=stt, settings=settings()) as c:
            await c.start()
            await c.say()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            return c.out.of("stt.final")

    (final,) = run(call())
    assert final["text"] == "음 메도빅 하나요"


def test_a_backchannel_dropped_while_the_bot_speaks_is_flagged(tmp_path) -> None:
    # 봇이 말하는 중 "네네"는 반이중으로 버리지만 맞장구로 보였다는 것은 남긴다 (spec 이야기 14)
    stt = FakeSttStream(replies=["네네", "메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("bot.done", turn=0)
            await c.say()  # 인사 재생 중
            await asyncio.sleep(0.05)
            await c.played(0)
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    (dropped,) = records(c)[0]["dropped"]
    assert (dropped["text"], dropped["backchannel"]) == ("네네", True)


def test_the_end_of_speech_button_ends_the_wait(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑"])

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, settings=settings(cont_wait_s=2.0)) as c:
            await c.start()
            await c.say()
            began = time.monotonic()
            await c.session.feed_event({"type": "user.end_of_speech", "turn": 0})
            await c.out.wait_for("stt.final", turn=1)
            waited = time.monotonic() - began
            await c.out.wait_for("bot.done", turn=1)
            return c, waited

    c, waited = run(call())
    assert waited < 0.5
    assert records(c)[1]["turn_end"]["path"] == "button"


# ---------------------------------------------------------------- 미리 해석 (NLU 선호출)


def test_the_held_text_is_parsed_ahead_and_the_engine_reuses_it(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑"])
    nlu = CountingNlu()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu, settings=settings()) as c:
            await c.start()
            await c.say()
            await asyncio.sleep(0.1)
            assert nlu.calls == ["메도빅 두 개랑"]  # 기다리는 동안 미리 불렀다
            assert c.out.of("stt.final") == []  # 엔진은 아직 안 돈다
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert nlu.calls == ["메도빅 두 개랑"]  # 엔진이 같은 (글, 문맥)으로 불러 다시 부르지 않았다
    assert c.out.of("state")[-1]["cart"][0]["qty"] == 2
    prefetch = records(c)[1]["nlu_prefetch"]
    assert (prefetch["prefetched"], prefetch["used"], prefetch["discarded"]) == (1, True, 0)
    assert prefetch["miss"] is None


def test_a_prefetch_for_text_that_then_grew_is_discarded(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑", "블린 하나요"])
    nlu = CountingNlu()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu, settings=settings()) as c:
            await c.start()
            await c.say()
            await asyncio.sleep(0.1)
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert nlu.calls == ["메도빅 두 개랑", "메도빅 두 개랑 블린 하나요"]
    prefetch = records(c)[1]["nlu_prefetch"]
    assert (prefetch["prefetched"], prefetch["used"], prefetch["discarded"]) == (1, False, 1)
    assert prefetch["miss"] == "text"


def test_a_prefetch_slower_than_its_time_limit_is_called_again(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑"])
    nlu = CountingNlu(block="메도빅 두 개랑")  # 미리 부른 첫 호출만 멈춘다

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu,
                        settings=settings(cont_wait_s=0.2, prefetch_timeout_s=0.4)) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            nlu.release.set()
            return c

    c = run(call())
    assert nlu.calls == ["메도빅 두 개랑", "메도빅 두 개랑"]
    prefetch = records(c)[1]["nlu_prefetch"]
    assert (prefetch["used"], prefetch["miss"], prefetch["discarded"]) == (False, "timeout", 1)
