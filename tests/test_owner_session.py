"""One session, several tasks: approvals survive interjections, photo drafts wait."""

import json

import pytest

import test_owner_assistant as base
from agenthan.agent import owner_assistant as owner
from agenthan.agent.task_ledger import TaskLedger
from agenthan.tools.dummy_store import DummyStore
from test_menu_photo import JPEG, KNOWLEDGE, NAMES, VISION, FakeV3

FakeChat, answer, call, read_file = base.FakeChat, base.answer, base.call, base.read_file
mock_index, offline_by_default = base.mock_index, base.offline_by_default  # fixtures

CONFIRM = {"reservation_id": "resv-1", "status": "confirmed"}


def routed(index, rules, default):
    def embed(text):
        return index[next((f for word, f in rules if word in text), default)]

    return embed


def reservation_then(tmp_path, index, *replies, rules=()):
    fake = FakeChat(call("update_reservation_status", CONFIRM), *replies)
    path = tmp_path / "store.json"
    bot = owner.OwnerAssistant(path, chat=fake, embed=routed(index, rules, "reservation"))
    assert "바꿀까요" in bot.ask("resv-1 예약 확정")
    return bot, fake, path


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("네", "approve"),
        ("네, 아까 번호 바꾸는 거 저장해 주세요", "approve"),
        ("확정 진행해 주세요", "approve"),
        ("그대로 올려줘", "approve"),
        ("네 그런데 리뷰부터", None),
        ("좋아요, 근데 저장 전에 리뷰 먼저 볼게요", None),
        ("예약 취소로 바꿔줘", None),
        ("네 10000원으로 해 주세요", None),
        ("아까 키워드는 그냥 취소할게요", "decline"),
        ("음 아니다, 나중에 할게요", "decline"),
        ("취소", "decline"),
    ],
)
def test_approval_intent(text, intent):
    assert owner.approval_intent(text) == intent


def test_preview_survives_an_interjection_and_is_approved_after(tmp_path, mock_index):
    bot, fake, path = reservation_then(
        tmp_path,
        mock_index,
        call("get_settlement_summary", {"period": "this_week"}, "call-2"),
        answer("이번 주 정산은 116,400원이에요."),
        rules=[("정산", "settlement")],
    )
    out = bot.ask("이번 주 정산 얼마예요?")
    assert "116,400" in out and "아직 저장 전이에요" in out
    assert bot.pending_action is not None and not path.exists()
    assert "저장했어요" in bot.ask("네, 아까 예약 확정 저장해 주세요")
    assert read_file(path)["reservations"][0]["status"] == "confirmed"
    assert len(fake.requests) == 3  # The approval ran the held preview without the model.


def test_contrast_reply_does_not_approve(tmp_path, mock_index):
    bot, fake, path = reservation_then(
        tmp_path,
        mock_index,
        answer("정산은 116,400원이에요."),
        rules=[("정산", "settlement")],
    )
    out = bot.ask("네 그런데 정산부터 알려 줘요")
    assert not path.exists() and bot.pending_action is not None
    assert "아직 저장 전이에요" in out


def test_store_change_after_preview_asks_again(tmp_path, mock_index):
    bot, fake, path = reservation_then(tmp_path, mock_index)
    store = DummyStore(path)
    data = store.read()
    data["reservations"][0]["status"] = "cancelled"  # Changed elsewhere after the preview.
    store.write(data)
    out = bot.ask("네")
    assert "바꿀까요" in out
    assert read_file(path)["reservations"][0]["status"] == "cancelled"
    assert bot.pending_action["preview"]["previous_status"] == "cancelled"
    bot.ask("네")
    assert read_file(path)["reservations"][0]["status"] == "confirmed"


