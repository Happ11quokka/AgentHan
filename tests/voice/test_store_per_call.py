"""통화마다 가게 스냅숏 하나 (전화 메뉴 티켓 01). 바깥 행동만 본다.

전화 앱은 가게 객체 하나가 아니라 "지금의 가게 스냅숏을 돌려주는 공급자"를 받는다. 통화 하나는
시작할 때 받은 스냅숏을 끝까지 쓴다. 사장님 허브처럼 통화 밖에서 메뉴를 읽는 곳은 읽을 때마다
공급자를 거친다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice import adapters
from agenthan.voice.call.fakes import FakeClock, FakeSttStream
from agenthan.voice.owner.hub import OwnerHub, UnknownCard
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.store_snapshot import StoreSnapshot, fixed_store
from agenthan.voice.ws import WS_PATH, phone_router

NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
SILENT_FRAME = bytes(3_200)  # 100ms PCM16LE 16kHz
MAX_MESSAGES = 500


def _old() -> StoreSnapshot:
    return StoreSnapshot.of(FakeStoreKnowledge.from_fixture(), StoreConfig.from_fixture())


def _renamed() -> StoreSnapshot:
    """같은 가게인데 메도빅(m01)이 티라미수가 되고 새 메뉴(m99)가 생겼다."""
    menu = FakeStoreKnowledge.from_fixture().list_menu()
    menu = [replace(m, name_ko="티라미수", aliases=()) if m.id == "m01" else m for m in menu]
    menu.append(replace(menu[0], id="m99", name_ko="크림브륄레", aliases=()))
    return StoreSnapshot.of(FakeStoreKnowledge(menu), StoreConfig.from_fixture())


class Switch:
    """테스트가 바꿔 끼우는 공급자. 부른 횟수를 센다."""

    def __init__(self, snapshot: StoreSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def __call__(self) -> StoreSnapshot:
        self.calls += 1
        return self.snapshot


def _app(tmp_path, stores, *, sink=None, stt_factory=None, **kw: Any) -> FastAPI:
    settings = VoiceSettings(**({"nlu": "fake", "stt": "fake", "tts": "fake",
                                 "log_dir": tmp_path / "logs", "now": NOW} | kw))
    app = FastAPI()
    app.include_router(phone_router(stores=stores, order_sink=sink or MemoryOrderSink(),
                                    settings=settings, stt_factory=stt_factory))
    return app


class Phone:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.events: list[dict[str, Any]] = []

    def until(self, type_: str, turn: int | None = None) -> dict[str, Any]:
        for _ in range(MAX_MESSAGES):
            message = self.ws.receive()
            assert message["type"] != "websocket.close", f"{type_}를 기다리는데 닫혔다"
            if message.get("text") is None:
                continue
            event = json.loads(message["text"])
            self.events.append(event)
            assert event["type"] != "error", event
            if event["type"] == type_ and turn in (None, event["turn"]):
                return event
        raise AssertionError(f"{type_}(turn={turn})가 오지 않았다")

    def send(self, **event: Any) -> None:
        self.ws.send_text(json.dumps(event, ensure_ascii=False))

    def played(self, turn: int) -> None:
        done = self.until("bot.done", turn)
        self.send(type="playback.started", turn=turn, seq=0)
        self.send(type="playback.done", turn=turn, seq=done["sentences"] - 1,
                  output_latency=0.02)

    def start(self) -> None:
        self.send(type="call.start", turn=0)
        self.until("call.ready", 0)
        self.played(0)

    def order(self) -> dict[str, Any]:
        """대본 네 마디(메뉴 · 그게 다예요 · 시각 · 네)를 말하고 `order`를 받는다."""
        for turn in (1, 2, 3, 4):
            self.send(type="user.end_of_speech", turn=turn - 1)
            for _ in range(10):
                self.ws.send_bytes(SILENT_FRAME)
            self.until("stt.final", turn)
            if turn < 4:
                self.played(turn)
        return self.until("order", 4)


def _script(menu: str) -> tuple[str, ...]:
    return (f"{menu} 하나요", "그게 다예요", "6시 반이요", "네")


def test_a_call_keeps_the_store_it_started_with_and_the_next_call_gets_the_new_one(
    tmp_path,
) -> None:
    stores = Switch(_old())
    scripts = iter([_script("메도빅"), _script("티라미수")])
    sink = MemoryOrderSink()
    client = TestClient(_app(tmp_path, stores, sink=sink,
                             stt_factory=lambda: FakeSttStream(replies=next(scripts))))
    with client.websocket_connect(WS_PATH) as first_ws:
        first = Phone(first_ws)
        first.start()
        stores.snapshot = _renamed()  # 첫 통화 중에 가게가 바뀐다
        with client.websocket_connect(WS_PATH) as second_ws:
            second = Phone(second_ws)
            second.start()
            later = second.order()
        earlier = first.order()

    names = {order.id: [line.name for line in order.lines] for order in sink.orders}
    assert names[earlier["order"]["id"]] == ["메도빅"]
    assert names[later["order"]["id"]] == ["티라미수"]
    assert stores.calls == 2, "통화마다 시작할 때 한 번만 묻는다"


def test_the_stt_boost_words_come_from_the_call_s_store(tmp_path, monkeypatch) -> None:
    """메뉴 이름이 바뀌어도 서버를 다시 띄우지 않는다: 다음 통화의 부스팅 단어가 새 메뉴다."""
    made: list[tuple[str, ...]] = []

    def capture(settings, *, boost_words=()):
        def make() -> FakeSttStream:
            made.append(tuple(boost_words))
            return FakeSttStream()
        return make

    monkeypatch.setattr(adapters, "stt_factory", capture)
    stores = Switch(_old())
    client = TestClient(_app(tmp_path, stores, stt="clova", stt_boost_weight=1.0))
    for snapshot in (stores.snapshot, _renamed()):
        stores.snapshot = snapshot
        with client.websocket_connect(WS_PATH) as ws:
            Phone(ws).start()

    old, new = made
    assert old[:3] == ("메도빅", "꿀케이크", "허니케이크")
    assert "사워크림" in old
    assert "티라미수" in new and "메도빅" not in new


def test_no_boost_words_when_boosting_is_off(tmp_path, monkeypatch) -> None:
    made: list[tuple[str, ...]] = []

    def capture(settings, *, boost_words=()):
        def make() -> FakeSttStream:
            made.append(tuple(boost_words))
            return FakeSttStream()
        return make

    monkeypatch.setattr(adapters, "stt_factory", capture)
    client = TestClient(_app(tmp_path, fixed_store(_old()), stt="clova"))
    with client.websocket_connect(WS_PATH) as ws:
        Phone(ws).start()
    assert made == [()]


def test_the_owner_hub_reads_the_store_through_the_provider_each_time() -> None:
    stores = Switch(_old())
    hub = OwnerHub(stores=stores, clock=FakeClock(NOW))
    with pytest.raises(UnknownCard):
        hub.undo_sold_out("m99")
    stores.snapshot = _renamed()  # 크림브륄레(m99)가 생겼다
    assert hub.undo_sold_out("m99")["items"] == []


def test_a_snapshot_is_built_once_from_the_store_menu() -> None:
    snapshot = _old()
    assert snapshot.config == StoreConfig.from_fixture()
    assert fixed_store(snapshot)() is snapshot
