"""WS 라우터: FastAPI `TestClient` WebSocket으로 바깥 행동만 본다 (spec Testing Decisions).

가짜 STT·TTS로 연결 → `call.ready` → 오디오·이벤트 → `order` → 끊김 정리를 확인한다.
손님 말은 "말 끝" 버튼 + 무음 프레임으로 낸다. 가짜 STT가 `epFlag`마다 대본의 다음 글을 낸다.
"""

from __future__ import annotations

import json
import struct
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import PortError
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.ws import WS_PATH, phone_router

ORDER_SCRIPT = ("메도빅 하나요", "그게 다예요", "6시 반이요", "네")
NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
SILENT_FRAME = bytes(3_200)  # 100ms PCM16LE 16kHz
MAX_MESSAGES = 500  # 이만큼 받아도 기다리는 것이 안 오면 실패 (무한 대기 방지)


def _settings(tmp_path, **kw: Any) -> VoiceSettings:
    base: dict[str, Any] = dict(
        nlu="fake", stt="fake", tts="fake", log_dir=tmp_path / "logs", now=NOW,
        fake_replies=ORDER_SCRIPT,
    )
    return VoiceSettings(**(base | kw))


def _app(tmp_path, *, sink=None, stt_factory=None, tts_factory=None, **kw: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(
        phone_router(
            store=FakeStoreKnowledge.from_fixture(),
            config=StoreConfig.from_fixture(),
            order_sink=sink if sink is not None else MemoryOrderSink(),
            settings=_settings(tmp_path, **kw),
            stt_factory=stt_factory,
            tts_factory=tts_factory,
        )
    )
    return app


class Phone:
    """손님 휴대폰 흉내. 받은 이벤트(dict)와 오디오 프레임(bytes)을 순서대로 쌓는다."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.events: list[dict[str, Any]] = []
        self.frames: list[bytes] = []
        self.received: list[dict[str, Any] | bytes] = []  # 받은 순서 그대로

    def of(self, type_: str, turn: int | None = None) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type_ and turn in (None, e["turn"])]

    def until(self, type_: str, turn: int | None = None) -> dict[str, Any]:
        for _ in range(MAX_MESSAGES):
            message = self.ws.receive()
            if message["type"] == "websocket.close":
                raise AssertionError(f"{type_}를 기다리는데 연결이 닫혔다: {self.events[-3:]}")
            if message.get("bytes") is not None:
                self.frames.append(message["bytes"])
                self.received.append(message["bytes"])
                continue
            event = json.loads(message["text"])
            self.events.append(event)
            self.received.append(event)
            if event["type"] == "error":
                raise AssertionError(f"error 이벤트: {event}")
            if event["type"] == type_ and turn in (None, event["turn"]):
                return event
        raise AssertionError(f"{type_}(turn={turn})가 오지 않았다")

    def closed_by_server(self) -> bool:
        """서버가 소켓을 닫을 때까지 받는다. 닫히면 True."""
        for _ in range(MAX_MESSAGES):
            message = self.ws.receive()
            if message["type"] == "websocket.close":
                return True
            if message.get("text") is not None:
                self.events.append(json.loads(message["text"]))
        return False

    def send(self, **event: Any) -> None:
        self.ws.send_text(json.dumps(event, ensure_ascii=False))

    def played(self, turn: int) -> None:
        done = self.until("bot.done", turn)
        last = done["sentences"] - 1
        self.send(type="playback.started", turn=turn, seq=0)
        self.send(type="playback.done", turn=turn, seq=last, output_latency=0.02)

    def say(self, turn: int) -> None:
        """'말 끝' 버튼 + 무음 1초. 가짜 STT가 대본의 다음 글을 낸다."""
        self.send(type="user.end_of_speech", turn=turn - 1)
        for _ in range(10):
            self.ws.send_bytes(SILENT_FRAME)
        self.until("stt.final", turn)


def test_one_call_over_websocket_from_ready_to_order(tmp_path) -> None:
    sink = MemoryOrderSink()
    client = TestClient(_app(tmp_path, sink=sink))
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0, mic={"echoCancellation": "all"})
        ready = phone.until("call.ready", 0)
        assert ready["sample_rate"] == 16_000
        phone.played(0)
        for turn in (1, 2, 3, 4):
            phone.say(turn)
            if turn < 4:
                phone.played(turn)
        order = phone.until("order", 4)
        phone.played(4)
        assert phone.closed_by_server()

    assert [e["text"] for e in phone.of("stt.final")] == list(ORDER_SCRIPT)
    assert order["order"]["status"] == "confirmed"
    assert order["order"]["pickup_time"] == "2026-11-08T18:30:00+09:00"
    assert order["total"] == 7_500
    assert [o.status for o in sink.orders] == ["confirmed"]
    assert all("turn" in e for e in phone.events)


def test_client_hangup_closes_the_session_and_the_stt_stream(tmp_path) -> None:
    streams: list[FakeSttStream] = []

    def make_stt() -> FakeSttStream:
        streams.append(FakeSttStream())
        return streams[-1]

    client = TestClient(_app(tmp_path, stt_factory=make_stt))
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0)
        phone.until("call.ready", 0)
    # 손님이 끊었다 (with를 나가면 TestClient가 close 1000을 보낸다)
    (stt,) = streams
    assert stt.closed
    assert list((tmp_path / "logs").glob("phone-*.jsonl")), "끊겨도 지연 기록은 남는다"


def test_bot_audio_goes_out_as_binary_frames_with_an_8_byte_header(tmp_path) -> None:
    """헤더 `<BBHI`: kind=1, flags bit0=문장의 마지막 조각, seq, turn. 뒤는 PCM16LE."""
    pcm = bytes(range(256)) * 50  # 12,800바이트 = 400ms → 200ms 조각 둘
    client = TestClient(_app(tmp_path, tts_factory=lambda: FakeTts(pcm)))
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0)
        done = phone.until("bot.done", 0)

    assert done["sentences"] == 2  # "네, 샘플 러시안 베이커리입니다." + 주문 안내
    for seq in range(done["sentences"]):
        say_at = phone.received.index(phone.of("bot.say", 0)[seq])
        end_at = phone.received.index(phone.of("bot.audio.end", 0)[seq])
        between = phone.received[say_at + 1 : end_at]
        assert all(isinstance(m, bytes) for m in between), "문장 오디오는 bot.say와 audio.end 사이"
        headers = [struct.unpack("<BBHI", f[:8]) for f in between]
        assert headers == [(1, 0, seq, 0), (1, 1, seq, 0)]  # kind, flags(마지막 조각), seq, turn
        assert b"".join(f[8:] for f in between) == pcm


class ClosableTts(FakeTts):
    """진짜 TTS처럼 HTTP 연결을 들고 있어서 `aclose()`가 있는 TTS."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_hangup_also_closes_a_tts_that_holds_a_connection(tmp_path) -> None:
    made: list[ClosableTts] = []
    client = TestClient(_app(tmp_path, tts_factory=lambda: made.append(ClosableTts()) or made[-1]))
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0)
        phone.until("call.ready", 0)
    assert [t.closed for t in made] == [True]


def test_port_that_cannot_be_made_sends_one_error_and_closes(tmp_path) -> None:
    def no_key() -> FakeSttStream:
        raise PortError("CLOVA_SPEECH_SECRET_KEY가 없어요", code="stt_config", retry=False)

    client = TestClient(_app(tmp_path, stt_factory=no_key))
    with client.websocket_connect(WS_PATH) as ws:
        error = json.loads(ws.receive_text())
        assert ws.receive()["type"] == "websocket.close"
    assert error == {"type": "error", "turn": 0, "code": "stt_config",
                     "message": "CLOVA_SPEECH_SECRET_KEY가 없어요", "retry": False}


def test_text_that_is_not_an_event_is_ignored(tmp_path) -> None:
    client = TestClient(_app(tmp_path))
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        ws.send_text("이건 JSON이 아니에요")
        ws.send_text("[1, 2]")
        phone.send(type="call.start", turn=0)
        assert phone.until("call.ready", 0)["turn"] == 0


def test_call_end_from_the_phone_closes_the_socket(tmp_path) -> None:
    client = TestClient(_app(tmp_path))
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.send(type="call.start", turn=0)
        phone.until("call.ready", 0)
        phone.send(type="call.end", turn=0)
        assert phone.closed_by_server()