def test_photo_drafts_wait_while_another_request_runs(tmp_path, mock_index):
    hours = {"day": "sun", "open_time": "10:00", "close_time": "18:00"}
    fake = FakeChat(call("update_business_hours", hours))
    path = tmp_path / "store.json"
    bot = owner.OwnerAssistant(
        path,
        chat=fake,
        embed=lambda _: mock_index["business_hours"],
        knowledge=KNOWLEDGE,
        clova_v3=FakeV3(VISION, NAMES),
    )
    bot.ask_photo(JPEG)
    out = bot.ask("사진 메뉴는 잠깐 두고, 일요일 영업을 10시부터 6시까지로 바꿔 주세요")
    assert "바꿀까요" in out and bot.menu_drafts is not None
    out = bot.ask("네")
    assert "사진 메뉴 초안도 저장 전" in out
    assert read_file(path)["shop"]["hours"]["sun"]["open_time"] == "10:00"
    assert {m["name"] for m in read_file(path)["menus"]} == {
        "메도빅",
        "나폴레옹",
        "아메리카노",
        "블린",
    }
    assert bot.ask("취소할게요") == "메뉴 등록을 취소했어요. 저장한 것은 없어요."


def test_held_collecting_task_resumes_after_a_read(tmp_path, mock_index):
    fake = FakeChat(
        answer("가격을 알려 주세요."),
        call("get_settlement_summary", {"period": "this_week"}, "call-2"),
        answer("정산은 116,400원이에요."),
        call("upsert_menu", {"name": "연습 차", "price": 5000}, "call-3"),
    )
    rules = [("정산", "settlement"), ("원", "menu")]
    bot = owner.OwnerAssistant(
        tmp_path / "store.json", chat=fake, embed=routed(mock_index, rules, "menu")
    )
    bot.ask("신메뉴 연습 차 등록할게요")
    assert "이어서" in bot.ask("이번 주 정산은요?")
    assert bot.pending_feature == "menu"
    assert "바꿀까요" in bot.ask("5000원")
    assert any("신메뉴" in m.get("content", "") for m in fake.requests[-1][0])


def test_ledger_stack_pauses_and_resumes():
    ledger = TaskLedger()
    first = ledger.start("business_info")
    ledger.set_preview(first, {"name": "x", "arguments": {}, "preview": {}})
    second = ledger.start("settlement")
    assert first.status == "paused" and ledger.active() is second
    assert ledger.close(second, "done") is first
    assert first.status == "awaiting_approval" and first.preview["version"] == 1
    ledger.drop_preview(first)
    assert first.version == 2 and first.status == "collecting"
    for _ in range(4):
        ledger.tick()
    ledger._pause(first)
    assert not ledger.quiet(first)
    for _ in range(4):
        ledger.tick()
    assert ledger.quiet(first) and first.is_open  # Quiet, but kept.
    assert json.dumps(ledger.log(), ensure_ascii=False) == '["t2 settlement: done"]'


def test_model_sees_only_this_features_store_slice_and_plain_earlier_turns(tmp_path, mock_index):
    bot, fake, path = reservation_then(
        tmp_path,
        mock_index,
        call("update_business_info", {"field": "phone", "value": "02-777-8888"}, "call-2"),
        rules=[("번호", "business_info")],
    )
    bot.ask("가게 전화번호를 02-777-8888로 바꿔 주세요")
    messages, _ = fake.requests[-1]
    system = messages[0]["content"]
    assert '"phone": "02-000-0000"' in system and "resv-1" not in system and "메도빅" not in system
    assert "오늘: " in system
    # The reservation turn's tool call and result are not resent with another feature.
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in messages[1:-1])
    assert messages[-1] == {"role": "user", "content": "가게 전화번호를 02-777-8888로 바꿔 주세요"}


def test_code_keeps_confirmed_values_and_fills_what_the_model_drops(tmp_path, mock_index):
    fake = FakeChat(
        call("upsert_menu", {"name": "연습 차", "price": None}),
        answer("가격을 알려 주세요."),
        call("upsert_menu", {"price": 5000}, "call-2"),
    )
    bot = owner.OwnerAssistant(
        tmp_path / "store.json", chat=fake, embed=lambda _: mock_index["menu"]
    )
    bot.ask("신메뉴 연습 차 등록할게요")
    first = [json.loads(m["content"]) for m in fake.requests[1][0] if m["role"] == "tool"][-1]
    assert first["missing_fields"] == ["가격 (원)"]  # null is "not given", not a value
    assert "연습 차" in bot.ask("5000원이요")
    assert json.loads(bot.pending_action["arguments"]) == {"name": "연습 차", "price": 5000}
    assert bot.ledger.active().slots["name"].source == "owner"


