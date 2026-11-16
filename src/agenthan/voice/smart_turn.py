"""Smart Turn 어댑터: 손님 오디오 → 말이 끝났을 확률 (3단계 spec "Smart Turn", 티켓 03).

`TurnModel` 포트(`call/ports.py`)를 Pipecat Smart Turn v3.2 CPU int8 ONNX로 구현한다.
자체 호스팅 오픈 모델이다(BSD-2, 바탕 whisper-tiny 인코더 Apache-2.0).
외부 모델 API는 부르지 않는다.

- 입력 규칙은 원본(`pipecat-ai/smart-turn` `inference.py`)과 같다: 16kHz mono, 8초보다 길면
  앞을 자르고 짧으면 앞에 0을 채운다. 0을 채운 8초 전체로 평균 0·분산 1 정규화
- 특징은 `transformers` 없이 numpy로 만든다(`WhisperFeatureExtractor(chunk_length=8)`와 같은 식:
  n_fft 400, hop 160, Slaney 멜 80개, log10, 최댓값 −8 아래 자름, (x+4)/4, 마지막 프레임 버림).
  연구 노트 `s3-smart-turn.md` Q2에서 차이 2.4e-7, 확률 소수 넷째 자리까지 같음을 확인했다
- 출력 `logits`는 이름과 달리 sigmoid를 거친 확률이다
- ONNX 세션 하나를 여러 스레드가 같이 쓴다(`run`은 스레드 안전). intra_op 1 · inter_op 1 ·
  순차 실행. 2 vCPU 서버에서 통화 둘이 동시에 끝나도 이벤트 루프와 덜 다툰다
- 모델 파일은 앱 시작 때 SHA-256을 확인한다. 없거나 틀리면 경고를 남기고 `None`(규칙만)
- onnxruntime 공식 빌드는 리눅스·맥에서도 1DS로 Microsoft에 사용 기록을 보낸다(기본 켬, 휠의
  `Privacy.md`). 외부 호출을 만들지 않게 이 모듈이 뜰 때 `ORT_DISABLE_TELEMETRY=1`을 둔다
  (런타임이 뜨기 전이어야 한다. 이미지에도 같은 ENV가 있다)
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")  # onnxruntime import 전에

__all__ = [
    "MODEL_FILE",
    "MODEL_REVISION",
    "MODEL_SHA256",
    "MODEL_URL",
    "SAMPLE_RATE",
    "WINDOW_SAMPLES",
    "SmartTurnOnnx",
    "fit_window",
    "load_turn_model",
    "log_mel",
    "pcm16_to_float",
]

# 고정 버전 (연구 노트 Q1). Dockerfile이 같은 URL·체크섬으로 받는다
MODEL_FILE = "smart-turn-v3.2-cpu.onnx"
MODEL_REVISION = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
MODEL_URL = (
    f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/{MODEL_REVISION}/{MODEL_FILE}"
)
MODEL_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"

SAMPLE_RATE = 16_000
WINDOW_SECONDS = 8
WINDOW_SAMPLES = SAMPLE_RATE * WINDOW_SECONDS
_N_FFT, _HOP, _N_MELS = 400, 160, 80


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    """PCM16LE 바이트 → float32 [-1, 1)."""
    return np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float32) / 32_768


def fit_window(audio: np.ndarray) -> np.ndarray:
    """마지막 8초만 남기고, 짧으면 앞에 0을 채운다(말이 벡터 끝에 오게)."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size >= WINDOW_SAMPLES:
        return audio[-WINDOW_SAMPLES:]
    return np.pad(audio, (WINDOW_SAMPLES - audio.size, 0))


def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    """Slaney 멜 눈금: 1kHz 아래는 선형, 위는 로그."""
    hz = np.atleast_1d(np.asarray(hz, dtype=np.float64))
    mel = 3.0 * hz / 200.0
    high = hz >= 1_000.0
    mel[high] = 15.0 + np.log(hz[high] / 1_000.0) * (27.0 / np.log(6.4))
    return mel


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    mel = np.atleast_1d(np.asarray(mel, dtype=np.float64))
    hz = 200.0 * mel / 3.0
    high = mel >= 15.0
    hz[high] = 1_000.0 * np.exp((np.log(6.4) / 27.0) * (mel[high] - 15.0))
    return hz


def _mel_filters() -> np.ndarray:
    """(201, 80) 삼각 필터, Slaney 면적 정규화."""
    edges = _mel_to_hz(np.linspace(_hz_to_mel(0.0)[0], _hz_to_mel(SAMPLE_RATE / 2)[0], _N_MELS + 2))
    fft_hz = np.linspace(0, SAMPLE_RATE // 2, _N_FFT // 2 + 1)
    width = np.diff(edges)
    slopes = edges[None, :] - fft_hz[:, None]
    down = -slopes[:, :-2] / width[:-1]
    up = slopes[:, 2:] / width[1:]
    bank = np.maximum(0.0, np.minimum(down, up))
    return bank * (2.0 / (edges[2:] - edges[:-2]))[None, :]


_FILTERS = _mel_filters()
_HANN = np.hanning(_N_FFT + 1)[:-1]  # 주기형 Hann


def log_mel(window: np.ndarray) -> np.ndarray:
    """8초 창 → 로그 멜 (80, 800) float32. 정규화는 창 전체(채운 0 포함)로 한다."""
    x = np.asarray(window, dtype=np.float32)
    if x.size != WINDOW_SAMPLES:
        raise ValueError(f"8초({WINDOW_SAMPLES}샘플) 창이 필요해요: {x.size}")
    x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
    padded = np.pad(x.astype(np.float64), (_N_FFT // 2, _N_FFT // 2), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, _N_FFT)[::_HOP]
    power = np.abs(np.fft.rfft(frames * _HANN, axis=-1)) ** 2  # (801, 201)
    mel = np.log10(np.maximum(1e-10, power @ _FILTERS).T)[:, :-1]  # (80, 800)
    mel = np.maximum(mel, mel.max() - 8.0)
    return ((mel + 4.0) / 4.0).astype(np.float32)


class SmartTurnOnnx:
    """`TurnModel` 구현. `p_complete`는 동기다(세션이 전용 스레드 풀에서 부른다)."""

    def __init__(self, path: Path) -> None:
        import onnxruntime as ort  # 무거운 import는 모델을 쓸 때만

        ort.disable_telemetry_events()  # 위 환경 변수와 같이. 남는 이벤트도 끈다
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session: Any = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def p_complete(self, pcm16: bytes) -> float:
        features = log_mel(fit_window(pcm16_to_float(pcm16)))[None]
        out = self._session.run(None, {"input_features": features})
        return float(out[0][0, 0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_turn_model(path: Path) -> SmartTurnOnnx | None:
    """모델 파일을 확인하고 연다. 없거나 체크섬이 틀리거나 못 열면 경고를 남기고 `None`.

    `None`이면 통화는 끝말 규칙만으로 돈다. 통화를 막지 않는다.
    """
    if not path.is_file():
        log.warning("Smart Turn 모델 파일이 없어 끝말 규칙만 씀: %s", path)
        return None
    digest = _sha256(path)
    if digest != MODEL_SHA256:
        log.warning("Smart Turn 모델 SHA-256이 고정값과 달라 끝말 규칙만 씀: %s (%s…)",
                    path, digest[:12])
        return None
    try:
        return SmartTurnOnnx(path)
    except Exception:
        log.exception("Smart Turn 모델을 열지 못해 끝말 규칙만 씀: %s", path)
        return None
