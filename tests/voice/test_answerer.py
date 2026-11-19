"""손님 질문에 가게 자료로 답하기: 자료 줄, 답 검사, HCX 글 읽기 (5단계 티켓 11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.answerer import (
    AnswerReply,
    AnswerRequest,
    checked,
    notes_from,
    store_facts,
)
from agenthan.voice.nlu.hcx_answer import SYSTEM, build_messages, parse_reply
from agenthan.voice.store_config import StoreConfig

PHO = Path(__file__).parents[2] / "fixtures" / "phone" / "sample_pho_store.json"


def _facts(notes: dict[str, list[str]] | None = None) -> tuple[str, ...]:
    store = FakeStoreKnowledge.from_fixture()
    return store_facts(StoreConfig.from_fixture(), store.list_menu(), store.recent_reviews(30),
                       notes_from(notes or {}))


def _request(text: str, notes: dict[str, list[str]] | None = None) -> AnswerRequest:
    return AnswerRequest(text, _facts(notes))


def test_store_facts_carry_hours_prices_options_allergens_and_reviews() -> None:
    store, medovik, _, americano, blini = _facts()
    assert "영업시간: 오전 10시부터 오후 9시까지" in store
    assert "준비에 15분쯤 걸림" in store
    assert medovik.startswith("메뉴 메도빅 | 가격 7,500원")
    assert "옵션: 홀케이크 변경(+38,000원)" in medovik
    assert "알레르기 표시: 밀, 우유, 계란" in medovik
    assert "손님 후기: 메도빅이 너무 달지 않고 촉촉해요." in medovik
    assert "꼭 고를 것 온도: 따뜻하게/차갑게" in americano
    assert "사워크림 빼기(추가 금액 없음)" in blini


def test_store_facts_come_from_any_store_data() -> None:
    store = FakeStoreKnowledge.from_fixture(PHO)
    facts = store_facts(StoreConfig.from_fixture(PHO), store.list_menu())
    assert len(facts) == len(store.list_menu()) + 1
    assert all(line.startswith("메뉴 ") for line in facts[1:])


def test_owner_notes_are_marked_in_the_facts() -> None:
    store, medovik, *_ = _facts({"": ["가게 앞에 두 대 댈 수 있어요"], "m01": ["견과 안 들어감"]})
    assert "사장님 메모: 가게 앞에 두 대 댈 수 있어요" in store
    assert "사장님 메모: 견과 안 들어감" in medovik


def test_an_answer_with_evidence_in_the_facts_is_kept() -> None:
    reply = AnswerReply("answer", "네, 메도빅에 우유 들어가요.", "알레르기 표시: 밀, 우유, 계란")
    assert checked(reply, _request("메도빅에 우유 들어가요?")) == reply


@pytest.mark.parametrize(("say", "evidence", "reason"), [
    # 자료에 없는 근거
    ("블린에는 견과류가 들어가지 않아요.", "견과류는 들어가지 않아요", "no_evidence"),
    ("메도빅이 제일 인기가 많아요.", "", "no_evidence"),
    # 머리말만 옮긴 근거
    ("네, 들어가요.", "알레르기 표시", "no_evidence"),
    # 자료에 없는 숫자
    ("메도빅은 8,000원이에요.", "가격 7,500원", "number"),
    # 근거는 있지만 "안 들어간다"는 근거는 아니다
    ("블린에는 견과류가 안 들어가요.", "얇은 러시아식 팬케이크", "denies"),
    ("주차는 안 돼요.", "전화로는 찾으러 오는 주문(포장)만 받음", "denies"),
])
def test_an_ungrounded_answer_becomes_unsure(say: str, evidence: str, reason: str) -> None:
    result = checked(AnswerReply("answer", say, evidence), _request("블린에 견과류 들어가요?"))
    assert (result.kind, result.say, result.reason) == ("unsure", "", reason)


def test_the_owner_note_can_say_something_is_not_in_it() -> None:
    request = _request("메도빅에 견과류 들어가요?", {"m01": ["견과 안 들어감"]})
    reply = AnswerReply("answer", "아니요, 견과류는 안 들어가요.", "견과 안 들어감")
    assert checked(reply, request).kind == "answer"


def test_a_promise_to_change_the_order_is_not_an_answer() -> None:
    reply = AnswerReply("answer", "하나는 사워크림 빼고 드릴게요.", "사워크림 빼기")
    assert checked(reply, _request("하나는 사워크림 빼고")).kind == "not_question"


def test_pushy_sentences_and_plus_signs_are_dropped() -> None:
    reply = AnswerReply("answer", "디카페인으로 돼요, +500원이에요. 한번 드셔보세요.",
                        "디카페인 변경(+500원)")
    assert checked(reply, _request("디카페인 돼요?")).say == "디카페인으로 돼요, 500원이에요."


def test_evidence_with_a_word_or_two_changed_still_counts() -> None:
    reply = AnswerReply("answer", "손님들은 많이 달지 않고 촉촉하다고 하니 참고 부탁드려요.",
                        "많이 달지 않고 촉촉해요")
    result = checked(reply, _request("메도빅 많이 달아요?"))
    assert (result.kind, result.say) == ("answer", "손님들은 많이 달지 않고 촉촉하다고 하세요.")


def test_a_plain_list_of_menu_names_needs_no_evidence() -> None:
    listing = AnswerReply("answer", "메도빅, 나폴레옹, 아메리카노, 블린 있어요.")
    assert checked(listing, _request("뭐가 맛있어요")).kind == "answer"
    ranking = AnswerReply("answer", "메도빅이랑 블린이 제일 인기가 많아요.")
    assert checked(ranking, _request("뭐가 잘 나가요")).reason == "no_evidence"


def test_a_number_the_customer_said_may_be_repeated() -> None:
    reply = AnswerReply("answer", "네, 8시에도 열어요.", "오후 9시까지")
    assert checked(reply, _request("8시에도 해요?")).kind == "answer"


@pytest.mark.parametrize(("content", "expected"), [
    ("종류: 답\n메뉴: 메도빅\n근거: 가격 7,500원\n답: 메도빅은 7,500원이에요.",
     AnswerReply("answer", "메도빅은 7,500원이에요.", "가격 7,500원", "메도빅")),
    ("종류: 답변\n메뉴: 없음\n근거: 오후 9시까지\n답: 오후 9시까지 해요.",
     AnswerReply("answer", "오후 9시까지 해요.", "오후 9시까지")),
    ("종류: 모름\n메뉴: 블린\n근거: 없음\n답: 없음", AnswerReply("unsure", menu="블린")),
    ("종류: 질문아님\n메뉴: 없음\n근거: 없음\n답: 없음", AnswerReply("not_question")),
    ("메도빅은 달지 않아요.", AnswerReply("unsure", reason="bad_format")),
])
def test_the_model_text_is_read_into_a_reply(content: str, expected: AnswerReply) -> None:
    assert parse_reply(content) == expected


def test_the_prompt_names_no_real_store_and_ends_with_this_call() -> None:
    request = AnswerRequest("메도빅 달아요?", _facts(), focus="메도빅", recent=("AI: 어떤 걸로?",))
    messages = build_messages(request)
    assert "메도빅" not in SYSTEM and "베이커리" not in SYSTEM
    examples = "".join(m["content"] for m in messages[1:-1])
    assert "메도빅" not in examples
    last = messages[-1]["content"]
    assert "[가게 자료]" in last and "[직전 대화]" in last
    assert "[지금 이야기하던 메뉴] 메도빅" in last and "[손님 말] 메도빅 달아요?" in last
