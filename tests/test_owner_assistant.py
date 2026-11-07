import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agenthan.agent import owner_assistant as owner
from agenthan.agent.clova_chat import ClovaChatError, chat
from agenthan.rag import place_matcher as matcher
from agenthan.tools.dummy_store import SEED_PATH, DummyStore, DummyStoreError
from agenthan.tools.owner_tools import TOOL_NAMES, execute_tool, tool_schema


@pytest.fixture(autouse=True)
def offline_by_default(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        return
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        pytest.fail("Owner unit tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)


def call(name, arguments, call_id="call-1"):
    return {
        "role": "assistant",
        "content": "",
        "toolCalls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


def answer(text):
    return {"role": "assistant", "content": text}


class FakeChat:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    def __call__(self, messages, *, tools, tool_choice):
        self.requests.append(copy.deepcopy((messages, tools, tool_choice)))
        return next(self.replies)


def fake_embed(_):
    # Orthogonal to every catalog vector: use the first 18 basis vectors in tests.
    return [0.0] * 18 + [1.0] + [0.0] * 1005


@pytest.fixture
def mock_index(monkeypatch):
    index = {}
    for i, feature in enumerate(matcher.load_features()):
        vector = [0.0] * 1024
        vector[i] = 1.0
        index[feature["id"]] = vector
    monkeypatch.setattr(matcher, "_load_index", lambda _: index)
    return index


def feature_embed(index, feature_id):
    return lambda _: index[feature_id]


def read_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def tool_results(request):
    return [json.loads(m["content"]) for m in request[0] if m["role"] == "tool"]


def test_schemas_follow_catalog():
    for feature in matcher.load_features():
        if not feature["input_supported"]:
            continue
        schema = tool_schema(feature["id"])["function"]["parameters"]
        assert set(schema["properties"]) == {f["id"] for f in feature["input_fields"]}
        assert schema["required"] == [f["id"] for f in feature["input_fields"] if f["required"]]
        for field in feature["input_fields"]:
            assert field["name_ko"] in schema["properties"][field["id"]]["description"]
    assert tool_schema("menu")["function"]["parameters"]["required"] == ["name", "price"]
    assert tool_schema("review_reply")["function"]["parameters"]["required"] == [
        "review_text",
        "reply_text",
    ]


def test_missing_price_then_followup_saves(tmp_path, mock_index):
    path = tmp_path / "store.json"
    # Start with an existing copy so byte-for-byte non-mutation is meaningful.
    original = SEED_PATH.read_bytes()
    path.write_bytes(original)
    fake = FakeChat(
        call("upsert_menu", {"name": "연습 차"}),
        answer("가격 (원)을 알려주세요."),
        call("upsert_menu", {"name": "연습 차", "price": 5000}, "call-2"),
        answer("저장했습니다."),
    )
    embedded = []

    def embed(text):
        embedded.append(text)
        # A bare numeric reply has no independent feature; context rematching is needed.
        return mock_index["menu"] if "신메뉴" in text else fake_embed(text)

    assistant = owner.OwnerAssistant(path, chat=fake, embed=embed)
    first = assistant.ask("신메뉴 연습 차 등록하고 싶어요")
    assert "가격" in first
    assert path.read_bytes() == original
    assert tool_results(fake.requests[1])[-1]["missing_fields"] == ["가격 (원)"]
    second = assistant.ask("5000원")
    assert "연습 차" in second and "5000" in second
    saved = read_file(path)["menus"]
    assert next(m for m in saved if m["name"] == "연습 차")["price"] == 5000
    assert "5000원" in embedded
    assert any("신메뉴" in text and "5000원" in text for text in embedded)
    assert any(m["role"] == "user" and "신메뉴" in m["content"] for m in fake.requests[2][0])
    assert fake.requests[0][1] == [tool_schema("menu")]
    assert fake.requests[-1][1:] == ([], "none")
    assert SEED_PATH.read_bytes() == original


def test_model_can_ask_without_calling_then_keep_context(tmp_path, mock_index):
    path = tmp_path / "store.json"
    fake = FakeChat(
        answer("메뉴 이름과 가격을 알려주세요."),
        call("upsert_menu", {"name": "연습 차", "price": 3000}),
        answer("저장했어요."),
    )
    assistant = owner.OwnerAssistant(path, chat=fake, embed=feature_embed(mock_index, "menu"))
    assert "가격" in assistant.ask("신메뉴 등록하고 싶어요")
    assert not path.exists()
    assert "3000" in assistant.ask("연습 차, 3000원")
    assert any(
        m["role"] == "assistant" and "알려주세요" in m["content"] for m in fake.requests[1][0]
    )


def test_update_existing_menu_rereads_price(tmp_path, mock_index):
    path = tmp_path / "store.json"
    seed_before = SEED_PATH.read_bytes()
    path.write_bytes(seed_before)
    before = read_file(path)
    fake = FakeChat(
        call("upsert_menu", {"name": "  연습   라떼  ", "price": 4500}),
        answer("가격을 수정했어요."),
    )
    assistant = owner.OwnerAssistant(path, chat=fake, embed=feature_embed(mock_index, "menu"))
    result = assistant.ask("연습 라떼 메뉴 가격을 4500원으로 수정")
    after = read_file(path)
    tool = tool_results(fake.requests[1])[-1]
    assert tool["action"] == "updated"
    assert tool["previous_price"] == before["menus"][0]["price"] == 4000
    assert tool["record"] == after["menus"][0]
    assert after["menus"][0]["price"] == 4500
    assert len(before["menus"]) == len(after["menus"])
    assert "4000" in result and "4500" in result
    assert SEED_PATH.read_bytes() == seed_before


def test_normalized_name_is_upserted(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    assert store.upsert_menu("ＴＥＳＴ  차", 1000)["action"] == "created"
    result = store.upsert_menu("test 차", 2000)
    assert result["action"] == "updated"
    assert result["previous_price"] == 1000
    assert len(store.read()["menus"]) == 3


def test_review_reply_saved_without_sending(tmp_path, mock_index):
    path = tmp_path / "store.json"
    seed = SEED_PATH.read_bytes()
    fake = FakeChat(
        call("save_review_reply", {"review_text": "음료가 맛있어요.", "reply_text": "감사합니다!"}),
        answer("답글을 저장했어요. 발송하지 않았어요."),
    )
    assistant = owner.OwnerAssistant(
        path, chat=fake, embed=feature_embed(mock_index, "review_reply")
    )
    response = assistant.ask("리뷰 '음료가 맛있어요.'에 '감사합니다!' 답글 저장해 주세요")
    review = read_file(path)["reviews"][0]
    assert review["reply_text"] == "감사합니다!"
    assert review["status"] == "saved_not_sent"
    assert tool_results(fake.requests[1])[-1]["record"] == review
    assert "감사합니다!" in response and "saved_not_sent" in response
    assert fake.requests[0][1] == [tool_schema("review_reply")]
    assert SEED_PATH.read_bytes() == seed


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize(
    ("feature_id", "arguments", "labels"),
    [
        ("menu", {}, ["메뉴 이름", "가격 (원)"]),
        ("menu", {"name": "차"}, ["가격 (원)"]),
        ("menu", {"name": " ", "price": 5000}, ["메뉴 이름"]),
        ("menu", {"name": 42, "price": 5000}, ["메뉴 이름"]),
        *[
            ("menu", {"name": "차", "price": p}, ["가격 (원)"])
            for p in (None, "", 0, -1, "abc", True, 1.5, "5000", [], {})
        ],
        ("review_reply", {}, ["손님 리뷰 내용", "답글 내용"]),
        ("review_reply", {"review_text": "음료가 맛있어요."}, ["답글 내용"]),
        ("review_reply", {"review_text": " ", "reply_text": "감사합니다"}, ["손님 리뷰 내용"]),
        ("review_reply", {"review_text": "음료가 맛있어요.", "reply_text": "\t"}, ["답글 내용"]),
    ],
)
def test_invalid_arguments_do_not_write(tmp_path, existing, feature_id, arguments, labels):
    path = tmp_path / "store.json"
    before = SEED_PATH.read_bytes()
    if existing:
        path.write_bytes(before)
    result = execute_tool(DummyStore(path), feature_id, TOOL_NAMES[feature_id], arguments)
    assert not result["ok"]
    assert result["missing_fields"] == labels
    if existing:
        assert path.read_bytes() == before
    else:
        assert not path.exists()


@pytest.mark.parametrize("arguments", [None, [], "not json", "[]"])
def test_malformed_arguments_do_not_write(tmp_path, arguments):
    path = tmp_path / "store.json"
    result = execute_tool(DummyStore(path), "menu", "upsert_menu", arguments)
    assert result["missing_fields"] == ["메뉴 이름", "가격 (원)"]
    assert not path.exists()


def test_unknown_tool_unknown_review_and_optional_fields_do_not_write(tmp_path):
    path = tmp_path / "store.json"
    store = DummyStore(path)
    assert execute_tool(store, "menu", "send_reply", {})["error"] == "tool_not_allowed"
    result = execute_tool(
        store,
        "review_reply",
        "save_review_reply",
        {
            "review_text": "없는 리뷰",
            "reply_text": "감사합니다",
        },
    )
    assert result["error"] == "review_not_found"
    for extra in ({"photo": "photo.png"}, {"description": "설명"}, {"unknown": "value"}):
        assert (
            execute_tool(
                store,
                "menu",
                "upsert_menu",
                {
                    "name": "차",
                    "price": 1000,
                    **extra,
                },
            )["error"]
            == "unsupported_fields"
        )
    assert not path.exists()


@pytest.mark.parametrize(
    "feature_id,text", [("reservation", "예약 받고 싶어요"), (None, "오늘 날씨는?")]
)
def test_unsupported_or_unrelated_has_no_tools(tmp_path, mock_index, feature_id, text):
    fake = FakeChat()
    embed = feature_embed(mock_index, feature_id) if feature_id else fake_embed
    assistant = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=embed)
    response = assistant.ask(text)
    assert fake.requests == []
    assert not assistant.store.path.exists()
    if feature_id:
        feature = matcher.get_feature(feature_id)
        assert feature["name_ko"] in response and feature["summary_ko"] in response
        assert "지원하지" in response
    else:
        assert "메뉴" in response and "답글" in response


def test_tool_limit_and_next_turn_recovery(tmp_path, mock_index):
    fake = FakeChat(
        *[call("upsert_menu", {}, f"call-{i}") for i in range(4)],
        call("upsert_menu", {"name": "차", "price": 1000}, "call-5"),
        answer("저장했어요."),
    )
    path = tmp_path / "store.json"
    assistant = owner.OwnerAssistant(path, chat=fake, embed=feature_embed(mock_index, "menu"))
    assert "한도" in assistant.ask("메뉴 등록")
    assert len(fake.requests) == 4
    assert fake.requests[-1][1:] == ([], "none")
    assert not path.exists()
    # Refused calls have matching tool results so later history is well formed.
    assert assistant.history[-2]["role"] == "tool"
    assert json.loads(assistant.history[-2]["content"])["error"] == "tool_limit"
    assert "1000" in assistant.ask("차 메뉴 1000원 등록")


def test_batch_exceeding_limit_is_not_partially_executed(tmp_path, mock_index):
    batch = call("upsert_menu", {"name": "차", "price": 1000})
    batch["toolCalls"] += call("upsert_menu", {"name": "차2", "price": 2000}, "call-2")["toolCalls"]
    fake = FakeChat(batch)
    assistant = owner.OwnerAssistant(
        tmp_path / "store.json",
        chat=fake,
        embed=feature_embed(mock_index, "menu"),
        max_tool_calls=1,
    )
    assert "한도" in assistant.ask("메뉴 등록")
    assert not assistant.store.path.exists()


def test_store_rejects_seed_and_invalid_direct_input(tmp_path):
    with pytest.raises(DummyStoreError):
        DummyStore(SEED_PATH)
    store = DummyStore(tmp_path / "store.json")
    with pytest.raises(DummyStoreError):
        store.upsert_menu("차", 0)
    with pytest.raises(DummyStoreError):
        store.save_review_reply("", "")
    assert not store.path.exists()


def test_atomic_write_preserves_existing_file_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "store.json"
    before = SEED_PATH.read_bytes()
    path.write_bytes(before)

    def fail_replace(self, target):
        assert self.parent == path.parent
        assert read_file(self)["menus"][-1]["name"] == "연습 차"
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(DummyStoreError):
        DummyStore(path).upsert_menu("연습 차", 1000)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_cli_without_key_exits_one(tmp_path):
    env = dict(os.environ)
    env.pop("CLOVASTUDIO_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenthan.agent.owner_assistant",
            "--store",
            str(tmp_path / "store.json"),
        ],
        input="메뉴 등록\n",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "CLOVASTUDIO_API_KEY 환경변수를 설정해 주세요."
    assert not (tmp_path / "store.json").exists()


def test_cli_api_failure_sanitized(tmp_path, monkeypatch, capsys):
    secret = "fake-secret-server-body"
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-test-key")
    monkeypatch.setattr(sys, "argv", ["owner_assistant", "--store", str(tmp_path / "store.json")])
    monkeypatch.setattr("builtins.input", lambda _: "메뉴 등록")

    def fail(self, text):
        raise ClovaChatError(secret)

    monkeypatch.setattr(owner.OwnerAssistant, "ask", fail)
    assert owner.main() == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "fake-test-key" not in output.out + output.err


@pytest.mark.parametrize("text", ["exit", None])
def test_cli_exit_and_eof(tmp_path, monkeypatch, text):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-test-key")
    monkeypatch.setattr(sys, "argv", ["owner_assistant", "--store", str(tmp_path / "store.json")])

    def read(_):
        if text is None:
            raise EOFError
        return text

    monkeypatch.setattr("builtins.input", read)
    assert owner.main() == 0


@pytest.mark.network
def test_live_hcx_menu_call_and_save(tmp_path):
    if not os.environ.get("CLOVASTUDIO_API_KEY"):
        pytest.skip("CLOVASTUDIO_API_KEY is not configured")
    before = SEED_PATH.read_bytes()
    responses = []

    def live_chat(messages, *, tools, tool_choice):
        response = chat(messages, tools=tools, tool_choice=tool_choice)
        responses.append(response)
        return response

    # This test isolates real HCX function calling; matching uses an injected embed
    # failure to select the existing keyword fallback without another API call.
    def keyword_only(_):
        raise ValueError("Use keyword matching for this chat integration test")

    path = tmp_path / "store.json"
    assistant = owner.OwnerAssistant(path, chat=live_chat, embed=keyword_only)
    result = assistant.ask(
        "메뉴 이름은 '연습 허브차', 가격은 5500원입니다. 신메뉴로 등록해 주세요."
    )
    calls = [c for response in responses for c in response.get("toolCalls", [])]
    assert calls and calls[0]["function"]["name"] == "upsert_menu"
    menu = next(m for m in read_file(path)["menus"] if m["name"] == "연습 허브차")
    assert menu["price"] == 5500
    assert "연습 허브차" in result and "5500" in result
    assert SEED_PATH.read_bytes() == before
