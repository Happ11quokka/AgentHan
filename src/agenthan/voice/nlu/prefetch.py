"""미리 해석: NLU를 감싸 이어짐 대기 중에 미리 부른 결과를 엔진이 다시 쓰게 한다 (3단계 티켓 02).

세션이 엔진에 이 래퍼를 넣는다. 엔진은 지금처럼 `handle` 안에서 `parse(text, ctx)`를 부른다.

- `prefetch(text, ctx, submit)`: 이어짐 대기 중 붙잡은 글로 안쪽 NLU를 미리 부른다(다른 스레드).
  호출은 `submit`이 돌린다(세션의 엔진 풀). 이미 같은 키로 불러 두었으면 아무것도 하지 않는다
- `parse(text, ctx)`: 미리 부른 키 `(글, NluContext)`와 같으면 그 결과를 기다려 돌려준다(적중).
  다르면 미리 부른 것은 버리고 새로 부른다. `NluContext`에는 시각 필드가 없어 키가 흔들리지 않는다
- 미리 부른 호출에도 시간 제한(`timeout_s`, 미리 부른 때부터)이 있다. 넘으면 버리고 새로 부른다
- 아직 시작도 못 한 미리 부르기(풀이 꽉 참)는 취소하고 새로 부른다. 그래서 엔진 스레드가 같은 풀의
  대기열에 있는 일을 기다리며 막히지 않는다
- 요청 본문은 엔진이 부를 때와 같다(같은 `parse(text, ctx)`). HCX 녹화본을 다시 만들 필요가 없다

기록(`take_stats`, 엔진 턴마다 한 번): `prefetched` 미리 부른 수, `used` 적중했나,
`discarded` 부르고 안 쓴 호출 수, `miss` 안 쓴 이유(text·ctx·timeout·error·not_started,
적중이면 None), `wait_ms` 엔진이 미리 부른 결과를 기다린 시간.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from agenthan.voice.nlu.base import Act, Nlu, NluContext

Submit = Callable[[Callable[[], None]], Any]


@dataclass
class _Slot:
    text: str
    ctx: NluContext
    future: Future[list[Act]]
    started: float


def _blank() -> dict[str, Any]:
    return {"prefetched": 0, "used": False, "discarded": 0, "miss": None, "wait_ms": None}


class PrefetchNlu:
    def __init__(
        self, inner: Nlu, *, timeout_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._inner = inner
        self._timeout_s = timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._slot: _Slot | None = None
        self._stats = _blank()

    def prefetch(self, text: str, ctx: NluContext, submit: Submit) -> None:
        with self._lock:
            old = self._slot
            if old is not None and (old.text, old.ctx) == (text, ctx):
                return
            if old is not None:
                self._discard(old)
            slot = _Slot(text, ctx, Future(), self._clock())
            self._slot = slot
            self._stats["prefetched"] += 1
        submit(lambda: self._run(slot))

    def _run(self, slot: _Slot) -> None:
        if not slot.future.set_running_or_notify_cancel():
            return  # 시작 전에 취소됐다
        try:
            slot.future.set_result(self._inner.parse(slot.text, slot.ctx))
        except BaseException as err:  # 엔진 쪽 `parse`가 새로 불러 같은 오류를 올린다
            slot.future.set_exception(err)

    def _discard(self, slot: _Slot) -> None:
        """잠금 안에서 부른다. 이미 돌기 시작한 호출만 버린 호출로 센다."""
        if not slot.future.cancel():
            self._stats["discarded"] += 1

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        with self._lock:
            slot, self._slot = self._slot, None
            if slot is None:
                pass
            elif slot.text != text or slot.ctx != ctx:
                self._stats["miss"] = "text" if slot.text != text else "ctx"
                self._discard(slot)
                slot = None
            elif slot.future.cancel():
                self._stats["miss"] = "not_started"
                slot = None
        if slot is None:
            return self._inner.parse(text, ctx)
        waited_from = self._clock()
        left = self._timeout_s - (waited_from - slot.started)
        try:
            acts = slot.future.result(timeout=max(left, 0.0))
        except TimeoutError:
            with self._lock:
                self._stats["miss"] = "timeout"
                self._stats["discarded"] += 1
            return self._inner.parse(text, ctx)
        except Exception:
            with self._lock:
                self._stats["miss"] = "error"
            return self._inner.parse(text, ctx)
        with self._lock:
            self._stats["used"] = True
            self._stats["wait_ms"] = round((self._clock() - waited_from) * 1000)
        return list(acts)

    def take_stats(self) -> dict[str, Any] | None:
        """지난 `take_stats` 뒤의 기록. 미리 부른 적이 없으면 None."""
        with self._lock:
            stats, self._stats = self._stats, _blank()
        return stats if stats["prefetched"] or stats["discarded"] else None
