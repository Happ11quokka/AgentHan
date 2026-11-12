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
        answer("정산은 116,400원이에요."),  # Asked once more to use the read tool.
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
    hours = {"days": ["sun"], "open_time": "10:00", "close_time": "18:00"}
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
        call("update_business_info", {"phone": "02-777-8888"}, "call-2"),
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
        call("update_business_info", {"phone": "02-000-0001"}),
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
        call("update_business_info", {"phone": "02-777-8888"}),
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
        call("update_business_hours", {"days": ["mon"], "closed": True}),
        call(
            "update_business_hours", {"days": ["fri"], "open_time": "09:00", "close_time": "23:00"}
        ),
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


def test_formal_self_confirmation_counts_as_imitation():
    assert owner.FAKE_ASK.search("나폴레옹 가격을 7500원으로 변경하시겠습니까?")
    assert not owner.ASKS_VALUE.search("나폴레옹 가격을 7500원으로 변경하시겠습니까?")
    assert owner.FAKE_ASK.search("order-1 주문을 접수하겠습니다.")
    assert owner.FAKE_ASK.search('create_coupon(title="평일 낮 10% 혜택", discount_value=10)')


def test_said_price_takes_one_clear_price():
    assert owner.OwnerAssistant._said_price("가격은 12000원이에요") == 12000
    assert owner.OwnerAssistant._said_price("만 이천 원으로 해 줘") == 12000
    assert owner.OwnerAssistant._said_price("1번 8000원, 2번 9000원") is None
    assert owner.OwnerAssistant._said_price("이름을 펠메니로") is None


def test_rejected_request_is_asked_once_more(tmp_path):
    from agenthan.agent.clova_chat import ClovaRequestRejected

    replies = iter(
        [
            ClovaRequestRejected("rejected"),
            base.answer("부재중 전화는 2통이에요."),
            base.answer("부재중 전화는 2통이에요."),  # After the read-tool nudge.
            base.answer("부재중 전화는 2통이에요."),  # Phrasing the read code ran.
        ]
    )

    def chat(messages, *, tools):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=chat, embed=base.fake_embed)
    bot.route = lambda text: owner.Route("smartcall", "select")
    # The model never called the read, so code ran it and the result is shown under the answer.
    assert bot.ask("부재중 전화 있어요?").startswith("부재중 전화는 2통이에요.\n더미 조회 결과")
    assert bot.trace["rejected_retry"] is True and bot.trace["code_read"] is True


def test_named_menu_price_question(tmp_path):
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=base.FakeChat(), embed=base.fake_embed)
    assert bot._names_menu_price("메도빅 가격 지금 얼마로 돼 있죠?")
    assert bot._names_menu_price("꿀케이크 얼마예요?")  # alias
    assert not bot._names_menu_price("메도빅 맛있다는 리뷰 있어?")
    assert not bot._names_menu_price("은행 영업시간 알려줘")


