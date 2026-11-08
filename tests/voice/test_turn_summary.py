"""턴 기록 요약 (3단계 티켓 01): 통화 JSONL → 말 끝 → 첫 목소리 P50·P90, 통화당 느린 턴 수 등."""

from __future__ import annotations

import json

from agenthan.voice.call.turn_summary import main, summarize


def _turn(call: str, turn: int, voice: int | None = None, **extra) -> dict:
    line = {"call": call, "turn": turn, "text": "비밀 글", "marks": extra.pop("marks", {}),
            **extra}
    if voice is not None:
        line["end_to_voice_ms"] = voice
    return line


LINES = [
    _turn("c1", 0),  # 인사
    _turn("c1", 1, 1_000, end_by="vad", marks={"ep_sent": 100, "stt_final": 600}),
    _turn("c1", 2, 3_000, end_by="vad", end_to_thinking_ms=1_500,
          marks={"ep_sent": 100, "stt_final": 900, "thinking": 1_600}),
    _turn("c1", 3, 2_600, end_by="gap", empty=[{"start_ms": 0, "end_ms": 10}]),
    _turn("c2", 0),
    _turn("c2", 1, 1_200, end_by="vad", marks={"ep_sent": 100, "stt_final": 1_900}),
    _turn("c2", 2, 1_400, end_by="button", turn_end={"label": "end", "path": "rule"}),
    _turn("c2", 3, end_by="vad", queued=True),
    _turn("c2", 4, bot_turn="no_input"),
]


def test_voice_latency_percentiles_and_slow_turns_per_call() -> None:
    s = summarize(LINES)
    # 말 끝 → 첫 목소리 1,000 · 1,200 · 1,400 · 2,600 · 3,000 (선형 보간)
    assert s["voice"] == {"n": 5, "p50_ms": 1_400, "p90_ms": 2_840}
    assert s["slow_over_ms"] == 2_500
    assert s["slow_turns_per_call"] == {"c1": 2, "c2": 0}
    assert s["calls_with_slow_turn"] == 1
    assert (s["calls"], s["guest_turns"]) == (2, 6)


def test_paths_thinking_empty_and_bot_turns_are_counted() -> None:
    s = summarize(LINES)
    assert s["end_by"] == {"button": 1, "gap": 1, "vad": 4}
    assert s["turn_end_path"] == {"rule": 1}  # 말 끝 판정 경로(규칙·모델·상한·버튼, 티켓 02)
    assert s["thinking_turns"] == 1
    assert s["thinking"] == {"n": 1, "p50_ms": 1_500, "p90_ms": 1_500}
    assert s["empty_turns"] == 1
    assert (s["queued_turns"], s["bot_turns"]) == (1, 1)


def test_stt_wait_from_ep_flag_sent_to_text_is_summarized_for_hold_max() -> None:
    # `hold_max_s`를 정하는 값: 무음 감지로 끝난 턴의 `ep_sent` → `stt_final`
    s = summarize(LINES)
    # 500 · 800 · 1,800
    assert s["stt_wait"] == {"n": 3, "p50_ms": 800, "p90_ms": 1_600, "p99_ms": 1_780}


def test_the_command_prints_numbers_without_any_transcript(tmp_path, capsys) -> None:
    path = tmp_path / "phone-20261108.jsonl"
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in LINES),
                    encoding="utf-8")
    main([str(path)])
    out = capsys.readouterr().out
    assert "비밀 글" not in out
    assert json.loads(out)["voice"]["p50_ms"] == 1_400
