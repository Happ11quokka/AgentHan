"""3단계 리뷰 고침의 회귀 테스트 (`phone-s3-turn-management/review.md`).

통화 세션의 바깥 행동만 본다. 가짜 STT·TTS·시계와 합성 PCM으로 결정적으로 만든다.
"""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import Executor, Future, ThreadPoolExecutor

import pytest
from call_harness import Call, silence, tone

from agenthan.voice.call.barge import BargeSettings
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.session import SessionSettings

READBACK = ["메도빅 하나요", "그게 다예요", "6시 반이요"]


def run(coro):
    return asyncio.run(coro)


def lines(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(s) for s in path.read_text().splitlines()]


def slow_readback(text: str) -> float:
    """최종 복창의 금액 문장 합성을 늦춘다. 그 사이(첫 소리 전)에 손님이 말한다."""
    return 0.4 if "총" in text else 0.0


# ---------------------------------------------------------------- P1


@pytest.mark.parametrize("barge", [False, True], ids=["half_duplex", "voice_barge_in"])
@pytest.mark.parametrize("early", ["네", "응"])
def test_a_yes_said_while_the_readback_is_being_synthesized_does_not_confirm(
    tmp_path, barge, early
) -> None:
    # 픽업 시각 뒤 "네… 네"의 두 번째 "네"가 복창 첫 소리 전에 나왔다. 그 말은 `_pending`에
    # 들어가 복창 재생이 끝난 뒤에 판정된다. 금액을 듣기 전에 한 말이라 확정이 아니다(Q10)
    settings = SessionSettings(barge=BargeSettings()) if barge else SessionSettings()
    stt = FakeSttStream(replies=[*READBACK, early, "네"], voiced_start=True)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=FakeTts(delays=slow_readback),
                        settings=settings) as c:
            await c.start(voice_barge_in=barge)
            await c.turn(1)
            await c.turn(2)
            await c.say()
            await c.out.wait_for("stt.final", turn=3)
            await c.say()  # 복창 첫 문장을 합성하는 중
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await asyncio.sleep(0.05)
            assert c.out.of("order") == [], "금액 전에 한 말로 확정되면 안 된다"
            assert c.out.of("stt.final", 4) == []
            await c.turn(4)  # 복창을 다 듣고 한 "네"는 확정이다
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    c = run(call())
    (order,) = c.out.of("order")
    assert order["turn"] == 4 and order["order"]["status"] == "confirmed"
    record = next(line for line in lines(c) if line["turn"] == 3)
    assert [(d["text"], d["reason"]) for d in record["dropped"]] == [(early, "early_yes")]


def test_a_correction_said_while_the_readback_is_being_synthesized_is_kept(tmp_path) -> None:
    stt = FakeSttStream(replies=[*READBACK, "메도빅 두 개로 바꿔 주세요"], voiced_start=True)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=FakeTts(delays=slow_readback)) as c:
            await c.start()
            await c.turn(1)
            await c.turn(2)
            await c.say()
            await c.out.wait_for("stt.final", turn=3)
            await c.say()
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await c.out.wait_for("bot.done", turn=4)
            return c

    c = run(call())
    assert c.out.of("order") == []
    assert "15,000원" in " ".join(e["text"] for e in c.out.of("bot.say", 4))


# ---------------------------------------------------------------- C1


def test_the_end_button_during_the_wait_keeps_the_words_whose_text_is_still_coming(
    tmp_path,
) -> None:
    # "메도빅 두 개랑"으로 이어짐 대기 중 손님이 "아메리카노 하나요"를 말하고 "말 끝"을 눌렀다.
    # 그 말의 epFlag 응답(글)은 아직 오지 않았다. 버튼이 기다리던 말만 넘기면 뒷말이 다음 턴이
    # 되어 봇이 두 번 답한다
    stt = FakeSttStream(replies=["메도빅 두 개랑", "아메리카노 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=SessionSettings(cont_wait_s=2.0)) as c:
            await c.start()
            await c.say()
            await asyncio.sleep(0.05)
            assert c.out.of("stt.final") == []  # 이어짐 대기 중
            await c.feed(tone(800) + silence(500))  # epFlag는 갔고 응답은 600ms 뒤(오디오 시각)
            await c.session.feed_event({"type": "user.end_of_speech", "turn": 0})
            await c.feed(silence(800))
            await c.out.wait_for("bot.done", turn=1)
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert [e["text"] for e in c.out.of("stt.final")] == ["메도빅 두 개랑 아메리카노 하나요"]


# ---------------------------------------------------------------- P3


def test_a_long_order_in_several_parts_is_not_split_by_the_overall_wait_limit(tmp_path) -> None:
    # 전체 상한(`cont_max_s`)은 배경 소리가 "말하는 중"을 안 풀 때를 막는다. 글이 계속 들어오는
    # 긴 주문은 첫 이어짐 판정부터 상한을 넘겨도 말하는 중에 쪼개지 않는다(Q3 "다시 셈")
    stt = FakeSttStream(replies=["메도빅 두 개랑", "블린 하나랑", "아메리카노 하나요"])
    limits = SessionSettings(cont_wait_s=0.6, cont_max_s=1.0)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=limits) as c:
            await c.start()
            await c.say()
            await asyncio.sleep(0.5)
            await c.say()  # 둘째 부분 (글이 들어와 이어짐을 다시 판정)
            await c.feed(tone(400))  # 셋째 부분을 말하는 중
            await asyncio.sleep(0.8)  # 첫 판정부터 1.3초: 전체 상한을 넘었다
            assert c.out.of("stt.final") == [], "말하는 중에 쪼개면 안 된다"
            await c.feed(silence(1_000))
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert [e["text"] for e in c.out.of("stt.final")] == [
        "메도빅 두 개랑 블린 하나랑 아메리카노 하나요"
    ]


def test_background_sound_without_text_still_ends_the_wait_at_the_overall_limit(
    tmp_path,
) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑"])
    limits = SessionSettings(cont_wait_s=0.3, cont_max_s=0.6)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=limits) as c:
            await c.start()
            await c.say()
            await c.feed(tone(300))  # 글이 나오지 않는 소리가 "말하는 중"을 붙잡는다
            await c.out.wait_for("stt.final", turn=1)
            return c

    c = run(call())
    assert [e["text"] for e in c.out.of("stt.final")] == ["메도빅 두 개랑"]
    turn1 = next(line for line in lines(c) if line["turn"] == 1)
    assert turn1["turn_end"]["path"] == "cap"


