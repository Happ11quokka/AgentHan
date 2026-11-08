"""TTS 첫 문장 앞 무음 자르기 (3단계 티켓 01, Coval TTFA 방식).

TTS가 문장 앞에 무음을 넣으면 그 길이만큼 손님은 아무것도 못 듣는데 지연 지표는 좋아 보인다.
첫 가청 소리를 이렇게 찾는다(Coval TTFA 벤치마크, 2026-09-08).
- `window_ms`(10ms) 창을 `hop_ms`(1ms)씩 옮기며 RMS를 잰다. RMS가 `threshold`(풀스케일의 1%,
  PCM16로 약 328) 이상인 첫 창이 나오면, 그 창 안에서 크기가 문턱 이상인 첫 샘플이 첫 소리다
- 첫 소리 `keep_ms`(20ms) 앞에서 자른다. 말소리 앞머리(파열음 등)가 잘리지 않게 조금 남긴다
- `max_ms`(1초) 안에 첫 소리가 없으면 자르지 않는다(통째로 조용한 문장, 잘못 잰 경우)

CLOVA Voice가 문장 앞에 넣는 무음 길이는 아직 재지 않았다(합성음 저장 금지라 녹음이 없다).
잘라 낸 길이는 턴 기록 `tts_trim_ms`에 남으므로 실제 통화에서 확인한다.
"""

from __future__ import annotations

import array
import sys
from dataclasses import dataclass

from agenthan.voice.call.frames import SAMPLE_RATE

SAMPLES_PER_MS = SAMPLE_RATE // 1000


@dataclass(frozen=True)
class TrimSettings:
    threshold: float = 0.01 * 32_768  # PCM16 RMS (풀스케일의 1%)
    window_ms: int = 10
    hop_ms: int = 1
    keep_ms: int = 20
    max_ms: int = 1_000
    enabled: bool = True


def trim_leading_silence(pcm: bytes, settings: TrimSettings) -> tuple[bytes, int]:
    """PCM16LE 16kHz mono → (앞 무음을 자른 PCM, 잘라 낸 ms). 1ms 단위로 자른다."""
    if not settings.enabled:
        return pcm, 0
    onset = _first_audible_sample(pcm, settings)
    if onset is None:
        return pcm, 0
    cut_ms = max(0, onset - settings.keep_ms * SAMPLES_PER_MS) // SAMPLES_PER_MS
    return pcm[cut_ms * SAMPLES_PER_MS * 2:], cut_ms


def _first_audible_sample(pcm: bytes, settings: TrimSettings) -> int | None:
    window = settings.window_ms * SAMPLES_PER_MS
    hop = max(1, settings.hop_ms * SAMPLES_PER_MS)
    scan = (settings.max_ms + settings.window_ms) * SAMPLES_PER_MS
    samples = array.array("h")
    head = pcm[: min(len(pcm), scan * 2)]
    samples.frombytes(head[: len(head) - len(head) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if len(samples) < window:
        return None
    squares = [0]  # 앞에서부터 제곱합 (창 RMS를 창마다 다시 더하지 않게)
    for s in samples:
        squares.append(squares[-1] + s * s)
    limit = settings.threshold**2 * window
    for start in range(0, min(len(samples) - window, settings.max_ms * SAMPLES_PER_MS) + 1, hop):
        if squares[start + window] - squares[start] >= limit:
            for i in range(start, start + window):
                if abs(samples[i]) >= settings.threshold:
                    return i
            return start
    return None
