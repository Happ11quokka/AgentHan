"""티켓 05: NLU 앞 규칙 단계. 걸리면 act로 바로 바꾸고, 안 걸리면 None (NLU로 넘긴다)."""

import pytest

from agenthan.voice.dialog.rules import (
    apply_rules,
    coupon_offer_answer,
    frustrated,
    mentions_coupon,
    no_and_done,
    without_coupon_clauses,
)
from agenthan.voice.nlu.base import Act


@pytest.mark.parametrize(
    "text",
    ["네", "예", "응", "맞아요", "네 맞아요", "네, 맞아요.", "네네", "맞습니다", "좋아요"],
)
def test_yes(text: str) -> None:
    assert apply_rules(text) == [Act("yes")]


@pytest.mark.parametrize(
    "text", ["아니요", "아뇨", "아니", "아니에요", "틀려요", "아니요, 틀려요."]
)
def test_no(text: str) -> None:
    assert apply_rules(text) == [Act("no")]


@pytest.mark.parametrize(
    "text",
    [
        "취소할게요",
        "취소해 주세요",
        "주문 취소해 주세요",
        "주문 취소할게요",
        "그냥 주문 취소할래요",
        "주문 안 할게요",
        "다 취소해 주세요",
    ],
)
def test_cancel_order(text: str) -> None:
    assert apply_rules(text) == [Act("cancel_order", free_text=text)]


@pytest.mark.parametrize(
    "text",
    [
        "사장님 바꿔 주세요",
        "사장님이랑 통화하고 싶어요",
        "사람이랑 통화할게요",
        "직원 바꿔 주세요",
        "상담원 연결해 주세요",
        "사장님 좀 바꿔 줄래요?",
        # 6단계 품질: 사람 요청
        "사람이랑 얘기할게요",
        "직원한테 직접 말할래요",
        "사장님하고 얘기하고 싶어요",
        "사장님 계세요?",
        "사장님 지금 안 계세요?",
        "기계 말고 사람이요",
        "AI 말고 사람 좀요",
    ],
)
def test_want_owner(text: str) -> None:
    assert apply_rules(text) == [Act("want_owner", free_text=text)]


@pytest.mark.parametrize("text", ["다시 말해 주세요", "뭐라고요?", "다시 한 번 말씀해 주세요"])
def test_repeat_please(text: str) -> None:
    assert apply_rules(text) == [Act("repeat_please")]


@pytest.mark.parametrize(
    "text",
    [
        "아니요, 두 개요",  # 뒤에 내용이 붙으면 NLU가 읽는다
        "아니요 아메리카노 두 잔이요",
        "네, 6시 반이요",
        "네 개요",  # 수량 4
        "블린 취소할게요",  # 메뉴 빼기 (remove_item)
        "그거 취소해 주세요",
        "메도빅 하나요",
        "사장님이 직접 만드세요?",  # 질문이지 연결 요청이 아니다
        "사장님이랑 얘기했는데요",  # 지난 일. 연결 요청이 아니다
        "사람 많아요?",
        "음",
    ],
)
def test_passes_to_nlu(text: str) -> None:
    assert apply_rules(text) is None


# ------------------------------------------------ "아니요, 그게 다예요" (6단계 티켓 02)
# 쪼개졌다 합쳐진 "아니요 + 끝 표현"은 NLU 없이 [no, done_ordering]이다. NLU가 내던 것과 같은
# 모양이라 엔진이 상태에 맞게 읽는다("더 필요하신 거?" 뒤면 주문 끝, 복창 중이면 확정도 거부도 아님)

NO_AND_DONE = [Act("no"), Act("done_ordering")]


