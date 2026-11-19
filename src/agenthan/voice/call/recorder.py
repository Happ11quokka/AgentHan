"""통화 녹음기 (5단계 티켓 01): 손님·봇 소리를 같은 시간축의 스테레오 WAV 하나로 쓴다.

디버그용이다. `PHONE_RECORD_DIR`이 있을 때만 세션이 만든다(기본 꺼짐. 발표·심사 때는 끈다).

- 왼쪽 채널 = 서버가 받은 손님 마이크(16kHz PCM16 그대로, 반이중 중 0 프레임 포함)
- 오른쪽 채널 = 봇 목소리. 실제로 보낸 조각만 넣고, 말 끊기면 그 뒤는 지운다(`bot_stop`)
- 시간축은 턴 기록 `marks`와 같다(세션 시작 = 0ms). 녹음의 N초 = `marks`의 N×1000

손님 프레임은 앞 프레임 바로 뒤에 이어 붙인다. 망 때문에 몰려 와도 소리는 이어져 있기 때문이다.
도착 시각이 이어 붙일 자리보다 `jitter_ms` 넘게 뒤면(브라우저가 한동안 안 보냄) 그 사이를
무음으로 두고 도착 시각에 놓는다.
봇 조각은 보낸 시각에 놓되, 앞 조각이 아직 재생 중이면 그 뒤에 잇는다(브라우저가 순서대로 재생).
보낸 시각 기준이라 실제 재생보다 망·버퍼 지연만큼(보통 0.1~0.5초) 이르다.
"""

from __future__ import annotations

import array
import wave
from pathlib import Path

RATE = 16_000
BYTES_PER_MS = RATE * 2 // 1000  # PCM16 mono


class _Track:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.cursor = 0  # 다음 소리를 놓을 자리(바이트)

    def put(self, pcm: bytes, at: int) -> None:
        if at > len(self.buf):
            self.buf.extend(bytes(at - len(self.buf)))
        end = at + len(pcm)
        self.buf[at:end] = pcm
        self.cursor = end

    def cut(self, at: int) -> None:
        del self.buf[at:]
        self.cursor = min(self.cursor, at)


class CallRecorder:
    def __init__(self, path: Path, *, jitter_ms: int = 500) -> None:
        self.path = path
        self._jitter = jitter_ms * BYTES_PER_MS
        self._customer = _Track()
        self._bot = _Track()
        self._customer_started = False

    def customer(self, pcm: bytes, *, at_ms: int) -> None:
        """손님 프레임 하나. `at_ms`는 서버가 받은 때."""
        track, arrived = self._customer, _bytes(at_ms)
        if not self._customer_started or arrived - self._jitter > track.cursor:
            self._customer_started = True
            track.put(pcm, arrived)
        else:
            track.put(pcm, track.cursor)

    def bot(self, pcm: bytes, *, at_ms: int) -> None:
        """봇 소리 조각 하나. `at_ms`는 보낸 때."""
        self._bot.put(pcm, max(self._bot.cursor, _bytes(at_ms)))

    def bot_stop(self, *, at_ms: int) -> None:
        """말 끊기: 이 시각 뒤의 봇 소리는 브라우저가 버렸으니 지운다."""
        self._bot.cut(_bytes(at_ms))

    def write(self) -> Path:
        """스테레오 WAV로 쓴다(왼쪽 손님, 오른쪽 봇). 짧은 쪽은 무음으로 채운다."""
        left, right = self._customer.buf, self._bot.buf
        size = max(len(left), len(right))
        mono_l = array.array("h", bytes(left) + bytes(size - len(left)))
        mono_r = array.array("h", bytes(right) + bytes(size - len(right)))
        stereo = array.array("h", bytes(size * 2))
        stereo[0::2] = mono_l
        stereo[1::2] = mono_r
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self.path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(stereo.tobytes())
        return self.path


def _bytes(ms: int) -> int:
    return max(0, ms) * BYTES_PER_MS
