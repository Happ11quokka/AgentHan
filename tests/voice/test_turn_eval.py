"""오프라인 말 끝 평가 도구 (3단계 티켓 03). 데이터 없이 합성 멈춤으로 시간 모델만 본다.

시간 모델(연구 노트 `s3-smart-turn.md` 9-2와 같음, 초는 멈춤 시작 = 마지막 말 끝 기준):
무음 감지 말 끝 0.4초, STT 확정 글 1.0초, 다시 말하면 0.2초 뒤 말 시작으로 본다.
끝남이면 1.0초에 넘기고, 이어짐이면 max(1.0, 상한)에 넘긴다. 쉼(hold) 뒤 말이 넘긴 뒤에야
다시 시작되면(쉼 + 0.2 > 넘긴 시각) 오끊김이다. 대기는 진짜 끝(eot)을 넘긴 시각의 평균이다.
기대값은 이 정의로 손으로 계산한 값이다.
"""

from __future__ import annotations

import pytest

from agenthan.voice.turn_eval import POLICIES, Pause, Policy, simulate, summarize


def pause(label: str, dur: float, text: str = "메도빅 하나", score: float | None = None,
          kind: str = "open", menu_names: tuple[str, ...] = ()) -> Pause:
    return Pause(source="t", item="x", index=0, label=label, dur=dur, text=text,
                 question_kind=kind, score=score, menu_names=menu_names)


NOW = Policy("now", rules=False, model=False)
RULES = Policy("rules", rules=True, model=False)
MODEL = Policy("model", rules=False, model=True)
BOTH = Policy("both", rules=True, model=True)


def test_without_judging_a_long_hold_is_cut_and_the_end_is_committed_with_the_text() -> None:
    result = simulate([pause("hold", 1.5), pause("eot", 3.0)], NOW)
    assert (result.holds, result.cut) == (1, 1)  # 1.5 + 0.2 > 1.0
    assert result.waits == [1.0]


def test_a_short_hold_is_merged_by_the_fragment_hold_whatever_the_verdict() -> None:
    result = simulate([pause("hold", 0.6)], NOW)  # 0.6 + 0.2 < 1.0
    assert result.cut == 0


def test_a_hold_shorter_than_the_vad_end_never_triggers_a_decision() -> None:
    result = simulate([pause("hold", 0.3, score=0.99)], MODEL)
    assert (result.holds, result.cut) == (1, 0)


def test_holds_outside_the_harness_range_are_not_counted() -> None:
    result = simulate([pause("hold", 0.15), pause("hold", 6.0)], NOW)
    assert result.holds == 0


def test_a_rule_continuation_waits_up_to_the_cap() -> None:
    held = simulate([pause("hold", 1.5, text="메도빅 두 개랑")], RULES)
    assert held.cut == 0  # 넘김 2.0초, 다시 말함 1.7초
    late = simulate([pause("eot", 3.0, text="메도빅 두 개랑")], RULES)
    assert late.waits == [2.0]


def test_the_cap_comes_from_the_policy() -> None:
    result = simulate([pause("eot", 3.0, text="메도빅 두 개랑")], Policy("c", True, False, cap=1.6))
    assert result.waits == [1.6]


def test_model_only_continues_below_one_half() -> None:
    result = simulate([pause("eot", 3.0, score=0.4), pause("eot", 3.0, score=0.6)], MODEL)
    assert result.waits == [2.0, 1.0]


def test_combined_uses_the_bands_and_lets_firm_rules_win() -> None:
    pauses = [
        pause("eot", 3.0, text="메도빅 두 개랑", score=0.9),  # 모델 끝남 → 1.0
        pause("eot", 3.0, text="메도빅 하나", score=0.1),  # 모델 이어짐 → 2.0
        pause("eot", 3.0, text="메도빅 두 개랑", score=0.5),  # 가운데 → 규칙 이어짐 → 2.0
        # 확실한 끝(종결어미 + 메뉴가 든 주문) → 1.0
        pause("eot", 3.0, text="메도빅 하나요", score=0.1, menu_names=("메도빅",)),
        pause("eot", 3.0, text="네", score=0.1, kind="confirm"),  # 확인 뒤 예 → 1.0
        pause("eot", 3.0, text="그게 다예요", score=0.1),  # 메뉴 없는 종결어미 → 모델 이어짐 2.0
    ]
    assert simulate(pauses, BOTH).waits == [1.0, 2.0, 2.0, 1.0, 1.0, 2.0]


