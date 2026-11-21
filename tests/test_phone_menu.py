"""비서 메뉴로 전화 주문 (전화 메뉴 티켓 02). 발표 서버 하나에서 비서 API로 메뉴를 바꾸고
전화(WS)로 주문한다.

바깥에서 보이는 것만 본다: 비서 대화에서 메뉴를 더하고·고치고·지운 뒤 새 통화가 그 메뉴로 주문을
받는지, 진행 중인 통화는 시작할 때의 메뉴로 끝나는지. 손님 말은 가짜 STT 대본(`fake_replies`)이다.
`with TestClient(app)` 안에서만 쓴다(사장님 허브가 이벤트 루프 하나에 묶인다).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agenthan.app import pilot
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.serve import PhoneStores, create_app
from agenthan.tools.dummy_store import DummyStore, watch
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.store_snapshot import StoreSnapshot
from agenthan.voice.ws import WS_PATH

NOW = datetime.fromisoformat("2026-11-08T16:00:00+09:00")
KEY = "test-owner-key"
SILENT_FRAME = bytes(3_200)  # 100ms PCM16LE 16kHz
MAX_MESSAGES = 500
SESSIONS = "/assistant/sessions"


def _order_script(*first: str) -> tuple[str, ...]:
    return (*first, "그게 다예요", "6시 반이요", "네")


def _client(tmp_path: Path, replies: tuple[str, ...], **env: str) -> TestClient:
    settings = VoiceSettings(
        nlu="fake", stt="fake", tts="fake", log_dir=tmp_path / "logs", now=NOW,
        web_dist=tmp_path / "dist", fake_replies=replies, turn_model="off", owner_key=KEY,
        env={"AGENTHAN_SESSIONS_DIR": str(tmp_path / "s"), **env},
    )
    return TestClient(create_app(settings))


class Phone:
    """손님 휴대폰 흉내 (`tests/voice/test_phone_ws.py`의 것을 줄였다)."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.events: list[dict[str, Any]] = []

    def says(self) -> list[str]:
        return [e["text"] for e in self.events if e["type"] == "bot.say"]

    def until(self, type_: str, turn: int | None = None) -> dict[str, Any]:
        for _ in range(MAX_MESSAGES):
            message = self.ws.receive()
            if message["type"] == "websocket.close":
                raise AssertionError(f"{type_}를 기다리는데 연결이 닫혔다: {self.events[-3:]}")
            if message.get("bytes") is not None:
                continue
            event = json.loads(message["text"])
            self.events.append(event)
            assert event["type"] != "error", event
            if event["type"] == type_ and turn in (None, event["turn"]):
                return event
        raise AssertionError(f"{type_}(turn={turn})가 오지 않았다")

    def send(self, **event: Any) -> None:
        self.ws.send_text(json.dumps(event, ensure_ascii=False))

    def start(self) -> None:
        self.send(type="call.start", turn=0)
        self.until("call.ready", 0)
        self.played(0)

    def played(self, turn: int) -> None:
        done = self.until("bot.done", turn)
        self.send(type="playback.started", turn=turn, seq=0)
        self.send(type="playback.done", turn=turn, seq=done["sentences"] - 1, output_latency=0.02)

    def say(self, turn: int) -> None:
        """'말 끝' 버튼 + 무음 1초. 가짜 STT가 대본의 다음 글을 낸다."""
        self.send(type="user.end_of_speech", turn=turn - 1)
        for _ in range(10):
            self.ws.send_bytes(SILENT_FRAME)
        self.until("stt.final", turn)

    def order(self, turns: int, start: int = 1) -> dict[str, Any]:
        """대본을 `start`번째 말부터 끝까지 말하고 마지막 "네"의 `order` 이벤트."""
        for turn in range(start, turns + 1):
            self.say(turn)
            if turn < turns:
                self.played(turn)
        return self.until("order", turns)


def _call(client: TestClient, turns: int) -> tuple[dict[str, Any], Phone]:
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.start()
        return phone.order(turns), phone


def _first_answer(client: TestClient) -> str:
    """첫 손님 말에 대한 답(문장들을 이은 것)만 듣고 끊는다."""
    with client.websocket_connect(WS_PATH) as ws:
        phone = Phone(ws)
        phone.start()
        phone.say(1)
        phone.until("bot.done", 1)
        phone.send(type="call.end", turn=1)
        return " ".join(e["text"] for e in phone.events
                        if e["type"] == "bot.say" and e["turn"] == 1)


def _lines(order: dict[str, Any]) -> list[tuple[str, int, str]]:
    return [(line["menu_id"], line["quantity"], line["request"])
            for line in order["order"]["lines"]]


