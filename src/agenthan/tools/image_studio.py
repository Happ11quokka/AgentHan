"""메뉴 사진 스튜디오 보정(2차 다듬기, GPU 없이 CPU에서 1초 안팎).

1차 흰 배경 사진(menu_board.white_photo)은 배경만 지운다. 사장님이 "사진 다듬어 줘"라고 하면
이 모듈이 배달앱 메뉴 사진처럼 다시 만든다. 음식 자체를 새로 그리지 않으므로 모양이 바뀌지 않는다.

1. 음식 영역 마스크: 기본 GrabCut. AGENTHAN_CUTOUT_MODEL=rembg이고 rembg가 설치돼 있으면 오픈
   배경 제거 모델(U2Net, ONNX CPU, 사진당 0.4초)을 쓴다. 코드 기본은 꺼져 있고, 시연 서버
   이미지(deploy/phone/Dockerfile)는 모델을 넣어 켠다(2026-11-21 팀 결정). 네이버 모델이 아니라
   PRD §0 Smart Turn과 같은 자체 호스팅 오픈 모델로 분류한다.
2. 색 보정: 밝기 대비(CLAHE, L 채널), 화이트밸런스(gray-world 절반), 채도 +12%, 선명도(언샤프).
3. 구도: 음식을 정사각형 가운데에 12% 여백으로 놓고, 바닥에 부드러운 그림자를 깐다.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable

import cv2
import numpy as np

from agenthan.tools.image_cutout import ImageCutoutError, _foreground_mask

log = logging.getLogger(__name__)

MODEL_ENV = "AGENTHAN_CUTOUT_MODEL"  # grabcut(기본) | rembg
Mask = Callable[[np.ndarray], np.ndarray]


def _decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None or min(image.shape[:2]) < 32:
        raise ImageCutoutError("사진을 읽지 못했어요.")
    return image


def grabcut_mask(image: np.ndarray) -> np.ndarray:
    return _foreground_mask(image, (0.06, 0.06, 0.94, 0.94), 512, 5)


_rembg_session = None
_rembg_lock = threading.Lock()  # 사진 여러 장을 동시에 자를 때 모델을 한 번만 읽는다


def _session():
    """U2Net 세션(한 번만 만든다). 처음 부를 때 rembg를 import하고 모델을 읽는다(약 170MB)."""
    global _rembg_session
    from rembg import new_session  # 선택 의존성: uv sync --extra cutout

    with _rembg_lock:
        if _rembg_session is None:
            _rembg_session = new_session("u2net")
        return _rembg_session


def rembg_mask(image: np.ndarray) -> np.ndarray:
    """오픈 배경 제거 모델(U2Net). 사진 한 장 0.2초(서버 8코어, 4장 동시 0.8초)."""
    from rembg import remove

    session = _session()
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    mask = remove(rgb, session=session, only_mask=True)
    return np.asarray(mask, dtype=np.uint8)


def warm() -> bool:
    """rembg를 켰으면 지금 import하고 모델 세션을 만든다. 켜지 않았으면 아무것도 않고 False.

    `import rembg`는 pymatting의 numba 함수를 컴파일한다. 캐시(NUMBA_CACHE_DIR)가 비어 있으면
    서버(8 vCPU)에서 34.5초 걸렸다(캐시가 있으면 0.9초, 2026-11-21 실측). 이걸 첫 메뉴판 사진이
    떠안으면 메뉴 사진 칸이 모두 import 잠금에 묶여 38초쯤 뒤 한꺼번에 끝났다. 그래서 앱이 뜰 때
    뒤에서 미리 부른다(warm_in_background)."""
    if model_name() != "rembg":
        return False
    started = time.monotonic()
    _session()
    log.info("cutout model warm %.2fs", time.monotonic() - started)
    return True


def warm_in_background() -> threading.Thread:
    """앱 시작용: warm()을 데몬 스레드로. 실패해도 첫 사진이 다시 시도하므로 로그만 남긴다."""

    def run() -> None:
        try:
            warm()
        except Exception:  # noqa: BLE001 - 미리 읽기가 앱 시작을 막지 않게
            log.warning("cutout model warm failed", exc_info=True)

    thread = threading.Thread(target=run, name="cutout-warm", daemon=True)
    thread.start()
    return thread


def model_name() -> str:
    """지금 쓰는 마스크 방법. rembg를 켰는데 설치가 안 돼 있으면 grabcut."""
    if os.environ.get(MODEL_ENV, "").strip().lower() != "rembg":
        return "grabcut"
    try:
        import rembg  # noqa: F401
    except ImportError:
        log.warning("%s=rembg but rembg is not installed; using GrabCut", MODEL_ENV)
        return "grabcut"
    return "rembg"


def default_mask() -> Mask:
    return rembg_mask if model_name() == "rembg" else grabcut_mask


def _enhance(image: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    light, a, b = cv2.split(lab)
    light = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(light)
    image = cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2BGR).astype(np.float32)
    # 화이트밸런스: 음식 영역만 보고(흰 접시·배경이 섞이면 파랗게 뜬다), 채널마다 ±6%까지만,
    # 그것도 절반만 맞춘다(다 맞추면 음식 고유색까지 지운다).
    food = image[alpha >= 128] if np.count_nonzero(alpha >= 128) else image.reshape(-1, 3)
    means = food.reshape(-1, 3).mean(axis=0)
    gain = np.clip(means.mean() / np.maximum(means, 1), 0.94, 1.06)
    image *= 1 + (gain - 1) * 0.5
    hsv = cv2.cvtColor(np.clip(image, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(
        np.float32
    )
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.12, 0, 255)
    image = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    image = 255 * (image / 255) ** 0.9  # 밝게 살짝(감마): 메뉴판 사진은 대개 어둡다
    blur = cv2.GaussianBlur(image, (0, 0), 2.0)
    return np.clip(image * 1.4 - blur * 0.4, 0, 255).astype(np.uint8)


def studio(
    data: bytes,
    *,
    mask: Mask | None = None,
    enhance: bool = True,
    shadow: bool = True,
    size: int = 1024,
) -> bytes:
    """음식 사진 → 흰 배경 정사각형 JPEG(색 보정·가운데 정렬·바닥 그림자)."""
    image = _decode(data)
    try:
        alpha = (mask or default_mask())(image)
    except (cv2.error, ImageCutoutError, ValueError):
        alpha = np.full(image.shape[:2], 255, np.uint8)
    if alpha.shape != image.shape[:2]:
        alpha = cv2.resize(alpha, (image.shape[1], image.shape[0]))
    ratio = float(np.count_nonzero(alpha >= 128)) / alpha.size
    if not 0.03 <= ratio <= 0.95:
        alpha = np.full(image.shape[:2], 255, np.uint8)  # 못 나눴으면 사진 전체를 쓴다
    if enhance:
        image = _enhance(image, alpha)
    # 가장자리 1px 안쪽으로 줄이고 부드럽게: 배경 테두리 잔상을 없앤다
    alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8))
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.6)
    x, y, w, h = cv2.boundingRect((alpha >= 128).astype(np.uint8))
    if w < 2 or h < 2:
        x, y, w, h = 0, 0, image.shape[1], image.shape[0]
    piece = image[y : y + h, x : x + w].astype(np.float32)
    piece_alpha = alpha[y : y + h, x : x + w].astype(np.float32)[..., None] / 255

    inner = size * 0.76  # 여백 12%씩
    scale = inner / max(w, h)
    pw, ph = max(1, round(w * scale)), max(1, round(h * scale))
    piece = cv2.resize(piece, (pw, ph), interpolation=cv2.INTER_AREA)
    piece_alpha = cv2.resize(piece_alpha, (pw, ph), interpolation=cv2.INTER_AREA)[..., None]
    left = (size - pw) // 2
    top = (size - ph) // 2 - round(size * 0.02)  # 그림자 자리만큼 살짝 위로

    canvas = np.full((size, size, 3), 255, np.float32)
    if shadow:
        layer = np.zeros((size, size), np.float32)
        center = (size // 2, min(size - 1, top + ph - round(ph * 0.02)))
        axes = (max(1, round(pw * 0.42)), max(1, round(ph * 0.06)))
        cv2.ellipse(layer, center, axes, 0, 0, 360, 1.0, -1)
        layer = cv2.GaussianBlur(layer, (0, 0), max(4, math.ceil(size * 0.025)))
        canvas *= (1 - 0.22 * layer)[..., None]
    region = canvas[top : top + ph, left : left + pw]
    canvas[top : top + ph, left : left + pw] = piece * piece_alpha + region * (1 - piece_alpha)
    ok, encoded = cv2.imencode(
        ".jpg", np.clip(canvas, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 92]
    )
    if not ok:
        raise ImageCutoutError("결과 사진을 만들지 못했어요.")
    return encoded.tobytes()
