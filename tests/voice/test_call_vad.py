"""무음 감지 기본값 (티켓 07 녹음 측정으로 맞춤).

동현 녹음 17개의 잡음 바닥·말소리 크기를 흉내 낸다.
"""

from __future__ import annotations

import math
import struct

from agenthan.voice.call.vad import SilenceDetector

SAMPLES = 1_600  # 100ms


def _frames(ms: int, rms_level: float) -> list[bytes]:
    amplitude = rms_level * math.sqrt(2)
    one = struct.pack(f"<{SAMPLES}h", *(int(amplitude * math.sin(2 * math.pi * 300 * i / 16_000))
                                        for i in range(SAMPLES)))
    return [one] * (ms // 100)


def _end_at_ms(frames: list[bytes]) -> int | None:
    vad = SilenceDetector()
    for i, frame in enumerate(frames):
        if vad.feed(frame):
            return (i + 1) * 100
    return None


def test_speech_ends_inside_room_noise_of_about_rms_1000() -> None:
    """A1·A2처럼 말 사이·뒤 배경 소리가 RMS 400~1,400이어도 녹음 안에서 말 끝을 잡는다."""
    frames = _frames(300, 900) + _frames(1_500, 4_000) + _frames(1_500, 1_100)

    assert _end_at_ms(frames) == 1_800 + 400  # 말 끝(1.8초) 뒤 무음 400ms


def test_quiet_speech_around_rms_2000_still_counts_as_speech() -> None:
    """A4·D2처럼 작게 말해도(RMS 1,600~2,700) 말로 본다."""
    frames = _frames(300, 300) + _frames(1_000, 2_000) + _frames(1_000, 300)

    assert _end_at_ms(frames) == 1_300 + 400


def test_a_short_dip_between_words_does_not_end_speech() -> None:
    """낱말 사이 300ms 쉼(B1 "하나요, 사워크림은")은 말 끝이 아니다."""
    frames = (_frames(800, 4_000) + _frames(300, 600) + _frames(800, 4_000)
              + _frames(1_000, 300))

    assert _end_at_ms(frames) == 1_900 + 400
