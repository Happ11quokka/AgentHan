"""A-1 메뉴 사진 → 초안 → 승인 후 저장. 네트워크 없이 가짜 CLOVA 응답으로 확인한다."""

import copy
import io
import json
import urllib.error

import pytest

from agenthan.agent import menu_photo
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


def test_photo_shows_drafts_and_saves_nothing_before_approval(tmp_path):
    bot = assistant(tmp_path)
    out = bot.ask_photo(JPEG, "이거 올려 줘")
    assert out.startswith("메뉴판에서 메뉴 3개를 읽었어요.")
    assert "1. 메도빅 (Медовик) · 7,500원" in out and "[기존 메뉴]" in out
    assert "3. ? (Пельмени) · ?" in out and "가격 확인 필요" in out
    assert out.endswith("고칠 곳 말하기 예: 2번 가격 8000원)")
    assert [f["name_ko"] for f in bot.menu_form()] == ["메도빅", "시르니키", ""]
    assert not (tmp_path / "store.json").exists()


def test_approve_saves_complete_drafts_only(tmp_path):
    bot = assistant(tmp_path)
    bot.ask_photo(JPEG)
    out = bot.ask("네 저장해 주세요")
    assert "- 메도빅 · 7,500원" in out and "- 시르니키 · 8,500원" in out
    assert out.endswith("이름이나 가격이 없어 저장하지 않은 메뉴: Пельмени")
    saved = menu_names(tmp_path)
    assert saved["시르니키"]["description"] == "러시아식 치즈 팬케이크."
    assert saved["시르니키"]["name_native"] == "Сырники"
    assert bot.menu_drafts is None and bot.menu_form() == []


def test_cancel_saves_nothing(tmp_path):
    bot = assistant(tmp_path)
    bot.ask_photo(JPEG)
    assert bot.ask("취소할게요") == "메뉴 등록을 취소했어요. 저장한 것은 없어요."
    assert not (tmp_path / "store.json").exists()


def test_edit_then_approve(tmp_path):
    chat = FakeChat(
        tool_call({"number": 3, "name_ko": "펠메니", "price": 10000}),
        tool_call({"number": 2, "remove": True}, "call-2"),
        {"role": "assistant", "content": "3번을 고치고 2번을 뺐어요."},
    )
    bot = assistant(tmp_path, chat)
    bot.ask_photo(JPEG)
    out = bot.ask("3번은 펠메니 10000원, 2번은 빼 줘")
    assert out.startswith("3번을 고치고 2번을 뺐어요.\n고쳤어요.")
    assert "2. 펠메니 (Пельмени) · 10,000원" in out and "시르니키" not in out
    # 편집 요청에는 초안 번호와 편집 도구만 붙는다
    messages, tools = chat.requests[0]
    assert [t["function"]["name"] for t in tools] == ["edit_menu_draft"]
    assert '"number": 3' in messages[0]["content"]
    bot.ask("그대로 올려줘")
    assert set(menu_names(tmp_path)) == {"메도빅", "나폴레옹", "아메리카노", "블린", "펠메니"}


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"number": 9, "price": 1000}, "no_such_number"),
        ({"number": 1, "price": 0}, "invalid_price"),
        ("not json", "invalid_arguments"),
    ],
)
def test_invalid_edits_change_nothing(tmp_path, arguments, error):
    bot = assistant(tmp_path)
    bot.ask_photo(JPEG)
    before = bot.menu_form()
    assert bot._apply_draft_edit(arguments)["error"] == error
    assert bot.menu_form() == before


def test_photo_without_menu(tmp_path):
    bot = assistant(tmp_path, vision="[]")
    assert bot.ask_photo(JPEG).startswith("사진에서 메뉴를 찾지 못했어요")
    assert bot.menu_drafts is None


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
