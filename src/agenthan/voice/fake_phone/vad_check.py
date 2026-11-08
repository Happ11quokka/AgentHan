"""무음 감지 오프라인 점검 (티켓 07): 녹음을 세션과 같은 무음 감지기(`call/vad.py`)에 넣어 본다.

네트워크·키 없이 `VadSettings` 후보를 비교한다. 녹음 뒤에는 가짜 휴대폰처럼 0 프레임을 붙인다.
- `inside`: 녹음 안(뒤 잡음 구간)에서 말 끝을 잡았나. 거짓이면 실제 폰에서는(0 프레임이 없으니)
  무음 감지가 말 끝을 못 잡고 STT gap700 안전망이나 "말 끝" 버튼에 기댄다
- `end_to_ep_ms`: 기준 말 끝(`audio.speech_end_s`) → 그 뒤 첫 `epFlag` 프레임
- `ep_ms`: `epFlag`를 붙인 프레임 끝(조기 epFlag, 무음 `ep_silence_ms`). `end_ms`: 말 끝(턴 판정)
  프레임 끝. 기준 말 끝 전의 `epFlag`는 말 중간 쉼에서 나간 것이다(조각이 는다)

    uv run python -m agenthan.voice.fake_phone.vad_check private/recordings/donghyeon/wav \\
        1500/200/400/400 1500/200/400/250    # 임계값/말 시작/말 끝 무음[/epFlag 무음]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agenthan.voice.call.vad import SilenceDetector, VadSettings
from agenthan.voice.fake_phone.audio import read_pcm, speech_end_s
from agenthan.voice.fake_phone.client import FRAME_BYTES, FRAME_MS

TAIL_MS = 2_000


def check_clip(pcm: bytes, settings: VadSettings, *, tail_ms: int = TAIL_MS) -> dict[str, Any]:
    end_s = speech_end_s(pcm)
    speech_end = None if end_s is None else round(end_s * 1000)
    audio = pcm + bytes(-len(pcm) % FRAME_BYTES)
    clip_ms = len(audio) // FRAME_BYTES * FRAME_MS
    audio += bytes(tail_ms // FRAME_MS * FRAME_BYTES)
    vad = SilenceDetector(settings)
    eps, ends = [], []
    for i in range(len(audio) // FRAME_BYTES):
        if vad.feed(audio[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]):
            ends.append((i + 1) * FRAME_MS)
        if vad.ep_due:
            eps.append((i + 1) * FRAME_MS)
    after = [ep for ep in eps if speech_end is not None and ep >= speech_end]
    ended = [end for end in ends if speech_end is not None and end >= speech_end]
    return {
        "speech_end_ms": speech_end,
        "ep_ms": eps,
        "end_ms": ends,
        "inside": bool(ended) and ended[0] <= clip_ms,
        "end_to_ep_ms": after[0] - speech_end if after and speech_end is not None else None,
    }


def parse_candidate(text: str) -> VadSettings:
    """`임계값/말 시작ms/말 끝 무음ms[/epFlag 무음ms]` → 설정. epFlag 무음이 없으면 기본값."""
    threshold, start, end, *ep = text.split("/")
    extra = {"ep_silence_ms": int(ep[0])} if ep else {}
    return VadSettings(threshold=float(threshold), start_ms=int(start), end_silence_ms=int(end),
                       **extra)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="무음 감지 오프라인 점검")
    parser.add_argument("wav_dir", type=Path)
    parser.add_argument("candidates", nargs="+",
                        help="임계값/말 시작ms/말 끝 무음ms[/epFlag 무음ms]")
    args = parser.parse_args(argv)
    clips = {p.stem: read_pcm(p) for p in sorted(args.wav_dir.glob("*.wav"))}
    for text in args.candidates:
        settings = parse_candidate(text)
        rows = {k: check_clip(pcm, settings) for k, pcm in clips.items()}
        inside = sum(r["inside"] for r in rows.values())
        early = sum(sum(1 for ep in r["ep_ms"] if r["speech_end_ms"] and ep < r["speech_end_ms"])
                    for r in rows.values())
        cells = " ".join(f"{k}:{'' if r['inside'] else '!'}{r['end_to_ep_ms']}"
                         for k, r in rows.items())
        print(f"{text:>14}  녹음 안 말 끝 {inside}/{len(rows)}  말 끝 전 epFlag {early}  {cells}")


if __name__ == "__main__":
    main()
