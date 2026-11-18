"""HCX 한도 대응 (6단계): 짧은 재시도, 규칙 NLU 폴백, 분당 버킷, 미리 해석 건너뛰기, usage 기록.

키 없이 돈다. HTTP는 `httpx.MockTransport`로 흉내 낸다(녹화본을 쓰지 않는다).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice.dialog.answerer import AnswerRequest
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.budget import RateBudget, UsageLog
from agenthan.voice.nlu.clova import ClovaClient, ClovaError
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.nlu.hcx import HcxNlu
from agenthan.voice.nlu.hcx_answer import HcxAnswerer
from agenthan.voice.nlu.prefetch import PrefetchNlu
from agenthan.voice.runtime import HcxRuntime
from agenthan.voice.settings import SettingsError, VoiceSettings
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.ws import WS_PATH, phone_router

KEY = "test-key-not-real"
CTX = NluContext(menu_names=("가", "나"), cart_summary="", last_question="", state="ORDERING")
USAGE = {"promptTokens": 900, "completionTokens": 40, "totalTokens": 940}


def ok(content: str = '{"acts":[{"type":"done_ordering"}]}', usage: Any = USAGE) -> httpx.Response:
    result: dict[str, Any] = {"message": {"role": "assistant", "content": content}}
    if usage is not None:
        result["usage"] = usage
    return httpx.Response(200, json={"status": {"code": "20000"}, "result": result},
                          headers={"x-ratelimit-remaining-requests": "88",
                                   "x-ratelimit-remaining-tokens": "70000"})


def client(handler, sleeps: list[float] | None = None, **kw: Any) -> ClovaClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ClovaClient(KEY, http=http, sleep=(sleeps if sleeps is not None else []).append, **kw)


class Minute:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- 재시도

def test_the_call_client_retries_a_429_once_after_a_short_wait() -> None:
    replies = iter([httpx.Response(429), ok()])
    sleeps: list[float] = []
    result = client(lambda r: next(replies), sleeps, max_retries=1, backoff_s=0.4).chat("m", {})
    assert result["usage"] == USAGE
    assert sleeps == [0.4]


def test_the_call_client_gives_up_after_one_retry() -> None:
    sleeps: list[float] = []
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(503)

    with pytest.raises(ClovaError) as err:
        client(handler, sleeps, max_retries=1, backoff_s=0.4).chat("m", {})
    assert (err.value.code, len(seen), sleeps) == ("http-503", 2, [0.4])


# ---------------------------------------------------------------- 폴백

def test_when_hcx_fails_the_turn_falls_back_to_the_rule_nlu() -> None:
    nlu = HcxNlu(client(lambda r: httpx.Response(429), max_retries=0), fallback=FakeNlu())
    assert [a.type for a in nlu.parse("네", CTX)] == ["yes"]
    # 규칙도 모르는 말이면 unknown: 엔진이 정중히 되묻는다(통화는 이어진다)
    assert nlu.parse("으으음 그 뭐냐", CTX)[0].type == "unknown"


def test_a_network_error_also_falls_back() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    nlu = HcxNlu(client(down), fallback=FakeNlu())
    assert [a.type for a in nlu.parse("네", CTX)] == ["yes"]


def test_without_a_fallback_a_failure_is_unknown_as_before() -> None:
    nlu = HcxNlu(client(lambda r: httpx.Response(500), max_retries=0))
    assert nlu.parse("네", CTX) == [Act("unknown", free_text="네")]


# ---------------------------------------------------------------- maxTokens

def test_max_tokens_stays_the_recorded_value_unless_set() -> None:
    # 요청 본문이 바뀌면 정답셋 녹화본(cassette)이 맞지 않는다. 기본값은 그대로 둔다
    assert HcxNlu(ClovaClient(KEY), task_id="t1").payload("네", CTX)["maxTokens"] == 512
    assert HcxNlu(ClovaClient(KEY)).payload("네", CTX)["maxTokens"] == 1024
    tuned = HcxNlu(ClovaClient(KEY), task_id="t1", max_tokens=200)
    assert tuned.payload("네", CTX)["maxTokens"] == 200


# ---------------------------------------------------------------- 분당 버킷

def test_the_budget_counts_requests_in_the_last_minute() -> None:
    now = Minute()
    budget = RateBudget(rpm=3, clock=now)
    assert [budget.take() for _ in range(4)] == [True, True, True, False]
    now.t = 61
    assert budget.take()


def test_the_budget_counts_tokens_as_prompt_plus_max_tokens() -> None:
    now = Minute()
    budget = RateBudget(tpm=3_000, clock=now)
    assert budget.take(tokens=512)  # 아직 프롬프트 크기를 모른다
    budget.settle(prompt_tokens=900, max_tokens=512)  # 실제로는 1,412
    assert budget.take(tokens=512)  # 다음 요청도 1,412로 어림 → 2,824
    assert not budget.take(tokens=512)
    assert budget.used() == {"requests": 2, "tokens": 2_824}


def test_no_limit_means_no_counting() -> None:
    budget = RateBudget()
    assert all(budget.take() for _ in range(1_000)) and budget.roomy()


def test_prefetch_is_only_roomy_below_the_share() -> None:
    budget = RateBudget(rpm=10, clock=Minute())
    for _ in range(5):
        assert budget.roomy() and budget.take()
    for _ in range(5):
        assert not budget.roomy()  # 반 넘게 썼으면 버려질 수 있는 호출은 하지 않는다
        assert budget.take()


def test_over_the_budget_the_turn_goes_to_the_rule_nlu_without_calling_hcx() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return ok()

    nlu = HcxNlu(client(handler), fallback=FakeNlu(), budget=RateBudget(rpm=1, clock=Minute()))
    assert [a.type for a in nlu.parse("그게 다예요", CTX)] == ["done_ordering"]
    assert [a.type for a in nlu.parse("네", CTX)] == ["yes"]
    assert len(calls) == 1


def test_prefetch_is_skipped_when_the_budget_is_tight() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["messages"][-1]["content"])
        return ok()

    budget = RateBudget(rpm=2, clock=Minute())
    wrapped = PrefetchNlu(HcxNlu(client(handler), budget=budget), timeout_s=5.0)

    def run(fn: Any) -> None:
        fn()

    wrapped.prefetch("가 하나요", CTX, run)
    assert len(calls) == 1  # 여유가 있을 때는 미리 부른다
    wrapped.parse("가 하나요", CTX)
    wrapped.take_stats()
    wrapped.prefetch("나 하나요", CTX, run)  # 반을 썼다: 건너뛴다
    assert len(calls) == 1
    assert wrapped.take_stats() == {"prefetched": 0, "used": False, "discarded": 0,
                                    "miss": None, "wait_ms": None, "skipped": 1}
    assert [a.type for a in wrapped.parse("나 하나요", CTX)] == ["done_ordering"]
    assert len(calls) == 2


# ---------------------------------------------------------------- usage 기록

def _lines(tmp_path) -> list[dict[str, Any]]:
    files = sorted(tmp_path.glob("hcx-*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text().splitlines()]


def test_usage_is_written_to_a_jsonl_without_the_customer_words(tmp_path) -> None:
    usage = UsageLog(tmp_path, today=lambda: "20261112")
    nlu = HcxNlu(client(lambda r: ok(), observe=usage.observe), task_id="t1", usage=usage)
    nlu.parse("메도빅 하나 주세요", CTX)
    (line,) = _lines(tmp_path)
    assert (tmp_path / "hcx-20261112.jsonl").is_file()
    assert line["kind"] == "nlu" and line["status"] == "ok"
    assert (line["prompt"], line["completion"], line["max_tokens"]) == (900, 40, 512)
    assert (line["left_requests"], line["left_tokens"]) == (88, 70_000)
    assert "메도빅" not in json.dumps(line, ensure_ascii=False)


def test_failures_and_fallbacks_are_written_too(tmp_path) -> None:
    usage = UsageLog(tmp_path, today=lambda: "20261112")
    nlu = HcxNlu(client(lambda r: httpx.Response(429), max_retries=0), fallback=FakeNlu(),
                 usage=usage, budget=RateBudget(rpm=1, clock=Minute()))
    nlu.parse("네", CTX)
    nlu.parse("네", CTX)
    assert [(e["status"], e["fallback"]) for e in _lines(tmp_path)] == [
        ("http-429", True), ("over_budget", True)]


def test_the_answerer_records_its_usage(tmp_path) -> None:
    usage = UsageLog(tmp_path, today=lambda: "20261112")
    reply = "종류: 모름\n메뉴: 없음\n근거: 없음\n답: 없음"
    answerer = HcxAnswerer(client(lambda r: ok(reply)), usage=usage)
    assert answerer.answer(AnswerRequest("주차 돼요?", ("가게: 예시",))).kind == "unsure"
    (line,) = _lines(tmp_path)
    assert (line["kind"], line["prompt"], line["max_tokens"]) == ("answer", 900, 256)


def test_a_usage_log_that_cannot_be_written_does_not_break_the_turn(tmp_path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("")
    nlu = HcxNlu(client(lambda r: ok()), usage=UsageLog(blocked / "logs"))
    assert [a.type for a in nlu.parse("그게 다예요", CTX)] == ["done_ordering"]


# ---------------------------------------------------------------- 프로세스 공유

def _settings(**env: str) -> VoiceSettings:
    return VoiceSettings.from_env({"PHONE_NLU": "hcx", "CLOVASTUDIO_API_KEY": KEY, **env},
                                  env_file=None)


def test_the_runtime_builds_one_nlu_and_answerer_for_every_call(tmp_path) -> None:
    runtime = HcxRuntime(_settings(PHONE_LOG_DIR=str(tmp_path), PHONE_NLU_TASK_ID="t9",
                                   PHONE_NLU_MAX_TOKENS="200", PHONE_HCX_RPM="70"))
    nlu = runtime.nlu()
    assert nlu is runtime.nlu() and runtime.answerer() is runtime.answerer()
    assert isinstance(nlu, HcxNlu)
    assert nlu.payload("네", CTX)["maxTokens"] == 200
    assert runtime.ready() == {"nlu": True, "answerer": True}
    runtime.close()


def test_the_runtime_has_no_answerer_when_it_is_switched_off() -> None:
    runtime = HcxRuntime(_settings(PHONE_ANSWER="off"))
    assert runtime.answerer() is None


def _call_events(app: FastAPI, turns: int) -> list[dict[str, Any]]:
    """가짜 STT 대본(메도빅 하나요 → 그게 다예요 → 6시 반이요 → 네)으로 통화 하나를 돌린다."""
    events: list[dict[str, Any]] = []
    with TestClient(app).websocket_connect(WS_PATH) as ws:
        def until(type_: str, turn: int) -> None:
            for _ in range(500):
                message = ws.receive()
                if message["type"] == "websocket.close":
                    raise AssertionError(f"{type_}를 기다리는데 닫힘: {events[-3:]}")
                if message.get("text") is None:
                    continue
                event = json.loads(message["text"])
                events.append(event)
                if event["type"] == "error" or (event["type"], event["turn"]) == (type_, turn):
                    return
            raise AssertionError(f"{type_}({turn})가 오지 않음")

        def send(**event: Any) -> None:
            ws.send_text(json.dumps(event, ensure_ascii=False))

        send(type="call.start", turn=0)
        until("call.ready", 0)
        for turn in range(turns + 1):
            if events[-1]["type"] == "error":
                break
            until("bot.done", turn)
            if events[-1]["type"] == "error" or turn == turns:
                break
            send(type="playback.started", turn=turn, seq=0)
            send(type="playback.done", turn=turn, seq=events[-1]["sentences"] - 1)
            send(type="user.end_of_speech", turn=turn)
            for _ in range(10):
                ws.send_bytes(bytes(3_200))
            until("stt.final", turn + 1)
    return events


def _hcx_app(tmp_path, transport: httpx.BaseTransport | None, **env: str) -> FastAPI:
    settings = VoiceSettings.from_env(
        {"PHONE_NLU": "hcx", "PHONE_LOG_DIR": str(tmp_path / "logs"),
         "PHONE_NOW": "2026-11-08T16:00:00+09:00", "PHONE_ANSWER": "off",
         "PHONE_TURN_MODEL": "off", **env}, env_file=None)
    app = FastAPI()
    app.include_router(phone_router(
        store=FakeStoreKnowledge.from_fixture(), config=StoreConfig.from_fixture(),
        order_sink=MemoryOrderSink(), settings=settings,
        hcx=HcxRuntime(settings, transport=transport) if transport is not None else None,
    ))
    return app


def test_a_call_still_reaches_the_order_when_hcx_answers_429_every_time(tmp_path) -> None:
    calls: list[int] = []

    def limited(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "0"})

    app = _hcx_app(tmp_path, httpx.MockTransport(limited), CLOVASTUDIO_API_KEY=KEY)
    events = _call_events(app, turns=4)
    assert not [e for e in events if e["type"] == "error"]
    assert [e["type"] for e in events if e["type"] == "order"] == ["order"]
    assert calls  # HCX를 부르긴 했다(한 번 다시 부른 뒤 규칙 NLU로)
    lines = _lines(tmp_path / "logs")
    assert lines and all(e["status"] == "http-429" and e["fallback"] for e in lines)


def test_without_the_hcx_key_the_call_ends_with_the_config_error_as_before(
        tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # 저장소 루트의 .env를 읽지 않게
    events = _call_events(_hcx_app(tmp_path, None), turns=1)
    (error,) = [e for e in events if e["type"] == "error"]
    assert (error["code"], error["retry"]) == ("nlu_config", False)


def test_hcx_settings_defaults_keep_the_old_request_and_add_the_fallback() -> None:
    settings = VoiceSettings.from_env({}, env_file=None)
    assert (settings.nlu_max_tokens, settings.hcx_rpm, settings.hcx_tpm) == (0, 0, 0)
    assert settings.nlu_fallback is True and settings.nlu_timeout_s == 4.0
    with pytest.raises(SettingsError, match="PHONE_HCX_RPM"):
        VoiceSettings.from_env({"PHONE_HCX_RPM": "-3"}, env_file=None)
