"""무음 감지 오프라인 점검 (티켓 07).

녹음 하나를 세션과 같은 무음 감지기에 넣어 말 끝 판정을 본다.
"""

from __future__ import annotations

import math
import struct

from agenthan.voice.call.vad import VadSettings
from agenthan.voice.fake_phone.vad_check import check_clip


def _level(ms: int, rms_level: float) -> bytes:
    amplitude = rms_level * math.sqrt(2)
    n = ms * 16
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 300 * i / 16_000))
                                   for i in range(n)))


NOISY = _level(300, 1_000) + _level(1_000, 4_000) + _level(1_000, 1_000)  # 말 끝 1.3초


def test_ep_inside_the_recording_when_noise_is_under_the_threshold() -> None:
    result = check_clip(NOISY, VadSettings(threshold=1_500, start_ms=200, end_silence_ms=400))

    # 조기 epFlag(무음 250ms)는 1,600ms 프레임 끝, 말 끝(턴 판정, 400ms)은 1,700ms (3단계 티켓 01)
    assert result == {"speech_end_ms": 1_300, "ep_ms": [1_600], "end_ms": [1_700],
                      "inside": True, "end_to_ep_ms": 300}


def test_noise_over_the_threshold_only_ends_on_the_zero_frames_after_the_recording() -> None:
    result = check_clip(NOISY, VadSettings(threshold=500, start_ms=200, end_silence_ms=500))

    assert result["inside"] is False
    assert result["end_ms"] == [2_300 + 500]  # 녹음 끝(2.3초) 뒤 0 프레임 500ms
    assert result["ep_ms"] == [2_300 + 300]  # 조기 epFlag는 0 프레임 250ms를 넘기는 프레임
    assert result["end_to_ep_ms"] == 1_300


def test_a_candidate_can_name_the_early_ep_flag_silence() -> None:
    from agenthan.voice.fake_phone.vad_check import parse_candidate

    assert parse_candidate("1500/200/400") == VadSettings(1_500, 200, 400)
    assert parse_candidate("1500/200/400/300") == VadSettings(1_500, 200, 400, ep_silence_ms=300)