def _session(client: TestClient, **params: str) -> str:
    started = client.post(SESSIONS, params=params)
    assert started.status_code == 200
    return started.json()["session_id"]


def _menu_id(client: TestClient, session: str, name: str) -> str:
    menus = client.get(f"{SESSIONS}/{session}/place").json()["place"]["menu"]
    return next(m["id"] for m in menus if m["name"] == name)


def _edit(client: TestClient, session: str, menu_id: str, **fields: Any) -> None:
    assert client.patch(f"{SESSIONS}/{session}/menus/{menu_id}", json=fields).status_code == 200


# ---------------------------------------------------------------- 더하기·고치기·지우기


def test_a_menu_added_in_the_assistant_is_ordered_on_the_next_call(tmp_path) -> None:
    """온보딩으로 더한 메뉴를 다음 통화가 그 이름·가격으로 받는다. 이름을 조금 틀려도 찾는다."""
    script = ("쿠키수 하나요", "네", "그게 다예요", "6시 반이요", "네")
    with _client(tmp_path, script) as client:
        session = _session(client)
        added = client.post(f"{SESSIONS}/{session}/onboarding", json={
            "name": "샘플 러시안 베이커리", "category": "베이커리·카페",
            "menus": [{"name": "쿠키슈", "price": 3200}]})
        assert added.status_code == 200
        cookie = _menu_id(client, session, "쿠키슈")

        order, phone = _call(client, 5)

    assert "혹시 쿠키슈 말씀하신 거 맞으세요?" in phone.says()
    assert _lines(order) == [(cookie, 1, "")]
    assert order["total"] == 3200
    assert any("3,200원" in text for text in phone.says())  # 복창 금액이 비서에 적힌 가격


def test_price_and_name_edits_reach_the_next_call(tmp_path) -> None:
    with _client(tmp_path, _order_script("허니 메도빅 하나요")) as client:
        session = _session(client)
        _edit(client, session, "m01", name="허니 메도빅", price=8000)

        order, _ = _call(client, 4)

    assert _lines(order) == [("m01", 1, "")]
    assert order["total"] == 8000


def test_a_deleted_menu_is_not_on_the_phone_menu(tmp_path) -> None:
    with _client(tmp_path, ("블린 하나요",)) as client:
        session = _session(client)
        assert client.delete(f"{SESSIONS}/{session}/menus/m04").status_code == 200

        answer = _first_answer(client)

    # 손님에게 알려 주는 메뉴도 비서 메뉴다 (피로시키는 비서 가게에만 있다)
    assert answer.startswith("죄송해요, 그 메뉴는 없어요. 저희는 메도빅, 나폴레옹, 아메리카노, "
                             "메도빅 홀케이크, 피로시키")
    assert "블린" not in answer


def test_a_call_already_running_keeps_the_menu_it_started_with(tmp_path) -> None:
    with _client(tmp_path, _order_script("메도빅 하나요")) as client:
        session = _session(client)
        with client.websocket_connect(WS_PATH) as ws:
            running = Phone(ws)
            running.start()
            running.say(1)
            running.played(1)
            _edit(client, session, "m01", price=9900)  # 통화 중에 가격을 고친다
            before = running.order(4, start=2)

        after, _ = _call(client, 4)

    assert before["total"] == 7500  # 시작할 때의 가격으로 끝난다
    assert after["total"] == 9900


def test_the_chat_that_saved_last_wins(tmp_path) -> None:
    with _client(tmp_path, _order_script("메도빅 하나요")) as client:
        first, second = _session(client), _session(client)
        _edit(client, first, "m01", price=8000)
        _edit(client, second, "m01", price=8800)
        assert _call(client, 4)[0]["total"] == 8800

        _edit(client, first, "m01", description="꿀 케이크")  # 먼저 대화가 다시 저장한다
        assert _call(client, 4)[0]["total"] == 8000


# ---------------------------------------------------------------- 기본 가게·빈 가게


def test_without_any_saved_chat_the_phone_takes_the_pilot_menu(tmp_path) -> None:
    """사장님 화면 없이 혼자 건 전화도 파일럿 가게 메뉴로 주문된다. 메뉴를 비운 시연 대화는
    전화 메뉴를 비우지 않는다."""
    with _client(tmp_path, _order_script("피로시키 하나요")) as client:
        assert _call(client, 4)[0]["total"] == 4500  # 대화가 하나도 없다
        _session(client, menu="empty")  # 메뉴판 사진 시연: 메뉴를 비운 가게를 저장한다
        order, phone = _call(client, 4)

    assert _lines(order) == [("m06", 1, "")]
    assert not any("따뜻하게" in text for text in phone.says())


