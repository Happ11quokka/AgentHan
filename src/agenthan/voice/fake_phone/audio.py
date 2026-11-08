"""녹음 변환: m4a·wav 등 → 16kHz mono PCM16 WAV (맥 `afconvert`), WAV → PCM 바이트 (티켓 07).

녹음과 변환본은 `private/` 아래에만 둔다(목소리는 개인정보, gitignore·pre-commit 훅이 막음).
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

from agenthan.voice.call.vad import rms

SAMPLE_RATE = 16_000


def convert_all(src_dir: Path, out_dir: Path, *, pattern: str = "*.m4a") -> list[Path]:
    """`src_dir`의 녹음(`pattern`)을 `out_dir/<이름>.wav`로 바꾼다. 이름순으로 돌려준다."""
    if shutil.which("afconvert") is None:
        raise RuntimeError("afconvert(macOS)가 필요해요")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sorted(p for p in src_dir.glob(pattern) if p.is_file()):
        dst = out_dir / f"{src.stem}.wav"
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1",
             str(src), str(dst)],
            check=True, capture_output=True,
        )
        written.append(dst)
    return written


def read_pcm(path: Path) -> bytes:
    """16kHz mono PCM16 WAV의 샘플 바이트. 형식이 다르면 `ValueError`."""
    with wave.open(str(path)) as w:
        fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth())
        if fmt != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path.name}: 16kHz mono PCM16이 아니에요 (rate·ch·bytes={fmt})")
        return w.readframes(w.getnframes())


REFERENCE_RMS = 1_200  # 기준 말 끝: 이 RMS 이상이
REFERENCE_MIN_MS = 200  # 이만큼 이어지고
REFERENCE_PEAK = 2_000  # 그 안에 이 RMS를 넘는 프레임이 있는 마지막 구간의 끝
# 녹음 17개(동현)에서 눈으로 본 말 끝과 14개는 0.1초 안. D1 +0.4, D5 -0.4, D2 -0.9(작은 끝말)


def speech_end_s(pcm: bytes, *, threshold: float = REFERENCE_RMS,
                 min_ms: int = REFERENCE_MIN_MS, peak: float = REFERENCE_PEAK) -> float | None:
    """녹음 안에서 말이 끝난 시각(초). 짧게 튄 소리(딸깍·숨)와 잡음 덩어리는 말로 안 본다.

    서버 무음 감지와 따로 정한 측정 기준값이다(말 끝 → 결과·첫 소리를 VAD 설정과 상관없이 재려고).
    말 구간이 없으면 None.
    """
    frame = 100 * 32
    levels = [rms(pcm[i:i + frame]) for i in range(0, len(pcm) - frame + 1, frame)]
    need = max(1, min_ms // 100)
    end = None
    run = 0
    top = 0.0
    for i, level in enumerate(levels):
        if level >= threshold:
            run += 1
            top = max(top, level)
        else:
            run, top = 0, 0.0
        if run >= need and top >= peak:
            end = i + 1
    return None if end is None else end / 10
