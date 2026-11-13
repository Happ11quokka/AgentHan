import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agenthan.agent import owner_assistant as owner
from agenthan.agent import place_matcher as matcher
from agenthan.agent.clova_chat import ClovaChatError, chat
from agenthan.tools.dummy_store import SEED_PATH, DummyStore, DummyStoreError
from agenthan.tools.feature_tools import (
    DELEGATED_FEATURES,
    execute_feature_tool,
    feature_tool_schema,
)


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
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


def answer(text):
    return {"role": "assistant", "content": text}


class FakeChat:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    def __call__(self, messages, *, tools):
        self.requests.append(copy.deepcopy((messages, tools)))
        return next(self.replies)


def fake_embed(_):
    # Orthogonal to every catalog vector: use the first 19 basis vectors in tests.
    return [0.0] * 19 + [1.0] + [0.0] * 1004


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


def test_menu_schema():
    params = feature_tool_schema("menu")["function"]["parameters"]
    assert params["required"] == ["name"]
    assert set(params["properties"]) == {"name", "action", "price", "new_name", "description"}
    assert "photo" not in params["properties"]
    with pytest.raises(ValueError):
        feature_tool_schema("review")  # delegated to agenthan.agent.review


def test_missing_price_then_followup_saves(tmp_path, mock_index):
    path = tmp_path / "store.json"
    # Start with an existing copy so byte-for-byte non-mutation is meaningful.
    original = SEED_PATH.read_bytes()
    path.write_bytes(original)
    fake = FakeChat(
        call("upsert_menu", {"name": "연습 차"}),
        answer("가격 (원)을 알려주세요."),
        call("upsert_menu", {"name": "연습 차", "price": 5000}, "call-2"),
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
    assert "연습 차" in second and "5000" in second and "바꿀까요" in second
    assert path.read_bytes() == original  # Previewed only, waiting for approval.
    assert "5000원" in embedded
    assert any(m["role"] == "user" and "신메뉴" in m["content"] for m in fake.requests[2][0])
    assert fake.requests[0][1] == [feature_tool_schema("menu")]
    third = assistant.ask("네")
    assert len(fake.requests) == 3  # Approval runs the previewed call without the model.
    assert "더미 저장 확인" in third
    saved = read_file(path)["menus"]
    assert next(m for m in saved if m["name"] == "연습 차")["price"] == 5000
    assert assistant.pending_feature is None and assistant.pending_action is None
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
    fake = FakeChat(call("upsert_menu", {"name": "  메도빅  ", "price": 8000}))
    assistant = owner.OwnerAssistant(path, chat=fake, embed=feature_embed(mock_index, "menu"))
    preview = assistant.ask("메도빅 메뉴 가격을 8000원으로 수정")
    assert "8000" in preview and read_file(path) == before
    pending = json.loads(assistant.history[-2]["content"])
    assert pending["action"] == "pending_approval"
    result = assistant.ask("네")
    after = read_file(path)
    tool = assistant.trace["results"][-1]
    assert tool["action"] == "updated"
    assert tool["previous_price"] == before["menus"][0]["price"] == 7500
    assert tool["record"] == after["menus"][0]
    assert after["menus"][0]["price"] == 8000
    assert len(before["menus"]) == len(after["menus"])
    assert "7500" in result and "8000" in result
    assert SEED_PATH.read_bytes() == seed_before


def test_normalized_name_is_upserted(tmp_path):
    store = DummyStore(tmp_path / "store.json")
    assert store.upsert_menu("ＴＥＳＴ  차", 1000)["action"] == "created"
    result = store.upsert_menu("test 차", 2000)
    assert result["action"] == "updated"
    assert result["previous_price"] == 1000
    assert len(store.read()["menus"]) == 5


def keyword_only(_):
    raise ValueError("force keyword-only matching")


def test_pending_menu_switches_when_owner_clearly_asks_other_feature(tmp_path):
    fake = FakeChat(answer("가격을 알려주세요."), answer("어떤 예약인가요?"))
    assistant = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=keyword_only)
    assistant.ask("신메뉴 등록하고 싶어요")
    assert assistant.pending_feature == "menu"
    assert assistant.ask("예약 받고 싶어요") == "어떤 예약인가요?"
    assert [t["function"]["name"] for t in fake.requests[1][1]] == ["update_reservation_status"]
    assert assistant.pending_feature == "reservation"


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize(
    ("feature_id", "arguments", "labels"),
    [
        ("menu", {}, ["메뉴 이름"]),
        ("menu", {"name": "차"}, ["가격 (원)"]),
        ("menu", {"name": " ", "price": 5000}, ["메뉴 이름"]),
        ("menu", {"name": 42, "price": 5000}, ["메뉴 이름"]),
        *[
            ("menu", {"name": "차", "price": p}, ["가격 (원)"])
            for p in (None, "", 0, -1, "abc", True, 1.5, "5000", [], {})
        ],
    ],
)
def test_invalid_arguments_do_not_write(tmp_path, existing, feature_id, arguments, labels):
    path = tmp_path / "store.json"
    before = SEED_PATH.read_bytes()
    if existing:
        path.write_bytes(before)
    result = execute_feature_tool(DummyStore(path), "upsert_menu", arguments)
    assert not result["ok"]
    assert result["missing_fields"] == labels
    if existing:
        assert path.read_bytes() == before
    else:
        assert not path.exists()


