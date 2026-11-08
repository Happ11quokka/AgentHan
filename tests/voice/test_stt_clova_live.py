"""실제 CLOVA Speech로 녹음 하나를 받아 적는 측정 (opt-in, 키 필요, 티켓 02).

평소 `uv run pytest`에서는 건너뛴다. 돌리기 (저장소 루트, macOS `afconvert` 필요):

    uv run pytest tests/voice/test_stt_clova_live.py -m live -s

- 키: 환경 변수 또는 `.env`(이 파일에서 위로 올라가며 처음 찾은 것, `AGENTHAN_ENV_FILE`로 바꿈)의
  `CLOVA_SPEECH_SECRET_KEY`·`CLOVA_SPEECH_GRPC_ENDPOINT`. 키는 출력하지 않는다
- 녹음: `private/recordings/donghyeon/A1.m4a`(위로 올라가며 찾음, `AGENTHAN_RECORDING`으로 바꿈).
  16kHz PCM 변환본은 pytest 임시 폴더(`--basetemp`)에만 둔다. 저장소에 쓰지 않는다
- 보내기: 100ms 프레임을 마이크처럼 실제 속도로. 세션과 같은 무음 감지기(`call/vad.py`)가
  말 끝 프레임에 `epFlag`를 붙인다(주 경로). 끝에 무음 3초
- 출력: 받아 적은 글, `epdType`, 말 끝(마지막 말소리 프레임) → 결과 도착 시간.
  결과 JSON은 녹음이 있는 `private/` 아래 `stt-live/`에만 쓴다(`AGENTHAN_LIVE_OUT`으로 바꿈)
- 통과 기준이 아니라 측정용이다. 글이 하나도 안 오면 실패한다
- macOS에서 gRPC 기본 DNS(c-ares)가 "Could not contact DNS servers"로 실패하면
  `GRPC_DNS_RESOLVER=native`를 앞에 붙인다(2026-11-08 이 맥에서 겪음)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import wave
from datetime import datetime
from pathlib import Path

import pytest

from agenthan.voice.call.vad import SilenceDetector
from agenthan.voice.stt.clova import ClovaSttSettings, ClovaSttStream

pytestmark = pytest.mark.live  # `-m live`일 때만 돈다 (tests/voice/conftest.py)

FRAME_BYTES = 3_200  # 100ms
TAIL_S = 3.0
HERE = Path(__file__).resolve()


def _find_up(relative: str) -> Path | None:
    for parent in HERE.parents:
        candidate = parent / relative
        if candidate.exists():
            return candidate
    return None


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path(os.environ["AGENTHAN_ENV_FILE"]) if "AGENTHAN_ENV_FILE" in os.environ \
        else _find_up(".env")
    if path is not None and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    for key in ("CLOVA_SPEECH_SECRET_KEY", "CLOVA_SPEECH_GRPC_ENDPOINT"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def _pcm16k(recording: Path, tmp: Path) -> bytes:
    if shutil.which("afconvert") is None:
        pytest.skip("afconvert(macOS)가 필요해요")
    wav = tmp / f"{recording.stem}.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(recording), str(wav)], check=True)
    with wave.open(str(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16_000, 1, 2)
        return w.readframes(w.getnframes())


async def _stream(stt: ClovaSttStream, pcm: bytes) -> dict:
    audio = pcm + bytes(int(32_000 * TAIL_S))
    audio += bytes(-len(audio) % FRAME_BYTES)
    vad = SilenceDetector()
    received: list[dict] = []
    marks: dict[str, list[float]] = {"speech_end": [], "ep_sent": []}
    t0 = 0.0

    async def read() -> None:
        async for r in stt.results():
            received.append({"at_s": time.monotonic() - t0, "text": r.text, "epd_type": r.epd_type,
                             "ep_flag": r.ep_flag, "seq_id": r.seq_id, "start_ms": r.start_ms,
                             "end_ms": r.end_ms, "confidence": r.confidence})

    started = time.monotonic()
    await stt.start()
    connect_s = time.monotonic() - started
    reader = asyncio.create_task(read())
    t0 = time.monotonic()
    seq = 0
    last_voiced_end = None
    for i in range(len(audio) // FRAME_BYTES):
        await asyncio.sleep(max(0.0, t0 + (i + 1) * 0.1 - time.monotonic()))  # 마이크처럼
        frame = audio[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]
        ended = vad.feed(frame)
        if vad.last_voiced:
            last_voiced_end = (i + 1) * 0.1
        if ended:
            seq += 1
            marks["speech_end"].append(last_voiced_end or 0.0)
            marks["ep_sent"].append(time.monotonic() - t0)
        await stt.write(frame, ep_flag=ended, seq_id=seq if ended else 0)
    await asyncio.sleep(1.5)
    await stt.close()
    await asyncio.wait_for(reader, 3)
    return {"connect_s": round(connect_s, 3), "audio_s": len(pcm) / 32_000,
            "marks": marks, "results": received}


def _summary(run: dict) -> list[dict]:
    """epFlag 하나마다: 그 앞까지 온 글(합침), 말 끝 → 결과 시간, epFlag 송신 → 결과 시간."""
    rows = []
    texts = [r for r in run["results"] if r["text"].strip()]
    for n, (speech_end, ep_sent) in enumerate(
        zip(run["marks"]["speech_end"], run["marks"]["ep_sent"], strict=True), start=1
    ):
        answer = next((r for r in run["results"] if r["ep_flag"] and r["seq_id"] == n), None)
        last = max((r for r in texts if r["at_s"] <= (answer or {"at_s": 1e9})["at_s"]),
                   key=lambda r: r["at_s"], default=None)
        done_at = max(x["at_s"] for x in (answer, last) if x) if (answer or last) else None
        rows.append({
            "seq": n,
            "text": "".join(r["text"] for r in texts
                            if done_at is not None and r["at_s"] <= done_at).strip(),
            "speech_end_s": round(speech_end, 3),
            "ep_sent_s": round(ep_sent, 3),
            "speech_end_to_result_s": round(done_at - speech_end, 3) if done_at else None,
            "ep_sent_to_result_s": round(done_at - ep_sent, 3) if done_at else None,
        })
    return rows


def test_a1_recording_round_trip(tmp_path) -> None:
    env = _env()
    if not env.get("CLOVA_SPEECH_SECRET_KEY"):
        pytest.skip("CLOVA_SPEECH_SECRET_KEY가 없어요")
    recording = Path(os.environ["AGENTHAN_RECORDING"]) if "AGENTHAN_RECORDING" in os.environ \
        else _find_up("private/recordings/donghyeon/A1.m4a")
    if recording is None or not recording.exists():
        pytest.skip("녹음 private/recordings/donghyeon/A1.m4a가 없어요")

    pcm = _pcm16k(recording, tmp_path)
    run = asyncio.run(_stream(ClovaSttStream(ClovaSttSettings.from_env(env)), pcm))
    rows = _summary(run)

    print(f"\n[{recording.name}] 오디오 {run['audio_s']:.2f}초, 연결+CONFIG {run['connect_s']}초")
    for r in run["results"]:
        lag = r["at_s"] - r["end_ms"] / 1000  # 결과 구간 끝(오디오 시각) → 도착
        print(f"  {r['at_s']:6.2f}s  {r['epd_type']:>17}  ep={r['ep_flag']!s:5} "
              f"seq={r['seq_id']}  {r['start_ms']}-{r['end_ms']}ms (끝 → 도착 {lag:.2f}초)  "
              f"{r['text']!r}")
    for row in rows:
        print(f"  말 {row['seq']}: {row['text']!r}  말 끝 {row['speech_end_s']}s → 결과 "
              f"{row['speech_end_to_result_s']}초 (epFlag 송신 → {row['ep_sent_to_result_s']}초)")

    out_dir = Path(os.environ["AGENTHAN_LIVE_OUT"]) if "AGENTHAN_LIVE_OUT" in os.environ \
        else recording.parents[2] / "stt-live"  # .../private/stt-live
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{recording.stem}-{stamp}.json").write_text(
        json.dumps({"recording": recording.name, **run, "summary": rows},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    assert any(r["text"].strip() for r in run["results"]), "받아 적은 글이 하나도 없어요"
