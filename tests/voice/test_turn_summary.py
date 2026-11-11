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


def test_the_prefetch_hit_rate_and_discarded_calls_are_summarized() -> None:
    # 리뷰 P5: 미리 해석 적중률과 버린 호출 수 (spec 지표 확장)
    lines = [
        _turn("c1", 1, end_by="vad", nlu_prefetch={"prefetched": 1, "used": True,
                                                   "discarded": 0, "miss": None, "wait_ms": 0}),
        _turn("c1", 2, end_by="vad", nlu_prefetch={"prefetched": 2, "used": True,
                                                   "discarded": 1, "miss": None, "wait_ms": 40}),
        _turn("c1", 3, end_by="vad", nlu_prefetch={"prefetched": 1, "used": False, "discarded": 1,
                                                   "miss": "text", "wait_ms": None}),
        _turn("c1", 4, end_by="vad"),  # 미리 해석 없음
    ]
    s = summarize(lines)
    assert s["prefetch"] == {"turns": 3, "used": 2, "hit_rate": 0.67, "calls": 4, "discarded": 2,
                             "miss": {"text": 1}, "wait": {"n": 2, "p50_ms": 20, "p90_ms": 36}}
    assert summarize(LINES)["prefetch"]["turns"] == 0


def test_rewinds_gates_and_the_later_mic_close_are_summarized() -> None:
    # 6단계 티켓 02: 되감은 턴 수, 문지기가 첫 소리를 붙잡은 시간, 헛 문지기 수, 마이크를 더 연 시간
    lines = [
        _turn("c1", 1, 1_000, end_by="vad", mic_open_extra_ms=120),
        _turn("c1", 2, end_by="vad", superseded={"by_turn": 3, "gate_ms": 400, "text": "비밀 글"}),
        _turn("c1", 3, 1_300, end_by="vad", supersedes=2, mic_open_extra_ms=200),
        _turn("c1", 4, 1_500, end_by="vad", gate={"result": "empty", "wait_ms": 700}),
        _turn("c1", 5, 1_100, end_by="vad", gate={"result": "backchannel", "wait_ms": 0}),
        _turn("c1", 6, end_by="vad", superseded={"by_turn": 7, "gate_ms": 0, "text": "비밀 글"}),
        _turn("c1", 7, 1_200, end_by="vad", supersedes=6, queued=True),
        _turn("c1", 8, 900, end_by="vad",
              turn_end={"label": "end", "path": "cap", "reason": "cap:no_after_more"}),
        _turn("c1", 9, 950, end_by="vad", turn_end={"label": "end", "path": "rule"},
              turn_end_steps=[{"label": "continue", "reason": "no_after_more"}]),
    ]
    s = summarize(lines)
    assert s["rewind"] == {
        "superseded_turns": 2,
        # 되감은 턴은 손님 말 하나를 두 번 센 것이다. 뺀 값이 손님이 실제로 한 말 수다
        "guest_turns_net": 7,
        "gate_wait": {"n": 2, "p50_ms": 200, "p90_ms": 360},
    }
    assert s["gate"] == {
        "false_gates": 2,
        "by_result": {"backchannel": 1, "empty": 1},
        "wait": {"n": 2, "p50_ms": 350, "p90_ms": 630},
    }
    assert s["mic_open_extra"] == {"n": 2, "p50_ms": 160, "p90_ms": 192}
    # "더 필요하신 거?" 뒤 맨 "아니요"를 짧게 기다린 턴: 혼자 넘어감 · 뒷말과 합쳐짐
    assert s["more_no"] == {"alone": 1, "merged": 1}
    assert s["voice"]["n"] == 7  # 되감은 턴은 목소리가 없어서 말 끝 → 첫 목소리에 안 들어간다


def test_old_logs_without_the_new_fields_summarize_to_zero() -> None:
    s = summarize(LINES)
    assert s["rewind"] == {"superseded_turns": 0, "guest_turns_net": 6,
                           "gate_wait": {"n": 0, "p50_ms": None, "p90_ms": None}}
    assert s["gate"]["false_gates"] == 0
    assert s["mic_open_extra"]["n"] == 0
    assert s["more_no"] == {"alone": 0, "merged": 0}


def test_the_command_prints_numbers_without_any_transcript(tmp_path, capsys) -> None:
    path = tmp_path / "phone-20261108.jsonl"
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in LINES),
                    encoding="utf-8")
    main([str(path)])
    out = capsys.readouterr().out
    assert "비밀 글" not in out
    assert json.loads(out)["voice"]["p50_ms"] == 1_400
