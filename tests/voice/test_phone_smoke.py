"""6단계 품질: 배포 직후 스모크 (`python -m agenthan.voice.smoke`).

S1은 가게 데이터로 만든 대본을 엔진에 넣어 최종 Order를 보고, S2는 가짜 휴대폰으로 서버 WS에
통화를 건다. 여기서는 키 없이 도는 가짜 모드(가짜 NLU, 같은 프로세스의 가짜 STT·TTS 서버)로 본다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.smoke import calls, scripts
from agenthan.voice.smoke.__main__ import main, summary_line
from agenthan.voice.store_config import KST, StoreConfig
from agenthan.voice.ws import WS_PATH, phone_router

PHO = Path(__file__).parents[2] / "fixtures" / "phone" / "sample_pho_store.json"
ORDER_REPLIES = ("메도빅 하나요", "아니요 그게 다예요", "지금 바로 갈게요", "네")
PACE = 0.03


def sample() -> tuple[FakeStoreKnowledge, StoreConfig]:
    return FakeStoreKnowledge.from_fixture(), StoreConfig.from_fixture()


# ------------------------------------------------ S1: 대본 만들기


def test_scripts_are_built_from_the_store_menu_not_from_names_in_code() -> None:
    store, config = sample()
    now = scripts.script_now(config, datetime(2026, 11, 12, 3, 0, tzinfo=KST))
    built = scripts.build_scripts(store.list_menu(), config, now)
    assert [s.id for s in built] == [
        "single", "two-items", "change-qty", "remove-item", "required-option", "big-qty",
        "asap", "cancel", "handoff"]
    single = built[0]
    assert single.says == ("메도빅 하나 주세요", "아니요, 그게 다예요", "오후 1시에 갈게요", "네")
    assert single.expect == scripts.Expected(
        "confirmed", (("m01", 1, ()),), datetime(2026, 11, 12, 13, 0, tzinfo=KST))
    assert built[4].says[:2] == ("아메리카노 한 잔 주세요", "따뜻하게요")
    assert built[4].expect.lines == (("m03", 1, ("따뜻하게",)),)
    assert built[5].says[:2] == ("메도빅 12개 주세요", "아니요, 두 개요")
    assert built[7].expect is None  # 취소: 주문서 없음


def test_scripts_follow_another_store() -> None:
    store = FakeStoreKnowledge.from_fixture(PHO)
    config = StoreConfig.from_fixture(PHO)
    now = scripts.script_now(config, datetime(2026, 11, 12, 3, 0, tzinfo=KST))
    built = scripts.build_scripts(store.list_menu(), config, now)
    assert len(built) == 9
    text = " ".join(say for s in built for say in s.says)
    assert "소고기 쌀국수" in text and "메도빅" not in text
    # 기본값이 있는 꼭 고를 것은 묻지 않고 주문서에 그 값이 들어간다
    assert built[0].expect.lines == (("p01", 1, ("보통",)),)
    assert built[4].says[:2] == ("연유 커피 한 잔 주세요", "따뜻하게요")
    # 11:00에 여는 가게: 대본 시각 11:30, 픽업은 12시대를 피한다
    assert now.hour == 11 and built[0].expect.pickup.hour == 13


def test_no_orderable_menu_builds_nothing() -> None:
    store, config = sample()
    menu = [replace(m, sold_out=True) for m in store.list_menu()]
    assert scripts.build_scripts(menu, config, datetime(2026, 11, 12, 10, 30, tzinfo=KST)) == []


# ------------------------------------------------ S1: 돌리기


def run_s1(nlu: Any, fixture: Path | None = None, **kw: Any) -> scripts.S1Report:
    store = (FakeStoreKnowledge.from_fixture(fixture) if fixture
             else FakeStoreKnowledge.from_fixture())
    config = StoreConfig.from_fixture(fixture)
    return scripts.run_s1(store, config, nlu, today=datetime(2026, 11, 12, 3, 0, tzinfo=KST),
                          min_gap_s=0, **kw)


def test_every_script_passes_with_the_fake_nlu_and_stays_inside_the_call_budget() -> None:
    report = run_s1(FakeNlu())
    assert [(r.id, r.problems) for r in report.runs if not r.ok] == []
    assert (report.passed, report.total, report.flaky) == (9, 9, 0)
    assert report.ok
    # HCX 테스트 키 한도(분당 90요청)보다 한참 작다
    assert 15 <= report.nlu_calls <= 25


class Dumb:
    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        return [Act("unknown", free_text=text)]


def test_a_broken_nlu_fails_the_scripts_and_says_where() -> None:
    report = run_s1(Dumb())
    assert not report.ok
    failed = {r.id: r for r in report.runs if not r.ok}
    assert "single" in failed
    assert any("최종 Order" in p for p in failed["single"].problems)
    assert failed["single"].transcript  # 실패한 대본은 대화를 남긴다
    # 규칙 단계만으로 끝나는 대본은 NLU가 고장 나도 간다... 첫 주문 말은 NLU가 읽어야 한다
    assert set(failed) >= {"single", "two-items", "big-qty"}


class FlakyOnce:
    """처음 한 번만 못 알아듣는다."""

    def __init__(self) -> None:
        self._fake = FakeNlu()
        self.failed = False

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if not self.failed:
            self.failed = True
            return [Act("unknown", free_text=text)]
        return self._fake.parse(text, ctx)


def test_a_script_that_passes_on_the_second_try_is_flaky_not_failed() -> None:
    report = run_s1(FlakyOnce())
    assert report.ok and (report.passed, report.flaky) == (9, 1)
    assert [r.id for r in report.runs if r.flaky] == ["single"]


def test_nlu_calls_are_paced_under_the_rate_limit() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    paced = scripts.PacedNlu(FakeNlu(), min_gap_s=0.7, clock=lambda: now[0], sleep=sleep)
    ctx = NluContext((), "", "", "ordering")
    for _ in range(3):
        paced.parse("메도빅 하나요", ctx)
    assert paced.calls == 3
    assert slept == [0.7, 0.7]  # 첫 호출은 안 기다린다. 분당 86번을 넘지 않는다


# ------------------------------------------------ S2: 통화


@contextlib.asynccontextmanager
async def server(replies: tuple[str, ...] = ORDER_REPLIES, sink: MemoryOrderSink | None = None,
                 **settings: Any) -> AsyncIterator[str]:
    voice = VoiceSettings(nlu="fake", stt="fake", tts="fake", log_dir=None,
                          now=datetime.fromisoformat("2026-11-08T16:00:00+09:00"),
                          fake_replies=replies, **settings)
    app = FastAPI()
    app.include_router(phone_router(store=FakeStoreKnowledge.from_fixture(),
                                    config=StoreConfig.from_fixture(),
                                    order_sink=sink or MemoryOrderSink(), settings=voice))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                                        lifespan="off"))
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.01)
    port = srv.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}{WS_PATH}"
    finally:
        srv.should_exit = True
        await task


def run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, 60))


def plan(n: int = 1, *, hour: int = 16) -> list[calls.CallPlan]:
    store, config = sample()
    now = datetime(2026, 11, 8, hour, 0, tzinfo=KST)
    return calls.plan_calls(store.list_menu(), config, now, n)


def test_call_plans_come_from_the_store_and_fall_back_to_a_handoff_outside_hours() -> None:
    one, two, three = plan(3)
    assert (one.kind, one.says) == (
        "order", ("메도빅 하나 주세요", "아니요, 그게 다예요", "지금 바로 갈게요", "네"))
    assert one.expect_lines == (("m01", 1, ()),)
    assert (two.kind, two.says, two.expect_status) == (
        "handoff", ("사장님 바꿔 주세요",), "handoff")
    assert three.says[0] == "메도빅 하나랑 나폴레옹 하나 주세요"
    # 밤 11시: 주문을 끝까지 받을 수 없다. 사장님 연결 통화로 음성 경로만 본다
    (late,) = plan(1, hour=23)
    assert (late.kind, late.note) == ("handoff", "영업시간 밖이라 주문 통화 대신")


def test_a_call_against_the_fake_server_reaches_a_confirmed_order() -> None:
    async def go() -> calls.S2Report:
        async with server() as url:
            return await calls.run_s2(url, plan(1), voice=None, pace=PACE)

    report = run(go())
    assert report.ok and (report.passed, report.total) == (1, 1)
    (call,) = report.calls
    assert (call.status, call.end_reason, call.problems) == ("confirmed", "confirmed", [])
    assert call.lines == [["m01", 1]]
    assert len(call.voice_ms) == 4 and all(ms >= 0 for ms in call.voice_ms)
    assert report.voice["n"] == 4 and report.voice["p50_ms"] is not None
    assert call.routes == ["nlu", "rule", "nlu", "rule"]


def test_a_call_that_does_not_reach_an_order_fails() -> None:
    async def go() -> calls.S2Report:
        async with server(replies=("메도빅 하나요", "가나다라요")) as url:
            return await calls.run_s2(url, plan(1), voice=None, pace=PACE, retry=False,
                                      no_turn_timeout_s=3.0)

    report = run(go())
    assert not report.ok
    assert any("주문" in p for p in report.calls[0].problems)


def test_smoke_calls_carry_the_key_and_stay_out_of_the_order_sink() -> None:
    sink = MemoryOrderSink()

    async def go() -> calls.S2Report:
        async with server(sink=sink, smoke_key="s3cret") as url:
            return await calls.run_s2(url, plan(1), voice=None, pace=PACE, smoke_key="s3cret")

    report = run(go())
    assert report.ok and report.calls[0].marked
    assert sink.orders == []  # 사장님 화면·주문 집계에 섞이지 않는다


def test_without_the_key_the_call_is_an_ordinary_call() -> None:
    sink = MemoryOrderSink()

    async def go() -> calls.S2Report:
        async with server(sink=sink, smoke_key="s3cret") as url:
            return await calls.run_s2(url, plan(1), voice=None, pace=PACE, smoke_key="wrong")

    report = run(go())
    assert report.ok and not report.calls[0].marked
    assert len(sink.orders) == 1


# ------------------------------------------------ 명령


def test_the_command_prints_json_then_one_summary_line_and_exits_zero(capsys) -> None:
    code = main(["--nlu", "fake", "--skip-s2", "--min-gap-s", "0"], environ={})
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert out[-1].startswith("SMOKE PASS s1 9/9")
    report = json.loads("\n".join(out[:-1]))
    assert report["ok"] is True and report["s1"]["nlu"] == "fake"
    assert report["s2"] == {"skipped": True}


def test_the_command_exits_one_when_a_call_fails(capsys) -> None:
    # 서버가 없다: S2가 실패한다
    code = main(["--nlu", "fake", "--skip-s1", "--url", "ws://127.0.0.1:9/api/phone/ws"],
                environ={})
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert out[-1].startswith("SMOKE FAIL")


def test_hcx_without_a_key_is_a_config_error_not_a_silent_fake(capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    code = main(["--skip-s2"], environ={"PHONE_NLU": "hcx"}, env_file=None)
    assert code == 2
    assert "CLOVASTUDIO_API_KEY" in capsys.readouterr().err


def test_summary_line_names_what_ran() -> None:
    line = summary_line({"ok": True, "seconds": 12.3,
                         "s1": {"passed": 9, "total": 9, "flaky": 1, "nlu": "hcx",
                                "nlu_calls": 20},
                         "s2": {"passed": 1, "total": 1, "flaky": 0,
                                "voice": {"n": 4, "p50_ms": 1500, "p90_ms": 2100}}})
    assert line == ("SMOKE PASS s1 9/9 (flaky 1, nlu=hcx, calls 20) "
                    "s2 1/1 (flaky 0, voice p50 1500ms p90 2100ms n=4) 12.3s")
