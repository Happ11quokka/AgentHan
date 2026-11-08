"""무음 감지기: 올라온 PCM 프레임의 에너지(RMS)로 말 시작·끝을 잡는다 (spec 말 끝 판정).

말소리가 `start_ms` 이어지면 말 시작, 그 뒤 무음이 `end_silence_ms` 이어지면 말 끝이다.
타이머는 둘이다(3단계 티켓 01, Alexa 투기적 끝점·Pipecat 두 타이머).
- 무음이 `ep_silence_ms`(250ms)에 닿는 프레임에 세션이 STT `epFlag`를 붙인다(`ep_due`).
  STT 글을 그만큼 일찍 받는다. 쉼마다 한 번이다
- 무음이 `end_silence_ms`(400ms)에 닿으면 말 끝이다(`feed`가 참). 턴 판정은 여기서 한다.
  250~400ms 쉼 뒤 말이 다시 시작되면 같은 말 구간이 이어진다
시작값은 짐작이고 녹음으로 맞춘다(티켓 07).
"""

from __future__ import annotations

import array
import math
import sys
from dataclasses import dataclass, field

from agenthan.voice.call.frames import BYTES_PER_MS


@dataclass(frozen=True)
class VadSettings:
    """기본값은 티켓 07에서 동현 녹음 17개(맥 녹음, 잡음 제거 없음)로 맞췄다. 시작값은 500/200/500.

    - 임계값 500 → 1,500: 말 사이·뒤 배경 소리가 RMS 400~1,900(A1·A2·C2·D3)이라 500으로는
      17개 중 12개가 녹음 안에서 말 끝을 못 잡았다(0 프레임이 와야 끝남, 실제 폰은 0이 안 옴).
      말소리는 작게 말해도 RMS 1,600~2,700(A4·D2)이라 1,500이면 둘 다 맞는다.
      1,200은 앞 잡음을 말로 봐서 헛 epFlag가 5개였고 D1 말 끝을 녹음 안에서 못 잡았다.
      1,800은 B1 끝말("빼 주세요")을 놓쳤다
    - 말 끝 무음 500 → 400ms: 1,500에서 500ms는 C3 뒤 잡음 한 번(RMS 1,800)에 0.6초 늦어졌다.
      400ms는 17개 모두 잡고 말 끝 → `epFlag`가 0.1초 빠르다. 낱말 사이 쉼(B1 0.3초)은 넘지 않는다.
      D5의 1.4초 쉼은 어느 값으로도 두 말로 나뉜다(합치기는 세션 조각 붙잡기 몫)
    - 말 시작 200ms 그대로: 300ms로 올려도 줄어드는 헛말 시작이 없었다(B5 앞 잡음은 둘 다 잡음)
    - 실기기(브라우저 잡음 제거·자동 음량)에서는 값이 달라질 수 있다. 티켓 08에서
      `PHONE_VAD_THRESHOLD`·`PHONE_VAD_END_SILENCE_MS`로 바꿔 확인한다
    """

    threshold: float = 1_500.0  # PCM16 RMS. 이 값 이상이면 말소리 프레임
    start_ms: int = 200  # 말소리가 이만큼 이어져야 말 시작 (딸깍 소리 거르기)
    end_silence_ms: int = 400  # 말 시작 뒤 무음이 이만큼 이어지면 말 끝 (턴 판정)
    # 무음이 이만큼이면 epFlag를 먼저 보낸다 (3단계 티켓 01). `end_silence_ms`보다 크면 말 끝에.
    # 250ms: 티켓 07 녹음에서 낱말 사이 쉼(B1 0.3초)도 넘을 수 있어 조각이 는다. 조각은 세션이
    # 무음 400ms 전까지 붙잡아 합친다
    ep_silence_ms: int = 250


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
    ep_due: bool = False  # 마지막으로 넣은 프레임에 epFlag를 붙인다 (쉼이 `ep_silence_ms`에 닿음)
    _ep_sent: bool = False  # 이번 쉼에 epFlag를 이미 냈다
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
        self.ep_due = False
        if voiced:
            if self._voiced_ms == 0:
                self._voiced_from = start
            self._voiced_ms += ms
            self._silent_ms = 0
            self._ep_sent = False
            if not self.in_speech and self._voiced_ms >= self.settings.start_ms:
                self.in_speech = True
                self.segment_starts.append(self._voiced_from)
            return False
        self._voiced_ms = 0
        if not self.in_speech:
            return False
        self._silent_ms += ms
        ep_ms = min(self.settings.ep_silence_ms, self.settings.end_silence_ms)
        if not self._ep_sent and self._silent_ms >= ep_ms:
            self.ep_due = self._ep_sent = True
        if self._silent_ms >= self.settings.end_silence_ms:
            self.in_speech = False
            self._silent_ms = 0
            self._ep_sent = False
            return True
        return False

    def end_speech(self) -> None:
        """밖에서 말 끝을 정했다("말 끝" 버튼). 같은 말에 `epFlag`를 또 붙이지 않게 한다."""
        self.in_speech = False
        self._voiced_ms = 0
        self._silent_ms = 0
        self._ep_sent = False

    def speech_since(self, ms: int) -> bool:
        """`ms` 뒤에 말이 이어졌나: `ms` 이후에 **새로 시작한** 말 구간이 있다.

        말 구간으로 본 것만 센다. 그래야 붙잡은 턴이 그 구간의 말 끝(`epFlag`) 결과로 풀린다.
        `ms` 전부터 이어진 구간은 세지 않는다(리뷰 2번). STT는 그 사이에 gap(700ms)을 봤는데
        무음 감지는 400ms 무음도 못 봤다면, 이어진 것은 말이 아니라 임계값 위의 배경 소리다.
        """
        return any(start >= ms for start in self.segment_starts)
