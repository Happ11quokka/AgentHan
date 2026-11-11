"""TTS·STT 일시 오류 (6단계): 한 번 다시 부르기, 소리 없이 자막으로 넘기기, 연결 같이 쓰기."""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import grpc
import httpx
import pytest
from fake_nest import KEY, FakeNest, NestServer

from agenthan.voice import adapters
from agenthan.voice.call.ports import PortError
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.stt.clova import ClovaSttSettings, ClovaSttStream
from agenthan.voice.tts_clova import ClovaVoiceTts, QuietFallbackTts

PCM = bytes(range(256)) * 10


def run(coro):
    return asyncio.run(coro)


def wav(pcm: bytes) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def tts(replies: list[Any], sleeps: list[float], **kw: Any) -> ClovaVoiceTts:
    queue = iter(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        reply = next(queue)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ClovaVoiceTts("id", "secret", http=http, sleep=sleep, **kw)


OK = httpx.Response(200, content=wav(PCM))


# ---------------------------------------------------------------- TTS 재시도

@pytest.mark.parametrize("first", [
    httpx.Response(429), httpx.Response(502), httpx.ReadTimeout("slow"),
    httpx.Response(500, content=b'{"error":{"errorCode":"VS99"}}'),
], ids=["429", "502", "timeout", "vs99"])
def test_a_passing_tts_error_is_tried_once_more_after_a_short_wait(first) -> None:
    sleeps: list[float] = []
    assert run(tts([first, OK], sleeps, retries=1).synth("네.")) == PCM
    assert len(sleeps) == 1 and 0.3 <= sleeps[0] <= 0.8


def test_tts_gives_up_after_one_more_try() -> None:
    sleeps: list[float] = []
    with pytest.raises(PortError) as err:
        run(tts([httpx.Response(429), httpx.Response(429)], sleeps, retries=1).synth("네."))
    assert (err.value.code, len(sleeps)) == ("tts_429", 1)


def test_errors_that_will_not_pass_are_not_retried() -> None:
    sleeps: list[float] = []
    with pytest.raises(PortError) as err:
        run(tts([httpx.Response(401), OK], sleeps, retries=1).synth("네."))
    assert (err.value.code, sleeps) == ("tts_auth", [])


def test_without_retries_tts_fails_at_once_as_before() -> None:
    sleeps: list[float] = []
    with pytest.raises(PortError):
        run(tts([httpx.Response(429), OK], sleeps).synth("네."))
    assert sleeps == []


# ---------------------------------------------------------------- 소리 없이 자막으로

class Flaky:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.closed = False

    async def synth(self, text: str) -> bytes:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        self.closed = True


BUSY = PortError("한도", code="tts_429")


def test_one_failed_sentence_becomes_silence_so_the_call_goes_on_with_captions() -> None:
    quiet = QuietFallbackTts(Flaky([PCM, BUSY, PCM]))

    async def go() -> list[bytes]:
        return [await quiet.synth("네."), await quiet.synth("메도빅 하나 맞으세요?"),
                await quiet.synth("네.")]

    first, silent, third = run(go())
    assert first == third == PCM
    assert silent and set(silent) == {0} and len(silent) % 2 == 0
    # 글을 읽을 시간만큼 조용히 둔다 (글자 수에 맞춰, 상한 있음)
    assert 500 <= len(silent) // 32 <= 4_000


def test_a_second_failure_in_a_row_ends_the_call_politely_as_before() -> None:
    quiet = QuietFallbackTts(Flaky([BUSY, BUSY]))

    async def go() -> None:
        await quiet.synth("네.")
        await quiet.synth("네.")

    with pytest.raises(PortError) as err:
        run(go())
    assert err.value.code == "tts_429"


def test_a_config_error_is_never_hidden_and_close_reaches_the_inner_tts() -> None:
    inner = Flaky([PortError("인증", code="tts_auth", retry=False)])
    quiet = QuietFallbackTts(inner)
    with pytest.raises(PortError):
        run(quiet.synth("네."))
    run(quiet.aclose())
    assert inner.closed


# ---------------------------------------------------------------- 연결 같이 쓰기

def _clova_settings(**env: str) -> VoiceSettings:
    return VoiceSettings.from_env({"PHONE_TTS": "clova", "CLOVA_VOICE_CLIENT_ID": "id",
                                   "CLOVA_VOICE_CLIENT_SECRET": "secret", **env}, env_file=None)


def test_every_call_shares_one_tts_connection_pool_that_closes_with_the_server() -> None:
    async def go() -> tuple[bool, bool, bool, float | None]:
        factory = adapters.tts_factory(_clova_settings())
        one, two = factory(), factory()
        http_one, http_two = _http(one), _http(two)
        keepalive = http_one._transport._pool._keepalive_expiry  # noqa: SLF001
        await one.aclose()  # 통화가 끝나도 같이 쓰는 연결은 닫지 않는다
        open_after_call = not http_one.is_closed
        await factory.aclose()
        return http_one is http_two, open_after_call, http_one.is_closed, keepalive

    same, open_after_call, closed, keepalive = run(go())
    assert same and open_after_call and closed
    assert keepalive == 60.0


def _http(tts: Any) -> httpx.AsyncClient:
    return tts._http  # noqa: SLF001


def test_tts_retry_fallback_and_timeout_come_from_the_environment() -> None:
    settings = VoiceSettings.from_env({}, env_file=None)
    assert (settings.tts_retries, settings.tts_fallback, settings.tts_timeout_s) == (1, True, 10.0)
    off = VoiceSettings.from_env({"PHONE_TTS_RETRIES": "0", "PHONE_TTS_FALLBACK": "off",
                                  "PHONE_TTS_TIMEOUT_S": "5"}, env_file=None)
    assert (off.tts_retries, off.tts_fallback, off.tts_timeout_s) == (0, False, 5.0)
    assert not adapters.tts_factory(_clova_settings(PHONE_TTS_FALLBACK="off"))().quiet_fallback
    assert adapters.tts_factory(_clova_settings())().quiet_fallback


def test_the_adapter_itself_passes_one_failed_sentence_as_silence_when_asked() -> None:
    sleeps: list[float] = []
    busy = httpx.Response(429)
    quiet = tts([busy, busy, OK], sleeps, retries=1, quiet_fallback=True)

    async def go() -> tuple[bytes, bytes]:
        return await quiet.synth("메도빅 하나 맞으세요?"), await quiet.synth("네.")

    silent, spoken = run(go())
    assert set(silent) == {0} and spoken == PCM
    assert len(sleeps) == 1  # 한 번 다시 부른 뒤에야 무음으로 넘긴다


# ---------------------------------------------------------------- STT 연결 재시도

class BusyFirst(FakeNest):
    """첫 연결은 자리가 없다고 끊고, 다음부터는 받는다."""

    def __init__(self, code: grpc.StatusCode, **kw: Any) -> None:
        super().__init__(**kw)
        self._code = code
        self.attempts = 0

    async def recognize(self, request_iterator, context):
        self.attempts += 1
        if self.attempts == 1:
            await context.abort(self._code, "No more slot")
        async for response in super().recognize(request_iterator, context):
            yield response


def _stt(endpoint: str, **kw: Any) -> ClovaSttStream:
    return ClovaSttStream(ClovaSttSettings(secret_key=KEY, endpoint=endpoint,
                                           secure=False, **kw))


@pytest.mark.parametrize("code", [grpc.StatusCode.RESOURCE_EXHAUSTED,
                                  grpc.StatusCode.UNAVAILABLE])
def test_a_full_or_unavailable_stt_is_connected_once_more(code) -> None:
    async def go() -> int:
        servicer = BusyFirst(code)
        async with NestServer(servicer) as srv:
            stt = _stt(srv.endpoint, connect_retry_wait_s=0.01)
            await stt.start()
            await stt.close()
            return servicer.attempts

    assert run(go()) == 2


def test_stt_connect_gives_up_after_one_more_try_and_never_retries_a_wrong_key() -> None:
    class AlwaysBusy(FakeNest):
        attempts = 0

        async def recognize(self, request_iterator, context):
            type(self).attempts += 1
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "No more slot")
            yield  # pragma: no cover

    async def go() -> tuple[str, int, str, int]:
        async with NestServer(AlwaysBusy()) as srv:
            stt = _stt(srv.endpoint, connect_retry_wait_s=0.01)
            with pytest.raises(PortError) as busy:
                await stt.start()
            await stt.close()
        wrong = FakeNest(key="other")
        async with NestServer(wrong) as srv:
            stt = _stt(srv.endpoint, connect_retry_wait_s=0.01)
            with pytest.raises(PortError) as auth:
                await stt.start()
            await stt.close()
        return busy.value.code, AlwaysBusy.attempts, auth.value.code, len(wrong.connections)

    assert run(go()) == ("stt_busy", 2, "stt_auth", 1)
