"""Meaning acts on the ledger, per-feature guides, and record targets chosen among candidates."""

import json
import re
from datetime import date
from pathlib import Path

import test_owner_assistant as base
from agenthan.agent import feature_selector as fs
from agenthan.agent import owner_assistant as owner
from agenthan.agent import value_source
from agenthan.tools.dummy_store import DummyStore
from test_feature_selector import Select

FakeChat, answer, call, read_file = base.FakeChat, base.answer, base.call, base.read_file
mock_index, offline_by_default = base.mock_index, base.offline_by_default  # fixtures

TODAY = date(2026, 11, 10)  # Seed: resv-1 2026-11-10 18:00 (2명), resv-2 2026-11-11 12:30 (4명)
SEED = json.loads((Path(owner.__file__).parent.parent / "tools/dummy_store_seed.json").read_text())


def picked(feature_id, act=None):
    arguments = {"feature_id": feature_id, **({"act": act} if act else {})}
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "s", "function": {"name": "select_feature", "arguments": json.dumps(arguments)}}
        ],
    }


def bot_with(tmp_path, mock_index, chat, select, feature="reservation"):
    return owner.OwnerAssistant(
        tmp_path / "store.json",
        chat=chat,
        embed=lambda _: mock_index[feature],
        select_chat=select,
        today=TODAY,
    )


def tool_results(fake, index):
    return [json.loads(m["content"]) for m in fake.requests[index][0] if m["role"] == "tool"]


# ------------------------------------------------------------------ record candidates


def test_candidates_need_every_stated_condition():
    records = SEED["reservations"]
    fits, stated = value_source.candidates(records, "오늘 저녁 예약 취소해 줘", TODAY)
    assert stated and [r["id"] for r in fits] == ["resv-1"]
    # 오늘(11-10) and 점심 together fit neither: no record is guessed.
    fits, stated = value_source.candidates(records, "오늘 점심 예약", TODAY)
    assert stated and fits == []
    fits, stated = value_source.candidates(records, "예약 확정해 줘", TODAY)
    assert not stated and len(fits) == 2
    fits, _ = value_source.candidates(records, "4명 예약", TODAY)
    assert [r["id"] for r in fits] == ["resv-2"]
    fits, _ = value_source.candidates(SEED["orders"], "블린 주문 준비됐어요", TODAY)
    assert [r["id"] for r in fits] == ["order-2"]


def test_unstated_or_ambiguous_target_is_not_confirmed():
    store, said = SEED, "예약 확정해 줘"
    assert value_source.confirmed("reservation_id", "string", "resv-1", said, store) is None
    # Both inquiries mention 메도빅: the words do not single one out.
    assert value_source.confirmed("inquiry", "string", "talk-1", "메도빅 문의 답장", store) is None
    assert (
        value_source.confirmed("order_id", "string", "order-2", "블린 주문 접수", store) == "lookup"
    )


def test_owner_words_narrow_candidates_and_the_tool_enum(tmp_path, mock_index):
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "cancelled"})
    )
    bot = bot_with(tmp_path, mock_index, fake, Select(picked("reservation")))
    out = bot.ask("오늘 저녁 예약 취소해 줘")
    assert "바꿀까요" in out and bot.pending_action is not None
    system = fake.requests[0][0][0]["content"]
    assert "대상 후보" in system and "resv-1" in system and "resv-2" not in system
    prop = fake.requests[0][1][0]["function"]["parameters"]["properties"]["reservation_id"]
    assert prop["enum"] == ["resv-1"]


def test_ambiguous_target_asks_instead_of_taking_one(tmp_path, mock_index):
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "confirmed"}),
        answer("어느 예약인가요? 11월 10일 18:00 2명 / 11월 11일 12:30 4명"),
    )
    bot = bot_with(tmp_path, mock_index, fake, Select(picked("reservation")))
    out = bot.ask("예약 확정해 줘")
    assert "어느 예약" in out and bot.pending_action is None
    result = tool_results(fake, 1)[0]
    assert result["error"] == "ambiguous_target"
    assert [c["id"] for c in result["candidates"]] == ["resv-1", "resv-2"]
    assert not (tmp_path / "store.json").exists()


def test_id_outside_candidates_is_refused(tmp_path, mock_index):
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-2", "status": "confirmed"}),
        call(
            "update_reservation_status", {"reservation_id": "resv-9", "status": "confirmed"}, "c2"
        ),
        answer("어느 예약인가요?"),
    )
    bot = bot_with(tmp_path, mock_index, fake, Select(picked("reservation")))
    bot.ask("resv-1 예약 확정해 줘")
    assert [r["error"] for r in bot.trace["results"]] == ["not_a_candidate", "not_a_candidate"]
    assert bot.pending_action is None


def test_no_matching_record_leaves_the_target_open(tmp_path, mock_index):
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "confirmed"}),
        answer("모레 예약은 없어요."),
    )
    bot = bot_with(tmp_path, mock_index, fake, Select(picked("reservation")))
    bot.ask("모레 예약 확정해 줘")
    assert "맞는 건이 없음" in fake.requests[0][0][0]["content"]
    assert (
        "enum"
        not in fake.requests[0][1][0]["function"]["parameters"]["properties"]["reservation_id"]
    )
    assert bot.trace["results"][0]["error"] == "not_a_candidate" and bot.pending_action is None


