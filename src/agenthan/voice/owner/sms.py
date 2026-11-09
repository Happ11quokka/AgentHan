"""손님 문자 미리보기 문장 (4단계 spec "사장님 허브" 늦은 답·문자, 티켓 07).

실제 문자는 보내지 않는다. 허브가 이 문장으로 "손님에게 보낸 문자"를 만들어 사장님 카드와 손님
통화 화면("받은 문자")에 보인다. 문장은 모두 고정 틀이다(HCX로 다듬지 않는다, grilling Q14).
사장님 자유 입력 글만 번역문(또는 원문)을 « » 안에 그대로 넣는다. 사람 확인 전 초안(티켓 11).
"""

from __future__ import annotations

from collections.abc import Sequence

from agenthan.voice.dialog import templates

# 확정 문자에 이름을 다 적는 메뉴 줄 수. 넘으면 "외 N가지"
ORDER_TEXT_LINES = 3


def order_text(lines: Sequence[tuple[str, int]], total: int, pickup_label: str) -> str:
    """확정 주문: 메뉴 요약(이름·개수) · 합계 · 픽업 시각.

    `lines`는 (한국어 메뉴명, 개수), `pickup_label`은 `18:30`(비면 픽업 문장을 뺀다).
    """
    named = [f"{name} {qty}개" for name, qty in lines[:ORDER_TEXT_LINES]]
    more = len(lines) - ORDER_TEXT_LINES
    menu = ", ".join(named) + (f" 외 {more}가지" if more > 0 else "")
    parts = ["주문이 확정됐어요.", f"{menu}, 합계 {total:,}원." if menu else f"합계 {total:,}원."]
    if pickup_label:
        parts.append(f"{pickup_label}에 준비해 둘게요.")
    return " ".join(parts)


def late_answer_text(menu_name: str, answer: str, *, subject: str = "") -> str:
    """늦은 버튼 답(시간 초과 뒤·통화 끝 뒤). 통화에서 말했을 문장과 같은 고정 존댓말.

    `subject`는 들어가는지만 묻는 질문의 대상이다(버튼 뜻이 들어가요/안 들어가요, 리뷰 P2).
    """
    said = templates.owner_answer(answer, name=menu_name, subject=subject)
    return f"{_about(menu_name)} {said}"


def free_answer_text(menu_name: str, translated: str) -> str:
    """사장님 자유 입력 답의 한국어 번역. 사장님 말을 옮긴 것이라는 표시를 붙인다."""
    return f"{_about(menu_name)} 사장님 답변(번역): «{translated}»"


def untranslated_answer_text(menu_name: str, original: str) -> str:
    """자유 입력 답을 번역하지 못했다(번역기 없음·실패). 사장님 글 원문을 그대로."""
    return f"{_about(menu_name)} 사장님 답변(원문, 번역 실패): «{original}»"


def _about(menu_name: str) -> str:
    return f"{menu_name} 문의 답변이에요."