# ---------------------------------------------------------------- C4


def test_the_fragment_hold_limit_restarts_when_new_text_arrives(tmp_path) -> None:
    # 조기 epFlag(무음 250ms)로 짧은 쉼마다 글이 오고, 손님은 그 사이 계속 말한다. 붙잡기 상한
    # (`hold_cap_s`)을 첫 붙잡기부터 세면 셋째 부분을 말하는 중에 앞 두 부분만 턴이 된다
    stt = FakeSttStream(replies=["메도빅 두 개", "블린 하나", "아메리카노 하나요"])
    hold = SessionSettings(hold_max_s=0.3, hold_cap_s=0.8)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=hold) as c:
            await c.start()
            await c.feed(tone(400) + silence(300) + tone(700))  # 첫 글이 둘째 부분 중에 온다
            await asyncio.sleep(0.4)
            await c.feed(silence(300) + tone(700))  # 둘째 글이 셋째 부분 중에 온다
            await asyncio.sleep(0.6)  # 첫 붙잡기부터 1.0초(상한 0.8초), 둘째 글부터 0.6초
            assert c.out.of("stt.final") == [], "말하는 중에 붙잡은 조각을 풀면 안 된다"
            await c.feed(silence(1_000))
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert [e["text"] for e in c.out.of("stt.final")] == [
        "메도빅 두 개 블린 하나 아메리카노 하나요"
    ]


# ---------------------------------------------------------------- C5


class SlowTurnModel:
    """점수를 내는 데 `delay_s`가 걸리는 턴 모델 (실제 스레드 풀에서 돈다)."""

    def __init__(self, score: float, delay_s: float) -> None:
        self.score, self.delay_s = score, delay_s

    def p_complete(self, pcm16: bytes) -> float:
        time.sleep(self.delay_s)
        return self.score


def test_a_fast_stt_reply_still_waits_briefly_for_the_turn_model(tmp_path) -> None:
    # 조기 epFlag 응답이 무음 400ms 전에 오면 말 끝에서 바로 넘긴다. 같은 틱에 낸 점수 작업은
    # 아직 안 끝나서 늘 `late`였다. 잠깐(수십 ms) 기다려 점수를 쓴다
    stt = FakeSttStream(replies=["메도빅 두 개랑"], lag_ms=0)
    pool = ThreadPoolExecutor(1)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=SessionSettings(cont_wait_s=2.0),
                        turn_model=SlowTurnModel(0.95, 0.03), turn_executor=pool) as c:
            await c.start()
            await c.feed(tone(800) + silence(300))  # epFlag(250ms) 응답이 무음 400ms 전에 온다
            await asyncio.sleep(0.05)
            await c.feed(silence(900))
            await c.out.wait_for("bot.done", turn=1)
            return c

    try:
        c = run(call())
    finally:
        pool.shutdown()
    turn1 = next(line for line in lines(c) if line["turn"] == 1)
    assert turn1["turn_model"]["src"] == "model", json.dumps(turn1, ensure_ascii=False)
    assert (turn1["turn_end"]["path"], turn1["turn_end"]["label"]) == ("model", "end")


class QueuedExecutor(Executor):
    """받은 작업을 돌리지 않고 쌓아 둔다(바쁜 공유 풀). 낡은 작업이 취소되는지 본다."""

    def __init__(self) -> None:
        self.futures: list[Future] = []

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        self.futures.append(Future())
        return self.futures[-1]


