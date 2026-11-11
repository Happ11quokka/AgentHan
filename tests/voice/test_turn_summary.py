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


# ------------------------------------------------ 6단계 품질: 경로별 지연·구간·되묻기


def _marks(end: int, ep: int, stt: int, engine: int, tts: int, play: int) -> dict:
    """말 끝 0에서 시작해 구간 길이로 marks를 만든다 (판정 대기 10ms, 합성 → 송신 5ms)."""
    ep_sent = end + ep
    stt_final = ep_sent + stt
    engine_start = stt_final + 10
    engine_end = engine_start + engine
    tts_first = engine_end + tts
    audio_first = tts_first + 5
    return {"speech_end": end, "ep_sent": ep_sent, "stt_final": stt_final,
            "engine_start": engine_start, "engine_end": engine_end, "tts_start": engine_end,
            "tts_first": tts_first, "audio_first": audio_first, "play_start": audio_first + play}


ROUTED = [
    _turn("c1", 0),
    _turn("c1", 1, 1_800, end_by="vad", route="nlu", marks=_marks(0, 300, 400, 950, 40, 100)),
    _turn("c1", 2, 900, end_by="vad", route="rule", marks=_marks(0, 300, 380, 2, 100, 110)),
    _turn("c1", 3, 2_000, end_by="vad", route="nlu", marks=_marks(0, 320, 360, 1_100, 80, 120)),
    _turn("c1", 4, 1_000, end_by="vad", route="rule", marks=_marks(0, 300, 420, 4, 150, 110),
          reask={"slot": "confirm", "n": 1}),
    _turn("c2", 0),
    _turn("c2", 1, 2_600, end_by="vad", route="answer", answer={"kind": "answer"},
          marks=_marks(0, 310, 400, 1_700, 60, 115)),
    _turn("c2", 2, 2_400, end_by="vad", answer={"kind": "unsure"},  # 예전 기록: route 없음
          marks=_marks(0, 310, 400, 1_500, 60, 115)),
    _turn("c2", 3, 1_100, end_by="vad", marks=_marks(0, 300, 400, 3, 300, 90)),  # 예전 기록
    _turn("c2", 4, bot_turn="not_heard", reask={"slot": "not_heard", "n": 1}),
    _turn("c2", 5, 1_900, end_by="vad", route="nlu", marks=_marks(0, 300, 400, 1_000, 90, 100),
          reask={"slot": "order", "n": 2},
          handoff={"reason": "call_failures", "slot": "order", "reasks": 2}),
]


def test_voice_latency_is_split_by_engine_route() -> None:
    by_route = summarize(ROUTED)["by_route"]
    assert set(by_route) == {"rule", "nlu", "answer", "unknown"}
    assert by_route["rule"]["voice"] == {"n": 2, "p50_ms": 950, "p90_ms": 990}
    assert by_route["nlu"]["voice"] == {"n": 3, "p50_ms": 1_900, "p90_ms": 1_980}
    # route가 없는 예전 기록도 답변기 결과가 있으면 답변기 경로다
    assert by_route["answer"]["voice"] == {"n": 2, "p50_ms": 2_500, "p90_ms": 2_580}
    assert by_route["unknown"]["voice"]["n"] == 1


def test_each_stage_of_a_turn_gets_a_median() -> None:
    s = summarize(ROUTED)
    # 손님 턴 8개의 구간별 P50: 말 끝→epFlag, epFlag→STT 글, 엔진, TTS 첫 바이트, 보냄→재생 보고
    assert s["stages"] == {
        "end_to_ep": {"n": 8, "p50_ms": 300},
        "ep_to_stt": {"n": 8, "p50_ms": 400},
        "stt_to_engine": {"n": 8, "p50_ms": 10},
        "engine": {"n": 8, "p50_ms": 975},
        "tts_first": {"n": 8, "p50_ms": 85},
        "send_to_play": {"n": 8, "p50_ms": 110},
    }
    rule = s["by_route"]["rule"]["stages"]
    assert rule["engine"] == {"n": 2, "p50_ms": 3}
    assert rule["tts_first"] == {"n": 2, "p50_ms": 125}
    assert s["by_route"]["nlu"]["stages"]["engine"] == {"n": 3, "p50_ms": 1_000}
    assert s["by_route"]["answer"]["stages"]["engine"] == {"n": 2, "p50_ms": 1_600}


def test_reasks_and_handoff_reasons_are_counted() -> None:
    s = summarize(ROUTED)
    assert s["reask"] == {"turns": 3, "per_guest_turn": 0.38,
                          "by_slot": {"confirm": 1, "not_heard": 1, "order": 1}}
    assert s["handoff"] == {"call_failures": 1}


def test_old_logs_have_empty_route_and_stage_summaries() -> None:
    s = summarize([_turn("c1", 0), _turn("c1", 1, 1_000, end_by="vad")])
    assert s["by_route"] == {"unknown": {"voice": {"n": 1, "p50_ms": 1_000, "p90_ms": 1_000},
                                         "stages": {}}}
    assert s["stages"] == {}
    assert s["reask"] == {"turns": 0, "per_guest_turn": 0.0, "by_slot": {}}
    assert s["handoff"] == {}


def test_smoke_calls_are_left_out_unless_asked_for() -> None:
    lines = [
        _turn("c1", 0),
        _turn("c1", 1, 1_000, end_by="vad"),
        _turn("smoke1", 0, smoke=True),
        _turn("smoke1", 1, 9_000, end_by="vad"),
    ]
    s = summarize(lines)
    assert (s["calls"], s["guest_turns"], s["smoke_calls"]) == (1, 1, 1)
    assert s["voice"]["p50_ms"] == 1_000
    with_smoke = summarize(lines, include_smoke=True)
    assert (with_smoke["calls"], with_smoke["guest_turns"]) == (2, 2)
