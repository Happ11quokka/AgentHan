"""내리는 오디오 프레임 (spec WS 계약).

8바이트 헤더(kind·flags·seq·turn, 리틀엔디언) + PCM16LE 16kHz.
"""

import pytest

from agenthan.voice.call.frames import (
    FLAG_LAST_CHUNK,
    KIND_AUDIO,
    AudioFrame,
    pack_audio_frame,
    unpack_audio_frame,
)


def test_header_layout_is_little_endian_kind_flags_seq_turn() -> None:
    raw = pack_audio_frame(AudioFrame(turn=0x01020304, seq=0x0A0B, pcm=b"\x01\x02", last=True))
    assert raw == bytes([1, 1, 0x0B, 0x0A, 0x04, 0x03, 0x02, 0x01, 1, 2])
    assert KIND_AUDIO == 1 and FLAG_LAST_CHUNK == 1


def test_round_trip() -> None:
    frame = AudioFrame(turn=7, seq=2, pcm=bytes(range(10)), last=False)
    assert unpack_audio_frame(pack_audio_frame(frame)) == frame


def test_unpack_rejects_short_or_unknown_kind() -> None:
    with pytest.raises(ValueError):
        unpack_audio_frame(b"\x01\x00\x00")
    with pytest.raises(ValueError):
        unpack_audio_frame(bytes([9, 0, 0, 0, 0, 0, 0, 0]))
