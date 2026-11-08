"""HcxNlu·ClovaClient 단위 테스트. 네트워크 없이 `httpx.MockTransport`로 응답을 꾸민다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.clova import ClovaClient, ClovaError, load_api_key
from agenthan.voice.nlu.hcx import (
    FORCE_PARSE_TURN,
    MODEL,
    HcxNlu,
    acts_from_args,
    acts_from_result,
    build_messages,
)

MENU = ("메도빅", "나폴레옹", "아메리카노", "블린")
CTX = NluContext(
    menu_names=MENU,
    cart_summary="메도빅 하나",
    last_question="더 필요하신 거 있으세요?",
    state="ORDERING",
)
KEY = "test-key-not-real"


def ok_body(message: dict[str, Any]) -> dict[str, Any]:
    return {"status": {"code": "20000", "message": "OK"},
            "result": {"message": {"role": "assistant", **message}, "finishReason": "stop"}}


def tool_message(args: Any) -> dict[str, Any]:
    return {"content": "", "toolCalls": [
        {"id": "call_1", "type": "function", "function": {"name": "parse_turn", "arguments": args}}
    ]}


def client_with(handler, sleeps: list[float] | None = None) -> ClovaClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ClovaClient(KEY, http=http, sleep=(sleeps if sleeps is not None else []).append)


# ---------------------------------------------------------------- 프롬프트 (D4)


def test_prompt_holds_only_menu_cart_question_and_one_utterance() -> None:
    messages = build_messages("블린 하나요", CTX)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == "블린 하나요"
    system = messages[0]["content"]
    assert "메뉴 목록: 메도빅, 나폴레옹, 아메리카노, 블린" in system
    assert "장바구니: 메도빅 하나" in system
    assert "직전 질문: 더 필요하신 거 있으세요?" in system


def test_payload_offers_parse_turn_with_auto_by_default() -> None:
    payload = HcxNlu(ClovaClient(KEY)).payload("블린 하나요", CTX)
    assert [t["function"]["name"] for t in payload["tools"]] == ["parse_turn"]
    assert payload["toolChoice"] == "auto"
    assert payload["maxTokens"] >= 1024
    forced = HcxNlu(ClovaClient(KEY), tool_choice=FORCE_PARSE_TURN).payload("블린 하나요", CTX)
    assert forced["toolChoice"] == {"type": "function", "function": {"name": "parse_turn"}}


# ---------------------------------------------------------------- 응답 읽기


def test_reads_tool_call_arguments_object_and_string() -> None:
    args = {"acts": [{"type": "add_item", "menu_text": "블린", "menu_guess": "블린", "qty": 2}]}
    want = [Act("add_item", menu_text="블린", menu_guess="블린", qty=2)]
    assert acts_from_result({"message": tool_message(args)}, "블린 두 개요", CTX) == want
    as_string = tool_message(json.dumps(args, ensure_ascii=False))
    assert acts_from_result({"message": as_string}, "블린 두 개요", CTX) == want


def test_reads_json_written_in_content_with_trailing_junk() -> None:
    content = '{"acts":[{"type":"done_ordering"}]})'
    assert acts_from_result({"message": {"content": content}}, "그게 다예요", CTX) == [
        Act("done_ordering")
    ]


def test_multiple_acts_keep_order() -> None:
    args = {"acts": [
        {"type": "add_item", "menu_text": "메도빅", "qty": 1},
        {"type": "set_pickup_time", "time_text": "7시"},
    ]}
    acts = acts_from_args(args, "메도빅 하나 주시고 7시에 갈게요", CTX)
    assert [a.type for a in acts] == ["add_item", "set_pickup_time"]
    assert acts[1].time_text == "7시"


@pytest.mark.parametrize("message", [
    {"content": "done_ordering"},  # 라벨만 쓴 본문
    {"content": "죄송해요"},
    {"content": ""},
    {"content": '{"acts": "add_item"}'},
    {"content": '{"acts": []}'},
    tool_message({"wrong": 1}),
    tool_message("{not json"),
])
def test_unreadable_response_is_unknown(message: dict[str, Any]) -> None:
    assert acts_from_result({"message": message}, "음", CTX) == [Act("unknown", free_text="음")]


def test_invalid_acts_are_dropped_and_fields_validated() -> None:
    args = {"acts": [
        {"type": "order_cake"},  # 없는 타입
        "add_item",
        {"type": "add_item", "menu_text": "블린", "menu_guess": "없음", "qty": "2"},
        {"type": "change_qty", "menu_text": "블린", "qty": 0},
        {"type": "change_qty", "menu_text": "블린", "qty": True},
    ]}
    acts = acts_from_args(args, "블린 두 개요", CTX)
    assert acts == [
        Act("add_item", menu_text="블린", qty=2),  # 목록 밖 menu_guess는 버린다
        Act("change_qty", menu_text="블린"),
        Act("change_qty", menu_text="블린"),
    ]


# ---------------------------------------------------------------- 원문 대조


def test_invented_menu_is_dropped() -> None:
    """스파이크에서 본 것: "6시 반에 갈게요" → 아메리카노 추가 + 픽업 시간."""
    args = {"acts": [
        {"type": "add_item", "menu_text": "아메리카노", "menu_guess": "아메리카노", "qty": 1},
        {"type": "set_pickup_time", "time_text": "6시 반"},
    ]}
    assert acts_from_args(args, "6시 반에 갈게요", CTX) == [
        Act("set_pickup_time", time_text="6시 반")
    ]


def test_invented_menu_on_other_acts_is_cleared() -> None:
    args = {"acts": [{"type": "change_qty", "menu_text": "나폴레옹", "menu_guess": "나폴레옹",
                      "qty": 2}]}
    assert acts_from_args(args, "두 개요", CTX) == [Act("change_qty", qty=2)]


@pytest.mark.parametrize(("said", "want"), [
    ("메도비크 두 개요", "메도비크"),
    ("메도빜 하나요", "메도빜"),  # 판정은 매처가 손님 말 + menu_guess로 한다
    ("어 메도비크하나 주세요", "메도비크하나"),
])
def test_respelled_menu_goes_back_to_what_was_said(said: str, want: str) -> None:
    args = {"acts": [{"type": "add_item", "menu_text": "메도빅", "menu_guess": "메도빅", "qty": 2}]}
    acts = acts_from_args(args, said, CTX)
    assert acts == [Act("add_item", menu_text=want, menu_guess="메도빅", qty=2)]


def test_rewritten_time_falls_back_to_whole_utterance() -> None:
    args = {"acts": [{"type": "set_pickup_time", "time_text": "6:30"}]}
    assert acts_from_args(args, "6시 반에 갈게요", CTX) == [
        Act("set_pickup_time", time_text="6시 반에 갈게요")
    ]


def test_questions_carry_the_original_utterance() -> None:
    args = {"acts": [{"type": "ask_menu_info", "free_text": "메도빅 많이 단가요?"}]}
    assert acts_from_args(args, "메도빅 많이 달아요", CTX) == [
        Act("ask_menu_info", menu_text="메도빅", menu_guess="메도빅",
            free_text="메도빅 많이 달아요")
    ]


# ---------------------------------------------------------------- HcxNlu.parse + ClovaClient


def test_parse_posts_to_dash_model_with_bearer_and_request_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        args = {"acts": [{"type": "done_ordering"}]}
        return httpx.Response(200, json=ok_body(tool_message(args)))

    acts = HcxNlu(client_with(handler)).parse("그게 다예요", CTX)
    assert acts == [Act("done_ordering")]
    (req,) = seen
    assert req.url.path == f"/v3/chat-completions/{MODEL}"
    assert MODEL == "HCX-DASH-002"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["x-ncp-clovastudio-request-id"]
    assert json.loads(req.content)["messages"][1]["content"] == "그게 다예요"


def test_forced_choice_rejection_becomes_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": {"code": "40009",
                                                    "message": "Unsupported function"}})

    nlu = HcxNlu(client_with(handler), tool_choice=FORCE_PARSE_TURN)
    assert nlu.parse("주차는요?", CTX) == [Act("unknown", free_text="주차는요?")]
    with pytest.raises(ClovaError) as err:
        client_with(handler).chat(MODEL, {})
    assert err.value.code == "40009"


def test_retries_429_then_succeeds_with_retry_after() -> None:
    calls = iter([
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(503),
        httpx.Response(200, json=ok_body({"content": '{"acts":[{"type":"unknown"}]}'})),
    ])
    sleeps: list[float] = []
    result = client_with(lambda r: next(calls), sleeps).chat(MODEL, {})
    assert result["message"]["content"]
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_two_retries_or_long_retry_after() -> None:
    sleeps: list[float] = []
    with pytest.raises(ClovaError) as err:
        client_with(lambda r: httpx.Response(500), sleeps).chat(MODEL, {})
    assert err.value.code == "http-500"
    assert sleeps == [1.0, 2.0]

    sleeps.clear()
    long_wait = httpx.Response(429, headers={"Retry-After": "30"})
    with pytest.raises(ClovaError):
        client_with(lambda r: long_wait, sleeps).chat(MODEL, {})
    assert sleeps == []


def test_body_status_and_network_errors_raise_clova_error() -> None:
    bad_status = {"status": {"code": "40001", "message": "bad"}, "result": None}
    with pytest.raises(ClovaError):
        client_with(lambda r: httpx.Response(200, json=bad_status)).chat(MODEL, {})

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ClovaError) as err:
        client_with(boom).chat(MODEL, {})
    assert err.value.code == "network"
    assert HcxNlu(client_with(boom)).parse("블린이요", CTX) == [
        Act("unknown", free_text="블린이요")
    ]


def test_key_never_shows_in_repr_or_errors() -> None:
    client = client_with(lambda r: httpx.Response(401))
    assert KEY not in repr(client)
    with pytest.raises(ClovaError) as err:
        client.chat(MODEL, {})
    assert KEY not in str(err.value)


def test_key_must_be_raw() -> None:
    with pytest.raises(ValueError):
        ClovaClient("Bearer abc")
    with pytest.raises(ValueError):
        ClovaClient("")


def test_load_api_key_from_env_then_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("# 주석\nTAVILY_API_KEY=x\nCLOVASTUDIO_API_KEY=from-file\n", encoding="utf-8")
    assert load_api_key(env) == "from-file"
    assert load_api_key(tmp_path / "missing.env") == ""
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "from-env")
    assert load_api_key(env) == "from-env"


def test_from_env_without_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    with pytest.raises(ValueError):
        HcxNlu.from_env(tmp_path / "none.env")


def test_tool_call_failure_retries_once_without_tools() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if "tools" in bodies[-1]:
            return httpx.Response(400, json={"status": {"code": "40009"}})
        return httpx.Response(200, json=ok_body({"content": '{"acts":[{"type":"done_ordering"}]}'}))

    assert HcxNlu(client_with(handler)).parse("그게 다예요", CTX) == [Act("done_ordering")]
    assert ["tools" in b for b in bodies] == [True, False]


def test_other_errors_do_not_retry_and_tools_false_sends_no_tools() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(400, json={"status": {"code": "40001"}})

    assert HcxNlu(client_with(handler)).parse("음", CTX) == [Act("unknown", free_text="음")]
    assert len(bodies) == 1
    bodies.clear()
    HcxNlu(client_with(handler), tools=False).parse("음", CTX)
    assert len(bodies) == 1
    assert "tools" not in bodies[0] and "toolChoice" not in bodies[0]
