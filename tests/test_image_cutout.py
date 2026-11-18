"""합성 사진으로 배경 분리·좌표·실패 시 원본 보존을 오프라인 검증한다."""

import math
import struct
import sys
import time

import cv2
import numpy as np
import pytest

from agenthan.tools import image_cutout
from agenthan.tools.image_cutout import ImageCutoutError, cutout_on_white


def encode(image, extension=".png"):
    ok, data = cv2.imencode(extension, image)
    assert ok
    return data.tobytes()


def decode(data):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def scene(width=384, height=320, *, center=None, radius=None):
    rng = np.random.default_rng(10)
    yy, xx = np.indices((height, width))
    stripes = ((xx // 9 + yy // 17) % 2) * 25
    background = np.array([80, 130, 170]) + stripes[..., None]
    image = np.clip(background + rng.integers(-12, 13, (height, width, 3)), 0, 255).astype(np.uint8)
    center = center or (width // 2, height // 2)
    radius = radius or min(width, height) // 4
    truth = np.zeros((height, width), np.uint8)
    cv2.circle(truth, center, radius, 255, -1)
    cv2.circle(image, center, radius, (225, 85, 40), -1)
    for dx, dy in [(-0.3, -0.3), (0.3, -0.1), (0, 0.4)]:
        point = (int(center[0] + dx * radius), int(center[1] + dy * radius))
        cv2.circle(image, point, radius // 5, (30, 65, 220), -1)
    return image, truth


def capture_mask(monkeypatch):
    masks = []
    original = image_cutout._foreground_mask

    def capture(*args):
        mask = original(*args)
        masks.append(mask)
        return mask

    monkeypatch.setattr(image_cutout, "_foreground_mask", capture)
    return masks


def assert_iou(mask, truth):
    actual, expected = mask >= 128, truth > 0
    iou = np.count_nonzero(actual & expected) / np.count_nonzero(actual | expected)
    assert iou >= 0.8, f"IoU={iou:.3f}"


def assert_output_iou(output, truth):
    """정답 영역의 8% 여백·중앙 배치를 역변환해 최종 인코딩 사진을 검증한다."""
    x, y, width, height = cv2.boundingRect(truth)
    margin = math.ceil(max(width, height) * 0.08)
    side = max(width, height) + 2 * margin
    foreground = np.any(output < 245, axis=2).astype(np.uint8) * 255
    square = cv2.resize(foreground, (side, side), interpolation=cv2.INTER_NEAREST)
    left = x - margin - (side - width - 2 * margin) // 2
    top = y - margin - (side - height - 2 * margin) // 2
    right, bottom = min(truth.shape[1], left + side), min(truth.shape[0], top + side)
    clipped_left, clipped_top = max(0, left), max(0, top)
    restored = np.zeros_like(truth)
    restored[clipped_top:bottom, clipped_left:right] = square[
        clipped_top - top : bottom - top, clipped_left - left : right - left
    ]
    assert_iou(restored, truth)


@pytest.mark.parametrize("fmt, mime", [("JPEG", "image/jpeg"), ("PNG", "image/png")])
def test_cutout_square_white_corners_and_iou(fmt, mime):
    image, truth = scene()
    data = encode(image)
    result = cutout_on_white(data, size=256, fmt=fmt)
    output = decode(result.image)
    assert result.ok and result.reason == "" and result.mime == mime
    assert output.shape == (256, 256, 3)
    assert np.all(output[[0, 0, -1, -1], [0, -1, 0, -1]] >= 245)
    assert_output_iou(output, truth)
    assert abs(result.fg_ratio - np.count_nonzero(truth) / truth.size) < 0.01
    y, x = np.nonzero(np.any(output < 230, axis=2))
    assert abs((x.min() + x.max()) / 2 - 127.5) < 2
    assert abs((y.min() + y.max()) / 2 - 127.5) < 2
    assert data == encode(image)  # 입력 바이트는 변경하지 않는다.


def test_corner_food_with_box(monkeypatch):
    image, truth = scene(480, 400, center=(68, 70), radius=56)
    masks = capture_mask(monkeypatch)
    result = cutout_on_white(encode(image), box=(0, 0, 0.28, 0.34), fmt="PNG")
    assert result.ok
    assert_iou(masks[0], truth)
    output = decode(result.image)
    assert output.shape == (1024, 1024, 3)
    assert np.all(output[[0, 0, -1, -1], [0, -1, 0, -1]] == 255)
    assert_output_iou(output, truth)


def test_large_input_mask_restored_to_original_coordinates(monkeypatch):
    image, truth = scene(1500, 1100)
    masks = capture_mask(monkeypatch)
    result = cutout_on_white(encode(image), size=300)
    assert result.ok
    assert masks[0].shape == truth.shape
    assert_iou(masks[0], truth)
    assert_output_iou(decode(result.image), truth)


def test_uniform_image_falls_back_to_original_with_white_padding():
    image = np.full((80, 160, 3), (35, 110, 180), np.uint8)
    result = cutout_on_white(encode(image), size=160, fmt="PNG")
    output = decode(result.image)
    assert not result.ok and result.reason and result.fg_ratio < 0.03
    assert np.all(output[:40] == 255) and np.all(output[120:] == 255)
    np.testing.assert_array_equal(output[40:120], image)


@pytest.mark.parametrize("ratio", [0.02, 0.91])
def test_implausible_ratio_keeps_original(monkeypatch, ratio):
    image, _ = scene(100, 100)
    mask = np.zeros((100, 100), np.uint8)
    mask.flat[: int(mask.size * ratio)] = 255
    monkeypatch.setattr(image_cutout, "_foreground_mask", lambda *args: mask)
    result = cutout_on_white(encode(image), size=100, fmt="PNG")
    assert not result.ok and result.reason and result.fg_ratio == ratio
    np.testing.assert_array_equal(decode(result.image), image)


def test_grabcut_error_returns_photo(monkeypatch):
    def fail(*args):
        raise cv2.error("segmentation failed")

    monkeypatch.setattr(cv2, "grabCut", fail)
    result = cutout_on_white(encode(scene()[0]))
    assert not result.ok and result.reason
    assert decode(result.image).shape == (1024, 1024, 3)


def test_largest_component_kept_and_holes_filled(monkeypatch):
    image = np.zeros((100, 100, 3), np.uint8)

    def segmented(_image, labels, *args):
        labels[20:70, 20:70] = cv2.GC_PR_FGD
        labels[35:50, 35:50] = cv2.GC_BGD  # 접시 안의 구멍
        labels[80:90, 80:90] = cv2.GC_PR_FGD  # 떨어진 작은 덩어리

    monkeypatch.setattr(cv2, "grabCut", segmented)
    mask = image_cutout._foreground_mask(image, (0.08, 0.08, 0.92, 0.92))
    assert mask[42, 42] == 255
    assert mask[85, 85] == 0
    assert 0 < mask[20, 20] < 255  # 경계는 부드럽게 처리한다.


@pytest.mark.parametrize(
    "data, message",
    [
        (b"", "비어"),
        (b"this is not a photo", "읽지"),
        (b"x" * (20 * 1024 * 1024 + 1), "20MB"),
        (encode(np.zeros((63, 128, 3), np.uint8)), "64px"),
        (encode(np.zeros((128, 63, 3), np.uint8)), "64px"),
    ],
)
def test_invalid_image(data, message):
    with pytest.raises(ImageCutoutError, match=message):
        cutout_on_white(data)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"box": (-0.1, 0, 1, 1)},
        {"box": (0.8, 0, 0.2, 1)},
        {"box": (0, 0, float("nan"), 1)},
        {"box": (0, 1)},
        {"box": (0.5, 0.5, 0.5001, 0.5001)},
        {"size": 0},
        {"size": 1.5},
        {"fmt": "TIFF"},
    ],
)
def test_invalid_options(kwargs):
    with pytest.raises(ImageCutoutError):
        cutout_on_white(encode(scene()[0]), **kwargs)


def test_exif_rotation_is_applied_before_box():
    image, _ = scene(400, 280, center=(85, 90), radius=64)
    jpeg = encode(image, ".jpg")
    # Pillow 의존성 없이 Orientation=6(시계 방향 90도) EXIF APP1을 넣는다.
    tiff = b"II" + struct.pack("<HIH", 42, 8, 1)
    tiff += struct.pack("<HHI", 0x112, 3, 1) + struct.pack("<H", 6) + b"\0\0"
    tiff += struct.pack("<I", 0)
    exif = b"Exif\0\0" + tiff
    rotated_jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
    upright = cv2.rotate(decode(jpeg), cv2.ROTATE_90_CLOCKWISE)
    np.testing.assert_array_equal(decode(rotated_jpeg), upright)
    # 회전 뒤 음식은 오른쪽 위다. 실제 픽셀을 회전한 사진과 결과를 비교한다.
    hint = (0.40, 0.01, 0.94, 0.40)
    cv2.setRNGSeed(10)
    actual = cutout_on_white(rotated_jpeg, box=hint, size=200, fmt="PNG")
    cv2.setRNGSeed(10)
    expected = cutout_on_white(encode(upright), box=hint, size=200, fmt="PNG")
    assert actual.ok and expected.ok
    np.testing.assert_allclose(decode(actual.image), decode(expected.image), atol=2)


def test_cli_preserves_input_and_writes_output(tmp_path, monkeypatch, capsys):
    source, target = tmp_path / "in.png", tmp_path / "out.jpg"
    data = encode(scene()[0])
    source.write_bytes(data)
    monkeypatch.setattr(
        sys, "argv", ["image_cutout", str(source), str(target), "--box", "0.1,0.1,0.9,0.9"]
    )
    image_cutout.main()
    assert source.read_bytes() == data
    assert decode(target.read_bytes()).shape == (1024, 1024, 3)
    assert "완료" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["image_cutout", str(source), str(source)])
    with pytest.raises(SystemExit):
        image_cutout.main()
    assert source.read_bytes() == data


def test_1024_photo_performance(record_property):
    data = encode(scene(1024, 1024)[0])
    start = time.perf_counter()
    result = cutout_on_white(data)
    elapsed = time.perf_counter() - start
    record_property("cutout_seconds", elapsed)
    print(f"1024px GrabCut: {elapsed:.3f}s")
    assert result.ok
    assert elapsed < 10, f"1024px 처리에 {elapsed:.3f}초 걸림"