def test_with_the_assistant_off_the_phone_keeps_its_own_store(tmp_path) -> None:
    with _client(tmp_path, ("피로시키 하나요",), AGENTHAN_ASSISTANT="off") as client:
        answer = _first_answer(client)
    assert answer.startswith(
        "죄송해요, 그 메뉴는 없어요. 저희는 메도빅, 나폴레옹, 아메리카노, 블린이")


# ---------------------------------------------------------------- 옵션 말·품절


def test_an_option_on_an_assistant_menu_is_a_request_the_owner_sees(tmp_path) -> None:
    """비서 메뉴에는 옵션이 없다: "아이스로 주세요"는 요청 사항, 금액 그대로, 온도를 묻지 않는다."""
    script = _order_script("아메리카노 하나요", "아이스로 주세요")
    with _client(tmp_path, script) as client:
        order, phone = _call(client, 5)
        with client.stream("GET", f"/api/owner/stream?key={KEY}") as stream:
            snapshot = _first_sse(stream.iter_lines())

    assert _lines(order) == [("m03", 1, "아이스")]
    assert order["total"] == 4500
    assert "아메리카노에 아이스라고 적어 둘게요." in phone.says()
    assert not any("따뜻하게" in text for text in phone.says())
    (card,) = snapshot["orders"]
    assert [(line["name_ko"], line["request"]) for line in card["lines"]] == [
        ("아메리카노", "아이스")]


def test_a_menu_sold_out_in_the_assistant_is_sold_out_on_the_phone(tmp_path) -> None:
    with _client(tmp_path, ("메도빅 하나요",)) as client:
        session = _session(client)
        store = DummyStore(tmp_path / "s" / session / "store.json")  # 대화 도구가 쓰는 것처럼
        data = store.read()
        next(m for m in data["menus"] if m["id"] == "m01")["sold_out"] = True
        store.write(data)

        answer = _first_answer(client)

    assert answer.startswith("죄송해요, 메도빅은 오늘 다 떨어졌어요.")


def test_today_sold_out_goes_by_menu_id_and_ignores_deleted_menus(tmp_path) -> None:
    """재료 소진 거절의 오늘 품절은 메뉴 id 목록이다. 비서에서 지운 메뉴의 id는 그냥 넘어간다."""
    stores = PhoneStores(StoreSnapshot.of(FakeStoreKnowledge.from_fixture(),
                                          StoreConfig.from_fixture()))
    assert stores.publish({"menus": [{"id": "m01", "name": "메도빅", "price": 7500}]})
    snapshot = stores()
    engine = DialogEngine(snapshot.store, FakeNlu(), snapshot.config, order_id="o-1",
                          sold_out=("m01", "deleted-menu"))
    engine.start(NOW)
    said = engine.handle("메도빅 하나요", NOW).utterances
    assert said == ["죄송해요, 메도빅은 오늘 다 떨어졌어요."]


# ---------------------------------------------------------------- 게시하는 저장


def test_only_saves_of_a_chat_store_are_heard(tmp_path) -> None:
    """미리보기 사본(`_dry_run`·`place.preview`)은 다른 경로라 게시하지 않는다. 듣는 쪽이 실패해도
    저장은 된다."""
    heard: list[dict] = []
    chat = tmp_path / "chat" / "store.json"
    watch(chat, heard.append)
    DummyStore(tmp_path / "preview" / "store.json").write(pilot.seed())
    assert heard == []
    DummyStore(chat).write(pilot.seed())
    assert [len(data["menus"]) for data in heard] == [13]

    def broken(data: dict) -> None:
        raise RuntimeError("게시 실패")

    watch(chat, broken)
    data = DummyStore(chat).read()
    data["menus"] = data["menus"][:1]
    DummyStore(chat).write(data)
    assert len(DummyStore(chat).read()["menus"]) == 1


def test_an_empty_menu_is_not_published(tmp_path) -> None:
    stores = PhoneStores(StoreSnapshot.of(FakeStoreKnowledge.from_fixture(),
                                          StoreConfig.from_fixture()))
    assert stores.publish(pilot.seed())
    published = stores()
    assert not stores.publish({"menus": []})
    assert stores() is published
    assert stores.publish(pilot.seed())  # 같은 게시 값은 만들어 둔 스냅숏 그대로
    assert stores() is published


def _first_sse(lines) -> dict[str, Any]:
    for line in lines:
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError("SSE 이벤트가 오지 않았다")
