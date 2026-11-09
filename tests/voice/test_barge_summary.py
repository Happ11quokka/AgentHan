"""음성 끼어들기 측정 요약 (3단계 티켓 06): 통화 JSONL → 에코 바닥·판정 수·멈춤 지연."""

from __future__ import annotations

import asyncio
import json

from call_harness import Call, silence, tone

from agenthan.voice.call.barge import BargeSettings
from agenthan.voice.call.barge_summary import main, summarize
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.frames import BYTES_PER_MS
from agenthan.voice.call.session import SessionSettings


def _window(kind, p50, p95, mx, events=(), results=(), stop_ms=1_000):
    return {"kind": kind, "stop_ms": stop_ms, "rms": {"frames": 10, "p50": p50, "p95": p95,
                                                      "max": mx},
            "events": list(events), "results": list(results)}


LINES = [
    {"call": "c1", "turn": 0, "client": {"mic": {"echoCancellation": True,
                                                 "autoGainControl": True},
                                         "ctx_order": "context-first", "voice_barge_in": True},
     "barge": _window("blocked", 300, 900, 1_200)},
    {"call": "c1", "turn": 1, "text": "비밀 글",
     "listen_rms": {"voiced": {"frames": 8, "p10": 2_000, "p50": 3_000, "p95": 4_000, "max": 5_000},
                    "quiet": {"frames": 5, "p10": 100, "p50": 200, "p95": 400, "max": 600}},
     "turn_model": {"src": "model", "ms": 31.2}, "turn_end": {"score": 0.82},
     "barge": _window("open", 200, 700, 800,
                      events=[{"act": "duck", "by": "sound", "at_ms": 1_000},
                              {"act": "unduck", "by": "text", "at_ms": 1_900}],
                      results=[{"verdict": "backchannel", "text": "네", "start_ms": 1_000,
                                "end_ms": 1_600}])},
    {"call": "c1", "turn": 2,
     "listen_rms": {"voiced": {"frames": 4, "p10": 1_800, "p50": 2_600, "p95": 3_000, "max": 3_100},
                    "quiet": {"frames": 0}},
     "turn_model": {"src": "late"}, "turn_end": {"score": None},
     "barge": _window("open", 400, 1_100, 1_300, stop_ms=1_250,
                      events=[{"act": "duck", "by": "sound", "at_ms": 5_000},
                              {"act": "stop", "by": "sound", "at_ms": 6_250}],
                      results=[{"verdict": "echo", "text": "더 필요하신"}]),
     "interrupt": {"by": "voice", "reason": "sound", "report_ms": 120}},
    {"call": "c2", "turn": 0, "client": {"mic": {"echoCancellation": True,
                                                 "autoGainControl": False},
                                         "ctx_order": "mic-first", "voice_barge_in": True},
     "barge": _window("blocked", 100, 300, 400)},
    {"call": "c2", "turn": 1,
     "barge": _window("readback", 100, 200, 300,
                      events=[{"act": "duck", "by": "sound", "at_ms": 100},
                              {"act": "stop", "by": "text", "at_ms": 900}],
                      results=[{"verdict": "accept"}]),
     "interrupt": {"by": "voice", "reason": "text", "report_ms": 80}},
]


def test_per_call_rows_compare_mic_settings_and_echo_floor() -> None:
    rows = {row["call"]: row for row in summarize(LINES)["calls"]}
    assert rows["c1"] == {
        "call": "c1", "echo_cancellation": True, "auto_gain": True, "ctx_order": "context-first",
        "barge_capable": True, "windows": 3, "echo_p95": 1_100, "duck": 2, "unduck": 1,
        "stop": 1, "false_stops": 1, "last_stop_ms": 1_250,
    }
    assert (rows["c2"]["ctx_order"], rows["c2"]["auto_gain"], rows["c2"]["false_stops"]) == (
        "mic-first", False, 0
    )


