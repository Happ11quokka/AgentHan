"""티켓 05: NLU 앞 규칙 단계. 걸리면 act로 바로 바꾸고, 안 걸리면 None (NLU로 넘긴다)."""

import pytest

from agenthan.voice.dialog.rules import apply_rules
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
        "음",
    ],
)
def test_passes_to_nlu(text: str) -> None:
    assert apply_rules(text) is None
