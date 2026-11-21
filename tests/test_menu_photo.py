"""A-1 메뉴 사진 → 초안 → 승인 후 저장. 네트워크 없이 가짜 CLOVA 응답으로 확인한다."""

import copy
import io
import json
import urllib.error

import pytest

from agenthan.agent import menu_board, menu_photo
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.core import llm
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.tools.dummy_store import DummyStore

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
KNOWLEDGE = FakeStoreKnowledge.from_fixture()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        pytest.fail("menu photo unit tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)


class FakeV3:
    """사진 요청에는 vision 답을, 메뉴 하나 요청에는 names[원어]를 돌려준다."""

    def __init__(self, vision: str, names: dict | None = None):
        self.vision = vision
        self.names = names or {}
        self.calls = []

    def __call__(self, model, body):
        self.calls.append((model, copy.deepcopy(body)))
        content = body["messages"][-1]["content"]
        if isinstance(content, list):
            if content[1]["text"] == "사진을 분류해 menu_board, food, other 중 한 단어만 답하세요.":
                return {"message": {"content": "menu_board"}}
            if content[1]["text"] == menu_board.BOARD_PROMPT:
                # 분류와 읽기를 합친 질문(menu_board.read_board)에는 같은 줄을 kind로 감싸 답한다.
                try:
                    rows = menu_photo._json_block(self.vision, "[")
                except ValueError:
                    rows = []
                kind = "menu_board" if rows else "other"
                answer = {"kind": kind, "items": rows}
                return {"message": {"content": json.dumps(answer, ensure_ascii=False)}}
            assert content[0]["dataUri"]["data"].startswith("data:image/jpeg;base64,")
            return {"message": {"content": self.vision}}
        name = content.rsplit("메뉴 원어 이름: ", 1)[1]
        return {"message": {"content": self.names.get(name, "{}")}}


VISION = """```json
[{"name": "Медовник", "price": 7500}, {"name": "Сырники", "price": "8 500 ₩"},
 {"name": "Пельмени", "price": null}, {"price": 1000}, "x"]
```"""
NAMES = {
    "Сырники": '{"name_ko": "시르니키", "description_ko": "러시아식 치즈 팬케이크."}',
    "Пельмени": '{"name_ko": "Pelmeni", "description_ko": ""}',
}


def test_read_menu_photo_parses_names_and_prices():
    rows = menu_photo.read_menu_photo(JPEG, call=FakeV3(VISION))
    assert rows == [("Медовник", 7500), ("Сырники", 8500), ("Пельмени", None)]


def test_read_menu_photo_sends_image_without_tools():
    fake = FakeV3("[]")
    assert menu_photo.read_menu_photo(JPEG, call=fake) == []
    model, body = fake.calls[0]
    assert model == "HCX-005" and "tools" not in body


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a....", b"\xff\xd8\xff" + b"0" * menu_photo.MAX_IMAGE_BYTES],
)
def test_bad_images_are_rejected_before_calling(data):
    fake = FakeV3("[]")
    with pytest.raises(menu_photo.MenuPhotoError):
        menu_photo.read_menu_photo(data, call=fake)
    assert fake.calls == []


def test_unreadable_answer_is_a_safe_error():
    with pytest.raises(menu_photo.MenuPhotoError, match="읽지 못했어요"):
        menu_photo.read_menu_photo(JPEG, call=FakeV3("메뉴판이 흐려요"))


def test_vision_api_error_hides_details():
    def fail(model, body):
        raise llm.ClovaV3Error("CLOVA HTTP 오류 (500)")

    with pytest.raises(menu_photo.MenuPhotoError, match=r"^CLOVA HTTP 오류 \(500\)$"):
        menu_photo.read_menu_photo(JPEG, call=fail)


@pytest.mark.parametrize(
    ("read", "expected", "how"),
    [
        ("Медовик", "메도빅", "exact"),
        ("메도빅", "메도빅", "exact"),
        ("꿀케이크", "메도빅", "alias"),
        ("Медовник", "메도빅", "fuzzy"),  # HCX-005가 실제로 잘못 읽은 형태
        ("Блины", "블린", "exact"),
    ],
)
def test_match_known_menu(read, expected, how):
    item, matched_by = menu_photo.match_known(read, KNOWLEDGE)
    assert (item.name_ko, matched_by) == (expected, how)


@pytest.mark.parametrize("read", ["Народный", "Сырники", ""])
def test_unknown_menu_is_not_forced_onto_existing(read):
    assert menu_photo.match_known(read, KNOWLEDGE) is None


