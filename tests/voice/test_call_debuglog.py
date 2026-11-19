"""통화 진단 기록 (5단계 티켓 03): STT 결과 원본과 프레임 도착 간격."""

from __future__ import annotations

import asyncio
import json

from call_harness import Call

from agenthan.voice.call.debuglog import CallDebugLog
from agenthan.voice.call.fakes import FakeSttStream
from agenthan.voice.call.ports import SttResult


def test_frames_arriving_on_time_leave_no_gaps() -> None:
    d = CallDebugLog()
    for i in range(30):
        d.frame(at_ms=1_000 + i * 100, nbytes=3_200)
    f = d.as_dict()["frames"]
    assert f["count"] == 30 and f["audio_ms"] == 3_000
    assert f["late"] == [] and f["max_gap_ms"] == 100


def test_a_stall_is_kept_with_how_far_audio_fell_behind_the_clock() -> None:
    d = CallDebugLog(late_ms=250)
    d.frame(at_ms=0, nbytes=3_200)
    d.frame(at_ms=100, nbytes=3_200)
    d.frame(at_ms=900, nbytes=3_200)  # 0.8초 멈췄다가
    for _ in range(6):
        d.frame(at_ms=905, nbytes=3_200)  # 몰려 온다
    f = d.as_dict()["frames"]
    assert f["max_gap_ms"] == 800
    assert f["late"] == [{"at_ms": 900, "gap_ms": 800, "behind_ms": 700}]
    assert f["max_behind_ms"] == 700


def test_stt_results_and_ep_flags_keep_arrival_time_and_audio_time() -> None:
    d = CallDebugLog()
    d.stt_origin(at_ms=5_000)
    d.ep(at_ms=9_082, audio_ms=3_900)
    d.result(at_ms=9_447, r=SttResult("메도비", 1_200, 1_900, "endPoint", ep_flag=True, seq_id=1,
                                      confidence=0.4))
    out = d.as_dict()
    assert out["stt_origin_ms"] == 5_000
    assert out["stt"] == [
        {"ep_at_ms": 9_082, "audio_ms": 3_900},
        {"at_ms": 9_447, "text": "메도비", "start_ms": 1_200, "end_ms": 1_900, "epd": "endPoint",
         "ep_flag": True, "seq": 1, "conf": 0.4},
    ]


def test_each_call_writes_one_stt_line_next_to_the_turn_log(tmp_path) -> None:
    async def go() -> Call:
        async with Call(tmp_path, stt=FakeSttStream(replies=["메도빅 하나요"])) as c:
            await c.start()
            await c.turn(1)
            return c

    c = asyncio.run(go())
    (line,) = [json.loads(s) for s in (c.log_dir / "stt-20261108.jsonl").read_text().splitlines()]
    assert line["call"] == "test-call"
    assert line["frames"]["count"] > 0
    texts = [e["text"] for e in line["stt"] if "text" in e]
    assert texts[0] == "메도빅 하나요"
    assert any("ep_at_ms" in e for e in line["stt"])
    assert isinstance(line["stt_origin_ms"], int)
