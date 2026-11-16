"""녹음 변환 (티켓 07): m4a 등 → 16kHz mono PCM16 WAV (맥 `afconvert`), WAV → PCM 바이트."""

from __future__ import annotations

import math
import shutil
import struct
import wave
from pathlib import Path

import pytest

from agenthan.voice.fake_phone.audio import convert_all, read_pcm, speech_end_s

needs_afconvert = pytest.mark.skipif(shutil.which("afconvert") is None,
                                     reason="afconvert(macOS)가 필요해요")


def _write_wav(path: Path, *, rate: int, channels: int, seconds: float) -> None:
    frames = int(rate * seconds)
    samples = []
    for i in range(frames):
        value = int(8_000 * math.sin(2 * math.pi * 440 * i / rate))
        samples += [value] * channels
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


@needs_afconvert
def test_convert_all_writes_16k_mono_wavs_named_after_each_recording(tmp_path) -> None:
    src = tmp_path / "rec"
    src.mkdir()
    _write_wav(src / "A1.wav", rate=44_100, channels=2, seconds=1.0)
    _write_wav(src / "B2.wav", rate=48_000, channels=1, seconds=0.5)
    (src / "notes.txt").write_text("녹음 아님")

    out = convert_all(src, src / "wav", pattern="*.wav")

    assert [p.name for p in out] == ["A1.wav", "B2.wav"]
    assert all(p.parent == src / "wav" for p in out)
    assert len(read_pcm(out[0])) == pytest.approx(32_000, abs=640)  # 1초 = 32,000바이트
    assert len(read_pcm(out[1])) == pytest.approx(16_000, abs=640)


def test_read_pcm_refuses_audio_that_is_not_16k_mono_pcm16(tmp_path) -> None:
    path = tmp_path / "x.wav"
    _write_wav(path, rate=44_100, channels=2, seconds=0.1)

    with pytest.raises(ValueError, match="16kHz"):
        read_pcm(path)


def test_read_pcm_returns_the_raw_samples(tmp_path) -> None:
    path = tmp_path / "ok.wav"
    _write_wav(path, rate=16_000, channels=1, seconds=0.25)

    pcm = read_pcm(path)

    assert len(pcm) == 8_000
    assert struct.unpack_from("<h", pcm, 2)[0] == int(8_000 * math.sin(2 * math.pi * 440 / 16_000))


def _tone(ms: int, amplitude: int) -> bytes:
    n = ms * 16
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 300 * i / 16_000))
                                   for i in range(n)))


def test_speech_end_is_the_end_of_the_last_loud_stretch_ignoring_single_frame_blips() -> None:
    pcm = (_tone(300, 300) + _tone(600, 5_000) + _tone(300, 600)  # 잡음 · 말 · 잡음
           + _tone(100, 5_000) + _tone(500, 600))  # 딸깍(100ms 하나) · 잡음

    assert speech_end_s(pcm) == pytest.approx(0.9)


def test_speech_end_of_noise_only_audio_is_none() -> None:
    assert speech_end_s(_tone(1_000, 600)) is None