def test_a_stale_turn_model_job_is_cancelled_when_new_speech_starts(tmp_path) -> None:
    # 리뷰 C7: 낡은 점수 작업이 공유 2스레드 풀에 쌓이면 다른 통화의 점수가 늦는다
    stt = FakeSttStream(replies=["메도빅 두 개랑", "블린 하나요"])
    pool = QueuedExecutor()

    async def call() -> None:
        async with Call(tmp_path, stt=stt, settings=SessionSettings(cont_wait_s=2.0),
                        turn_model=SlowTurnModel(0.5, 0.0), turn_executor=pool) as c:
            await c.start()
            await c.say()  # 말 끝 → 점수 작업 1 (쌓임)
            await c.say()  # 새 말 구간 → 작업 1은 낡았다. 말 끝 → 작업 2
            await c.out.wait_for("bot.done", turn=1)

    run(call())
    assert len(pool.futures) == 2
    assert pool.futures[0].cancelled()


# ---------------------------------------------------------------- P4 / C3


def test_the_no_input_limit_does_not_reprompt_while_the_guest_is_speaking(tmp_path) -> None:
    # 글 없는 소리가 마감을 밀어도 처음 마감 + `no_input_slack_s`에는 되묻는다. 그래도 손님이
    # 말하는 중에는 끼어들지 않고, 그 말이 끝나면 바로 되묻는다
    quiet = SessionSettings(no_input_s=0.3, no_input_slack_s=0.3)

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(), settings=quiet) as c:
            await c.start()
            await c.feed(tone(200))  # 말하기 시작
            await asyncio.sleep(0.9)  # 하드 마감(0.6초)을 넘겼다. 아직 말하는 중
            assert c.out.of("bot.say", 1) == [], "말하는 중에 되묻지 않는다"
            await c.feed(silence(500))
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert c.out.of("bot.say", 1)[0]["text"] == "여보세요?"


def test_steady_background_noise_cannot_hold_off_the_reprompt_forever(tmp_path) -> None:
    # 감사 2번: 문턱 위 소음이 끊기지 않으면 무음 감지가 말 구간을 끝내지 않는다. 말 구간이
    # `no_input_speech_max_s`보다 길어지면 그 소리는 말하는 중으로 보지 않고 상한에서 되묻는다
    quiet = SessionSettings(no_input_s=0.3, no_input_slack_s=0.3, no_input_speech_max_s=0.8)

    async def call() -> tuple[Call, float, float]:
        async with Call(tmp_path, stt=FakeSttStream(), settings=quiet) as c:
            await c.start()
            began = time.monotonic()
            asked = None
            while time.monotonic() - began < 3.0:  # 소음이 100ms마다 계속 올라온다
                await c.feed(tone(100))
                if c.out.of("bot.say", 1) and asked is None:
                    asked = time.monotonic()
                    break
                await asyncio.sleep(0.1)
            assert asked is not None, "소음이 이어져도 되묻는다"
            await c.out.wait_for("bot.done", turn=1)
            return c, began, asked

    c, began, asked = run(call())
    assert c.out.of("bot.say", 1)[0]["text"] == "여보세요?"
    assert asked - began >= 0.8 - 0.05  # 말 구간 상한 전에는 말하는 중으로 본다


# ---------------------------------------------------------------- C2


def test_how_much_was_heard_stays_on_a_cut_turn_even_when_a_queued_word_opens_the_next(
    tmp_path,
) -> None:
    # 첫 소리 전에 한 말이 대기 중이면 끊는 순간 다음 턴이 열린다. 그래도 그 뒤에 온
    # `playback.interrupted`의 들은 만큼은 끊긴 턴 기록에 남는다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    tts = FakeTts(delays={"메도빅 하나요.": 0.4, "더 필요하신 거 있으세요?": 5.0})

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await c.say()  # 첫 문장 합성 중
            await c.out.wait_for("bot.say", turn=1)
            await c.session.feed_event({"type": "user.interrupt", "turn": 1})
            await c.out.wait_for("stt.final", turn=2)
            await c.session.feed_event({"type": "playback.interrupted", "turn": 1, "seq": 0,
                                        "played_ms": 200})
            await c.out.wait_for("bot.done", turn=2)
            await c.session.feed_event({"type": "call.end", "turn": 2})
            return c

    c = run(call())
    record = next(line for line in lines(c) if line["turn"] == 1)
    assert record["interrupt"]["seq"] == 0 and record["interrupt"]["played_ms"] == 200
    assert record["interrupt"]["heard"][0]["heard"] == "partial"


# ---------------------------------------------------------------- C6


def test_a_confirm_turn_without_the_readback_can_be_interrupted(tmp_path) -> None:
    # 복창 중 "잠깐만요" → "네, 천천히 말씀하세요"는 CONFIRM 상태지만 복창이 아니다
    stt = FakeSttStream(replies=[*READBACK, "잠깐만요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            return c

    c = run(call())
    speaking = {e["turn"]: e["interruptible"] for e in c.out.of("state")
                if e["phase"] == "speaking"}
    assert speaking[3] is False  # 최종 복창
    assert speaking[4] is True
    assert [e["text"] for e in c.out.of("bot.say", 4)] == ["네, 천천히 말씀하세요."]
