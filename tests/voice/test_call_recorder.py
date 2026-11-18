"""통화 녹음기 (5단계 티켓 01): 손님·봇 소리를 같은 시간축의 스테레오 WAV 하나로."""

from __future__ import annotations

import array
import wave

from agenthan.voice.call.recorder import CallRecorder

MS = 32  # 16kHz PCM16 mono 1ms = 32바이트


def pcm(ms: int, value: int) -> bytes:
    return array.array("h", [value] * (ms * 16)).tobytes()


def channels(path) -> tuple[list[int], list[int]]:
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 16_000)
        data = array.array("h", w.readframes(w.getnframes()))
    return list(data[0::2]), list(data[1::2])


def at_ms(samples: list[int], ms: int) -> int:
    return samples[ms * 16]


def test_customer_frames_follow_each_other_even_if_they_arrive_in_a_burst(tmp_path) -> None:
    rec = CallRecorder(tmp_path / "c.wav")
    rec.customer(pcm(100, 1), at_ms=1_000)
    rec.customer(pcm(100, 2), at_ms=1_000)  # 망 때문에 한꺼번에 도착
    rec.customer(pcm(100, 3), at_ms=1_250)
    rec.write()
    left, right = channels(tmp_path / "c.wav")
    assert at_ms(left, 999) == 0
    assert (at_ms(left, 1_050), at_ms(left, 1_150), at_ms(left, 1_250)) == (1, 2, 3)
    assert len(left) == 1_300 * 16
    assert set(right) == {0}


def test_customer_gap_longer_than_the_allowance_is_kept_as_silence(tmp_path) -> None:
    rec = CallRecorder(tmp_path / "c.wav", jitter_ms=500)
    rec.customer(pcm(100, 1), at_ms=0)
    rec.customer(pcm(100, 2), at_ms=3_000)  # 브라우저가 3초 동안 안 보냄(탭 전환 등)
    rec.write()
    left, _ = channels(tmp_path / "c.wav")
    assert at_ms(left, 50) == 1
    assert at_ms(left, 1_500) == 0
    assert at_ms(left, 3_050) == 2


def test_bot_sentences_play_one_after_another_from_when_they_were_sent(tmp_path) -> None:
    rec = CallRecorder(tmp_path / "c.wav")
    rec.bot(pcm(300, 5), at_ms=500)
    rec.bot(pcm(300, 6), at_ms=520)  # 첫 문장 재생 중에 보냄 → 그 뒤에 이어 재생
    rec.bot(pcm(100, 7), at_ms=2_000)
    rec.write()
    left, right = channels(tmp_path / "c.wav")
    assert (at_ms(right, 499), at_ms(right, 600), at_ms(right, 900), at_ms(right, 1_200)) == (
        0, 5, 6, 0
    )
    assert at_ms(right, 2_050) == 7
    assert set(left) == {0}  # 짧은 쪽은 무음으로 채운다


def test_bot_stop_erases_audio_the_browser_never_played(tmp_path) -> None:
    rec = CallRecorder(tmp_path / "c.wav")
    rec.bot(pcm(1_000, 5), at_ms=0)
    rec.bot_stop(at_ms=400)  # 말 끊기
    rec.bot(pcm(100, 6), at_ms=600)
    rec.write()
    _, right = channels(tmp_path / "c.wav")
    assert at_ms(right, 300) == 5
    assert at_ms(right, 500) == 0
    assert at_ms(right, 650) == 6
    assert len(right) == 700 * 16


def test_write_makes_the_folder_and_an_empty_call_still_gives_a_file(tmp_path) -> None:
    path = tmp_path / "20261111" / "c.wav"
    CallRecorder(path).write()
    assert channels(path) == ([], [])
