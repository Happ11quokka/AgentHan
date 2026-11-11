"""서버 집안일 (6단계 운영 보강): 보관 기한이 지난 통화 기록 지우기 + 디스크 사용률 경고.

- **보관 기한** (`PHONE_LOG_KEEP_DAYS`, 기본 14일, 0이면 지우지 않음): 손님 말이 든 턴 기록
  `phone-YYYYMMDD.jsonl`, STT 원본 `stt-YYYYMMDD.jsonl`, HCX 사용량 `hcx-YYYYMMDD.jsonl`, 통화 녹음
  `<record_dir>/YYYYMMDD/<통화 id>.wav`를 마지막으로 쓴 때(mtime)부터 기한이 지나면 지운다.
  서버가 쓴 이름만 건드린다(하루 예산 파일·점검 표시·다른 사람이 둔 파일은 그대로). 로그 폴더
  바로 아래만 보고 하위 폴더는 녹음 폴더만 본다
- **디스크 경고** (`PHONE_DISK_WARN_PCT`, 기본 80, 0이면 끔): 로그 폴더가 있는 디스크 사용률이
  넘으면 경고 로그 한 줄. 알림 도구는 붙이지 않는다(서버 한 대, `deploy.sh logs app`으로 본다)

서버가 시작할 때 한 번, 그 뒤 한 시간마다 돈다(`Housekeeper.run_forever`). 파일 일은 스레드에서
한다(이벤트 루프를 막지 않는다).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from collections.abc import Callable, Iterable
from pathlib import Path

log = logging.getLogger(__name__)

DAY_S = 86_400.0
INTERVAL_S = 3_600.0
# 서버가 쓰는 기록 파일 이름 (`call/latency.py`, `call/debuglog.py`, `nlu/budget.py`)
_LOG_NAME = re.compile(r"^(phone|stt|hcx)-[\w-]+\.jsonl$")
_DAY_DIR = re.compile(r"^\d{8}$")


def sweep(dirs: Iterable[Path], *, keep_days: float, now: float | None = None) -> int:
    """기한이 지난 기록·녹음을 지우고 지운 파일 수를 돌려준다. `keep_days`가 0이면 안 지운다."""
    if keep_days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - keep_days * DAY_S
    removed = 0
    for folder in dirs:
        if not folder.is_dir():
            continue
        for path in list(folder.iterdir()):
            if path.is_file() and _LOG_NAME.match(path.name):
                removed += _remove_if_old(path, cutoff)
            elif path.is_dir() and _DAY_DIR.match(path.name):  # 녹음: YYYYMMDD/<통화>.wav
                for wav in list(path.glob("*.wav")):
                    removed += _remove_if_old(wav, cutoff)
                try:
                    path.rmdir()  # 비었을 때만 지워진다
                except OSError:
                    pass
    return removed


def _remove_if_old(path: Path, cutoff: float) -> int:
    try:
        if path.stat().st_mtime >= cutoff:
            return 0
        path.unlink()
        return 1
    except OSError:
        log.warning("기한 지난 기록을 지우지 못함: %s", path.name)
        return 0


def disk_used_pct(path: Path) -> float | None:
    """`path`가 있는 디스크의 사용률(%). 폴더가 아직 없으면 있는 윗폴더로 잰다."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return None
    return usage.used / usage.total * 100 if usage.total else None


class Housekeeper:
    def __init__(
        self,
        *,
        log_dir: Path | None,
        record_dir: Path | None,
        keep_days: float,
        disk_warn_pct: float,
        disk_pct: Callable[[Path], float | None] = disk_used_pct,
        interval_s: float = INTERVAL_S,
    ) -> None:
        self._dirs = [d for d in (log_dir, record_dir) if d is not None]
        self._probe = log_dir or Path(".")
        self._keep_days = keep_days
        self._warn_pct = disk_warn_pct
        self._disk_pct = disk_pct
        self._interval_s = interval_s
        self._last_pct: float | None = None

    def run_once(self) -> None:
        removed = sweep(self._dirs, keep_days=self._keep_days)
        if removed:
            log.info("보관 기한(%g일)이 지난 통화 기록·녹음 %d개를 지움", self._keep_days, removed)
        pct = self._last_pct = self._disk_pct(self._probe)
        if pct is not None and 0 < self._warn_pct <= pct:
            log.warning("디스크 사용률 %.0f%% (경고 기준 %g%%). 오래된 이미지·로그를 정리하세요: "
                        "deploy/phone/README.md 비상 대응", pct, self._warn_pct)

    async def run_forever(self) -> None:
        """지금 한 번, 그 뒤 `interval_s`마다. 서버 lifespan이 작업으로 띄우고 끝날 때 취소한다."""
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # 집안일이 죽어도 통화 서버는 돈다
                log.exception("집안일(기록 정리·디스크 확인) 실패")
            await asyncio.sleep(self._interval_s)

    def status(self) -> dict[str, object]:
        pct = self._last_pct
        return {"disk_pct": round(pct) if pct is not None else None,
                "keep_days": self._keep_days}
