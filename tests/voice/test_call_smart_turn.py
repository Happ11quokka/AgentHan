"""통화 세션 + 턴 모델(Smart Turn) (3단계 spec "Smart Turn", 티켓 03). 바깥 행동만 본다.

- 말 끝(무음 400ms)마다 한 번 최근 오디오로 끝남 확률을 낸다. 0.7 이상이면 끝남, 0.3 이하면 이어짐,
  그 사이는 끝말 규칙. 확실한 규칙 판정은 모델보다 앞선다
- 모델이 실패하거나 판정 때까지 결과가 없으면 규칙만 쓴다
- 모델이 보는 오디오: 통화마다 최근 8초. 봇이 말하는 동안 들어온 프레임은 넣지 않는다.
  이어짐 대기 중 다시 말하면 다음 말 끝에서 턴 전체로 다시 본다

턴 모델은 `FakeTurnModel`, 실행기는 부른 자리에서 바로 도는 `InlineExecutor`라 결정적이다.
"""

from __future__ import annotations

import asyncio
import json
import time

from call_harness import Call, InlineExecutor, StalledExecutor, silence, tone

from agenthan.voice.call.fakes import FakeSttStream, FakeTurnModel
from agenthan.voice.call.session import SessionSettings

CAP = 0.5  # 시험용 이어짐 상한(초). 기본값은 2.0


def run(coro):
    return asyncio.run(coro)


def records(c: Call) -> list[dict]:
    path = c.log_dir / "phone-20261108.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def first_turn(tmp_path, text: str, scores, *, executor=None) -> tuple[Call, float]:
    """손님이 `text`를 한 번 말한다. stt.final까지 걸린 시간(마지막 유성 프레임부터)을 돌려준다."""
    stt = FakeSttStream(replies=[text])
    model = FakeTurnModel(scores)

    async def call() -> tuple[Call, float]:
        async with Call(tmp_path, stt=stt, settings=SessionSettings(cont_wait_s=CAP),
                        turn_model=model, turn_executor=executor or InlineExecutor()) as c:
            await c.start()
            await c.feed(tone(800))
            spoke = time.monotonic()
            await c.feed(silence(1_200))
            await c.out.wait_for("stt.final", turn=1)
            waited = time.monotonic() - spoke
            await c.out.wait_for("bot.done", turn=1)
            return c, waited

    c, waited = run(call())
    c.model = model  # type: ignore[attr-defined]
    return c, waited


def test_a_high_score_ends_a_turn_the_rules_would_have_held(tmp_path) -> None:
    c, waited = first_turn(tmp_path, "메도빅 두 개랑", [0.9])
    assert waited < CAP - 0.1  # 이어짐 상한까지 기다리지 않았다
    turn1 = records(c)[1]
    assert turn1["turn_end"]["label"] == "end"
    assert (turn1["turn_end"]["path"], turn1["turn_end"]["score"]) == ("model", 0.9)
    assert turn1["turn_model"]["src"] == "model"


def test_a_low_score_holds_a_turn_the_rules_would_have_ended(tmp_path) -> None:
    c, waited = first_turn(tmp_path, "포장이고요", [0.1])  # 규칙은 끝남(애매)
    assert waited >= CAP - 0.05
    turn1 = records(c)[1]
    assert (turn1["turn_end"]["path"], turn1["turn_end"]["reason"]) == ("cap", "cap:model<=0.3")
    assert turn1["turn_end"]["score"] == 0.1


def test_a_middle_score_leaves_the_decision_to_the_rules(tmp_path) -> None:
    c, waited = first_turn(tmp_path, "메도빅 두 개랑", [0.5])
    assert waited >= CAP - 0.05  # 규칙(연결어미 -랑)대로 이어짐
    turn1 = records(c)[1]
    assert turn1["turn_end"]["reason"] == "cap:connective:랑"
    assert turn1["turn_end"]["score"] == 0.5


