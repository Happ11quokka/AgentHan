"""메뉴 사진 다듬기(2차): HyperCLOVA X SEED Omni로 흰 배경 제품 사진을 다시 그린다.

1차 흰 배경 사진은 OpenCV로 잘라 바로 만든다(agent/menu_board.py). 사장님이 "사진 더 예쁘게"처럼
따로 부탁할 때만 이 2차를 부른다. 이미지 한 장에 수십 초가 걸려서(GPU, 추정 30~80초) 메뉴판을
올릴 때마다 돌리지 않는다.

모델은 네이버 오픈 가중치 `naver-hyperclovax/HyperCLOVAX-SEED-Omni-8B`를 우리 GPU 서버에 올려
쓴다(외부 API 아님, PRD "네이버 클라우드 모델만"). GPU 서버 쪽은 작은 HTTP 어댑터 하나만 둔다:

    POST {AGENTHAN_IMAGE_POLISH_URL}
    Authorization: Bearer {AGENTHAN_IMAGE_POLISH_TOKEN}   (있으면)
    {"prompt": "...", "image": "<base64 JPEG/PNG>"}
    → {"image": "<base64 PNG/JPEG>"}

AGENTHAN_IMAGE_POLISH_URL이 없으면 꺼져 있다: 비서는 "준비 중"이라고 답하고 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

URL_ENV = "AGENTHAN_IMAGE_POLISH_URL"
TOKEN_ENV = "AGENTHAN_IMAGE_POLISH_TOKEN"
TIMEOUT = 180  # 초. GPU 한 장 생성이 길다.

PROMPT = (
    "이 음식 사진을 배달앱 메뉴 사진처럼 다듬어 주세요. 음식의 모양·색·담음새는 그대로 두고, "
    "배경만 깨끗한 순백색으로, 부드러운 그림자와 밝은 조명의 정사각형 제품 사진으로 만드세요. "
    "음식: {name}"
)


class ImagePolishError(Exception):
    """사장님께 그대로 보여도 되는 오류."""


def enabled() -> bool:
    return bool(os.environ.get(URL_ENV, "").strip())


def _mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    raise ImagePolishError("다듬은 사진 형식을 알 수 없어요.")


def polish(photo: bytes, name: str, *, timeout: float = TIMEOUT) -> tuple[bytes, str]:
    """1차 흰 배경 사진 → (다듬은 사진 바이트, MIME)."""
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        raise ImagePolishError("사진 다듬기는 아직 준비 중이에요.")
    body = json.dumps(
        {"prompt": PROMPT.format(name=name), "image": base64.b64encode(photo).decode()}
    ).encode()
    headers = {"Content-Type": "application/json"}
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 설정한 주소만
            raw = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise ImagePolishError("사진을 다듬지 못했어요. 잠시 뒤 다시 시도해 주세요.") from error
    encoded = raw.get("image") if isinstance(raw, dict) else None
    if not isinstance(encoded, str):
        raise ImagePolishError("사진을 다듬지 못했어요. 잠시 뒤 다시 시도해 주세요.")
    try:
        image = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ImagePolishError("사진을 다듬지 못했어요. 잠시 뒤 다시 시도해 주세요.") from error
    return image, _mime(image)
