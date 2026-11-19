"""대화 상태 (spec §5)."""

from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    GREETING = "greeting"
    ORDERING = "ordering"
    CLARIFY = "clarify"
    ASK_PICKUP = "ask_pickup"
    CONFIRM = "confirm"
    CLOSING = "closing"
    FALLBACK = "fallback"
    DONE = "done"


# 이 상태로 턴이 끝나면 엔진은 다음 턴부터 DONE이다.
TERMINAL = frozenset({State.CLOSING, State.FALLBACK, State.DONE})
