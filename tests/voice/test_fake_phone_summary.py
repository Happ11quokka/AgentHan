"""측정 요약 (티켓 07): 통화 기록 → 녹음별 줄, 모드별 요약, 통화별 주문 결과."""

from __future__ import annotations

import pytest

from agenthan.voice.fake_phone.client import CallRun, ClipRun
from agenthan.voice.fake_phone.summary import call_outcome, clip_rows, match_script, overview


@pytest.mark.parametrize(
    ("heard", "script", "verdict"),
    [
        ("메도빅 2개랑 아이스 아메리카노 하나요", "메도빅 두 개랑 아이스 아메리카노 하나요", "yes"),
        ("6시 반쯤 갈게요", "여섯 시 반쯤 갈게요", "yes"),
        ("아니요, 그게 다예요.", "아니요 그게 다예요", "yes"),
        ("맥도비 두개랑 아이스 아메리카노 하나요", "메도빅 두 개랑 아이스 아메리카노 하나요",
         "partial"),
        ("그게 다예요", "아니요, 그게 다예요", "partial"),
        ("", "네", "no"),
        ("사장님", "아메리카노 따뜻하게 세 잔이요, 하나는 디카페인으로요", "no"),
    ],
)
def test_transcript_is_compared_with_the_script_ignoring_spacing_and_number_style(
    heard, script, verdict
) -> None:
    assert match_script(heard, script) == verdict


def _run() -> CallRun:
    """녹음 둘: A1은 조각 2개로 합쳐 무음 감지로 끝남, A2는 gap으로 끝남."""
    a1 = ClipRun("A1", sent_at_ms=1_000, done_at_ms=9_000, speech_end_ms=7_600, turns=[1],
                 texts=["메도빅 두개랑 아이스 아메리카노 하나요"])
    a2 = ClipRun("A2", sent_at_ms=15_000, done_at_ms=21_000, speech_end_ms=18_800, turns=[2],
                 texts=["아니요 그게 다예요"])
    return CallRun(
        clips=[a1, a2],
        events=[
            {"type": "stt.final", "turn": 1, "t": 8_500.0},
            {"type": "bot.say", "turn": 1, "seq": 0, "text": "메도빅 두 개, ...", "t": 9_200.0},
            {"type": "stt.final", "turn": 2, "t": 19_900.0},
            {"type": "order", "turn": 2, "t": 20_000.0, "total": 19_500,
             "order": {"status": "confirmed", "pickup_time": "2026-11-08T18:30:00+09:00",
                       "lines": [{"menu_id": "m01", "quantity": 2}]},
             "cart": [{"name": "메도빅", "qty": 2, "options": []}]},
            {"type": "bot.say", "turn": 2, "seq": 0, "text": "6시 반에 준비해 둘게요.",
             "t": 20_400.0},
        ],
        sent=[
            {"type": "playback.started", "turn": 1, "seq": 0, "t": 9_300.0},
            {"type": "playback.started", "turn": 1, "seq": 1, "t": 11_000.0},
            {"type": "playback.started", "turn": 2, "seq": 0, "t": 20_500.0},
        ],
        metrics=[
            {"turn": 1, "fragments": 2, "end_by": "vad", "text": "...",
             "marks": {"engine_start": 100, "engine_end": 900, "tts_start": 901,
                       "tts_first": 1_300}},
            {"turn": 2, "fragments": 1, "end_by": "gap",
             "marks": {"engine_start": 10, "engine_end": 30, "tts_start": 31, "tts_first": 431}},
        ],
        order=None,
        closed_by_server=True,
    )


SCRIPT = {"A1": "메도빅 두 개랑 아이스 아메리카노 하나요", "A2": "아니요, 그게 다예요"}


def test_clip_rows_give_transcript_match_fragments_and_latencies_per_recording() -> None:
    rows = clip_rows(_run(), SCRIPT)

    a1, a2 = rows
    assert a1 == {
        "clip": "A1", "text": "메도빅 두개랑 아이스 아메리카노 하나요", "match": "yes",
        "turns": 1, "fragments": 2, "end_by": "vad",
        "end_to_result_ms": 900, "end_to_first_sound_ms": 1_700,
        "engine_ms": 800, "tts_first_ms": 399, "muted_ms": 0,
    }
    assert (a2["end_by"], a2["end_to_result_ms"], a2["end_to_first_sound_ms"]) == ("gap", 1_100,
                                                                                    1_700)


def test_a_clip_that_made_no_turn_has_no_latencies() -> None:
    run = _run()
    run.clips.append(ClipRun("D3", sent_at_ms=30_000, done_at_ms=36_000, speech_end_ms=32_700))

    row = clip_rows(run, {**SCRIPT, "D3": "음… 그러니까… 저기요"})[-1]

    assert row == {"clip": "D3", "text": "", "match": "no", "turns": 0, "fragments": 0,
                   "end_by": "-", "end_to_result_ms": None, "end_to_first_sound_ms": None,
                   "engine_ms": None, "tts_first_ms": None, "muted_ms": 0}


def test_overview_counts_how_utterances_ended_and_takes_medians() -> None:
    rows = clip_rows(_run(), SCRIPT)
    rows.append({**rows[0], "clip": "B1", "end_by": "gap", "end_to_result_ms": 2_000,
                 "end_to_first_sound_ms": 3_000})

    summary = overview(rows)

    assert summary["end_by"] == {"gap": 2, "vad": 1}
    assert summary["median_end_to_result_ms"] == 1_100
    assert summary["median_end_to_first_sound_ms"] == 1_700
    assert summary["match"] == {"yes": 3}


def test_call_outcome_reports_the_final_order() -> None:
    outcome = call_outcome(_run())

    assert outcome == {"status": "confirmed", "total": 19_500,
                       "pickup_time": "2026-11-08T18:30:00+09:00", "items": "메도빅 2",
                       "last_bot": "6시 반에 준비해 둘게요."}


def test_call_outcome_without_order_says_where_it_stopped() -> None:
    run = _run()
    run.events = [e for e in run.events if e["type"] != "order"]
    run.events.append({"type": "error", "turn": 2, "code": "stt_disconnected", "t": 21_000.0})

    outcome = call_outcome(run)

    assert outcome["status"] == "error:stt_disconnected"
    assert outcome["last_bot"] == "6시 반에 준비해 둘게요."
    assert outcome["total"] is None