@pytest.mark.parametrize("arguments", [None, [], "not json", "[]"])
def test_malformed_arguments_do_not_write(tmp_path, arguments):
    path = tmp_path / "store.json"
    result = execute_feature_tool(DummyStore(path), "upsert_menu", arguments)
    assert result["missing_fields"] == ["메뉴 이름"]
    assert not path.exists()


def test_unknown_tool_and_unsupported_fields_do_not_write(tmp_path):
    path = tmp_path / "store.json"
    store = DummyStore(path)
    assert execute_feature_tool(store, "send_reply", {})["error"] == "tool_not_allowed"
    assert execute_feature_tool(store, "save_review_reply", {})["error"] == "tool_not_allowed"
    for extra in ({"photo": "photo.png"}, {"unknown": "value"}):
        result = execute_feature_tool(store, "upsert_menu", {"name": "차", "price": 1000, **extra})
        assert result["error"] == "unsupported_fields"
    assert not path.exists()


def test_unrelated_has_no_tools(tmp_path):
    fake = FakeChat()
    assistant = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=fake_embed)
    response = assistant.ask("오늘 날씨는?")
    assert fake.requests == []
    assert not assistant.store.path.exists()
    assert "메뉴" in response and "답글" in response


TOOL_LOOP_FEATURES = sorted(
    owner.TOOL_FEATURE_IDS - {"statistics"}  # statistics goes to report_explainer
)


def test_every_catalog_feature_has_a_route():
    routed = owner.TOOL_FEATURE_IDS | DELEGATED_FEATURES
    assert routed == {f["id"] for f in matcher.load_features()}
    assert len(TOOL_LOOP_FEATURES) == 16


def with_targets(feature_id, ids):
    """The feature's tool whose record id may only be one of the candidate ids."""
    schema = feature_tool_schema(feature_id)
    if feature_id in owner.RECORD_TARGETS:
        param = owner.RECORD_TARGETS[feature_id][1]
        schema["function"]["parameters"]["properties"][param]["enum"] = list(ids)
    return schema


# Seed records the owner has not pointed at yet: all of them are candidates (capped).
SEED_IDS = {
    "order": ["order-1", "order-2"],
    "reservation": ["resv-1", "resv-2"],
    "talktalk": ["talk-1", "talk-2"],
}


@pytest.mark.parametrize("feature_id", TOOL_LOOP_FEATURES)
def test_only_matched_feature_tool_is_exposed(tmp_path, mock_index, feature_id):
    # Read tools that need nothing get one nudge after a question back, hence two answers.
    fake = FakeChat(answer("무엇을 도와드릴까요?"), answer("무엇을 도와드릴까요?"))
    assistant = owner.OwnerAssistant(
        tmp_path / "store.json", chat=fake, embed=feature_embed(mock_index, feature_id)
    )
    assistant.ask("요청")
    assert fake.requests[0][1] == [with_targets(feature_id, SEED_IDS.get(feature_id, []))]
    assert assistant.pending_feature == feature_id
    assert not assistant.store.path.exists()


def test_reservation_asks_missing_then_updates(tmp_path, mock_index):
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-1"}),
        answer("어떤 상태로 바꿀까요?"),
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "confirmed"}),
    )
    path = tmp_path / "store.json"
    assistant = owner.OwnerAssistant(
        path, chat=fake, embed=feature_embed(mock_index, "reservation")
    )
    response = assistant.ask("resv-1 예약 처리해줘")
    assert "확인할 항목" in response
    assert not path.exists()
    response = assistant.ask("확정")
    assert "바꿀까요" in response and not path.exists()
    response = assistant.ask("네")
    assert "더미 저장 확인" in response
    reservations = read_file(path)["reservations"]
    assert [r["status"] for r in reservations] == ["confirmed", "requested"]
    assert assistant.pending_feature is None