@pytest.mark.parametrize(
    "text",
    [
        "아니요 그게 다예요",
        "아니요, 그게 다예요.",
        "아니요. 그게 다예요.",  # CLOVA가 조각마다 마침표를 붙인다
        "아뇨 그게 다에요",  # STT 표기 흔들림
        "아니요 그게 다 예요",
        "아니요 그게다예요",
        "아니요 그게 다요",
        "아니오 그게 답니다",
        "아니요 그게 다입니다",
        "아니요 그게 끝이에요",
        "아니요 그게 끝이예요",
        "아니요 이게 다예요",
        "아니요 그거면 돼요",
        "아니요 그거면 됐어요",
        "아니요 됐어요",
        "아뇨 됐어요",
        "아니요 됬어요",
        "아니요 됐습니다",
        "아니요 없어요",
        "아니요 더 없어요",
        "아니요 더 필요 없어요",
        "아니요 필요 없어요",
        "아니요 괜찮아요",
        "아니요 괜찮습니다",
        "아니 됐어요",
        "아니에요 괜찮아요",
        "아니요 아니요 그게 다예요",
        "아니요 됐어요 그게 다예요",
        "없어요",
        "없어요.",
        "없습니다",
        "더 없어요",
        "없어요 그게 다예요",
    ],
)
def test_no_with_a_closing_phrase_skips_nlu(text: str) -> None:
    assert apply_rules(text) == NO_AND_DONE


@pytest.mark.parametrize(
    "text",
    [
        "아니요 아메리카노도 하나요",  # 내용 있는 뒷말은 NLU가 읽는다
        "아니요 그게 아니라 두 개요",
        "아니요 그게",  # 덜 끝난 말
        "아니요 그게 다 얼마예요",
        "아니요 메도빅은 됐어요",  # 메뉴 빼기일 수 있다
        "아니요 돼요",  # "그거면" 없이는 무엇이 된다는 말인지 모른다
        "아니요 다 돼요",
        "아니요 견과류 없어요",
        "견과류 없어요",
        "그게 다예요",  # 머리말 없는 끝 표현은 전처럼 NLU (다른 질문의 답일 수 있다)
        "됐어요",
        "괜찮아요",  # "4시 15분 괜찮으세요?"의 답이면 "네"다
        "네 괜찮아요",
        "아니요 괜찮아요 아메리카노 하나 더 주세요",
        "없어요 아메리카노요",
    ],
)
def test_no_with_other_content_still_goes_to_nlu(text: str) -> None:
    assert apply_rules(text) is None


def test_the_engine_can_tell_the_closing_no_rule_from_a_plain_no() -> None:
    # 엔진은 예/아니요를 묻는 중(메뉴 확인·시각 제안·취소 확인)에는 이 규칙을 쓰지 않는다
    assert no_and_done("아니요, 그게 다예요.") is True
    assert no_and_done("없어요") is True
    assert no_and_done("아니요") is False
    assert no_and_done("아니요 두 개요") is False


# ------------------------------------------------ 짜증 표현 (6단계 품질)
# act가 아니다. 엔진이 이미 되물은 통화에서만 보고 사장님께 넘긴다. 오탐이 적은 말만 둔다


@pytest.mark.parametrize(
    "text",
    [
        "몇 번을 말해요",
        "아니 몇 번이나 말해야 돼요",
        "몇 번째 말하는 거예요",
        "아까 말했잖아요",
        "메도빅 하나라고 말했잖아요",
        "방금 말씀드렸잖아요",
        "왜 이렇게 못 알아들어요",
        "자꾸 못 알아듣네",
        "말귀를 못 알아듣네요",
        "아 답답하네",
        "진짜 답답해요",
        "짜증나네",
        "아 짜증 나",
        "에이씨",
        "그냥 끊을게요",
        "됐어요 그냥 끊을게요",
        "끊을게요.",
        "전화 끊을게요",
    ],
)
def test_frustrated(text: str) -> None:
    assert frustrated(text)


@pytest.mark.parametrize(
    "text",
    [
        "됐어요",  # 주문 끝일 수 있다
        "됐어요 그냥",
        "아 진짜요?",
        "괜찮아요",
        "아니요 그게 다예요",
        "메도빅 하나요",
        "몇 시까지 해요?",
        "몇 번 버스 타요?",
        "말씀드린 대로 여섯 시요",
        "포장 끈 끊어지지 않게 해 주세요",
        "",
    ],
)
def test_not_frustrated(text: str) -> None:
    assert not frustrated(text)


# ---------------------------------------------------------------- 쿠폰 문의 (쿠폰 티켓 11)
# HCX 골드셋은 바꾸지 않는다(도구 스키마에 쿠폰 문의가 없다). 골드셋 꼴 사례는 여기 둔다.


