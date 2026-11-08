"""CLOVA Voice 실측 (opt-in, 키 필요, 호출 9번). 평소 `uv run pytest`에서는 건너뛴다.

    AGENTHAN_ENV_FILE=<저장소 루트>/.env uv run pytest tests/voice/test_tts_clova_live.py -m live -s

- 10·40·120자 안팎 문장의 첫 바이트(응답 헤더 도착)·전체 시간을 찍는다
- 서버 캐시(같은 문장 반복 35~98ms)를 피하려고 문장마다 숫자 말을 바꿔 처음 보는 문장을 만든다
- 첫 호출(TLS 포함)은 따로 찍고 표에서 뺀다
- 받은 음성은 메모리에서만 보고 버린다(이용 조건, 레포에 남기지 않음)
"""

from __future__ import annotations

import asyncio
import os
import random
import statistics
import time
from array import array
from pathlib import Path

import httpx
import pytest

from agenthan.voice.call.ports import PortError
from agenthan.voice.tts_clova import ClovaVoiceTts

pytestmark = pytest.mark.live

ENV_FILE = Path(os.environ.get("AGENTHAN_ENV_FILE", ".env"))
NUMS = ["두", "세", "네", "한", "여섯", "일곱", "여덟", "아홉", "열", "다섯"]
MENUS = ["메도빅", "치즈케이크", "카페라떼", "레몬티", "아메리카노", "크루아상", "바닐라라떼"]


def sentences(rng: random.Random) -> dict[str, str]:
    n1, n2, n3 = rng.sample(NUMS, 3)
    m1, m2 = rng.sample(MENUS, 2)
    return {
        "s10": f"네, {n1} 개 맞아요.",
        "s40": f"{m1} {n1} 개랑 {m2} {n2} 잔 맞으세요? 포장으로 준비할게요.",
        "s120": (
            f"주문하신 {m1} {n1} 개와 {m2} {n2} 잔은 지금부터 준비를 시작해서 "
            f"{n3} 시쯤 가게 앞에서 찾아가실 수 있고, 결제는 오실 때 카드나 현금으로 "
            "하시면 되고, 더 필요하신 게 있으면 지금 말씀해 주시면 됩니다."
        ),
    }


class Timed:
    """httpx 응답 훅으로 헤더 도착(= 첫 바이트, CLOVA는 합성 뒤 한 번에 보냄) 시각을 잰다."""

    def __init__(self) -> None:
        self.t0 = 0.0
        self.header_at = 0.0
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0), event_hooks={"response": [self._hook]}
        )

    async def _hook(self, response: httpx.Response) -> None:
        self.header_at = time.perf_counter()

    async def synth(self, tts: ClovaVoiceTts, text: str) -> tuple[bytes, float, float]:
        self.t0 = time.perf_counter()
        pcm = await tts.synth(text)
        end = time.perf_counter()
        return pcm, (self.header_at - self.t0) * 1000, (end - self.t0) * 1000


def check_pcm(pcm: bytes, text: str) -> float:
    """PCM16LE 16kHz mono(형식은 어댑터가 fmt로 이미 확인). 길이·소리 있음만 본다. 초를 돌려준다."""
    assert len(pcm) % 2 == 0
    seconds = len(pcm) / 32000
    assert seconds > 0.1 * len(text) * 0.5, (len(text), seconds)  # 글자당 0.05초 넘게
    samples = array("h", pcm)
    assert max(abs(s) for s in samples) > 1000
    return seconds


def test_live_ttfb_by_length_and_pcm_format() -> None:
    timed = Timed()
    tts = ClovaVoiceTts.from_env(ENV_FILE, http=timed.http)
    rng = random.Random(time.time_ns())

    async def measure() -> list[tuple[str, int, float, float, float]]:
        rows = []
        warm = sentences(rng)["s10"]
        pcm, ttfb, total = await timed.synth(tts, warm)
        rows.append(("warmup(TLS)", len(warm), ttfb, total, check_pcm(pcm, warm)))
        for _ in range(2):
            for label, text in sentences(rng).items():
                pcm, ttfb, total = await timed.synth(tts, text)
                rows.append((label, len(text), ttfb, total, check_pcm(pcm, text)))
        await tts.aclose()
        return rows

    rows = asyncio.run(measure())
    print("\nlabel        chars  ttfb_ms  total_ms  audio_s")
    for label, chars, ttfb, total, secs in rows:
        print(f"{label:<12} {chars:>5}  {ttfb:>7.0f}  {total:>8.0f}  {secs:>7.2f}")
    for label in ("s10", "s40", "s120"):
        ttfbs = [r[2] for r in rows if r[0] == label]
        print(f"{label} ttfb median {statistics.median(ttfbs):.0f} ms")


def test_live_bad_speaker_and_bad_key_map_to_port_errors() -> None:
    async def both() -> tuple[PortError, PortError]:
        bad_speaker = ClovaVoiceTts.from_env(ENV_FILE, speaker="no_such_speaker")
        bad_key = ClovaVoiceTts("not-a-real-id", "not-a-real-secret")
        errors = []
        for tts in (bad_speaker, bad_key):
            with pytest.raises(PortError) as err:
                await tts.synth("네.")
            errors.append(err.value)
            await tts.aclose()
        return errors[0], errors[1]

    speaker_err, key_err = asyncio.run(both())
    print(f"\nbad speaker → {speaker_err.code} / bad key → {key_err.code}")
    assert speaker_err.code == "tts_vs02"
    assert (key_err.code, key_err.retry) == ("tts_auth", False)