def test_fabricated_value_is_refused_before_preview(tmp_path, mock_index):
    fake = FakeChat(
        call("update_business_info", {"field": "phone", "value": "02-000-0001"}),
        answer("새 전화번호를 알려 주세요."),
    )
    path = tmp_path / "store.json"
    bot = owner.OwnerAssistant(path, chat=fake, embed=lambda _: mock_index["business_info"])
    out = bot.ask("전화번호 바꿔 주세요")
    assert "새 전화번호" in out and bot.pending_action is None and not path.exists()
    result = [json.loads(m["content"]) for m in fake.requests[1][0] if m["role"] == "tool"][-1]
    assert result["error"] == "unconfirmed_value"


def test_question_about_the_shown_change_keeps_it(tmp_path, mock_index):
    bot, fake, path = reservation_then(tmp_path, mock_index, answer("2명 예약이에요."))
    bot.ask("그 예약 몇 명이었죠?")
    assert bot.pending_action is not None
    bot.ask("네")
    assert read_file(path)["reservations"][0]["status"] == "confirmed"


def test_imitated_preview_is_retried_and_false_claim_replaced(tmp_path, mock_index):
    fake = FakeChat(
        answer('"02-777-8888"로 바꿀까요?\n네 / 취소'),  # Imitates the system preview.
        call("update_business_info", {"field": "phone", "value": "02-777-8888"}),
    )
    path = tmp_path / "store.json"
    bot = owner.OwnerAssistant(path, chat=fake, embed=lambda _: mock_index["business_info"])
    assert "바꿀까요" in bot.ask("전화번호 02-777-8888로 바꿔 주세요")
    assert bot.pending_action is not None and bot.trace["nudged"]
    assert "주의" in fake.requests[1][0][0]["content"]

    fake = FakeChat(answer("전화번호를 변경했습니다."), answer("변경했습니다!"))
    bot = owner.OwnerAssistant(
        tmp_path / "other.json", chat=fake, embed=lambda _: mock_index["business_info"]
    )
    assert bot.ask("전화번호 02-777-8888로 바꿔 주세요").startswith(owner.NOT_SAVED_TEXT)


def test_two_changes_are_shown_and_approved_together(tmp_path, mock_index):
    second = {"reservation_id": "resv-2", "status": "confirmed"}
    fake = FakeChat(
        call("update_reservation_status", CONFIRM),
        call("update_reservation_status", second, "c2"),
    )
    path = tmp_path / "store.json"
    bot = owner.OwnerAssistant(path, chat=fake, embed=lambda _: mock_index["reservation"])
    out = bot.ask("resv-1이랑 resv-2 둘 다 확정해 주세요")
    assert '"resv-1"' in out and '"resv-2"' in out and not path.exists()
    bot.ask("둘 다 확정해 주세요")
    assert [r["status"] for r in read_file(path)["reservations"]] == ["confirmed", "confirmed"]


def test_added_item_joins_the_shown_batch(tmp_path, mock_index):
    fake = FakeChat(
        call("update_business_hours", {"day": "mon", "closed": True}),
        call("update_business_hours", {"day": "fri", "open_time": "09:00", "close_time": "23:00"}),
    )
    path = tmp_path / "store.json"
    bot = owner.OwnerAssistant(path, chat=fake, embed=lambda _: mock_index["business_hours"])
    bot.ask("월요일은 쉬는 걸로 해 주세요")
    out = bot.ask("그리고 금요일은 9시부터 밤 11시까지요")
    assert '"closed": true' in out and '"23:00"' in out
    assert owner.approval_intent("둘 다 저장해 주세요") == "approve"
    bot.ask("둘 다 저장해 주세요")
    hours = read_file(path)["shop"]["hours"]
    assert hours["mon"] == {"closed": True} and hours["fri"]["close_time"] == "23:00"


def test_rejected_interjection_gives_the_held_change_back(tmp_path, mock_index):
    bot, fake, path = reservation_then(tmp_path, mock_index, rules=[("부재중", "smartcall")])

    def reject(messages, *, tools):
        raise owner.ClovaRequestRejected("rejected")

    bot.chat = reject
    out = bot.ask("부재중 전화 온 거 있어요?")
    assert "다시 말씀해 주세요" in out and "아직 저장 전이에요" in out
    bot.ask("네")
    assert read_file(path)["reservations"][0]["status"] == "confirmed"