def test_without_a_score_the_combined_policy_is_the_rules() -> None:
    pauses = [pause("eot", 3.0, text="메도빅 두 개랑"), pause("eot", 3.0, text="메도빅 하나")]
    assert simulate(pauses, BOTH).waits == simulate(pauses, RULES).waits == [2.0, 1.0]


def test_summary_rows_break_down_by_question_kind() -> None:
    pauses = [pause("hold", 1.5, kind="open"), pause("eot", 3.0, kind="open"),
              pause("eot", 3.0, text="메도빅 두 개랑", kind="confirm")]
    rows = summarize(pauses, [RULES])
    by_group = {(r["group"], r["policy"]): r for r in rows}
    assert by_group[("all", "rules")]["cut_rate"] == pytest.approx(1.0)
    assert by_group[("all", "rules")]["mean_wait_s"] == pytest.approx(1.5)
    assert by_group[("confirm", "rules")]["holds"] == 0
    assert by_group[("confirm", "rules")]["mean_wait_s"] == pytest.approx(2.0)


def test_the_shipped_policies_compare_now_rules_model_and_combined() -> None:
    names = [p.name for p in POLICIES]
    assert names[:4] == ["now", "rules", "smart_turn", "combined"]
    combined = POLICIES[3]
    assert (combined.low, combined.high, combined.cap) == (0.3, 0.7, 2.0)


def _wav(path, frames: list[bytes]) -> None:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"".join(frames))


def test_recordings_are_split_at_our_vad_ends_with_the_manifest_text(tmp_path) -> None:
    from call_harness import silence, tone

    from agenthan.voice.call.fakes import FakeTurnModel
    from agenthan.voice.turn_eval import recording_pauses

    # 말 0.8초 · 쉼 1.5초 · 말 0.6초 · 끝 무음 1.2초 (프레임 100ms)
    _wav(tmp_path / "R1.wav", tone(800) + silence(1_500) + tone(600) + silence(1_200))
    manifest = {"R1": {"question": "더 필요하신 거 있으세요?", "state": "ordering",
                       "texts": ["메도빅 두 개랑", "메도빅 두 개랑 블린 하나요"]}}
    model = FakeTurnModel([0.2, 0.9])
    pauses, mismatched = recording_pauses(tmp_path, manifest, model)
    assert mismatched == []
    hold, end = pauses
    assert (hold.label, hold.dur, hold.score) == ("hold", 1.5, 0.2)  # 0.8초부터 2.3초까지
    assert (end.label, end.dur, end.score) == ("eot", 1.2, 0.9)  # 2.9초부터 녹음 끝 4.1초
    assert (hold.text, end.text) == tuple(manifest["R1"]["texts"])
    assert model.heard_ms == [1_200, 3_300]  # 말 끝(무음 400ms)까지의 오디오


def test_results_must_stay_under_data_or_private(tmp_path) -> None:
    from agenthan.voice.turn_eval import main

    with pytest.raises(SystemExit, match="private"):
        main(["--out", str(tmp_path / "out")])
    (tmp_path / "pyproject.toml").write_text("")
    main(["--out", str(tmp_path / "private" / "turn-eval")])  # 저장소의 private/ 아래는 된다
    assert (tmp_path / "private" / "turn-eval" / "summary.md").is_file()


def test_pause_lengths_from_float_timestamps_are_read_to_the_millisecond() -> None:
    # EoT-Bench 멈춤은 0.1초 단위다. 8.7 - 8.5 = 0.1999…는 0.2초 쉼, 9.0 - 8.2 = 0.8000…01은
    # 0.8초 쉼(다시 말함 1.0초 = 확정 글과 같은 때, 오끊김 아님)
    result = simulate([pause("hold", 8.7 - 8.5), pause("hold", 9.0 - 8.2)], NOW)
    assert (result.holds, result.cut) == (2, 0)


def test_speech_that_restarts_right_after_the_vad_end_makes_that_end_a_hold(tmp_path) -> None:
    from call_harness import silence, tone

    from agenthan.voice.turn_eval import recording_pauses

    # 무음 400ms에 말 끝, 바로 다음 프레임부터 다시 말함 (B1 모양)
    _wav(tmp_path / "R2.wav", tone(800) + silence(400) + tone(600) + silence(800))
    manifest = {"R2": {"question": "", "state": "", "texts": ["블린 하나요", "블린 하나요 주세요"]}}
    hold, end = recording_pauses(tmp_path, manifest, None)[0]
    assert (hold.label, hold.dur) == ("hold", 0.4)
    assert end.label == "eot"
