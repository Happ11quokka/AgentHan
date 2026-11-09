"""Papago 번역 어댑터 (4단계 티켓 03). 키·네트워크 없이 돈다.

- 요청 모양·오류는 `httpx.MockTransport`로 꾸민 응답으로 본다
- 연결 유지·데우기(티켓 12)는 연결 수를 세는 로컬 HTTP 서버로 본다
- 실제 응답 형식과 티켓 04 실측 실패 문장의 회귀(티켓 12)는 녹화본(cassette)을 재생한다.
  녹화 다시 하기(실제 호출 3 + 8번, 키 필요):

    AGENTHAN_ENV_FILE=/path/to/AgentHan/.env \\
        uv run pytest tests/voice/test_owner_papago.py -k recorded --record-mode=rewrite

  녹화본에는 키 헤더(`X-NCP-APIGW-API-KEY-ID`·`X-NCP-APIGW-API-KEY`)를 남기지 않는다
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.owner.card_texts import CardTexts
from agenthan.voice.owner.lexicon import RU
from agenthan.voice.owner.papago import PAPAGO_URL, PapagoTranslator, papago_from_env
from agenthan.voice.owner.translate import TranslateError
from agenthan.voice.settings import read_env_file

CASSETTES = Path(__file__).with_name("cassettes") / "test_owner_papago"
ID, SECRET = "test-id-not-real", "test-secret-not-real"

FILTERED_REQUEST_HEADERS = ("x-ncp-apigw-api-key-id", "x-ncp-apigw-api-key", "cookie")
KEPT_RESPONSE_HEADERS = frozenset({"content-type"})


def _drop_response_headers(response: dict[str, Any]) -> dict[str, Any]:
    response["headers"] = {
        k: v for k, v in response["headers"].items() if k.lower() in KEPT_RESPONSE_HEADERS
    }
    return response


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return {
        "filter_headers": list(FILTERED_REQUEST_HEADERS),
        "match_on": ["method", "scheme", "host", "path", "body"],
        "decode_compressed_response": True,
        "before_record_response": _drop_response_headers,
    }


def ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"result": {
        "srcLangType": "ko", "tarLangType": "ru", "translatedText": text}}})


def papago(handler, **kw) -> PapagoTranslator:
    return PapagoTranslator(ID, SECRET, http=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)), **kw)


def translate(translator: PapagoTranslator, texts: list[str]) -> list[str]:
    async def run():
        try:
            return await translator.translate(texts, "ko", "ru")
        finally:
            await translator.aclose()

    return asyncio.run(run())


def test_each_sentence_is_one_request_and_answers_keep_the_order() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        text = json.loads(request.content)["text"]
        return ok({"하나": "один", "둘": "два"}[text])

    assert translate(papago(handler), ["하나", "둘"]) == ["один", "два"]

    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", PAPAGO_URL)
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == ID
    assert request.headers["X-NCP-APIGW-API-KEY"] == SECRET
    assert request.headers["Content-Type"].startswith("application/json")
    assert sorted(json.loads(r.content)["text"] for r in seen) == ["둘", "하나"]
    assert json.loads(request.content)["source"] == "ko"
    assert json.loads(request.content)["target"] == "ru"


def test_an_empty_sentence_is_not_sent() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok("да")

    assert translate(papago(handler), ["", "네"]) == ["", "да"]
    assert len(seen) == 1


@pytest.mark.parametrize(("status", "code"), [
    (401, "translate_auth"),
    (403, "translate_auth"),
    (429, "translate_429"),
    (400, "translate_http"),
    (500, "translate_http"),
])
def test_an_error_status_fails_without_leaking_keys_or_body(status: int, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"errorCode": "N2MT99", "message": "secret"}})

    with pytest.raises(TranslateError) as err:
        translate(papago(handler), ["고수 빼 주세요"])

    assert err.value.code == code
    assert ID not in str(err.value) and SECRET not in str(err.value)
    assert "secret" not in str(err.value)


def test_an_unexpected_body_fails() -> None:
    with pytest.raises(TranslateError) as err:
        translate(papago(lambda r: httpx.Response(200, json={"message": {}})), ["네"])
    assert err.value.code == "translate_bad_response"


def test_a_slow_answer_fails_as_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TranslateError) as err:
        translate(papago(handler), ["네"])
    assert err.value.code == "translate_timeout"


def test_keys_never_show_in_repr() -> None:
    text = repr(PapagoTranslator(ID, SECRET))
    assert ID not in text and SECRET not in text


def test_without_both_keys_there_is_no_translator() -> None:
    assert papago_from_env({}) is None
    assert papago_from_env({"PAPAGO_CLIENT_ID": ID}) is None
    assert papago_from_env({"PAPAGO_CLIENT_ID": ID, "PAPAGO_CLIENT_SECRET": " "}) is None
    assert isinstance(
        papago_from_env({"PAPAGO_CLIENT_ID": ID, "PAPAGO_CLIENT_SECRET": SECRET}),
        PapagoTranslator,
    )


# ---------------------------------------------------------------- 연결 유지·데우기 (티켓 12)


class LocalPapago:
    """연결 수를 세는 작은 HTTP/1.1 서버(127.0.0.1). 연결을 먼저 닫지 않고 요청마다 같은 답."""

    def __init__(self) -> None:
        self.connections = 0
        self.requests = 0
        self._server: asyncio.Server | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"http://{host}:{port}/nmt/v1/translation"

    async def __aenter__(self) -> LocalPapago:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        body = ok("да").content
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = re.search(rb"(?i)content-length:\s*(\d+)", head)
                await reader.readexactly(int(length.group(1)) if length else 0)
                self.requests += 1
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.mark.parametrize(("keepalive", "connections"), [
    (5.0, 1),  # 쉬는 동안 연결을 남겨 두고 다시 쓴다
    (0.05, 2),  # 유지 시간이 지나면 새로 연결한다
])
def test_an_idle_connection_is_reused_within_the_keepalive_time(
    keepalive: float, connections: int
) -> None:
    async def run() -> int:
        async with LocalPapago() as server:
            translator = PapagoTranslator(ID, SECRET, url=server.url, keepalive=keepalive)
            try:
                assert await translator.translate(["네"], "ko", "ru") == ["да"]
                await asyncio.sleep(0.3)
                assert await translator.translate(["네"], "ko", "ru") == ["да"]
            finally:
                await translator.aclose()
            return server.connections

    assert asyncio.run(run()) == connections


def test_warming_up_sends_one_short_sentence_and_then_reuses_the_connection() -> None:
    async def run() -> tuple[bool, int, int]:
        async with LocalPapago() as server:
            translator = PapagoTranslator(ID, SECRET, url=server.url)
            try:
                warmed = await translator.warm()
                await asyncio.sleep(0.1)
                await translator.translate(["[1] 빼 주세요"], "ko", "ru")
            finally:
                await translator.aclose()
            return warmed, server.connections, server.requests

    assert asyncio.run(run()) == (True, 1, 2)


def test_a_failed_warm_up_does_not_raise() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["text"])
        return httpx.Response(500)

    async def run() -> bool:
        translator = papago(handler)
        try:
            return await translator.warm()
        finally:
            await translator.aclose()

    assert asyncio.run(run()) is False
    assert len(seen) == 1 and len(seen[0]) <= 2


# ---------------------------------------------------------------- 녹화본


def _recording_translator() -> PapagoTranslator:
    """녹화할 때는 `AGENTHAN_ENV_FILE`(없으면 지금 폴더 `.env`)의 키, 재생할 때는 가짜 키."""
    env = {**read_env_file(Path(os.environ.get("AGENTHAN_ENV_FILE", ".env"))), **os.environ}
    return papago_from_env(env, timeout=10.0) or PapagoTranslator(ID, SECRET)


@pytest.mark.vcr
def test_recorded_papago_answers_in_russian_and_keeps_the_placeholder() -> None:
    """실제 Papago ko→ru 응답(2026-11-10 녹화). 형식과 자리표시자 `[1]`이 살아남는지만 본다."""
    out = translate(_recording_translator(), [
        "[1] 빼 주세요",
        "메도빅에 견과류 들어가요?",
        "[1] 따로 포장해 주세요",
    ])

    assert len(out) == 3
    assert all(re.search("[а-яА-Я]", t) for t in out)
    assert "[1]" in out[0] and "[1]" in out[2]


# 티켓 04 실측에서 틀렸거나 버려진 요청(`s4-papago-measure.md` 3·4·5·7)
# → 카드에 보일 번역에 꼭 들어갈 말
MEASURED_FAILURES = {
    "깨는 빼 주세요": ("кунжут", "Убер"),  # 실측: "Разбудите"(깨워 주세요)
    "사워크림 빼 주세요": ("сметана", "Убер"),  # 실측: "смету"(견적)
    "러시아 팬케이크에 연어 빼 주세요": ("Блины лосось", "Убер"),  # 실측: "дайте мне лосося"
    "아메리카노는 샷 추가해 주세요": ("шот эспрессо", "американо"),  # 실측: 한국어 + "снимок"
    "아메리카노 두 잔이랑 블린 하나요": ("американо", "Блины"),  # 실측: 한국어 + "рюмки"
    "메도빅이랑 나폴레옹 하나씩 따로 담아 주세요": ("Медовик Наполеон",),  # 실측: 버림
    "블린은 고수랑 파 둘 다 빼 주세요": ("кинза", "зелёный лук", "Убер"),  # 실측: 버림
    "6시 반에 찾으러 갈게요": ("6:30",),  # 실측: 숫자 확인이 버림
}


@pytest.mark.vcr
def test_recorded_measure_failures_now_reach_the_card() -> None:
    """가리기·확인·채우기를 거친 실제 Papago 응답(2026-11-10 녹화, 티켓 12).
    녹화 다시 하기는 머리 주석."""
    papago = _recording_translator()
    texts = CardTexts(papago, store=FakeStoreKnowledge.from_fixture(), lexicon=RU)
    requests = list(MEASURED_FAILURES)

    async def run() -> dict[str, str]:
        try:
            await texts.fetch("call-1", requests)
        finally:
            await papago.aclose()
        return {r: texts.known("call-1", r) for r in requests}

    shown = asyncio.run(run())

    for request, needed in MEASURED_FAILURES.items():
        assert all(word in shown[request] for word in needed), (request, shown[request])
        assert not re.search("[가-힣]", shown[request]), (request, shown[request])


def test_cassettes_hold_no_key() -> None:
    files = list(CASSETTES.rglob("*.yaml"))
    assert files, "녹화본이 없어요. --record-mode=rewrite로 녹화하세요"
    for f in files:
        lower = f.read_text(encoding="utf-8").lower()
        assert "x-ncp-apigw-api-key" not in lower
        assert "cookie" not in lower
