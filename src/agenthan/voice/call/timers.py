"""통화 세션의 타이머 (3단계 리뷰 S5). 이벤트 루프의 `call_later` 위에 얹은 얇은 도우미.

- `OneShot`: 한 번 울리는 타이머. 다시 걸면 앞 것을 지운다(생각 중 효과음·재생 끝 안전망 등)
- `Deadline`: "마감 + 상한, 손님이 말하는 중이면 다시 확인" 모양의 타이머(조각 붙잡기·이어짐
  대기·무응답). 울릴 때 `wait`가 마감이 됐는지 보고, 아직이면 스스로 다시 건다
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any

POLL_S = 0.1  # 손님이 말하는 중이거나 글을 기다리면 이 간격으로 다시 본다


class OneShot:
    def __init__(self, fire: Callable[..., None]) -> None:
        self._fire = fire
        self._handle: asyncio.TimerHandle | None = None

    @property
    def armed(self) -> bool:
        return self._handle is not None

    def arm(self, delay: float, *args: Any) -> None:
        self.cancel()
        self._handle = asyncio.get_running_loop().call_later(max(delay, 0.0), self._run, *args)

    def cancel(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _run(self, *args: Any) -> None:
        self._handle = None
        self._fire(*args)


class Deadline(OneShot):
    def wait(
        self,
        now: float,
        *,
        due: float,
        cap: float = math.inf,
        busy: bool = False,
        hold: bool = False,
        poll: float = POLL_S,
    ) -> bool:
        """아직 기다리면 다시 걸고 참, 마감이면 거짓(부른 쪽이 넘긴다).

        - `due`: 마감(보통 마지막 말 끝 + 대기 시간). 그 전에는 마감까지 기다린다
        - `busy`: 손님이 말하는 중·글을 기다리는 중. 상한 전까지는 `poll` 간격으로 다시 본다
        - `cap`: 상한. 넘으면 `busy`여도 넘긴다(배경 소리가 "말하는 중"을 안 풀 때)
        - `hold`: 상한과 상관없이 미룬다(손님 글을 붙잡는 중·지금 말하는 중이라 끼어들면 안 될 때)
        """
        if hold:
            self.arm(poll)
            return True
        if now >= cap:
            return False
        if busy:
            self.arm(min(poll, cap - now))
            return True
        if now < due:
            self.arm(min(due, cap) - now)
            return True
        return False
