"""기록 파일 날짜는 실제 시계로 (11/16 실기기 로그).

서버는 `PHONE_NOW` 시연 시계를 쓰는데, 통화마다 같은 시각(11/11 16:00)에서 시작해서 턴 기록·STT
원본·녹음이 모두 11/11 이름으로 한 파일에 쌓였다. 파일을 계속 덧붙이니 보관 기한(mtime 14일)도
지나지 않았고, `at`이 모두 16:00대라 어느 날 통화인지 가를 수 없었다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from call_harness import NOW, Call

from agenthan.voice.call.fakes import FakeClock, FakeSttStream

REAL = datetime.fromisoformat("2026-11-16T10:08:00+09:00")


def test_logs_and_recordings_are_named_by_the_real_date_not_the_demo_clock(tmp_path) -> None:
    record_dir = tmp_path / "rec"

    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=["메도빅 하나요"]),
                        wall_clock=FakeClock(REAL), record_dir=record_dir) as c:
            await c.start()
            await c.turn(1)
            return c

    c = asyncio.run(call())
    assert NOW.date() != REAL.date()  # 가게 시계(시연)는 11/08
    lines = [json.loads(x) for x in (c.log_dir / "phone-20261116.jsonl").read_text().splitlines()]
    assert lines and all(line["at"].startswith("2026-11-16T10:08") for line in lines)
    assert not (c.log_dir / "phone-20261108.jsonl").exists()
    assert (c.log_dir / "stt-20261116.jsonl").exists()
    assert list((record_dir / "20261116").glob("*.wav"))


def test_without_a_wall_clock_the_store_clock_names_the_files(tmp_path) -> None:
    async def call() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=["메도빅 하나요"])) as c:
            await c.start()
            await c.turn(1)
            return c

    c = asyncio.run(call())
    assert (c.log_dir / "phone-20261108.jsonl").exists()
