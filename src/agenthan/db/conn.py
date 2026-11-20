"""DB 연결과 장애 차단기. 값(비밀번호)은 절대 로그에 남기지 않는다."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger(__name__)

DB_URL_ENV = "AGENTHAN_DB_URL"
STORE_ID_ENV = "AGENTHAN_STORE_ID"  # 가게 지식 기본 가게. 비면 더미(dummy_store)
DEFAULT_STORE_ID = "dummy_store"
CONNECT_TIMEOUT_S = 3
FAIL_COOLDOWN_S = 30.0  # 연결 실패 뒤 DB를 쉬는 시간. 그동안은 JSON으로만

T = TypeVar("T")

_lock = threading.Lock()
_paused_until = 0.0


def db_url(url: str | None = None) -> str:
    return (url if url is not None else os.environ.get(DB_URL_ENV, "")).strip()


def enabled(url: str | None = None) -> bool:
    return bool(db_url(url))


def default_store_id() -> str:
    return os.environ.get(STORE_ID_ENV, "").strip() or DEFAULT_STORE_ID


def connect(url: str | None = None, *, autocommit: bool = True):
    """새 연결. 호출하는 쪽이 `with connect() as conn:`으로 닫는다."""
    import psycopg

    target = db_url(url)
    if not target:
        raise RuntimeError(f"{DB_URL_ENV}가 비어 있습니다")
    return psycopg.connect(target, autocommit=autocommit, connect_timeout=CONNECT_TIMEOUT_S)


def available(url: str | None = None) -> bool:
    """켜져 있고 쉬는 중이 아니면 True. 실제 연결 확인은 `guarded`가 한다."""
    return enabled(url) and time.monotonic() >= _paused_until


def pause(error: BaseException) -> None:
    global _paused_until
    with _lock:
        _paused_until = time.monotonic() + FAIL_COOLDOWN_S
    log.warning("DB를 %d초 쉽니다(JSON으로 동작): %s", FAIL_COOLDOWN_S, type(error).__name__)


def reset_pause() -> None:
    global _paused_until
    with _lock:
        _paused_until = 0.0


def guarded(work: Callable[[object], T], default: T, url: str | None = None) -> T:
    """DB가 쓸 수 있으면 `work(conn)`을 돌리고, 꺼져 있거나 실패하면 `default`를 돌려준다."""
    if not available(url):
        return default
    try:
        with connect(url) as conn:
            return work(conn)
    except Exception as error:  # 연결·쿼리 실패 → 잠깐 끄고 JSON으로
        pause(error)
        return default
