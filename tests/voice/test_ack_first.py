"""6단계 품질: 받아들인 대답의 첫 문장을 고정 "네."로 (지연 개선, 기본 끔).

CLOVA Voice는 같은 문장을 다시 부르면 첫 바이트가 약 0.3초 → 0.03초다(서버 캐시). 첫 문장이
"메도빅 두 개요."처럼 매번 바뀌는 대답은 그 이득을 못 본다. `PHONE_ACK_FIRST=on`이면 그런 대답
앞에 "네."를 따로 둔다. 짧은 조각의 억양을 사람이 들어 보고 켠다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from call_harness import Call

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeSttStream
from agenthan.voice.call.session import SessionSettings
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.settings import SettingsError, VoiceSettings
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)


def make_engine() -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="o-test")
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


def test_ack_first_puts_a_fixed_short_sentence_in_front() -> None:
    assert templates.ack_first(["메도빅 하나요. 더 필요하신 거 있으세요?"]) == [
        "네. 메도빅 하나요. 더 필요하신 거 있으세요?"]
    # 이미 "네,"로 시작하면 쉼표만 마침표로 바꾼다(문장 나누기는 마침표 뒤에서만 자른다)
    assert templates.ack_first(["네, 6시 반까지 준비해 둘게요. 감사합니다."]) == [
        "네. 6시 반까지 준비해 둘게요. 감사합니다."]
    assert templates.ack_first(["메도빅은 빼 드렸어요.", "더 필요하신 거 있으세요?"]) == [
        "네. 메도빅은 빼 드렸어요.", "더 필요하신 거 있으세요?"]
    assert templates.ack_first([]) == []


def test_engine_marks_turns_that_open_with_an_acceptance() -> None:
    engine = make_engine()
    assert talk(engine, "메도빅 하나요").ack  # "메도빅 하나요. 더 필요하신 거 있으세요?"
    assert talk(engine, "나폴레옹 하나요").ack
    assert talk(engine, "나폴레옹 빼 주세요").ack  # "나폴레옹은 빼 드렸어요."
    assert not talk(engine, "그게 다예요").ack  # "몇 시쯤 오세요?"
    assert not talk(engine, "6시 반이요").ack  # 복창은 이미 고정 문장으로 연다
    assert talk(engine, "아니요, 두 개요").ack  # "메도빅 두 개로 바꿔 드렸어요." + 복창
    assert talk(engine, "네").ack  # "네, 6시 반까지 준비해 둘게요. 감사합니다."


def test_turns_that_open_with_something_else_are_not_marked() -> None:
    engine = make_engine()
    assert not talk(engine, "음 그러니까").ack  # 죄송해요
    assert not talk(engine, "짜장면 하나요").ack  # 없는 메뉴
    assert not talk(engine, "메도빅 12개 주세요").ack  # 되묻기
    assert not engine.no_input(NOW).ack
    assert not make_engine().handle("잠깐만요", NOW, wait_request=True).ack


def said(c: Call, turn: int) -> list[str]:
    return [e["text"] for e in c.out.of("bot.say", turn)]


def run_call(tmp_path, settings: SessionSettings) -> Call:
    stt = FakeSttStream(replies=["메도빅 하나요", "아니요 그게 다예요", "6시 반이요", "네"])

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, settings=settings) as c:
            await c.start()
            for turn in (1, 2, 3, 4):
                await c.turn(turn)
            await c.session.wait_done()
            return c

    return asyncio.run(call())


def test_session_speaks_the_ack_as_its_own_first_sentence_when_on(tmp_path) -> None:
    c = run_call(tmp_path, SessionSettings(ack_first=True))
    assert said(c, 1) == ["네.", "메도빅 하나요.", "더 필요하신 거 있으세요?"]
    assert said(c, 2) == ["몇 시쯤 오세요?"]
    assert said(c, 3)[0] == "주문 확인할게요."
    assert said(c, 4) == ["네.", "6시 반까지 준비해 둘게요.", "감사합니다."]
    # 주문서의 대화 기록은 엔진이 만든 대사 그대로다
    (order,) = c.sink.orders
    assert "AI: 메도빅 하나요. 더 필요하신 거 있으세요?" in order.transcript


def test_session_leaves_the_lines_alone_by_default(tmp_path) -> None:
    c = run_call(tmp_path, SessionSettings())
    assert said(c, 1) == ["메도빅 하나요.", "더 필요하신 거 있으세요?"]
    assert said(c, 4) == ["네, 6시 반까지 준비해 둘게요.", "감사합니다."]


def test_ack_first_is_off_by_default_and_turned_on_by_name() -> None:
    default = VoiceSettings.from_env({}, env_file=None)
    assert default.ack_first is False
    assert default.session_settings().ack_first is False
    on = VoiceSettings.from_env({"PHONE_ACK_FIRST": "on"}, env_file=None)
    assert on.session_settings().ack_first is True
    try:
        VoiceSettings.from_env({"PHONE_ACK_FIRST": "true"}, env_file=None)
    except SettingsError as err:
        assert "PHONE_ACK_FIRST" in str(err)
    else:
        raise AssertionError("틀린 값이 조용히 꺼짐이 되면 안 된다")
