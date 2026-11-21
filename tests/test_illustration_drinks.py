"""음료 그림 표(티켓 03): 이 메뉴 이름이면 이 그림.

임베딩(티켓 06)이 끼어들지 않게 늘 실패하는 임베딩을 넘긴다.
"""

import pytest

from agenthan.illustration.matcher import pick_illustration


def broken_embed(_text):
    raise RuntimeError("임베딩 서버가 응답하지 않음")


# (메뉴 이름, 분류 또는 None, 기대하는 그림 id)
TABLE: list[tuple[str, str | None, str]] = []


@pytest.mark.parametrize(("name", "category", "expected"), TABLE)
def test_drink_illustrations(name, category, expected):
    assert pick_illustration(name, category, broken_embed) == expected
