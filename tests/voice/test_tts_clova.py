"""CLOVA Voice TTS 어댑터 (티켓 03). 네트워크 없이 httpx `MockTransport`로 본다.

어댑터는 문장 하나 → PCM 하나다. 150자 분할·동시 2개·순서·200ms 재분할은 세션 몫이라
여기서는 세션 + 어댑터 조합으로 한 번만 확인한다(`test_session_with_clova_tts_…`).
"""

from __future__ import annotations

import asyncio
import json
import struct
from urllib.parse import parse_qs

import httpx
import pytest
from call_harness import Call

from agenthan.voice.call.ports import PortError
from agenthan.voice.tts_clova import TTS_URL, ClovaVoiceTts

ID = "test-client-id"
SECRET = "test-client-secret"


def run(coro):
    return asyncio.run(coro)


def wav(pcm: bytes, *, rate: int = 16000, channels: int = 1, bits: int = 16, extra=()) -> bytes:
    """RIFF/WAVE. `extra`는 `fmt` 와 `data` 사이에 넣을 (id, 본문) 청크들."""
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    for cid, data in extra:
        body += cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
    body += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


PCM = bytes(range(256)) * 10  # 2,560바이트 = 80ms


def tts_with(handler) -> ClovaVoiceTts:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ClovaVoiceTts(ID, SECRET, http=http)


def test_synth_posts_form_with_nara_call_wav_16k_and_returns_pcm_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=wav(PCM), headers={"Content-Type": "audio/wav"})

    pcm = run(tts_with(handler).synth("네, 주문 받았어요."))

    assert pcm == PCM
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == TTS_URL == "https://naveropenapi.apigw.ntruss.com/tts-premium/v1/tts"
    assert req.headers["X-NCP-APIGW-API-KEY-ID"] == ID
    assert req.headers["X-NCP-APIGW-API-KEY"] == SECRET
    assert req.headers["Content-Type"] == "application/x-www-form-urlencoded"
    form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
    assert form == {
        "speaker": "nara_call",
        "text": "네, 주문 받았어요.",
        "format": "wav",
        "sampling-rate": "16000",
    }


def reply(content: bytes, status: int = 200, **headers: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content, headers=headers)

    return handler


def test_header_longer_than_44_bytes_is_skipped_by_walking_chunks() -> None:
    # LIST(홀수 길이 → 패딩 1바이트)와 fact 청크가 fmt와 data 사이에 있다. 헤더 = 44 + 8+5+1 + 8+4
    body = wav(PCM, extra=[(b"LIST", b"INFOx"), (b"fact", b"\0\0\0\0")])
    assert body.index(b"data") + 8 != 44

    assert run(tts_with(reply(body)).synth("네.")) == PCM


def test_data_size_past_end_of_body_gives_what_arrived() -> None:
    # 흘려 쓰는 WAV는 data 크기를 0xFFFFFFFF로 두기도 한다
    body = wav(PCM)
    body = body[:40] + struct.pack("<I", 0xFFFFFFFF) + body[44:]

    assert run(tts_with(reply(body)).synth("네.")) == PCM


@pytest.mark.parametrize(
    "body",
    [
        wav(PCM, rate=24000),
        wav(PCM, channels=2),
        wav(PCM, bits=8),
        b"ID3\x04 not a wav",
        wav(PCM)[:36],  # data 청크 없음
    ],
    ids=["24k", "stereo", "8bit", "mp3", "no-data"],
)
def test_audio_that_is_not_pcm16_16k_mono_is_a_port_error(body: bytes) -> None:
    with pytest.raises(PortError) as err:
        run(tts_with(reply(body)).synth("네."))

    assert err.value.code == "tts_bad_audio"


def _json(obj) -> bytes:
    return json.dumps(obj).encode()


GATEWAY_401 = _json(
    {"error": {"errorCode": "200", "message": "Authentication Failed", "details": "Invalid"}}
)


