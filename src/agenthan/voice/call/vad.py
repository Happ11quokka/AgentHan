"""무음 감지기: 올라온 PCM 프레임의 에너지(RMS)로 말 시작·끝을 잡는다 (spec 말 끝 판정).

말소리가 `start_ms` 이어지면 말 시작, 그 뒤 무음이 `end_silence_ms` 이어지면 말 끝이다.
말 끝 프레임에 세션이 STT `epFlag`를 붙인다. 시작값은 짐작이고 녹음으로 맞춘다(티켓 07).
"""

from __future__ import annotations

import array
import math
import sys
from dataclasses import dataclass, field

from agenthan.voice.call.frames import BYTES_PER_MS


@dataclass(frozen=True)
class VadSettings:
    threshold: float = 500.0  # PCM16 RMS. 이 값 이상이면 말소리 프레임
    start_ms: int = 200  # 말소리가 이만큼 이어져야 말 시작 (딸깍 소리 거르기)
    end_silence_ms: int = 500  # 말 시작 뒤 무음이 이만큼 이어지면 말 끝


def rms(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


@dataclass
class SilenceDetector:
    """프레임을 차례로 넣는다. 시각은 넣은 오디오 길이(ms)로 센다."""

    settings: VadSettings = field(default_factory=VadSettings)
    in_speech: bool = False
    offset_ms: int = 0  # 지금까지 넣은 오디오 길이
    segment_starts: list[int] = field(default_factory=list)  # 말 시작으로 본 구간의 시작 시각
    last_voiced: bool = False  # 마지막으로 넣은 프레임이 말소리였나
    _voiced_ms: int = 0
    _voiced_from: int = 0
    _silent_ms: int = 0

    def feed(self, pcm: bytes) -> bool:
        """프레임 하나. 이 프레임에서 말이 끝났으면 참."""
        ms = len(pcm) // BYTES_PER_MS
        start = self.offset_ms
        self.offset_ms += ms
        voiced = rms(pcm) >= self.settings.threshold
        self.last_voiced = voiced
        if voiced:
            if self._voiced_ms == 0:
                self._voiced_from = start
            self._voiced_ms += ms
            self._silent_ms = 0
            if not self.in_speech and self._voiced_ms >= self.settings.start_ms:
                self.in_speech = True
                self.segment_starts.append(self._voiced_from)
            return False
        self._voiced_ms = 0
        if not self.in_speech:
            return False
        self._silent_ms += ms
        if self._silent_ms >= self.settings.end_silence_ms:
            self.in_speech = False
            self._silent_ms = 0
            return True
        return False

    def end_speech(self) -> None:
        """밖에서 말 끝을 정했다("말 끝" 버튼). 같은 말에 `epFlag`를 또 붙이지 않게 한다."""
        self.in_speech = False
        self._voiced_ms = 0
        self._silent_ms = 0

    def speech_since(self, ms: int) -> bool:
        """`ms` 뒤에 말소리가 이어졌나: 지금 말하는 중이거나, `ms` 이후에 시작한 말 구간이 있다.

        말 구간으로 본 것만 센다. 그래야 붙잡은 턴이 그 구간의 말 끝(`epFlag`) 결과로 반드시 풀린다.
        """
        return self.in_speech or any(start >= ms for start in self.segment_starts)