def test_read_answer_without_the_read_tool_is_asked_again(tmp_path):
    fake = base.FakeChat(
        base.answer("부재중 전화는 1통이에요."),  # Made up, no tool call.
        base.call("list_call_history", {"missed_only": True}),
        base.answer("부재중 전화는 2통이에요."),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda text: owner.Route("smartcall", "select")
    assert bot.ask("부재중 전화 몇 통이에요?").startswith("부재중 전화는 2통이에요.")
    assert owner.READ_NUDGE in fake.requests[1][0][0]["content"]


def test_paraphrased_reason_is_dropped_not_asked(tmp_path):
    args = {"reservation_id": "resv-1", "status": "cancelled", "reason": "손님 개인 사정"}
    fake = base.FakeChat(base.call("update_reservation_status", args))
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda text: owner.Route("reservation", "select")
    out = bot.ask("resv-1 손님이 못 온대요 예약 취소로 바꿔주세요")
    assert "바꿀까요" in out and "사정" not in out
    assert bot.trace["dropped"] == ["reason"]
    assert bot.pending_action["actions"][0]["preview"]["record"]["status"] == "cancelled"


def test_keyword_list_sent_as_add_becomes_the_whole_list():
    store = {"shop": {"keywords": ["러시안 베이커리", "메도빅", "베이커리 카페"]}}
    said = "'러시아 케이크', '수제 메도빅', '홍차 맛집' 이렇게 세개로 해주세요"
    args = owner.OwnerAssistant._keywords_as_list(
        {"add": ["러시아 케이크", "수제 메도빅", "홍차 맛집"]}, said, store
    )
    assert args == {"keywords": ["러시아 케이크", "수제 메도빅", "홍차 맛집"]}
    added = owner.OwnerAssistant._keywords_as_list({"add": ["디저트"]}, "디저트 추가해 줘", store)
    assert added == {"add": ["디저트"]}
    assert owner.FAKE_ASK.search("쿠폰을 만들어보겠습니다. 잠시만 기다려 주세요.")
    assert owner.FAKE_ASK.search("11시까지로 바꾸시는 게 맞나요?")


def test_tool_call_written_as_text_runs_as_a_call(tmp_path):
    text = (
        'create_coupon(title="평일 낮 10% 혜택", discount_type="percent", discount_value=10, '
        'start_date="2026-11-10", end_date="2026-11-20")'
    )
    fake = base.FakeChat(base.answer(text))
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda t: owner.Route("coupon", "select")
    out = bot.ask(
        "'평일 낮 10% 혜택' 이름으로 10퍼센트 할인 쿠폰, 기간은 2026-11-10부터 2026-11-20까지"
    )
    assert "바꿀까요" in out and bot.trace["written_call"] is True
    assert not (tmp_path / "store.json").exists()  # Only a preview; approval still needed.
    assert owner.written_call("create_coupon(1, 2)", {"create_coupon"}) is None
    assert owner.written_call("upsert_menu(name='차')", {"create_coupon"}) is None


def test_tool_call_written_inside_a_sentence_runs_as_a_call(tmp_path):
    text = '알겠습니다. 5,500원으로 하겠습니다.\n\n도구 호출: upsert_menu(name="블린", price=9500)'
    fake = base.FakeChat(base.answer(text))
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda t: owner.Route("menu", "select")
    out = bot.ask("블린 9500원으로 바꿔 주세요")
    assert "바꿀까요" in out and bot.trace["written_call"] is True
    assert not (tmp_path / "store.json").exists()  # Only a preview; approval still needed.
    two = 'upsert_menu(name="a", price=1) 그리고 upsert_menu(name="b", price=2)'
    assert owner.written_call(two, {"upsert_menu"}) is None  # Not ours to pick one.


def test_more_text_only_confirmations_are_caught():
    for text in (
        "알겠습니다. '러시안 홍차'를 5,500원에 추가하도록 하겠습니다.",
        "추가하겠습니다. 도구를 불러오는 중...",
        "바로 바꿔 드리겠습니다",
        '이렇게 바꾸면 될까요? 더미 답변 작성 (실제 전송 없음): {"inquiry": "talk-1"}',
    ):
        assert owner.FAKE_ASK.search(text), text
    assert owner.FAKE_DONE.search("성공했습니다! 다음은 방금 추가한 메뉴의 기록입니다.")
    assert not owner.FAKE_ASK.search("러시안 홍차 가격은 얼마인가요?")


def test_finished_task_leaves_the_model_context(tmp_path):
    fake = base.FakeChat(
        base.call("upsert_menu", {"name": "아메리카노", "price": 4800}),
        base.answer("확인했어요."),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda t: owner.Route("menu", "select")
    assert "바꿀까요" in bot.ask("아메리카노 4800원으로 바꿔 주세요")
    assert bot.ask("네").startswith("저장했어요")
    bot.ask("나폴레옹도 7500원으로 바꿔 주세요")
    messages = fake.requests[-1][0]
    said = [m["content"] for m in messages if m["role"] == "user"]
    assert said == ["나폴레옹도 7500원으로 바꿔 주세요"]  # The finished menu change is gone,
    assert "저장함" in messages[0]["content"] and "4800" in messages[0]["content"]  # but noted.


def test_a_question_in_a_new_task_does_not_take_over_the_held_one(tmp_path):
    fake = base.FakeChat(
        base.answer("쿠폰 이름, 할인, 기간을 알려 주세요."),
        base.answer("새 주문이 2건 있어요. 어떤 주문을 접수하실 건가요?"),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    features = iter(["coupon", "order"])
    bot.route = lambda t: owner.Route(next(features), "select")
    bot.ask("쿠폰 하나 만들고 싶어요")
    out = bot.ask("아 그런데 오늘 들어온 주문 있어요?")
    assert "2건" in out
    assert bot.ledger.active().feature == "coupon"  # The coupon period goes to the coupon.


def test_owner_question_needs_no_change_words():
    def asks(text):
        return bool(owner.OWNER_QUESTION.search(text)) and not owner.CHANGE_WORDS.search(text)

    assert asks("오늘 들어온 주문 있어요?") and asks("톡톡 문의 온 거 있나요?")
    assert not asks("예약 취소할 수 있어요?") and not asks("주문 접수해 주세요")


def test_packed_values_ask_for_one_call_per_item(tmp_path):
    # Lengths do not line up with days, so it cannot be split: refused, then called again.
    packed = {"days": ["mon", "fri"], "closed": [True, False, False], "open_time": "09:00"}
    fake = base.FakeChat(
        base.call("update_business_hours", packed),
        base.call(
            "update_business_hours", {"days": ["fri"], "open_time": "09:00", "close_time": "23:00"}
        ),
        base.answer(""),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda t: owner.Route("business_hours", "select")
    out = bot.ask("금요일은 9시부터 밤 11시까지요")
    assert bot.trace["results"][0]["error"] == "one_value_per_call"
    assert "바꿀까요" in out and len(bot.pending_action["actions"]) == 1


def test_keywords_with_unsaid_removals_are_a_replacement():
    store = {"shop": {"keywords": ["메도빅", "러시아 디저트"]}}
    said = "'러시아 케이크', '수제 메도빅', '홍차 맛집' 이렇게 세개로 해주세요"
    new = ["러시아 케이크", "수제 메도빅", "홍차 맛집"]
    args = {"add": new, "remove": ["메도빅", "러시아 디저트"]}
    assert owner.OwnerAssistant._keywords_as_list(args, said, store) == {"keywords": new}
    partial = {"add": ["러시아 케이크", "홍차 맛집"], "remove": ["메도빅"]}
    # The model's own difference is applied; the value gate then asks about 러시아 디저트.
    assert owner.OwnerAssistant._keywords_as_list(partial, said, store) == {
        "keywords": ["러시아 디저트", "러시아 케이크", "홍차 맛집"]
    }
    three = {"shop": {"keywords": ["러시안 베이커리", "메도빅", "베이커리 카페"]}}
    diff = {"add": ["나폴레옹"], "remove": ["베이커리 카페"]}
    assert owner.OwnerAssistant._keywords_as_list(
        diff, "이제 키워드요. 메도빅, 나폴레옹, 러시안 베이커리로요", three
    ) == {"keywords": ["러시안 베이커리", "메도빅", "나폴레옹"]}
    quoted = {"add": ["블린"], "remove": ["메도빅"]}
    keep = owner.OwnerAssistant._keywords_as_list(quoted, "'메도빅' 빼고 '블린'으로 바꿔 줘", store)
    assert keep == quoted
    both = {"keywords": new, "remove": ["메도빅", "러시아 디저트"]}
    assert owner.OwnerAssistant._keywords_as_list(both, said, store) == {"keywords": new}
    said_remove = {"add": ["블린"], "remove": ["메도빅"]}
    kept = owner.OwnerAssistant._keywords_as_list(
        said_remove, "메도빅 빼고 블린으로 바꿔 줘", store
    )
    assert kept == said_remove


def test_ledger_log_keeps_old_tasks_of_the_same_feature():
    ledger = TaskLedger()
    first = ledger.start("menu")
    ledger.close(first, "done", "저장함")
    for feature in ("order", "coupon", "talktalk", "reservation", "settlement", "smartcall"):
        ledger.close(ledger.start(feature), "done")
    recent = ledger.log("menu", last=5)
    assert recent[0] == f"{first.id} menu: done (저장함)" and len(recent) == 6
    assert len(ledger.log(last=5)) == 5 and len(ledger.log()) == 7


def test_unsaid_menu_action_next_to_a_said_price_falls_back_to_save(tmp_path):
    fake = base.FakeChat(
        base.call("upsert_menu", {"name": "아메리카노", "action": "on_sale", "price": 5000})
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda t: owner.Route("menu", "select")
    out = bot.ask("아메리카노 가격 5000원으로 올려 주세요")
    assert "바꿀까요" in out and bot.trace["dropped"] == ["action"]
    assert bot.pending_action["actions"][0]["preview"]["record"]["price"] == 5000


def test_pos_check_answer_starts_with_the_yes_or_no(tmp_path):
    fake = base.FakeChat(
        base.call("check_pos_compatibility", {"pos_name": "하나로페이"}),
        base.answer("연동되는 계산기는 연습POS 시리즈입니다."),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda t: owner.Route("place_plus", "select")
    out = bot.ask("우리 계산기 '하나로페이' 써요 플레이스랑 연결 돼요?")
    assert out.startswith("'하나로페이'은(는) 지금 네이버 플레이스와 연동되지 않아요.")


def test_a_question_back_on_a_read_tool_that_needs_nothing_is_nudged(tmp_path):
    fake = base.FakeChat(
        base.answer("모두 확인할까요, 아니면 특정 기간만 볼까요?"),
        base.call("list_call_history", {"missed_only": True}),
        base.answer("부재중 전화는 2통이에요."),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda text: owner.Route("smartcall", "select")
    assert bot.ask("참, 부재중 전화 있었어요?").startswith("부재중 전화는 2통이에요.")
    assert owner.READ_NUDGE in fake.requests[1][0][0]["content"]


def test_a_read_question_back_for_a_value_already_said_is_nudged(tmp_path):
    fake = base.FakeChat(
        base.answer("이번 주 정산을 위해 날짜가 필요합니다. 몇 월 며칠부터 계산할까요?"),
        base.call("get_settlement_summary", {"period": "this_week"}),
        base.answer("이번 주 입금액은 116,400원이에요."),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda text: owner.Route("settlement", "select")
    assert "116,400원" in bot.ask("이번 주 정산 얼마예요?")
    schema = owner.feature_tool_schema("settlement")
    assert not owner.OwnerAssistant._read_needs_nothing_new("settlement", schema, "정산 얼마예요?")


def test_code_runs_the_read_when_the_model_still_will_not(tmp_path):
    fake = base.FakeChat(
        base.answer("부재중 전화가 총 5통 있습니다."),
        base.answer("죄송하지만 이 기능에서는 조회할 수 없습니다."),
        base.answer("부재중 전화는 2통이에요."),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda text: owner.Route("smartcall", "select")
    assert bot.ask("부재중 전화 몇 통이에요?").startswith("부재중 전화는 2통이에요.")
    assert bot.trace["code_read"] is True
    assert bot.trace["calls"][0] == {
        "name": "list_call_history",
        "arguments": '{"missed_only": true}',
    }
    schema = owner.feature_tool_schema("settlement")
    args = owner.OwnerAssistant._read_arguments("settlement", schema, "이번 주 정산 얼마예요?")
    assert args == {"period": "this_week"}


def test_items_packed_into_one_call_are_split_per_day(tmp_path):
    packed = {
        "days": ["mon", "fri"],
        "closed": [True, False],
        "open_time": ["09:00", "09:00"],
        "close_time": ["21:00", "23:00"],
    }
    fake = base.FakeChat(
        base.call("update_business_hours", {"days": ["mon"], "closed": True}),
        base.call("update_business_hours", packed),
    )
    bot = owner.OwnerAssistant(tmp_path / "store.json", chat=fake, embed=base.fake_embed)
    bot.route = lambda t: owner.Route("business_hours", "select")
    assert "바꿀까요" in bot.ask("월요일은 쉬는 걸로 해 주세요")
    out = bot.ask("그리고 금요일은 9시부터 밤 11시까지요")
    actions = bot.pending_action["actions"]
    assert [
        json.loads(a["arguments"]) if isinstance(a["arguments"], str) else a["arguments"]
        for a in actions
    ] == [
        {"days": ["mon"], "closed": True},
        {"days": ["fri"], "closed": False, "open_time": "09:00", "close_time": "23:00"},
    ]
    assert '"fri"' in out and '"mon"' in out
    assert bot.ask("둘 다 저장해 주세요").startswith("저장했어요")
    hours = DummyStore(tmp_path / "store.json").read()["shop"]["hours"]
    assert hours["mon"]["closed"] is True and hours["fri"]["close_time"] == "23:00"