def test_a_firm_rule_ending_beats_a_low_score(tmp_path) -> None:
    c, waited = first_turn(tmp_path, "메도빅 하나요", [0.05])
    assert waited < CAP - 0.1
    turn1 = records(c)[1]
    assert (turn1["turn_end"]["path"], turn1["turn_end"]["reason"]) == ("rule", "final_ending")


def test_a_failing_model_falls_back_to_the_rules(tmp_path) -> None:
    c, _ = first_turn(tmp_path, "메도빅 두 개랑", [RuntimeError("onnx 실패")])
    turn1 = records(c)[1]
    assert turn1["turn_end"]["score"] is None
    assert turn1["turn_end"]["reason"] == "cap:connective:랑"
    assert turn1["turn_model"]["src"] == "error"


def test_a_model_that_has_not_answered_by_the_decision_is_ignored(tmp_path) -> None:
    c, waited = first_turn(tmp_path, "메도빅 하나", [0.05], executor=StalledExecutor())
    assert waited < CAP - 0.1  # 규칙(기본 끝남)대로 바로 넘겼다
    turn1 = records(c)[1]
    assert (turn1["turn_end"]["path"], turn1["turn_end"]["score"]) == ("rule", None)
    assert turn1["turn_model"]["src"] == "late"


def test_the_model_runs_once_per_speech_end_on_the_audio_of_this_turn(tmp_path) -> None:
    c, _ = first_turn(tmp_path, "메도빅 하나요", [0.9])
    # 말 800ms + 말 끝 무음 400ms. 무음 프레임마다 다시 돌지 않는다
    assert c.model.heard_ms == [1_200]  # type: ignore[attr-defined]


def test_without_a_turn_model_the_rules_decide_alone(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt) as c:
            await c.start()
            await c.turn(1)
            return c

    turn1 = records(run(call()))[1]
    assert (turn1["turn_end"]["path"], turn1["turn_end"]["score"]) == ("rule", None)
    assert "turn_model" not in turn1


def test_the_model_keeps_only_the_last_eight_seconds(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요"])
    model = FakeTurnModel([0.9])

    async def call() -> None:
        async with Call(tmp_path, stt=stt, turn_model=model,
                        turn_executor=InlineExecutor()) as c:
            await c.start()
            await c.feed(tone(9_000) + silence(1_200))
            await c.out.wait_for("bot.done", turn=1)

    run(call())
    assert model.heard_ms == [8_000]


def test_frames_that_arrive_while_the_bot_speaks_are_left_out(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 하나요", "그게 다예요"])
    model = FakeTurnModel([0.9])

    async def call() -> None:
        async with Call(tmp_path, stt=stt, turn_model=model,
                        turn_executor=InlineExecutor()) as c:
            await c.start()
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(silence(3_000))  # 봇이 말하는 중(반이중 0 프레임)
            await c.played(1)
            await c.turn(2)

    run(call())
    first, second = model.heard_ms
    assert first == 1_200
    # 앞 턴을 넘긴 뒤 남은 무음 일부 + 이번 말 1.2초. 봇이 말하는 동안의 3초는 없다
    assert 1_200 <= second < 1_200 + 1_000


def test_renewed_speech_during_the_wait_is_judged_on_the_whole_turn(tmp_path) -> None:
    stt = FakeSttStream(replies=["메도빅 두 개랑", "아메리카노 하나"])
    model = FakeTurnModel([0.5, 0.9])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=SessionSettings(cont_wait_s=CAP),
                        turn_model=model, turn_executor=InlineExecutor()) as c:
            await c.start()
            await c.say()  # 규칙 이어짐(-랑), 점수 0.5 → 기다림
            await c.say()  # 상한 안에 다시 말함
            await c.out.wait_for("bot.done", turn=1)
            return c

    c = run(call())
    first, second = model.heard_ms
    assert first == 1_200 and second >= first + 1_200  # 앞말까지 같이 본다
    turn1 = records(c)[1]
    assert turn1["text"] == "메도빅 두 개랑 아메리카노 하나"
    assert (turn1["turn_end"]["path"], turn1["turn_end"]["score"]) == ("model", 0.9)
    assert [s["score"] for s in turn1["turn_end_steps"]] == [0.5]