def test_quoted_inquiry_text_is_mapped_to_its_id_by_code(tmp_path, mock_index):
    fake = FakeChat(
        call("save_talktalk_reply", {"inquiry": "단체 주문", "reply_text": "10개부터 가능합니다"})
    )
    bot = bot_with(tmp_path, mock_index, fake, Select(picked("talktalk")), "talktalk")
    bot.ask("단체 주문 물어본 톡톡에 '10개부터 가능합니다'라고 답해 줘")
    assert bot.pending_action["preview"]["record"]["id"] == "talk-2"


def test_store_change_after_selection_is_rechecked_before_saving(tmp_path, mock_index):
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "confirmed"})
    )
    bot = bot_with(tmp_path, mock_index, fake, Select(picked("reservation")))
    bot.ask("오늘 저녁 예약 확정해 줘")
    store = DummyStore(tmp_path / "store.json")
    data = store.read()
    data["reservations"][0]["status"] = "cancelled"
    store.write(data)
    out = bot.ask("네")
    assert "바꿀까요" in out  # Asked again on the new state instead of writing.
    assert read_file(tmp_path / "store.json")["reservations"][0]["status"] == "cancelled"


# ------------------------------------------------------------------ meaning acts


def test_act_is_asked_only_while_a_task_is_open(tmp_path, mock_index):
    select = Select(picked("business_hours"), picked("business_hours", "edit"))
    fake = FakeChat(
        call(
            "update_business_hours",
            {"entries": [{"days": ["sat"], "open_time": "10:00", "close_time": "20:00"}]},
        ),
        # The model carries over a different opening time from the older words (8시).
        call(
            "update_business_hours",
            {"entries": [{"days": ["sat"], "open_time": "08:00", "close_time": "21:00"}]},
            "c2",
        ),
    )
    bot = bot_with(tmp_path, mock_index, fake, select, "business_hours")
    bot.ask("토요일 10시부터 저녁 8시까지 열게요")
    out = bot.ask("아니, 닫는 건 9시로 할게요")
    first, second = (r[1][0]["function"]["parameters"]["properties"] for r in select.requests)
    assert "act" not in first and second["act"]["enum"] == list(fs.ACTS)
    assert "열린 작업" in select.requests[1][0][0]["content"]
    assert "바꿀까요" in out
    arguments = json.loads(bot.pending_action["arguments"])
    # Only the edited slot changed; the confirmed opening time stayed.
    edited = [{"days": ["sat"], "open_time": "10:00", "close_time": "21:00"}]
    assert arguments == {"entries": edited}
    assert bot.trace["kept_slots"] == ["open_time"] and bot.trace["act"] == "edit"
    task = bot.ledger.active()
    assert task.slots["entries"].value == edited
    before = [{"days": ["sat"], "open_time": "10:00", "close_time": "20:00"}]
    assert ("entries", before, edited) in [c[1:4] for c in task.changes]
    assert "고치는 중" in fake.requests[1][0][0]["content"]


def test_edit_act_voids_the_preview_and_never_writes(tmp_path, mock_index):
    select = Select(picked("reservation"), picked("reservation", "edit"))
    fake = FakeChat(
        call("update_reservation_status", {"reservation_id": "resv-1", "status": "confirmed"}),
        answer("무엇을 고칠까요?"),
    )
    bot = bot_with(tmp_path, mock_index, fake, select)
    bot.ask("resv-1 예약 확정해 줘")
    bot.ask("그거 좀 이상한데요")  # No edit words; the act alone voids the shown change.
    assert bot.pending_action is None
    assert bot.reply_to_approval("네") is None  # Nothing left to approve.
    assert not (tmp_path / "store.json").exists()


def test_resume_shows_the_held_change_again_without_a_model_call(tmp_path, mock_index):
    select = Select(
        picked("business_hours"), picked("coupon", "new"), picked("business_hours", "resume")
    )
    fake = FakeChat(
        call(
            "update_business_hours",
            {"entries": [{"days": ["sat"], "open_time": "10:00", "close_time": "20:00"}]},
        ),
        answer("쿠폰 이름, 할인, 기간을 알려 주세요."),
    )
    bot = bot_with(tmp_path, mock_index, fake, select, "business_hours")
    bot.ask("토요일 10시부터 저녁 8시까지 열게요")
    bot.ask("쿠폰 하나 만들래요")
    held = bot.ledger.find_open("business_hours")
    assert held.status == "paused" and held.preview is not None
    out = bot.ask("아까 영업시간 계속하자")
    assert "바꿀까요" in out and len(fake.requests) == 2
    assert bot.ledger.active().feature == "business_hours"
    assert "저장했어요" in bot.ask("네")
    assert read_file(tmp_path / "store.json")["shop"]["hours"]["sat"]["open_time"] == "10:00"
    assert bot.ledger.active().feature == "coupon"  # The coupon task waits underneath.