def test_read_only_tool_shows_lookup_label(tmp_path, mock_index):
    fake = FakeChat(
        call("get_settlement_summary", {"period": "this_month"}), answer("이번 달 정산이에요.")
    )
    path = tmp_path / "store.json"
    assistant = owner.OwnerAssistant(path, chat=fake, embed=feature_embed(mock_index, "settlement"))
    response = assistant.ask("이번 달 정산 알려줘")
    assert "더미 조회 결과" in response and "더미 저장 확인" not in response
    assert tool_results(fake.requests[1])[0]["ok"]


def test_other_feature_tool_is_refused(tmp_path, mock_index):
    fake = FakeChat(
        call("create_coupon", {"name": "할인", "discount_percent": 10}),
        answer("쿠폰은 여기서 만들 수 없어요."),
    )
    path = tmp_path / "store.json"
    assistant = owner.OwnerAssistant(
        path, chat=fake, embed=feature_embed(mock_index, "reservation")
    )
    assistant.ask("예약 확인")
    assert tool_results(fake.requests[1])[0]["error"] == "tool_not_allowed"
    assert not path.exists()


def reservation_preview(tmp_path, mock_index, *replies):
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "confirmed"}),
        *replies,
    )
    path = tmp_path / "store.json"
    assistant = owner.OwnerAssistant(
        path, chat=fake, embed=feature_embed(mock_index, "reservation")
    )
    assert "바꿀까요" in assistant.ask("resv-1 예약 확정")
    assert assistant.pending_action is not None and not path.exists()
    return assistant, fake, path


def test_decline_drops_pending_write(tmp_path, mock_index):
    assistant, fake, path = reservation_preview(tmp_path, mock_index)
    assert "저장한 것은 없어요" in assistant.ask("취소")
    assert assistant.pending_action is None and assistant.pending_feature is None
    assert len(fake.requests) == 1 and not path.exists()


def test_other_reply_edits_request_instead_of_approving(tmp_path, mock_index):
    assistant, fake, path = reservation_preview(
        tmp_path,
        mock_index,
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "cancelled"}),
    )
    # Starts with 예 but is not a bare yes: it must not approve the confirmed preview.
    assert "cancelled" in assistant.ask("예약 취소로 바꿔줘")
    assert not path.exists()
    assert json.loads(assistant.pending_action["arguments"])["status"] == "cancelled"
    assert fake.requests[1][1] == [with_targets("reservation", ["resv-1"])]
    assistant.ask("네")
    assert read_file(path)["reservations"][0]["status"] == "cancelled"


def test_read_only_tools_skip_approval():
    read_only = {
        "list_call_history",
        "get_naverpay_signup_guide",
        "get_settlement_summary",
        "check_pos_compatibility",
        "diagnose_search_visibility",
        "get_statistics",
        "search_help_articles",
    }
    for name in owner.TOOL_FEATURES:
        assert owner.needs_approval(name) is (name not in read_only), name


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
    assert fake.requests[-1][1] == []
    assert not path.exists()
    # Refused calls have matching tool results so later history is well formed.
    assert assistant.history[-2]["role"] == "tool"
    assert json.loads(assistant.history[-2]["content"])["error"] == "tool_limit"
    assert "1000" in assistant.ask("차 메뉴 1000원 등록")


def test_batch_exceeding_limit_is_not_partially_executed(tmp_path, mock_index):
    batch = call("upsert_menu", {"name": "차", "price": 1000})
    batch["tool_calls"] += call("upsert_menu", {"name": "차2", "price": 2000}, "call-2")[
        "tool_calls"
    ]
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

    def live_chat(messages, *, tools):
        response = chat(messages, tools=tools)
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
    calls = [c for response in responses for c in response.get("tool_calls", [])]
    assert calls and calls[0]["function"]["name"] == "upsert_menu"
    assert "연습 허브차" in result and "5500" in result
    assert not path.exists()  # Waiting for approval.
    assistant.ask("네")
    menu = next(m for m in read_file(path)["menus"] if m["name"] == "연습 허브차")
    assert menu["price"] == 5500
    assert SEED_PATH.read_bytes() == before


class Rejecting(FakeChat):
    def __call__(self, messages, *, tools):
        self.requests.append(copy.deepcopy((messages, tools)))
        raise owner.ClovaRequestRejected("rejected")


def test_hcx_400_answers_owner_and_drops_the_turn(tmp_path, mock_index):
    fake = Rejecting()
    path = tmp_path / "store.json"
    assistant = owner.OwnerAssistant(path, chat=fake, embed=feature_embed(mock_index, "smartcall"))
    response = assistant.ask("스마트콜에서 부재중 전화만 보여 주세요.")
    assert "다시 말씀해 주세요" in response and "저장한 것은 없어요" in response
    assert assistant.history == [] and assistant.trace["rejected"]
    assert assistant.pending_feature == "smartcall" and not path.exists()
