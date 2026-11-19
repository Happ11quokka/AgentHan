"""첫 소리 문지기 + 앞 턴 되감기 (6단계 티켓 02, `research/s6-turn-split.md` 1절).

손님이 "아니요"라고 하고 쉬었다가 "그게 다예요"를 덧붙이면, 전에는 "아니요"로 턴이 넘어간 뒤라
뒷말이 다음 턴이 됐고 봇은 "아니요"에만 답했다. `turn_rewind`를 켜면:

- 봇 답의 첫 소리 전에 손님이 내용 있는 말을 이으면 그 답을 버리고, 엔진을 턴 전으로 되돌린 뒤
  앞 말 + 뒷말을 한 턴으로 다시 처리한다
- 빈 글(기침·잡음)·맞장구·필러뿐이면 원래 답을 그대로 말한다(헛 문지기)
- 주문 확정·통화 끝·사장님 질문·보충 요청·복창 턴은 되감지 않는다
- 첫 소리가 나간 뒤, 생각 중 효과음 신호를 보낸 뒤에 시작한 말은 전처럼 다룬다
- 기본은 꺼짐(`PHONE_TURN_REWIND=off`). 꺼져 있으면 전과 똑같다
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from call_harness import Call, silence, tone

from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import SttResult
from agenthan.voice.call.session import SessionSettings
from agenthan.voice.dialog.knowledge import KnowledgeStore
from agenthan.voice.nlu.fake import FakeNlu

MORE_Q = "더 필요하신 거 있으세요?"
PICKUP_Q = "몇 시쯤 오세요?"
SORRY = "죄송해요, 제가 잘 못 알아들었어요."
NUTS = "메도빅에 견과류 들어가요?"
TASTE = "아메리카노 어떤 맛이에요?"


def run(coro):
    return asyncio.run(coro)


def on(**kw) -> SessionSettings:
    """되감기를 켠 설정. 맨 "아니요" 뒤 짧은 대기(차선 1)는 꺼서 문지기만 본다."""
    return SessionSettings(**{"turn_rewind": True, "more_no_wait_s": 0.0, **kw})


def slow(*sentences: str, seconds: float = 0.6) -> FakeTts:
    """이 문장들만 합성이 오래 걸리는 TTS (첫 소리 전 창을 벌린다)."""
    return FakeTts(delays=lambda text: seconds if text in sentences else 0.0)


def records(c: Call) -> dict[int, dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return {(r := json.loads(line))["turn"]: r for line in path.read_text().splitlines()}


def finals(c: Call) -> list[tuple[int, str]]:
    return [(e["turn"], e["text"]) for e in c.out.of("stt.final")]


def said(c: Call, turn: int) -> list[str]:
    return [e["text"] for e in c.out.of("bot.say", turn)]


class CountingNlu:
    """부른 글을 센다. `block`에 든 글은 `seconds` 동안 붙잡는다(느린 HCX 흉내)."""

    def __init__(self, block: str | None = None, seconds: float = 0.0) -> None:
        self.calls: list[str] = []
        self._block, self._seconds = block, seconds
        self._lock = threading.Lock()

    def parse(self, text, ctx):
        with self._lock:
            self.calls.append(text)
        if text == self._block:
            time.sleep(self._seconds)
        return FakeNlu().parse(text, ctx)


async def speak_again_while_thinking(c: Call, turn: int) -> None:
    """`turn`을 넘긴 직후(첫 소리 전)에 손님이 한 번 더 말한다."""
    await c.say()
    await c.out.wait_for("stt.final", turn=turn)
    await asyncio.sleep(0.05)
    assert not c.out.of("bot.say", turn), "아직 첫 소리 전이어야 한다"
    await c.say()


# ------------------------------------------------------------ 되감기


def test_a_trailing_phrase_before_the_first_sound_rewinds_the_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요", "그게 다예요"])
    nlu = CountingNlu()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu, tts=slow(PICKUP_Q), settings=on()) as c:
            await c.start()
            await c.turn(1)
            await speak_again_while_thinking(c, 2)  # "아니요" … "그게 다예요"
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert finals(c) == [(1, "메도빅 하나요"), (2, "아니요"), (3, "아니요 그게 다예요")]
    # "아니요"에 대한 답은 소리도 자막도 나가지 않았다. 합친 말에 한 번만 답한다
    assert said(c, 2) == [] and [f for f in c.out.frames if f.turn == 2] == []
    assert said(c, 3) == [PICKUP_Q]
    assert c.out.of("state")[-1]["dialog"] == "ask_pickup"
    assert [line["name"] for line in c.out.of("state")[-1]["cart"]] == ["메도빅"]
    assert c.out.of("turn.superseded") == [{"type": "turn.superseded", "turn": 2}]
    assert nlu.calls == ["메도빅 하나요"]  # "아니요"·"아니요 그게 다예요" 모두 규칙이 읽는다
    turn2, turn3 = records(c)[2], records(c)[3]
    assert turn2["superseded"]["by_turn"] == 3
    assert turn2["superseded"]["text"] == "그게 다예요"
    assert turn2["superseded"]["gate_ms"] >= 0
    assert "bot" not in turn2 and "play_start" not in turn2["marks"]
    assert (turn3["supersedes"], turn3["fragments"], turn3["utterances"]) == (2, 2, 2)
    assert "queued" not in turn3 and "speech_end" in turn3["marks"]  # 뒷말 끝부터 다시 잰다


def test_without_the_switch_the_trailing_phrase_is_still_the_next_turn(tmp_path) -> None:
    # 기본값(꺼짐): 티켓 02의 증상 그대로. 봇은 "아니요"에 답하고 "그게 다예요"는 다음 턴이 된다
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요", "그게 다예요"])
    off = SessionSettings(more_no_wait_s=0.0)
    assert off.turn_rewind is False

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(PICKUP_Q), settings=off) as c:
            await c.start()
            await c.turn(1)
            await speak_again_while_thinking(c, 2)
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            await c.out.wait_for("bot.done", turn=3)
            return c

    c = run(call())
    assert finals(c) == [(1, "메도빅 하나요"), (2, "아니요"), (3, "그게 다예요")]
    assert said(c, 2) == [PICKUP_Q]
    assert said(c, 3)[0] == SORRY  # 대화가 한 칸 밀렸다
    assert c.out.of("turn.superseded") == []
    assert records(c)[3]["queued"] is True


def test_the_rewound_turn_leaves_no_trace_in_the_cart(tmp_path) -> None:
    # 앞 말이 장바구니를 바꿨어도 되감으면 없던 일이다. 합친 말로 한 번만 담는다
    stt = FakeSttStream(replies=["메도빅 하나요", "블린도 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow("메도빅 하나요."),
                        settings=on()) as c:
            await c.start()
            await speak_again_while_thinking(c, 1)
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            return c

    c = run(call())
    assert finals(c)[-1] == (2, "메도빅 하나요 블린도 하나요")
    cart = c.out.of("state")[-1]["cart"]
    assert sorted((line["name"], line["qty"]) for line in cart) == [("메도빅", 1), ("블린", 1)]
    assert said(c, 1) == []


def test_the_owner_screen_only_hears_what_the_customer_heard(tmp_path) -> None:
    # 사장님 허브: 버린 봇 답("몇 시쯤 오세요?")과 반쪽 손님 말("아니요")은 가지 않는다
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(PICKUP_Q), settings=on()) as c:
            await c.start()
            await c.turn(1)
            await c.say()
            await c.out.wait_for("stt.final", turn=2)
            await asyncio.sleep(0.05)
            assert PICKUP_Q not in c.owner.bot_lines  # 첫 소리 전에는 알리지 않는다
            await c.say()
            await c.out.wait_for("bot.done", turn=3)
            return c

    c = run(call())
    assert c.owner.customer_lines == ["메도빅 하나요", "아니요 그게 다예요"]
    assert c.owner.bot_lines.count(PICKUP_Q) == 1
    kinds = [note[0] for note in c.owner.notes if note[0] in ("customer", "cart", "bot")]
    assert kinds[-3:] == ["customer", "cart", "bot"]  # 손님 말 → 장바구니 → 봇 말 순서 그대로


def test_a_hangup_before_the_first_sound_still_shows_what_the_customer_said(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow("메도빅 하나요."), settings=on()) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await asyncio.sleep(0.05)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = run(call())
    assert c.owner.customer_lines == ["메도빅 하나요"]
    assert not any("메도빅" in line for line in c.owner.bot_lines)  # 못 들은 봇 답은 안 남긴다


def test_without_the_switch_the_owner_screen_is_told_before_the_first_sound(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])
    line = f"메도빅 하나요. {MORE_Q}"

    async def call() -> None:
        async with Call(tmp_path, stt=stt, tts=slow("메도빅 하나요.")) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await asyncio.sleep(0.1)
            assert not c.out.of("bot.say", 1)
            assert c.owner.bot_lines[-1] == line  # 전처럼 엔진이 끝나자마자

    run(call())


def test_a_late_result_of_the_same_speech_rewinds_without_a_new_speech_start(tmp_path) -> None:
    # 무음 감지는 새 말 구간을 못 봤는데 글이 늦게 왔다(앞 조각으로 턴이 넘어간 뒤 뒷조각)
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(PICKUP_Q), settings=on()) as c:
            await c.start()
            await c.turn(1)
            await c.say()
            await c.out.wait_for("stt.final", turn=2)
            at = stt.audio_ms
            stt.push(SttResult("그게 다예요", at - 300, at, "endPoint", True))
            await c.out.wait_for("bot.done", turn=3)
            return c

    c = run(call())
    assert finals(c)[-1] == (3, "아니요 그게 다예요")
    assert said(c, 2) == [] and said(c, 3) == [PICKUP_Q]


def test_speech_that_starts_while_the_engine_is_busy_rewinds_once_it_returns(tmp_path) -> None:
    # 엔진(HCX) 스레드는 취소가 안 된다. 끝나기를 기다렸다가 되돌린다. 버릴 답은 합성하지도 않는다
    stt = FakeSttStream(replies=["메도빅 하나요", "블린도 하나요"])
    nlu = CountingNlu(block="메도빅 하나요", seconds=0.4)
    tts = FakeTts()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu, tts=tts, settings=on()) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("stt.final", turn=1)
            await c.say()  # 엔진이 아직 도는 중
            await c.out.wait_for("bot.done", turn=2)
            return c

    c = run(call())
    assert nlu.calls == ["메도빅 하나요", "메도빅 하나요 블린도 하나요"]
    assert finals(c)[-1] == (2, "메도빅 하나요 블린도 하나요")
    assert "메도빅 하나요." not in tts.calls
    assert len(c.out.of("state")[-1]["cart"]) == 2


def test_one_utterance_is_rewound_at_most_twice(tmp_path) -> None:
    # 쉬어 가며 계속 덧붙이는 손님: 되감을 때마다 엔진을 다시 부른다. 두 번 되감은 말은 그대로
    # 답하고 그 뒤의 말은 전처럼 다음 턴으로 받는다
    stt = FakeSttStream(replies=["메도빅 하나요", "블린도 하나요", "나폴레옹도 하나요",
                                 "그게 다예요"])
    greeting = ("네, 샘플 러시안 베이커리 AI 전화 주문입니다.", "어떤 걸로 준비해 드릴까요?")
    tts = FakeTts(delays=lambda text: 0.0 if text in greeting else 0.5)

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts, settings=on()) as c:
            await c.start()
            await c.say()
            for turn in (1, 2, 3):
                await c.out.wait_for("stt.final", turn=turn)
                await asyncio.sleep(0.05)
                await c.say()
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await c.out.wait_for("stt.final", turn=4)
            return c

    c = run(call())
    assert [e["turn"] for e in c.out.of("turn.superseded")] == [1, 2]
    merged = "메도빅 하나요 블린도 하나요 나폴레옹도 하나요"
    assert finals(c)[2:] == [(3, merged), (4, "그게 다예요")]
    assert said(c, 3)
    assert records(c)[4]["queued"] is True


# ------------------------------------------------------------ 헛 문지기


def test_a_noise_with_no_text_holds_the_first_sound_only_until_the_empty_reply(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요"])  # 그 뒤 응답은 빈 글

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, tts=slow(PICKUP_Q, seconds=0.2),
                        settings=on(gate_max_s=2.0)) as c:
            await c.start()
            await c.turn(1)
            await c.say()
            await c.out.wait_for("stt.final", turn=2)
            await c.feed(tone(300))  # 기침: 말 구간은 열렸는데 아직 안 끝났다
            await asyncio.sleep(0.4)  # 합성은 끝났다. 문지기가 첫 소리를 붙잡고 있다
            assert said(c, 2) == []
            began = time.monotonic()
            await c.feed(silence(1_200))  # 빈 epFlag 응답이 온다
            await c.out.wait_for("bot.done", turn=2)
            waited = time.monotonic() - began
            await c.played(2)
            await asyncio.sleep(0.05)
            return c, waited

    c, waited = run(call())
    assert waited < 1.0  # 상한(2초)까지 가지 않는다
    assert said(c, 2) == [PICKUP_Q]
    assert finals(c) == [(1, "메도빅 하나요"), (2, "아니요")]
    assert c.out.of("turn.superseded") == []
    turn2 = records(c)[2]
    assert turn2["gate"]["result"] == "empty"
    assert turn2["gate"]["wait_ms"] >= 150  # 합성(0.2초)이 끝난 뒤부터 빈 응답까지 붙잡았다
    assert len(turn2["empty"]) == 1


@pytest.mark.parametrize("word", ["음", "네"])
def test_a_backchannel_or_filler_before_the_first_sound_keeps_the_answer(tmp_path, word) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요", word])
    nlu = CountingNlu()

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, nlu=nlu, tts=slow(PICKUP_Q), settings=on()) as c:
            await c.start()
            await c.turn(1)
            await speak_again_while_thinking(c, 2)
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            await asyncio.sleep(0.1)
            return c

    c = run(call())
    assert said(c, 2) == [PICKUP_Q]
    assert finals(c) == [(1, "메도빅 하나요"), (2, "아니요")]  # 그 말로 새 턴을 열지 않는다
    assert nlu.calls == ["메도빅 하나요"]  # 엔진은 한 번만
    turn2 = records(c)[2]
    assert turn2["gate"]["result"] == "backchannel"
    assert [(d["text"], d["reason"]) for d in turn2["dropped"]] == [(word, "gate")]


def test_the_gate_gives_up_at_its_cap_when_the_sound_never_ends(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요"])

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, tts=slow(PICKUP_Q, seconds=0.1),
                        settings=on(gate_max_s=0.4)) as c:
            await c.start()
            await c.turn(1)
            await c.say()
            await c.out.wait_for("stt.final", turn=2)
            began = time.monotonic()
            await c.feed(tone(300))  # 끊기지 않는 배경 소리. 글도 말 끝도 없다
            await c.out.wait_for("bot.done", turn=2)
            return c, time.monotonic() - began

    c, waited = run(call())
    assert 0.35 <= waited < 1.0
    assert said(c, 2) == [PICKUP_Q]
    c_records = records(c)
    assert c_records[2]["gate"]["result"] == "timeout"


# ------------------------------------------------------------ 되감지 않는 턴


def test_a_confirmed_order_is_never_rewound(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요", "6시 반이요", "네", "아니 잠깐만"])

    def delays(text: str) -> float:
        return 0.6 if "준비해 둘게요" in text else 0.0

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=FakeTts(delays=delays), settings=on()) as c:
            await c.start()
            for turn in (1, 2, 3):
                await c.turn(turn)
            await c.say()
            await c.out.wait_for("order", turn=4)
            await c.say()  # 확정 뒤 첫 소리 전에 한 말
            await c.out.wait_for("bot.done", turn=4)
            await c.played(4)
            await asyncio.wait_for(c.session.wait_done(), 3)
            return c

    c = run(call())
    assert len(c.sink.orders) == 1 and c.sink.orders[0].status == "confirmed"
    assert c.out.of("turn.superseded") == []
    assert [e["turn"] for e in c.out.of("stt.final")] == [1, 2, 3, 4]
    assert said(c, 4)


def test_the_final_readback_is_never_rewound(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요", "6시 반이요", "아니요 두 개요"])

    def delays(text: str) -> float:
        return 0.6 if text.startswith("주문 확인할게요") else 0.0

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=FakeTts(delays=delays), settings=on()) as c:
            await c.start()
            for turn in (1, 2):
                await c.turn(turn)
            await speak_again_while_thinking(c, 3)  # 복창을 합성하는 중에 정정을 말했다
            await c.out.wait_for("bot.done", turn=3)
            await c.played(3)
            await c.out.wait_for("bot.done", turn=4)
            return c

    c = run(call())
    assert c.out.of("turn.superseded") == []
    assert said(c, 3)[0].startswith("주문 확인할게요")  # 복창은 그대로 나간다
    assert finals(c)[-1] == (4, "아니요 두 개요")  # 정정은 전처럼 다음 턴
    assert records(c)[4]["queued"] is True


def test_a_turn_that_asked_the_owner_is_never_rewound(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", NUTS, "블린도 하나요"])
    hold = "잠시만요, 사장님께 여쭤볼게요."

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(hold), settings=on()) as c:
            await c.start()
            await c.turn(1)
            await speak_again_while_thinking(c, 2)
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            await c.out.wait_for("stt.final", turn=3)
            return c

    c = run(call())
    assert len(c.owner.questions) == 1  # 질문 카드는 이미 사장님께 갔다
    assert c.out.of("turn.superseded") == []
    assert said(c, 2) == [hold]
    assert finals(c)[-1] == (3, "블린도 하나요")


def test_a_turn_that_passed_a_question_on_to_the_owner_is_never_rewound(tmp_path) -> None:
    stt = FakeSttStream(replies=[TASTE, "메도빅 하나요"])
    answer = "죄송해요, 그건 제가 정확히 모르겠어요."

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(answer), settings=on(),
                        knowledge=KnowledgeStore(), ask_owner=False) as c:
            await c.start()
            await speak_again_while_thinking(c, 1)
            await c.out.wait_for("bot.done", turn=1)
            await c.played(1)
            await c.out.wait_for("stt.final", turn=2)
            return c

    c = run(call())
    assert len(c.owner.knowledge) == 1  # 보충 요청은 한 번만 갔다
    assert c.out.of("turn.superseded") == []
    assert said(c, 1)[0] == answer
    assert finals(c)[-1] == (2, "메도빅 하나요")


def test_speech_during_the_greeting_synthesis_is_not_a_rewind(tmp_path) -> None:
    # 인사(턴 0)와 봇이 먼저 말하는 턴은 손님 말로 연 턴이 아니다
    stt = FakeSttStream(replies=["메도빅 하나요"])
    greeting = "네, 샘플 러시안 베이커리 AI 전화 주문입니다."

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(greeting), settings=on()) as c:
            await c.session.feed_event({"type": "call.start", "turn": 0})
            await c.out.wait_for("call.ready")
            await c.say()
            await c.out.wait_for("bot.done", turn=0)
            await c.played(0)
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    assert c.out.of("turn.superseded") == []
    assert said(c, 0)[0] == greeting
    assert finals(c) == [(1, "메도빅 하나요")]


# ------------------------------------------------------------ 첫 소리 뒤·효과음 뒤는 그대로


def test_speech_after_the_first_sound_is_dropped_as_before(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=on()) as c:
            await c.start()
            await c.turn(1)
            await c.say()
            await c.out.wait_for("bot.done", turn=2)  # 봇 소리가 나가는 중
            await c.say()
            await asyncio.sleep(0.05)
            await c.played(2)
            await asyncio.sleep(0.05)
            return c

    c = run(call())
    assert finals(c) == [(1, "메도빅 하나요"), (2, "아니요")]
    assert said(c, 2) == [PICKUP_Q]
    assert c.out.of("turn.superseded") == []
    turn2 = records(c)[2]
    assert [d["reason"] for d in turn2["dropped"]] == ["speaking"]
    assert "gate" not in turn2


def test_speech_that_starts_after_the_thinking_cue_does_not_arm_the_gate(tmp_path) -> None:
    # 효과음이 스피커로 나가는 동안 마이크가 열려 있다. 그 소리가 말 구간으로 잡혀 문지기를
    # 헛걸리게 하지 않도록, 효과음 신호를 보낸 뒤에 시작한 소리는 전처럼 다음 턴으로 둔다
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(PICKUP_Q, seconds=0.8),
                        settings=on(thinking_after_s=0.05)) as c:
            await c.start()
            await c.turn(1)
            await c.say()
            await c.out.wait_for("bot.thinking", turn=2)
            await c.say()
            await c.out.wait_for("bot.done", turn=2)
            await c.played(2)
            await c.out.wait_for("bot.done", turn=3)
            return c

    c = run(call())
    assert c.out.of("turn.superseded") == []
    assert said(c, 2) == [PICKUP_Q]
    assert finals(c)[-1] == (3, "그게 다예요")
    assert "gate" not in records(c)[2]


def test_the_thinking_cue_is_not_played_over_the_customer_who_is_still_talking(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요", "그게 다예요"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(PICKUP_Q, seconds=0.5),
                        settings=on(thinking_after_s=0.2)) as c:
            await c.start()
            await c.turn(1)
            await c.say()
            await c.out.wait_for("stt.final", turn=2)
            await c.feed(tone(300))  # 효과음 신호 전에 다시 말하기 시작했다
            await asyncio.sleep(0.4)  # 효과음을 낼 때가 지났다
            await c.feed(tone(300) + silence(1_200))
            await c.out.wait_for("bot.done", turn=3)
            return c

    c = run(call())
    assert c.out.of("bot.thinking", 2) == []
    assert finals(c)[-1] == (3, "아니요 그게 다예요")


def test_device_1116_a_time_said_right_after_okay_joins_it(tmp_path) -> None:
    # 11/16 실기기(아이폰): "몇 시쯤 오세요?"에 "오케이" … "6시". 꺼져 있을 때는 "오케이"에 되묻고
    # "6시"는 다음 턴이 됐다. "6시"는 앞 턴 첫 소리 0.3초 전에 시작했다. 켜면 한 턴으로 읽는다
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요", "오케이", "6시"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=slow(SORRY, PICKUP_Q), settings=on()) as c:
            await c.start()
            await c.turn(1)
            await c.turn(2)
            await speak_again_while_thinking(c, 3)  # "오케이" … "6시"
            await c.out.wait_for("bot.done", turn=4)
            await c.played(4)
            return c

    c = run(call())
    assert finals(c)[-1] == (4, "오케이 6시")
    assert said(c, 3) == []
    assert SORRY not in said(c, 4)
    assert c.out.of("state")[-1]["dialog"] == "confirm"