def test_echo_rms_results_events_and_stop_latency() -> None:
    s = summarize(LINES)
    assert s["windows"] == {"blocked": 2, "open": 2, "readback": 1}
    assert s["echo_rms"]["open"] == {"windows": 2, "p50": 300, "p95": 1_080, "max": 1_300}
    assert s["results"] == {"accept": 1, "backchannel": 1, "echo": 1}
    assert s["events"] == {"duck:sound": 3, "stop:sound": 1, "stop:text": 1, "unduck:text": 1}
    assert s["duck_to_unduck_ms"] == {"n": 1, "p50_ms": 900, "p90_ms": 900}
    assert s["false_stops"] == 1
    assert s["backchannel_ms"] == {"n": 1, "p50_ms": 600, "p90_ms": 600}
    assert s["stop_report_ms"] == {"n": 2, "p50_ms": 100, "p90_ms": 116}


def test_listening_rms_and_smart_turn_scores_for_the_thresholds() -> None:
    s = summarize(LINES)
    assert s["listen_rms"] == {"turns": 2, "voiced_p10": 1_900, "voiced_p50": 2_800,
                               "quiet_p95": 400, "quiet_max": 600}
    assert s["turn_model"] == {"src": {"late": 1, "model": 1},
                               "ms": {"n": 1, "p50_ms": 31, "p90_ms": 31},
                               "score_bands": {"le_0.3": 0, "middle": 0, "ge_0.7": 1}}


def test_score_bands_follow_the_bands_the_server_used(tmp_path, capsys) -> None:
    # 감사 7번: `PHONE_TURN_LOW/HIGH`를 바꿔 띄운 서버의 기록은 그 대역으로 센다
    scores = [{"call": "c", "turn": i, "turn_end": {"score": s}}
              for i, s in enumerate([0.1, 0.25, 0.5, 0.75, 0.9])]
    assert summarize(scores)["turn_model"]["score_bands"] == {
        "le_0.3": 2, "middle": 1, "ge_0.7": 2}
    assert summarize(scores, low=0.2, high=0.8)["turn_model"]["score_bands"] == {
        "le_0.2": 1, "middle": 3, "ge_0.8": 1}

    path = tmp_path / "phone.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in scores))
    main([str(path), "--low", "0.2", "--high", "0.8"])
    assert json.loads(capsys.readouterr().out)["turn_model"]["score_bands"]["ge_0.8"] == 1


def test_the_summary_prints_numbers_only(tmp_path, capsys) -> None:
    path = tmp_path / "phone.jsonl"
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in LINES))
    main([str(path)])
    out = capsys.readouterr().out
    assert "비밀 글" not in out and "더 필요하신" not in out
    assert json.loads(out)["false_stops"] == 1


def test_it_reads_what_a_shadow_call_writes(tmp_path) -> None:
    # 그림자 측정 통화의 실제 기록 모양으로 돈다 (세션과 요약의 필드 이름이 맞는지)
    settings = SessionSettings(barge=BargeSettings(mode="shadow"))
    stt = FakeSttStream(replies=["메도빅 하나요", "네"], voiced_start=True)
    tts = FakeTts(pcm=bytes(4_000 * BYTES_PER_MS))

    async def call() -> Call:
        async with Call(tmp_path, stt=stt, tts=tts, settings=settings) as c:
            await c.start(voice_barge_in=True, mic={"echoCancellation": True})
            await c.say()
            await c.out.wait_for("bot.done", turn=1)
            await c.feed(tone(500) + silence(1_200))
            await asyncio.sleep(0.05)
            await c.played(1)
            await c.session.feed_event({"type": "call.end", "turn": 1})
            return c

    c = asyncio.run(call())
    s = summarize(json.loads(line) for line in
                  (c.log_dir / "phone-20261108.jsonl").read_text().splitlines())
    assert s["windows"] == {"blocked": 1, "open": 1}
    assert s["results"] == {"backchannel": 1}
    assert s["events"] == {"duck:sound:shadow": 1, "unduck:text:shadow": 1}
    assert s["calls"][0]["echo_cancellation"] is True
    assert s["echo_rms"]["open"]["windows"] == 1