def test_resume_brings_a_held_task_back_into_the_candidates(tmp_path, mock_index):
    select = Select(
        picked("search_keywords"), picked("coupon", "new"), picked("search_keywords", "resume")
    )
    fake = FakeChat(
        answer("어떤 키워드로 바꿀까요?"),
        answer("쿠폰 이름, 할인, 기간을 알려 주세요."),
        call("set_representative_keywords", {"keywords": ["수제 잼", "브런치"]}),
    )
    bot = bot_with(tmp_path, mock_index, fake, select, "learning_support")
    bot.ask("대표키워드 손보려고요")
    bot.ask("쿠폰도 만들 거예요")
    out = bot.ask("아까 키워드요. 수제 잼, 브런치로요")
    enum = select.requests[2][1][0]["function"]["parameters"]["properties"]["feature_id"]["enum"]
    assert {"search_keywords", "coupon"} <= set(enum)
    assert "바꿀까요" in out and bot.ledger.active().feature == "search_keywords"
    assert "다시 하자고" in fake.requests[2][0][0]["content"]


def test_owner_named_record_id_keeps_its_feature_in_the_candidates(tmp_path, mock_index):
    select = Select(picked("reservation"))
    fake = FakeChat(answer("어느 예약인가요?"))
    bot = bot_with(tmp_path, mock_index, fake, select, "learning_support")
    bot.ask("resv-1이랑 resv-2 둘 다 확정해 주세요")
    enum = select.requests[0][1][0]["function"]["parameters"]["properties"]["feature_id"]["enum"]
    assert "reservation" in enum


def test_second_call_in_one_turn_is_not_filled_with_the_first_calls_values(tmp_path, mock_index):
    two = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"c{i}",
                "type": "function",
                "function": {"name": "update_business_hours", "arguments": args},
            }
            for i, args in enumerate(
                [
                    {"entries": [{"days": ["mon"], "closed": True}]},
                    {"entries": [{"days": ["fri"], "open_time": "09:00", "close_time": "23:00"}]},
                ]
            )
        ],
    }
    fake = FakeChat(answer("월요일을 쉬는 걸로 할까요?"), two, answer("이대로 바꿀까요?"))
    select = Select(picked("business_hours"), picked("business_hours", "answer"))
    bot = bot_with(
        tmp_path,
        mock_index,
        fake,
        select,
        "business_hours",
    )
    bot.ask("월요일은 쉬는 걸로 해 주세요")
    bot.ask("그리고 금요일은 9시부터 밤 11시까지요")
    actions = [json.loads(a["arguments"]) for a in bot.pending_action["actions"]]
    entries = [a["entries"] for a in actions]
    assert [e[0]["days"] for e in entries] == [["mon"], ["fri"]]
    assert entries[1][0].get("open_time") == "09:00" and not entries[1][0].get("closed")


def test_invalid_act_is_dropped_but_the_choice_kept():
    select = Select(picked("menu", "approve"))
    choice = fs.select(
        "메뉴", ["menu"], select_chat=select, tasks="진행 중 menu(메뉴, 값 모으는 중)"
    )
    assert choice.feature_id == "menu" and choice.act is None


# ------------------------------------------------------------------ guides


def test_only_the_chosen_feature_guide_is_sent(tmp_path, mock_index):
    fake = FakeChat(
        answer("몇 시부터 몇 시까지 여나요?"),
        answer("부재중 전화는 없어요."),
        answer("부재중 전화는 없어요."),  # After the read-tool nudge.
        answer("부재중 전화는 없어요."),  # Phrasing the read code ran.
    )
    bot = bot_with(
        tmp_path, mock_index, fake, Select(picked("business_hours"), picked("smartcall"))
    )
    bot.ask("영업시간 바꿀래요")
    system = fake.requests[0][0][0]["content"]
    assert "이 기능의 순서" in system and "update_business_hours(entries=" in system
    assert "upsert_menu(" not in system and "create_coupon(" not in system
    bot.ask("부재중 전화 있어요?")
    assert "이 기능의 순서" not in fake.requests[1][0][0]["content"]


def test_guides_cover_only_tool_features_and_name_their_own_tool():
    for feature_id, guide in owner.GUIDES.items():
        assert feature_id in owner.TOOL_FEATURE_IDS
        tool = owner.feature_tool_schema(feature_id)["function"]["name"]
        calls = set(re.findall(r"([a-z_]+)\(", " ".join(do for _, do in guide["examples"])))
        assert calls <= {tool}, feature_id
        assert 1 <= len(guide["steps"]) <= 3 and 2 <= len(guide["examples"]) <= 4


def test_guide_examples_are_not_evaluation_cases():
    root = Path(__file__).parent / "data"
    said = set()
    for path in [*root.glob("eval/*.jsonl"), *root.glob("*.jsonl")]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                said.update(re.findall(r'"(?:say|text|utterance)": "([^"]+)"', line))
    norm = {re.sub(r"\W", "", s) for s in said}
    for guide in owner.GUIDES.values():
        for say, _ in guide["examples"]:
            for quoted in re.findall(r'"([^"]+)"', say):
                assert re.sub(r"\W", "", quoted) not in norm, quoted
