"""내리는 오디오 프레임 (spec WS 계약).

    u8 kind (1 = 오디오) | u8 flags (bit0 = 문장의 마지막 조각) | u16 seq | u32 turn
    | PCM16LE 16kHz mono

모두 리틀엔디언이다. `seq`는 턴 안의 문장 번호(0부터)이고 `bot.say`·`playback.*`의 `seq`와 같다.
한 문장은 약 200ms(`CHUNK_BYTES`)씩 여러 프레임으로 나뉘고, 마지막 프레임에만 bit0이 켜진다.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

KIND_AUDIO = 1
FLAG_LAST_CHUNK = 1
HEADER = struct.Struct("<BBHI")  # 8바이트

SAMPLE_RATE = 16_000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # PCM16 mono → 32바이트/ms
CHUNK_BYTES = 200 * BYTES_PER_MS  # 내리는 조각 하나 ≈ 200ms = 6,400바이트


@dataclass(frozen=True)
class AudioFrame:
    turn: int
    seq: int
    pcm: bytes
    last: bool = False  # 이 문장의 마지막 조각


def pack_audio_frame(frame: AudioFrame) -> bytes:
    flags = FLAG_LAST_CHUNK if frame.last else 0
    return HEADER.pack(KIND_AUDIO, flags, frame.seq, frame.turn) + frame.pcm


def unpack_audio_frame(raw: bytes) -> AudioFrame:
    if len(raw) < HEADER.size:
        raise ValueError(f"오디오 프레임이 너무 짧아요: {len(raw)}바이트")
    kind, flags, seq, turn = HEADER.unpack_from(raw)
    if kind != KIND_AUDIO:
        raise ValueError(f"모르는 프레임 kind: {kind}")
    last = bool(flags & FLAG_LAST_CHUNK)
    return AudioFrame(turn=turn, seq=seq, pcm=raw[HEADER.size :], last=last)


def chunk_pcm(pcm: bytes, size: int = CHUNK_BYTES) -> Iterator[tuple[bytes, bool]]:
    """PCM을 `size`바이트씩 자른다. (조각, 마지막인지). 빈 PCM도 빈 마지막 조각 하나를 낸다."""
    if not pcm:
        yield b"", True
        return
    for start in range(0, len(pcm), size):
        yield pcm[start : start + size], start + size >= len(pcm)
