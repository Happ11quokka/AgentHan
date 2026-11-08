"""TTS 첫 문장 앞 무음 자르기 (`call/trim.py`, 3단계 티켓 01 · 리뷰 C9)."""

from __future__ import annotations

import math
import struct
import time

import pytest

from agenthan.voice.call.trim import TrimSettings, trim_leading_silence

RATE = 16  # 샘플/ms


def pcm(*parts: tuple[int, int]) -> bytes:
    """(길이 ms, 진폭) 구간들. 진폭 0이면 무음, 아니면 440Hz 사인."""
    out: list[int] = []
    for ms, amp in parts:
        out += [int(amp * math.sin(2 * math.pi * 440 * (len(out) + i) / 16_000))
                for i in range(ms * RATE)]
    return struct.pack(f"<{len(out)}h", *out)


@pytest.mark.parametrize(("parts", "cut"), [
    (((300, 0), (500, 8_000)), 280),  # 첫 소리 20ms 앞에서 자른다
    (((10, 0), (500, 8_000)), 0),  # 남길 20ms보다 짧은 무음
    (((500, 8_000),), 0),
    (((1_200, 0), (300, 8_000)), 0),  # 1초 안에 소리가 없으면 자르지 않는다
    (((300, 0), (500, 200)), 0),  # 문턱(풀스케일 1%) 아래의 작은 소리
    (((200, 0), (5, 8_000), (300, 0)), 180),
])
def test_leading_silence_is_cut_twenty_ms_before_the_first_sound(parts, cut) -> None:
    audio = pcm(*parts)
    trimmed, cut_ms = trim_leading_silence(audio, TrimSettings())
    assert cut_ms == cut
    assert trimmed == audio[cut * RATE * 2:]


def test_disabled_trim_keeps_the_audio() -> None:
    audio = pcm((300, 0), (300, 8_000))
    assert trim_leading_silence(audio, TrimSettings(enabled=False)) == (audio, 0)


def test_trimming_a_sentence_is_fast_enough_for_the_event_loop() -> None:
    # 리뷰 C9: 이벤트 루프에서 돈다. 1초 무음 + 소리(최악: 1초 넘게 훑는다)를 짧게 끝낸다
    audio = pcm((1_000, 0), (2_000, 8_000))
    start = time.perf_counter()
    for _ in range(10):
        trim_leading_silence(audio, TrimSettings())
    assert (time.perf_counter() - start) / 10 < 0.003
