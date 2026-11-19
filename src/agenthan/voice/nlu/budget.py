"""HCX 한도 대응 도구 (6단계 운영 보강): 분당 버킷과 사용량 기록.

CLOVA Studio는 주 계정 단위로 분당 요청 수(QPM)와 분당 토큰(TPM = 입력 토큰 + `maxTokens`)을
센다. 테스트 키의 HCX-DASH-002는 QPM 90 · TPM 80,000이다(`research/s6-production-readiness.md`).

- `RateBudget`: 프로세스 하나가 최근 60초에 쓴 요청 수·토큰을 센다. `take()`가 False면 그 턴은
  HCX를 부르지 않고 규칙 NLU로 간다. `roomy()`는 반(`PREFETCH_SHARE`) 아래일 때만 True라서,
  버려질 수 있는 미리 해석 호출은 여유가 있을 때만 한다. 상한이 0이면 세지 않는다(기본)
- `UsageLog`: 호출마다 응답 `usage`(입력·출력 토큰), `maxTokens`, 걸린 시간, 남은 한도 헤더,
  실패 코드, 폴백 여부를 `hcx-YYYYMMDD.jsonl`에 한 줄로 남긴다. 손님 말은 넣지 않는다

둘 다 여러 엔진 스레드가 같이 쓴다(잠금 있음).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

WINDOW_S = 60.0
PREFETCH_SHARE = 0.5  # 한도의 이만큼을 넘게 썼으면 미리 해석을 건너뛴다


class RateBudget:
    def __init__(self, *, rpm: int = 0, tpm: int = 0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._rpm = rpm
        self._tpm = tpm
        self._clock = clock
        self._lock = threading.Lock()
        self._events: deque[tuple[float, int, int]] = deque()  # (시각, 요청 수, 토큰)
        self._requests = 0
        self._tokens = 0
        self._size = 0  # 직전 요청의 실제 크기(입력 + maxTokens). 다음 요청 어림에 쓴다
        self._estimate = 0  # 직전 `take`가 잡은 어림

    @property
    def limited(self) -> bool:
        return self._rpm > 0 or self._tpm > 0

    def _trim(self) -> None:
        cutoff = self._clock() - WINDOW_S
        while self._events and self._events[0][0] <= cutoff:
            _, requests, tokens = self._events.popleft()
            self._requests -= requests
            self._tokens -= tokens

    def take(self, tokens: int = 0) -> bool:
        """요청 하나를 써도 되면 세고 True. `tokens`는 이 요청의 `maxTokens`."""
        if not self.limited:
            return True
        with self._lock:
            self._trim()
            estimate = max(self._size, tokens)
            if self._rpm > 0 and self._requests + 1 > self._rpm:
                return False
            if self._tpm > 0 and self._tokens + estimate > self._tpm:
                return False
            self._events.append((self._clock(), 1, estimate))
            self._requests += 1
            self._tokens += estimate
            self._estimate = estimate
            return True

    def settle(self, *, prompt_tokens: int, max_tokens: int) -> None:
        """응답의 `usage`로 직전 어림을 실제 크기(입력 + maxTokens)로 바로잡는다."""
        if not self.limited:
            return
        with self._lock:
            size = prompt_tokens + max_tokens
            delta = size - self._estimate
            self._size = self._estimate = size
            if delta:
                self._events.append((self._clock(), 0, delta))
                self._tokens += delta

    def roomy(self) -> bool:
        """버려질 수 있는 호출(미리 해석)을 해도 될 만큼 여유가 있나."""
        if not self.limited:
            return True
        with self._lock:
            self._trim()
            return not ((self._rpm > 0 and self._requests >= self._rpm * PREFETCH_SHARE)
                        or (self._tpm > 0 and self._tokens >= self._tpm * PREFETCH_SHARE))

    def used(self) -> dict[str, int]:
        with self._lock:
            self._trim()
            return {"requests": self._requests, "tokens": self._tokens}


class UsageLog:
    """`log_dir`가 None이면 아무것도 쓰지 않는다. 쓰기 실패는 경고 한 줄로 넘어간다."""

    def __init__(self, log_dir: Path | None, *,
                 today: Callable[[], str] | None = None) -> None:
        self._dir = log_dir
        self._today = today or (lambda: datetime.now().astimezone().strftime("%Y%m%d"))
        self._lock = threading.Lock()
        self._seen = threading.local()  # 이 스레드의 직전 응답 헤더

    def observe(self, status: int, headers: Mapping[str, str]) -> None:
        """`ClovaClient(observe=…)`: 응답의 남은 한도 헤더를 기억한다(같은 스레드의 기록에 붙임)."""
        self._seen.left = (_int(headers.get("x-ratelimit-remaining-requests")),
                           _int(headers.get("x-ratelimit-remaining-tokens")))

    def record(self, kind: str, status: str, *, usage: Any = None, max_tokens: int = 0,
               ms: int | None = None, fallback: bool = False) -> None:
        left = getattr(self._seen, "left", (None, None))
        self._seen.left = (None, None)
        if self._dir is None:
            return
        usage = usage if isinstance(usage, Mapping) else {}
        line = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "kind": kind, "status": status, "fallback": fallback, "ms": ms,
            "prompt": _int(usage.get("promptTokens")),
            "completion": _int(usage.get("completionTokens")),
            "max_tokens": max_tokens,
            "left_requests": left[0], "left_tokens": left[1],
        }
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                path = self._dir / f"hcx-{self._today()}.jsonl"
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            log.warning("HCX 사용량 기록을 쓰지 못함: %s", self._dir)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value)
    return None
