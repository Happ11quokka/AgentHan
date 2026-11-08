"""Smart Turn 어댑터 (3단계 티켓 03): 입력 규칙, numpy 특징, 모델 파일 확인.

특징의 기대값은 `transformers` 4.48.2 `WhisperFeatureExtractor(chunk_length=8)`에 같은 입력
(앞에 0을 채운 8초, `do_normalize=True`)을 넣어 따로 뽑은 값이다. 우리 코드로 다시 계산한 값이
아니다. 실제 모델 테스트는 모델 파일이 있을 때만 돈다(`PHONE_TURN_MODEL_PATH`).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

from agenthan.voice.smart_turn import (
    MODEL_SHA256,
    WINDOW_SAMPLES,
    fit_window,
    load_turn_model,
    log_mel,
    pcm16_to_float,
)

MODEL_PATH = Path(os.environ.get("PHONE_TURN_MODEL_PATH", "/app/models/smart-turn-v3.2-cpu.onnx"))


def tone_pcm() -> bytes:
    """1.5초: 점점 커지는 440Hz + 1,250Hz. PCM16LE."""
    n = np.arange(24_000)
    x = (0.3 * np.sin(2 * np.pi * 440 * n / 16_000) * (n / 24_000)
         + 0.1 * np.sin(2 * np.pi * 1_250 * n / 16_000 + 0.5))
    return np.round(x * 32_767).astype("<i2").tobytes()


def noise_pcm() -> bytes:
    """1초 잡음 (시드 0)."""
    rng = np.random.default_rng(0)
    x = np.clip(np.round(rng.standard_normal(16_000) * 0.1 * 32_767), -32_768, 32_767)
    return x.astype("<i2").tobytes()


# ---------------------------------------------------------------- 입력 규칙 (원본과 같음)


def test_short_audio_is_left_padded_with_zeros_to_eight_seconds() -> None:
    window = fit_window(np.ones(16_000, dtype=np.float32))
    assert window.shape == (WINDOW_SAMPLES,) == (128_000,)
    assert not window[:-16_000].any()  # 말이 벡터 끝에 온다
    assert (window[-16_000:] == 1).all()


def test_long_audio_keeps_only_the_last_eight_seconds() -> None:
    audio = np.arange(10 * 16_000, dtype=np.float32)
    window = fit_window(audio)
    assert window.shape == (128_000,)
    assert window[0] == 2 * 16_000 and window[-1] == audio[-1]


def test_pcm16_bytes_become_floats_in_minus_one_to_one() -> None:
    pcm = np.array([0, 16_384, -32_768, 32_767], dtype="<i2").tobytes()
    assert pcm16_to_float(pcm).tolist() == [0.0, 0.5, -1.0, 32_767 / 32_768]


# ---------------------------------------------------------------- 특징: 고정 입력 → 고정 출력


@pytest.mark.parametrize(
    ("pcm", "stats", "cells"),
    [
        (tone_pcm, (-0.024559, 0.237424, -0.069153, 1.930847),
         {(0, 0): -0.069153, (10, 700): 1.605746, (79, 799): 0.059684}),
        (noise_pcm, (-0.189397, 0.583970, -0.421421, 1.578579),
         {(0, 0): 0.219476, (10, 700): 1.323951, (40, 750): 1.102102, (79, 799): 1.388183,
          (5, 790): 1.053855}),
    ],
    ids=["tone", "noise"],
)
def test_log_mel_matches_the_whisper_feature_extractor(pcm, stats, cells) -> None:
    features = log_mel(fit_window(pcm16_to_float(pcm())))
    assert features.shape == (80, 800) and features.dtype == np.float32
    got = (features.mean(), features.std(), features.min(), features.max())
    assert got == pytest.approx(stats, abs=2e-5)
    for (i, j), want in cells.items():
        assert features[i, j] == pytest.approx(want, abs=2e-5)


def test_normalisation_covers_the_whole_window_including_the_padding() -> None:
    # 같은 소리라도 앞에 채운 0이 정규화에 들어가므로, 길이가 다르면 특징이 다르다
    loud = pcm16_to_float(tone_pcm())
    short = log_mel(fit_window(loud[-8_000:]))
    long = log_mel(fit_window(loud))
    assert not np.allclose(short[:, -50:], long[:, -50:])


# ---------------------------------------------------------------- 모델 파일 확인 (앱 시작 때)


def test_a_missing_model_file_means_rules_only(tmp_path, caplog) -> None:
    assert load_turn_model(tmp_path / "없음.onnx") is None
    assert "Smart Turn" in caplog.text


def test_a_model_file_with_the_wrong_checksum_is_not_loaded(tmp_path, caplog) -> None:
    fake = tmp_path / "smart-turn.onnx"
    fake.write_bytes(b"not the model")
    assert load_turn_model(fake) is None
    assert "SHA-256" in caplog.text


def test_onnx_runtime_telemetry_is_off_before_the_runtime_starts() -> None:
    # onnxruntime 공식 빌드는 리눅스에서도 1DS로 Microsoft에 사용 기록을 보낸다(기본 켬,
    # 휠의 Privacy.md). 외부 호출이 없어야 하므로 런타임이 뜨기 전에 끈다
    import agenthan.voice.smart_turn  # noqa: F401

    assert os.environ.get("ORT_DISABLE_TELEMETRY") == "1"


def test_the_pinned_checksum_is_the_v3_2_cpu_model() -> None:
    # 연구 노트 Q1: HF 리비전 f766f81d의 smart-turn-v3.2-cpu.onnx, LFS oid와 같은 값
    assert MODEL_SHA256 == "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"


# ---------------------------------------------------------------- 실제 모델 (파일이 있을 때만)

needs_model = pytest.mark.skipif(
    not MODEL_PATH.is_file(), reason=f"모델 파일 없음: {MODEL_PATH} (PHONE_TURN_MODEL_PATH)"
)


@needs_model
def test_the_real_model_scores_fixed_inputs_like_the_reference_pipeline() -> None:
    assert hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA256
    model = load_turn_model(MODEL_PATH)
    assert model is not None
    # 기대값: 같은 입력을 transformers 특징 + 같은 ONNX로 돌린 값
    assert model.p_complete(noise_pcm()) == pytest.approx(0.964720, abs=1e-3)
    assert model.p_complete(tone_pcm()) == pytest.approx(0.271964, abs=1e-3)


@needs_model
def test_the_real_model_scores_from_two_threads_at_once() -> None:
    from concurrent.futures import ThreadPoolExecutor

    model = load_turn_model(MODEL_PATH)
    assert model is not None
    with ThreadPoolExecutor(2) as pool:
        scores = list(pool.map(model.p_complete, [noise_pcm()] * 4))
    assert scores == pytest.approx([0.964720] * 4, abs=1e-3)