def test_latin_names_need_no_spelling_check():
    fake = FakeV3(
        '[{"name": "Bún riêu cua", "price": 11000}]',
        {"Bún riêu cua": '{"name_ko": "분 리에우 꾸아"}'},
    )
    (draft,) = menu_photo.draft_menus(JPEG, KNOWLEDGE, call=fake)
    assert (draft.name_ko, draft.name_native, draft.notes) == (
        "분 리에우 꾸아",
        "Bún riêu cua",
        ["generated"],
    )


def test_draft_menus_uses_store_knowledge_then_localizes():
    fake = FakeV3(VISION, NAMES)
    drafts = menu_photo.draft_menus(JPEG, KNOWLEDGE, store_context="베이커리", call=fake)
    medovik, syrniki, pelmeni = drafts
    # 기존 메뉴: 가게 지식의 이름·설명을 그대로, 생성 호출 없음
    assert (medovik.name_ko, medovik.menu_id, medovik.matched_by) == ("메도빅", "m01", "fuzzy")
    assert medovik.name_native == "Медовик" and medovik.description.startswith("꿀을")
    assert medovik.notes == [] and medovik.complete
    # 새 메뉴: HCX가 만든 한글 이름·설명은 AI 초안 표시
    assert (syrniki.name_ko, syrniki.price) == ("시르니키", 8500)
    assert syrniki.notes == ["generated", "check_spelling"]
    # 한글이 아닌 이름은 버리고 확인 요청, 가격 없음도 표시
    assert pelmeni.name_ko == "" and not pelmeni.complete
    assert pelmeni.notes == ["generated", "check_spelling", "price_missing", "name_missing"]
    localized = [b["messages"][-1]["content"].split(": ")[-1] for _, b in fake.calls[1:]]
    assert localized == ["Сырники", "Пельмени"]
    assert fake.calls[1][1]["messages"][-1]["content"].startswith("가게: 베이커리\n")
    assert medovik.to_form() == {
        "name_ko": "메도빅",
        "name_native": "Медовик",
        "price": 7500,
        "description": medovik.description,
        "menu_id": "m01",
    }


# ------------------------------------------------------------ 비서 대화 (HITL)


def tool_call(arguments, call_id="call-1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "edit_menu_draft", "arguments": json.dumps(arguments)},
            }
        ],
    }


class FakeChat:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    def __call__(self, messages, *, tools):
        self.requests.append(copy.deepcopy((messages, tools)))
        return next(self.replies)


def assistant(tmp_path, chat=None, vision=VISION):
    return OwnerAssistant(
        tmp_path / "store.json",
        chat=chat or FakeChat(),
        knowledge=KNOWLEDGE,
        clova_v3=FakeV3(vision, NAMES),
    )


def menu_names(tmp_path):
    return {m["name"]: m for m in DummyStore(tmp_path / "store.json").read()["menus"]}


def test_upsert_menu_keeps_optional_fields(tmp_path):
    store = DummyStore(tmp_path / "s.json")
    store.upsert_menu("메도빅", 7500, description=" 꿀  케이크 ", name_native="Медовик")
    result = store.upsert_menu("메도빅", 8000)
    assert result["action"] == "updated" and result["previous_price"] == 7500
    assert result["record"]["description"] == "꿀 케이크"
    assert result["record"]["name_native"] == "Медовик"


# ------------------------------------------------------------ core.llm.chat_v3


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("u", code, "x", headers, io.BytesIO(b"secret body"))


def test_chat_v3_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "test-key")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    replies = [
        http_error(429, "1"),
        Response(json.dumps({"status": {"code": "20000"}, "result": {"ok": 1}}).encode()),
    ]
    seen = []

    def urlopen(request, timeout):
        seen.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert llm.chat_v3("HCX-005", {"messages": []}) == {"ok": 1}
    assert seen[0].full_url.endswith("/v3/chat-completions/HCX-005")
    assert seen[0].get_header("Authorization") == "Bearer test-key"


def test_chat_v3_long_retry_after_fails_fast_without_body(monkeypatch):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "test-key")
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        raise http_error(429, "30")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(llm.ClovaV3Error) as info:
        llm.chat_v3("HCX-005", {"messages": []})
    assert calls == [1] and "secret" not in str(info.value) and "test-key" not in str(info.value)


def test_chat_v3_needs_key():
    with pytest.raises(llm.ClovaV3Error, match="CLOVASTUDIO_API_KEY"):
        llm.chat_v3("HCX-005", {"messages": []})
