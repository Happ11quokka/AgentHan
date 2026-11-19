"""학습 모델·외부 API 없이 GrabCut으로 음식 사진을 흰 배경에 놓는다."""

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_SIDE = 1024


class ImageCutoutError(ValueError):
    """사진을 처리할 수 없는 입력이다. 메시지는 사장님에게 보여 줄 수 있다."""


@dataclass(frozen=True)
class CutoutResult:
    image: bytes
    mime: str
    ok: bool
    reason: str
    fg_ratio: float


def _validate_box(box: tuple[float, float, float, float] | None) -> tuple:
    if box is None:
        return (0.08, 0.08, 0.92, 0.92)
    try:
        x0, y0, x1, y1 = box
        valid = all(math.isfinite(v) for v in box) and 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ImageCutoutError("음식 위치는 0~1 사이의 (x0, y0, x1, y1)로 지정해 주세요.")
    return (x0, y0, x1, y1)


def _foreground_mask(image: np.ndarray, box: tuple) -> np.ndarray:
    """가장 큰 영역의 구멍을 채운 뒤 1~2px 부드럽게 한 원본 좌표 마스크."""
    height, width = image.shape[:2]
    scale = min(1.0, _MAX_SIDE / max(height, width))
    small = cv2.resize(
        image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
    )
    h, w = small.shape[:2]
    x0, y0, x1, y1 = box
    # 힌트가 사진 전체여도 GrabCut에 필요한 확정 배경 테두리를 남긴다.
    left, top = max(1, int(x0 * w)), max(1, int(y0 * h))
    right, bottom = min(w - 1, math.ceil(x1 * w)), min(h - 1, math.ceil(y1 * h))
    if right - left < 2 or bottom - top < 2:
        raise ImageCutoutError("음식 위치가 너무 작아요. 범위를 넓혀 주세요.")
    labels = np.zeros((h, w), np.uint8)
    cv2.grabCut(
        small,
        labels,
        (left, top, right - left, bottom - top),
        np.zeros((1, 65), np.float64),
        np.zeros((1, 65), np.float64),
        5,
        cv2.GC_INIT_WITH_RECT,
    )
    mask = np.isin(labels, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count > 1:
        largest = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
        mask = (components == largest).astype(np.uint8) * 255
        padded = np.pad(mask, 1)
        outside = padded.copy()
        cv2.floodFill(outside, None, (0, 0), 255)
        mask = (padded | cv2.bitwise_not(outside))[1:-1, 1:-1]
    mask = cv2.GaussianBlur(mask, (5, 5), 0.8)
    return cv2.resize(mask, (width, height), interpolation=cv2.INTER_LINEAR)


def _square(image: np.ndarray, size: int) -> np.ndarray:
    height, width = image.shape[:2]
    side = max(height, width)
    canvas = np.full((side, side, 3), 255, np.uint8)
    top, left = (side - height) // 2, (side - width) // 2
    canvas[top : top + height, left : left + width] = image
    return cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)


def cutout_on_white(
    data: bytes,
    *,
    box: tuple[float, float, float, float] | None = None,
    size: int = 1024,
    fmt: str = "JPEG",
) -> CutoutResult:
    """EXIF 회전 반영 → GrabCut 5회 → 흰 배경·8% 여백의 정사각형 바이트.

    box는 회전 반영 후 사진 좌표다. 전경 비율이 3% 미만/90% 초과거나
    GrabCut이 실패하면 원본에 정사각형 흰 여백만 더해 ok=False로 반환한다.
    출력 형식은 JPEG(JPG) 또는 PNG이며 파일 저장·네트워크 호출은 하지 않는다.
    """
    if not data:
        raise ImageCutoutError("사진이 비어 있어요.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageCutoutError("사진은 20MB 이하로 올려 주세요.")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ImageCutoutError("결과 사진 크기는 양의 정수로 지정해 주세요.")
    formats = {
        "JPEG": (".jpg", "image/jpeg"),
        "JPG": (".jpg", "image/jpeg"),
        "PNG": (".png", "image/png"),
    }
    if not isinstance(fmt, str) or fmt.upper() not in formats:
        raise ImageCutoutError("결과 사진 형식은 JPEG 또는 PNG로 지정해 주세요.")
    extension, mime = formats[fmt.upper()]
    hint = _validate_box(box)
    try:
        # COLOR는 EXIF 방향을 자동 반영한다(UNCHANGED/IGNORE_ORIENTATION 사용 금지).
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise ImageCutoutError("사진을 읽지 못했어요. 올바른 이미지 파일을 올려 주세요.") from exc
    if image is None:
        raise ImageCutoutError("사진을 읽지 못했어요. 올바른 이미지 파일을 올려 주세요.")
    if min(image.shape[:2]) < 64:
        raise ImageCutoutError("사진의 가로와 세로는 모두 64px 이상이어야 해요.")

    reason = ""
    ratio = 0.0
    try:
        mask = _foreground_mask(image, hint)
        ratio = float(np.count_nonzero(mask >= 128) / mask.size)
        if ratio < 0.03:
            reason = "음식 영역이 3% 미만이라 배경을 지우지 못했어요."
        elif ratio > 0.90:
            reason = "음식 영역이 90%를 넘어 배경을 구분하지 못했어요."
    except cv2.error:
        reason = "사진의 배경을 구분하지 못했어요. 음식 위치를 지정해 다시 시도해 주세요."

    if reason:
        output = _square(image, size)
    else:
        alpha = mask.astype(np.float32)[..., None] / 255
        composite = np.rint(image * alpha + 255 * (1 - alpha)).astype(np.uint8)
        x, y, w, h = cv2.boundingRect((mask >= 128).astype(np.uint8))
        margin = max(1, math.ceil(max(w, h) * 0.08))
        # 사진 밖으로 나가는 여백도 흰색으로 유지한다.
        crop = np.full((h + 2 * margin, w + 2 * margin, 3), 255, np.uint8)
        left, top = max(0, x - margin), max(0, y - margin)
        right = min(image.shape[1], x + w + margin)
        bottom = min(image.shape[0], y + h + margin)
        offset_x, offset_y = left - x + margin, top - y + margin
        crop[offset_y : offset_y + bottom - top, offset_x : offset_x + right - left] = composite[
            top:bottom, left:right
        ]
        output = _square(crop, size)

    encoded, buffer = cv2.imencode(extension, output)
    if not encoded:
        raise ImageCutoutError("결과 사진을 만들지 못했어요.")
    return CutoutResult(buffer.tobytes(), mime, not reason, reason, ratio)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="음식 사진 배경을 지우고 흰 정사각형으로 만듭니다."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--box", help="회전 반영 후 음식 위치: x0,y0,x1,y1 (0~1)")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve() or (
        args.input.exists() and args.output.exists() and args.input.samefile(args.output)
    ):
        parser.error("원본을 보존하려면 다른 출력 경로를 지정해 주세요.")
    try:
        box = tuple(float(v) for v in args.box.split(",")) if args.box else None
        fmt = "PNG" if args.output.suffix.lower() == ".png" else "JPEG"
        result = cutout_on_white(args.input.read_bytes(), box=box, fmt=fmt)
        args.output.write_bytes(result.image)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"{'완료' if result.ok else result.reason} (전경 {result.fg_ratio:.1%})")


if __name__ == "__main__":
    main()
