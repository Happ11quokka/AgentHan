"""통화 문지기 (6단계 운영 보강). 공개 WS(`/api/phone/ws`)가 통화를 받기 전에 묻는 곳.

라우터(`ws.py`)가 연결마다 순서대로 본다.

1. **Origin** (`PHONE_ALLOWED_ORIGINS`): 목록이 있으면 그 오리진의 화면만 받는다. `accept()` 전에
   닫아 브라우저는 403을 본다. 비우면 검사하지 않는다(로컬 개발·테스트·가짜 휴대폰)
2. **점검(drain)**: `drain()`을 불렀거나 로그 폴더에 `DRAIN` 파일이 있으면 새 통화를 받지 않는다
   (`deploy.sh up`이 교체 전에 쓴다). 진행 중인 통화는 그대로 둔다
3. **동시 통화 상한** (`PHONE_MAX_CALLS`, 전부터 있던 `busy`)
4. **IP별 상한** (`PHONE_IP_MAX_CALLS` 동시, `PHONE_IP_CALLS_PER_HOUR` 시간당 시작 횟수):
   발표장 와이파이·통신사 NAT는 여러 사람이 주소 하나를 쓰므로 느슨하게 둔다. 0이면 끈다.
   루프백·사설 주소·주소를 모르는 연결은 세지 않는다. 로컬 개발이 막히지 않고, 프록시 설정을
   빠뜨려 모두가 Caddy 주소로 보이는 서버에서 손님 전체가 한 칸을 나눠 쓰는 사고도 없다
5. **하루 예산** (`PHONE_DAY_MAX_CALLS` 통화 수, `PHONE_DAY_MAX_MINUTES` 통화 분 합계): 진짜
   비용 상한. 가게 시간대의 날짜로 세고, 로그 폴더의 `phone-budget.json`에 적어 프로세스가 다시
   떠도 남는다. 둘 다 0(기본)이면 세지 않고 파일도 만들지 않는다

서버가 내려갈 때(`farewell`, SIGTERM): 새 통화를 그만 받고, 통화 중인 화면마다 `error`
(`restarting`, "서버 점검으로 통화를 끝낼게요…")를 한 번 보낸다. 라우터가 통화마다 보내는 함수를
`Admission.notify`에 걸어 둔다. uvicorn은 그 뒤에 열린 WS를 1012로 닫는다.

거절은 `Refusal`(코드·화면 문구·다시 걸면 되나)로 돌려주고, 라우터가 `busy`와 같은 `error`
이벤트로 보낸 뒤 소켓을 닫는다. 화면은 그 문구를 그대로 보여 준다.

손님 주소(`client_ip`): Caddy 뒤에서는 소켓 상대가 Caddy 컨테이너다. 상대가 믿는 프록시
(`PHONE_TRUSTED_PROXIES`, CIDR 목록 또는 `private`)일 때만 `X-Forwarded-For`를 오른쪽부터 읽어
믿는 프록시가 아닌 첫 주소를 쓴다(손님이 헤더 앞쪽에 꾸며 넣은 주소는 무시된다).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

BUDGET_FILE = "phone-budget.json"
DRAIN_FILE = "DRAIN"
HOUR_S = 3_600.0
PRIVATE = "private"  # `PHONE_TRUSTED_PROXIES`에 쓰는 말: 루프백 + 사설 대역 전부

# 화면에 보이는 거절 문구 (봇이 말하는 대사가 아니다. 대사는 `dialog/templates.py`)
MSG_BUSY = "지금 통화가 많아요. 잠시 후 다시 걸어 주세요."
MSG_BUSY_IP = "같은 곳에서 걸려 온 통화가 많아요. 잠시 후 다시 걸어 주세요."
MSG_RATE = "짧은 시간에 전화를 많이 거셨어요. 조금 뒤에 다시 걸어 주세요."
MSG_CLOSED = "오늘은 전화 주문이 마감됐어요. 내일 다시 걸어 주세요."
MSG_MAINTENANCE = "잠시 점검 중이에요. 1분쯤 뒤에 다시 걸어 주세요."
MSG_RESTART = "서버 점검으로 통화를 끝낼게요. 죄송해요, 1분쯤 뒤에 다시 걸어 주세요."


@dataclass(frozen=True)
class GateSettings:
    allowed_origins: tuple[str, ...] = ()
    max_calls: int = 0  # 0이면 안 본다 (라우터가 `PHONE_MAX_CALLS`를 넣는다)
    ip_max_calls: int = 0
    ip_calls_per_hour: int = 0
    day_max_calls: int = 0
    day_max_minutes: float = 0.0


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str
    retry: bool = True


@dataclass
class Admission:
    """`admit` 결과. `refusal`이 없으면 통화 한 칸을 잡은 것이고, 끝나면 `release`에 돌려준다."""

    ip: str
    started: float
    refusal: Refusal | None = None
    released: bool = False
    # 이 통화 화면에 `error` 이벤트를 보내는 함수 (라우터가 건다). 서버가 내려갈 때 쓴다
    notify: Callable[[Refusal], Awaitable[None]] | None = None


def normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def parse_networks(names: Sequence[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network
                                                  | str, ...]:
    """CIDR·주소 목록. `private`는 그대로 둔다. 틀린 값은 `ValueError`."""
    return tuple(n if n == PRIVATE else ipaddress.ip_network(n, strict=False) for n in names)


def _address(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address((host or "").strip())
    except ValueError:
        return None


def _local(host: str) -> bool:
    address = _address(host)
    return address is None or address.is_loopback or address.is_private


def _trusted(host: str | None, networks: Sequence[object]) -> bool:
    address = _address(host)
    if address is None:
        return False
    for network in networks:
        if network == PRIVATE:
            if address.is_loopback or address.is_private:
                return True
        elif address in network:  # type: ignore[operator]
            return True
    return False


def client_ip(peer: str | None, forwarded_for: str | None, trusted: Sequence[str]) -> str:
    """손님 주소. 소켓 상대가 믿는 프록시일 때만 `X-Forwarded-For`를 읽는다."""
    peer = (peer or "").strip()
    networks = parse_networks(trusted)
    if not forwarded_for or not _trusted(peer, networks):
        return peer
    for hop in reversed([h.strip() for h in forwarded_for.split(",") if h.strip()]):
        if not _trusted(hop, networks):
            return hop if _address(hop) is not None else peer
    return peer


class CallGate:
    """이벤트 루프 하나에서만 부른다(잠금 없음). 프로세스에 하나, 라우터·`/healthz`가 같이 쓴다."""

    def __init__(
        self,
        settings: GateSettings,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        today: Callable[[], str] | None = None,
        budget_file: Path | None = None,
        drain_file: Path | None = None,
    ) -> None:
        self._s = settings
        self._monotonic = monotonic
        self._today = today or (lambda: datetime.now().astimezone().date().isoformat())
        self._origins = frozenset(normalize_origin(o) for o in settings.allowed_origins)
        self._budget_on = settings.day_max_calls > 0 or settings.day_max_minutes > 0
        self._budget_file = budget_file if self._budget_on else None
        self._drain_file = drain_file
        self._draining = False
        self.active = 0
        self._live: list[Admission] = []
        self._by_ip: dict[str, int] = {}
        self._starts: dict[str, deque[float]] = {}
        self._day = ""
        self._calls = 0
        self._seconds = 0.0
        self._load()

    # ------------------------------------------------------------ Origin

    def origin_allowed(self, origin: str | None) -> bool:
        if not self._origins:
            return True
        return origin is not None and normalize_origin(origin) in self._origins

    # ------------------------------------------------------------ 점검

    def drain(self) -> None:
        """새 통화를 그만 받는다(프로세스가 끝날 때까지)."""
        self._draining = True

    @property
    def draining(self) -> bool:
        return self._draining or (self._drain_file is not None and self._drain_file.exists())

    def clear_stale_drain(self) -> None:
        """시작할 때 한 번: 지난 배포가 남긴 `DRAIN` 표시를 지운다(새 프로세스는 통화를 받는다)."""
        if self._drain_file is None:
            return
        try:
            self._drain_file.unlink(missing_ok=True)
        except OSError:
            log.warning("점검 표시 파일을 지우지 못함: %s", self._drain_file, exc_info=True)

    async def farewell(self, timeout_s: float = 1.0) -> int:
        """서버가 내려간다: 새 통화를 막고 통화 중인 화면에 알린다. 알린 통화 수를 돌려준다."""
        self.drain()
        notice = Refusal("restarting", MSG_RESTART)
        told = 0
        for admission in list(self._live):
            if admission.notify is None:
                continue
            try:
                await asyncio.wait_for(admission.notify(notice), timeout_s)
                told += 1
            except Exception:  # 이미 끊긴 손님
                log.info("종료 안내를 보내지 못함", exc_info=True)
        if told:
            log.info("서버 종료 안내를 통화 %d개에 보냄", told)
        return told

    # ------------------------------------------------------------ 받기·돌려주기

    def admit(self, ip: str) -> Admission:
        now = self._monotonic()
        refusal = self._check(ip, now)
        if refusal is not None:
            log.info("통화 거절 %s (%s)", refusal.code, ip or "주소 모름")
            return Admission(ip, now, refusal, released=True)
        self.active += 1
        if not _local(ip):
            self._by_ip[ip] = self._by_ip.get(ip, 0) + 1
            self._starts.setdefault(ip, deque()).append(now)
        if self._budget_on:
            self._roll_day()
            self._calls += 1
            self._save()
        admission = Admission(ip, now)
        self._live.append(admission)
        return admission

    def release(self, admission: Admission) -> None:
        if admission.released:
            return
        admission.released = True
        self.active = max(0, self.active - 1)
        if admission in self._live:
            self._live.remove(admission)
        ip = admission.ip
        if ip in self._by_ip:
            self._by_ip[ip] -= 1
            if self._by_ip[ip] <= 0:
                del self._by_ip[ip]
        if self._budget_on:
            self._roll_day()
            self._seconds += max(0.0, self._monotonic() - admission.started)
            self._save()

    def _check(self, ip: str, now: float) -> Refusal | None:
        s = self._s
        if self.draining:
            return Refusal("maintenance", MSG_MAINTENANCE)
        if s.max_calls > 0 and self.active >= s.max_calls:
            return Refusal("busy", MSG_BUSY)
        if not _local(ip):
            if s.ip_max_calls > 0 and self._by_ip.get(ip, 0) >= s.ip_max_calls:
                return Refusal("busy_ip", MSG_BUSY_IP)
            if s.ip_calls_per_hour > 0 and self._recent(ip, now) >= s.ip_calls_per_hour:
                return Refusal("rate_limited", MSG_RATE)
        if self._budget_on:
            self._roll_day()
            if (0 < s.day_max_calls <= self._calls
                    or 0 < s.day_max_minutes <= self._seconds / 60):
                return Refusal("closed_today", MSG_CLOSED, retry=False)
        return None

    def _recent(self, ip: str, now: float) -> int:
        starts = self._starts.get(ip)
        if not starts:
            return 0
        while starts and now - starts[0] >= HOUR_S:
            starts.popleft()
        if not starts:
            del self._starts[ip]
            return 0
        return len(starts)

    # ------------------------------------------------------------ 하루 예산 파일

    def _roll_day(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._calls, self._seconds = day, 0, 0.0

    def _load(self) -> None:
        path = self._budget_file
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._day = str(data["date"])
            self._calls = int(data["calls"])
            self._seconds = float(data["seconds"])
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("하루 예산 파일을 읽지 못해 0부터 셈: %s", path)
            self._day, self._calls, self._seconds = "", 0, 0.0

    def _save(self) -> None:
        path = self._budget_file
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": self._day, "calls": self._calls,
                                       "seconds": round(self._seconds, 1)}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:  # 디스크가 찼어도 통화는 받는다. 메모리 숫자로 계속 센다
            log.warning("하루 예산 파일을 쓰지 못함: %s", path, exc_info=True)

    # ------------------------------------------------------------ 상태

    def snapshot(self) -> dict[str, object]:
        """`/healthz`가 쓴다. 손님 주소는 넣지 않는다."""
        if self._budget_on:
            self._roll_day()
        return {
            "active": self.active,
            "draining": self.draining,
            "day": {"calls": self._calls, "minutes": round(self._seconds / 60, 1)}
            if self._budget_on else None,
        }