@pytest.mark.parametrize(
    "text",
    [
        "쿠폰",
        "할인",
        "쿠폰 돼요?",
        "할인 돼요?",
        "할인돼요?",
        "할인 되나요",
        "쿠폰 쓸게요",
        "쿠폰 쓸 수 있어요?",
        "쿠폰 있어요?",
        "쿠폰 있나요?",
        "혹시 할인 쿠폰 있나요?",
        "할인 같은 거 없어요?",
        "할인 같은 건 없나요?",
        "할인은 안 돼요?",
        "그럼 할인은요?",
        "쿠폰은요?",
        "쿠폰 적용해 주세요",
        "쿠폰 적용돼요?",
        "쿠폰 사용 가능해요?",
        "할인 받을 수 있어요?",
        "할인 해 주세요",
        "전화 주문 할인 돼요?",
        "포장 할인 있어요?",
        "근데 혹시 쿠폰 써도 돼요?",
        "네, 쿠폰 쓸게요",
        "아 그리고 쿠폰도 있어요?",
    ],
)
def test_coupon_question(text: str) -> None:
    assert apply_rules(text) == [Act("ask_coupon", free_text=text)]


@pytest.mark.parametrize(
    "text",
    [
        "할인 메뉴 주세요",  # 메뉴 주문이 섞인 말은 주문이 먼저다 (NLU가 읽는다)
        "할인하는 메뉴 하나 주세요",
        "할인 해서 메도빅 주세요",
        "메도빅 하나랑 쿠폰 쓸게요",
        "쿠폰 있으면 아메리카노 하나 더 할게요",
        "할인된 가격으로 메도빅 두 개요",
        "쿠폰 받으려면 얼마 더 사야 해요?",  # 다른 말이 섞인 물음: NLU가 읽고 엔진이 돌린다
        "메도빅 하나요",
        "주차 돼요?",
        "배달 돼요?",
    ],
)
def test_not_a_coupon_rule(text: str) -> None:
    acts = apply_rules(text)
    assert acts is None or all(a.type != "ask_coupon" for a in acts)


@pytest.mark.parametrize(
    ("text", "mentioned"),
    [
        ("쿠폰 받으려면 얼마 더 사야 해요?", True),
        ("메도빅도 할인돼요?", True),
        ("주차 돼요?", False),
        ("배달 돼요?", False),
    ],
)
def test_mentions_coupon(text: str, mentioned: bool) -> None:
    """NLU가 질문(ask_other·ask_menu_info)으로 낸 말에 쿠폰·할인이 있으면 엔진이 쿠폰 답을 한다."""
    assert mentions_coupon(text) is mentioned


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("네, 쿠폰 쓸게요", "yes"),
        ("쿠폰 쓸게요", "yes"),
        ("쿠폰 적용해 주세요", "yes"),
        ("네, 쿠폰 돼요?", "yes"),
        ("아니요, 쿠폰 안 써도 돼요", "no"),
        ("쿠폰 안 써도 돼요", "no"),
        ("네, 쿠폰 안 써도 돼요", "no"),
        ("아니요, 쿠폰 쓸게요", None),  # 엇갈린다: 되묻는다
        ("쿠폰 돼요?", None),  # 묻기만 했다: 권유를 다시 한다
        ("메도빅 하나요", None),
        ("네", None),  # 쿠폰 말이 아니면 규칙(yes)이 받는다
    ],
)
def test_coupon_offer_answer(text: str, answer: str | None) -> None:
    """쿠폰 권유("… 더 담으실까요?")에 쿠폰 말로 한 답 (리뷰): 머리말 예/아니요를 버리지 않는다."""
    assert coupon_offer_answer(text) == answer


@pytest.mark.parametrize(
    ("text", "rest"),
    [
        ("메도빅에 견과류 들어가요? 할인도 되나요?", "메도빅에 견과류 들어가요?"),
        ("메도빅에 견과류 들어가요 할인도 되나요", "메도빅에 견과류 들어가요"),
        ("할인돼요? 그리고 주차 돼요?", "그리고 주차 돼요?"),
        ("메도빅도 할인돼요?", ""),
        ("쿠폰 받으려면 얼마 더 사야 해요?", ""),
    ],
)
def test_without_coupon_clauses(text: str, rest: str) -> None:
    """NLU가 질문으로 낸 말에서 쿠폰·할인 마디를 뺀 나머지 (리뷰: 섞인 질문을 버리지 않는다)."""
    assert without_coupon_clauses(text) == rest