@pytest.mark.parametrize(
    ("status", "body", "code", "retry"),
    [
        (401, GATEWAY_401, "tts_auth", False),
        (403, GATEWAY_401, "tts_auth", False),
        (429, _json({"error": {"errorCode": "400", "message": "Quota Exceed"}}), "tts_429", True),
        (400, _json({"error": {"errorCode": "VS18", "message": "max length"}}), "tts_vs18", False),
        (400, _json({"errorCode": "VS02", "errorMessage": "Bad speaker"}), "tts_vs02", False),
        (400, _json({"error": {"errorCode": "VS26", "message": "Failed"}}), "tts_vs26", True),
        (500, _json({"error": {"errorCode": "VS99", "message": "Internal"}}), "tts_vs99", True),
        (502, b"<html>Bad Gateway</html>", "tts_http_502", True),
        (400, b"", "tts_http_400", False),
    ],
    ids=["401", "403", "429", "vs18", "vs02-flat", "vs26", "vs99", "502", "400-empty"],
)
def test_error_responses_become_port_errors_with_code_and_retry(
    status: int, body: bytes, code: str, retry: bool
) -> None:
    with pytest.raises(PortError) as err:
        run(tts_with(reply(body, status)).synth("네."))

    assert (err.value.code, err.value.retry) == (code, retry)
    assert SECRET not in str(err.value) and ID not in str(err.value)


def test_401_message_mentions_new_keys_take_a_while() -> None:
    with pytest.raises(PortError) as err:
        run(tts_with(reply(GATEWAY_401, 401)).synth("네."))

    assert "반영" in str(err.value)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ReadTimeout("slow"), "tts_timeout"),
        (httpx.ConnectTimeout("slow"), "tts_timeout"),
        (httpx.ConnectError("refused"), "tts_network"),
    ],
)
def test_timeouts_and_network_errors_are_retryable_port_errors(exc, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(PortError) as err:
        run(tts_with(handler).synth("네."))

    assert (err.value.code, err.value.retry) == (code, True)


def test_session_with_clova_tts_sends_each_sentence_pcm_in_order_two_at_a_time(tmp_path) -> None:
    """분할·동시 2개·순서·200ms 재분할은 세션 몫. 어댑터와 붙여도 그대로인지 본다."""
    first, second = "네, 샘플 러시안 베이커리입니다.", "주문 도와드릴게요."
    pcm = {first: b"\x01\x00" * 7000, second: b"\x02\x00" * 3000}
    active = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        text = parse_qs(request.content.decode())["text"][0]
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.1 if text == first else 0)  # 첫 문장이 늦게 끝난다
        active -= 1
        return httpx.Response(200, content=wav(pcm[text]))

    async def call() -> Call:
        async with Call(tmp_path, tts=tts_with(handler)) as c:
            await c.start()
            return c

    c = run(call())
    assert peak == 2
    assert [e["text"] for e in c.out.of("bot.say", 0)] == [first, second]
    by_seq = {0: b"", 1: b""}
    order = []
    for frame in c.out.frames:
        by_seq[frame.seq] += frame.pcm
        order.append(frame.seq)
    assert order == sorted(order)
    assert by_seq == {0: pcm[first], 1: pcm[second]}


def test_from_env_reads_environment_first_then_env_file(tmp_path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "CLOVA_VOICE_CLIENT_ID='file-id'\nCLOVA_VOICE_CLIENT_SECRET=\"file-secret\"\n",
        encoding="utf-8",
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=wav(PCM))

    monkeypatch.delenv("CLOVA_VOICE_CLIENT_ID", raising=False)
    monkeypatch.setenv("CLOVA_VOICE_CLIENT_SECRET", "env-secret")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ClovaVoiceTts.from_env(env, http=http)
    run(tts.synth("네."))

    assert seen[0].headers["X-NCP-APIGW-API-KEY-ID"] == "file-id"
    assert seen[0].headers["X-NCP-APIGW-API-KEY"] == "env-secret"
    assert "secret" not in repr(tts)


def test_from_env_without_keys_is_a_config_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CLOVA_VOICE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CLOVA_VOICE_CLIENT_SECRET", raising=False)

    with pytest.raises(PortError) as err:
        ClovaVoiceTts.from_env(tmp_path / "missing.env")

    assert (err.value.code, err.value.retry) == ("tts_config", False)
