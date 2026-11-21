"""메뉴판 사진 → 메뉴 탭에 바로 등록, 설명·흰 배경 사진은 뒤에서 채운다.

예전 흐름(menu_photo.draft_menus)은 분류 1번 → 읽기 1번 → 새 메뉴마다 설명 1번을 차례로 부르고,
사장님이 대화로 승인해야 저장했다. 시연에서 가장 느리게 느껴지는 단계라 이렇게 바꿨다
(docs/research/07-menu-photo-speed.md):

1. 읽기: 분류와 메뉴 읽기를 HCX-005 한 번으로 합친다(분류만 2.7초 들었다). 메뉴마다 그 메뉴의
   음식 사진 영역(box)도 같이 받는다. 사진은 긴 변 1280px로 줄여 보낸다(1024px 이상은 서버가
   같은 토큰 수로 줄여 읽어서 큰 사진은 시간만 더 든다).
2. 등록: 이름·가격이 있는 메뉴는 곧바로 가게 저장소에 올린다. 가격이 안 보이는 줄은 올리지 않고
   대화로 알려 달라고 한다(저장소는 가격 없는 메뉴를 받지 않는다).
3. 채우기(백그라운드): 메뉴마다 동시에 한국어 이름·설명(HCX-005)과 흰 배경 사진(OpenCV)을 만든다.
   다 된 칸부터 저장소에 쓰고, 화면은 /place를 다시 읽어 채워지는 모습을 본다.

자동으로 채운 칸은 ai에 남아 메뉴 탭에 점선 밑줄로 보이고, 사장님이 고치면 지워진다.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from agenthan.agent import dish_glossary, menu_photo
from agenthan.core.contracts import StoreKnowledge
from agenthan.core.llm import ClovaV3Error, chat_v3, image_content
from agenthan.tools.dummy_store import DummyStore, DummyStoreError
from agenthan.tools.image_cutout import ImageCutoutError, cutout_on_white

log = logging.getLogger(__name__)

READ_SIDE = 1280  # 메뉴판 읽기용 긴 변 (07-menu-photo-speed.md 3번)
WORKERS = 8  # 설명·사진 채우기 동시 실행 수 (메뉴 8개면 한 번에, 서비스 키 기준)

BOARD_PROMPT = (
    "사진을 보고 JSON 하나만 답하라.\n"
    '- 메뉴판(메뉴 이름과 가격이 적힌 판·전단·화면)이면 {"kind": "menu_board", "items": [...], '
    '"photos": [...]}\n'
    '- 음식만 찍은 사진이면 {"kind": "food"}\n'
    '- 그 밖이면 {"kind": "other"}\n'
    "items: 가격과 함께 적힌 메뉴마다 하나. "
    '{"name": 사진에 적힌 글자 그대로(번역하거나 다른 단어로 바꾸지 마라), '
    '"price": 원 단위 정수, 안 보이면 null}\n'
    "photos: 메뉴판 안의 음식 사진마다 하나. "
    '{"box": [x0, y0, x1, y1] (사진 전체 대비 0~1 비율), '
    '"label": 그 음식 사진 바로 아래나 옆에 적힌 메뉴 이름 그대로, 없으면 null}\n'
    "음식 사진에 붙은 이름표는 items에 다시 쓰지 마라. 같은 메뉴를 두 번 쓰지 마라."
)

Kind = Literal["menu_board", "food", "other"]
Box = tuple[float, float, float, float]
Call = Callable[[str, dict], dict]


@dataclass
class BoardItem:
    name: str
    price: int | None
    box: Box | None
    # 이름 옆 괄호 안 풀이("메도빅 (꿀 케이크)"의 "꿀 케이크"). 설명을 쓸 때 참고한다.
    subtitle: str = ""
    # 이 메뉴에 붙은 음식 사진의 이름표(사진 위치를 다시 물을 때 쓴다)
    photo_label: str = ""


SUBTITLE = re.compile(r"^(?P<name>.+?)\s*[(\[（]\s*(?P<sub>[^()\[\]（）]+?)\s*[)\]）]\s*$")


def split_subtitle(name: str) -> tuple[str, str]:
    """'메도빅 (꿀 케이크)' → ('메도빅', '꿀 케이크'). 괄호가 없으면 (이름, '')."""
    match = SUBTITLE.match(name)
    if not match or not match["name"].strip():
        return name, ""
    return " ".join(match["name"].split()), " ".join(match["sub"].split())


def _box(value: object) -> Box | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(v) not in (int, float) for v in value):
        return None
    x0, y0, x1, y1 = (float(v) for v in value)
    if max(x0, y0, x1, y1) > 1.0:  # 픽셀로 답한 경우는 버린다(사진 크기를 모른다)
        return None
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) or (x1 - x0) * (y1 - y0) < 0.004:
        return None
    return (x0, y0, x1, y1)


def read_board(data: bytes, *, call: Call = chat_v3) -> tuple[Kind, list[BoardItem]]:
    """사진 한 장 → (종류, 메뉴판이면 메뉴 줄). HCX-005 한 번."""
    small = shrink(data, READ_SIDE)
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    image_content(small, menu_photo.detect_mime(small)),
                    {"type": "text", "text": BOARD_PROMPT},
                ],
            }
        ],
        "maxTokens": 1500,
        "temperature": 0.1,
    }
    started = time.monotonic()
    try:
        text = menu_photo._content(call(menu_photo.VISION_MODEL, body))
    except ClovaV3Error as error:
        raise menu_photo.MenuPhotoError(str(error)) from None
    finally:
        log.info("menu board read %.2fs", time.monotonic() - started)
    try:
        raw = menu_photo._json_block(text, "{")
    except ValueError:
        # JSON 없이 종류만 한 단어로 답하는 때가 있다("food", "other").
        word = text.strip().strip("`\"'. ").lower()
        if word in ("food", "other"):
            return word, []
        raise menu_photo.MenuPhotoError(
            "사진에서 메뉴를 읽지 못했어요. 더 밝고 반듯한 사진으로 다시 보내 주세요."
        ) from None
    if not isinstance(raw, dict):
        return "other", []
    kind = raw.get("kind")
    if kind not in ("menu_board", "food", "other"):
        kind = "other"
    items: list[BoardItem] = []
    seen: set[str] = set()
    for row in raw.get("items") or [] if kind == "menu_board" else []:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        name, subtitle = split_subtitle(" ".join(row["name"].split()))
        key = menu_photo._norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        price = menu_photo.parse_price(row.get("price"))
        items.append(BoardItem(name, price, _box(row.get("box")), subtitle))
    if kind == "menu_board" and not items:
        kind = "other"
    items = items[: menu_photo.MAX_ITEMS]
    attach_photos(items, raw.get("photos") if kind == "menu_board" else None)
    return kind, items


LABEL_MIN = 0.6  # 사진 이름표 ↔ 메뉴 이름 글자 유사도 (Медовник↔Медовик 0.93)


def _label_score(label: str, name: str) -> float:
    """이름표와 메뉴 이름의 닮은 정도. 한쪽이 다른 쪽에 통째로 들어 있으면 1
    (메도빅 ↔ 메도빅 케이크)."""
    key = menu_photo._norm(name)
    if not key:
        return 0.0
    if label in key or key in label:
        return 1.0
    return difflib.SequenceMatcher(None, label, key).ratio()


def attach_photos(items: list[BoardItem], photos: object) -> None:
    """메뉴판 안 음식 사진을 이름표로 메뉴 줄에 붙인다.

    메뉴마다 사진 영역을 바로 물으면 엉뚱한 메뉴에 붙었다(실측 2026-11-21: 메도빅 사진이 보르시에).
    사진 밑 이름표는 모델이 잘 읽으므로 사진마다 이름표를 받아 글자 유사도로 가장 가까운 메뉴에
    붙인다. 이름표가 없거나 맞는 메뉴가 없는 사진은 버린다."""
    if not isinstance(photos, list):
        return
    taken: set[int] = set()
    for photo in photos:
        if not isinstance(photo, dict) or not isinstance(photo.get("label"), str):
            continue
        box = _box(photo.get("box"))
        label = menu_photo._norm(split_subtitle(photo["label"])[0])
        if box is None or not label:
            continue
        scores = [
            (_label_score(label, item.name), i) for i, item in enumerate(items) if i not in taken
        ]
        score, best = max(scores, default=(0.0, -1))
        if score >= LABEL_MIN:
            items[best].box = box
            items[best].photo_label = photo["label"].strip()
            taken.add(best)


def shrink(data: bytes, side: int) -> bytes:
    """긴 변을 side 이하 JPEG로. 읽을 수 없으면 그대로."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return data
    height, width = image.shape[:2]
    if max(height, width) <= side:
        return data
    scale = side / max(height, width)
    image = cv2.resize(
        image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
    )
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return encoded.tobytes() if ok else data


# ---------------------------------------------------------------- 음식 사진 자르기


def photo_regions(data: bytes) -> list[Box]:
    """메뉴판에서 사진처럼 보이는 네모 영역(윤곽선). HCX-005 box보다 테두리가 정확하다
    (IoU 0.8 대 0.5~0.65, 07-menu-photo-speed.md 6번). 어느 메뉴 사진인지는 모른다."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return []
    height, width = image.shape[:2]
    scale = min(1.0, 800 / max(height, width))
    small = cv2.resize(image, (round(width * scale), round(height * scale)))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    # 사진 칸은 글자 칸보다 질감이 많다: 경계를 굵게 잇고 바깥 윤곽만 본다.
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = small.shape[:2]
    boxes = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        area = bw * bh / (w * h)
        if 0.01 <= area <= 0.5 and 0.4 <= bw / max(bh, 1) <= 2.5:
            boxes.append((x / w, y / h, (x + bw) / w, (y + bh) / h))
    return boxes


def _iou(a: Box, b: Box) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def snap(box: Box, regions: list[Box]) -> Box:
    """모델 box를 가장 많이 겹치는 윤곽선 네모로 바꾼다. 마땅한 게 없으면 그대로."""
    best = max(regions, key=lambda r: _iou(box, r), default=None)
    return best if best is not None and _iou(box, best) >= 0.3 else box


def crop(data: bytes, box: Box, margin: float = 0.02) -> bytes:
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ImageCutoutError("사진을 읽지 못했어요.")
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    left, top = max(0, int((x0 - margin) * width)), max(0, int((y0 - margin) * height))
    right = min(width, int((x1 + margin) * width + 0.5))
    bottom = min(height, int((y1 + margin) * height + 0.5))
    ok, encoded = cv2.imencode(
        ".jpg", image[top:bottom, left:right], [cv2.IMWRITE_JPEG_QUALITY, 92]
    )
    if not ok:
        raise ImageCutoutError("사진을 자르지 못했어요.")
    return encoded.tobytes()


def white_photo(board: bytes, box: Box, regions: list[Box], cutout=None) -> tuple[bytes, bytes]:
    """메뉴판의 음식 사진 칸을 잘라 흰 배경 정사각형으로. (흰 배경 사진, 자른 원본 조각).
    배경을 못 지우면 자른 사진에 흰 여백만 둘러 쓴다(메뉴판 사진은 이미 음식 사진이라 쓸 만하다).

    윤곽선 칸에 맞춰졌으면 칸 테두리에 딱 맞게 자른다: 메뉴판 바탕이 섞이지 않아야 GrabCut이
    사진 속 식탁·나무 바탕을 배경으로 잡는다(여백 2%면 바탕이 남았다, 실측 2026-11-21).
    512px·5회: 사진 한 장 0.2~0.5초. 오픈 배경 제거 모델을 켰으면(AGENTHAN_CUTOUT_MODEL=rembg)
    그 마스크로 자른다."""
    from agenthan.tools import image_studio

    snapped = snap(box, regions)
    piece = crop(board, snapped, 0.0 if snapped in regions else 0.02)
    if cutout is None and image_studio.model_name() == "rembg":
        return image_studio.studio(piece, enhance=False, shadow=False), piece
    return (cutout or cutout_on_white)(piece, work_side=512, iterations=5).image, piece


PHOTOS_PROMPT = (
    "메뉴판 사진이다. 메뉴판 안의 음식 사진마다 하나씩 JSON 배열로만 답하라: "
    '[{"box": [x0, y0, x1, y1] (사진 전체 대비 0~1 비율), "label": 사진 바로 아래나 옆 이름표 글자 '
    "그대로}]. 음식 사진이 없으면 []."
)
REFINE_PROMPT = (
    '메뉴판 일부를 잘라 낸 그림이다. 이름표 "{label}" 바로 위(또는 옆)에 있는 음식 사진 칸 하나의 '
    "위치를 이 그림 기준 0~1 비율로 답하라. 사진 칸 테두리에 딱 맞게, 이름표 글자는 빼고. "
    'JSON 하나만: {{"box": [x0, y0, x1, y1]}}'
)


def _vision_json(data: bytes, prompt: str, opener: str, call: Call, max_tokens: int) -> object:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    image_content(data, menu_photo.detect_mime(data)),
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "maxTokens": max_tokens,
        "temperature": 0.1,
    }
    return menu_photo._json_block(menu_photo._content(call(menu_photo.VISION_MODEL, body)), opener)


def refine_box(board: bytes, box: Box, label: str, *, call: Call = chat_v3) -> Box:
    """첫 읽기의 사진 위치는 어긋난다(이름표 아래 칸이나 장식까지 잡음, 실측 2026-11-21).
    그 둘레를 넉넉히 잘라 이름표 바로 위 사진 칸을 다시 묻는다(약 2.7초). 실패하면 원래 위치."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(board, np.uint8), cv2.IMREAD_COLOR)
    if image is None or not label:
        return box
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    wx0, wy0 = max(0.0, x0 - 0.6 * bw), max(0.0, y0 - 0.6 * bh)
    wx1, wy1 = min(1.0, x1 + 0.6 * bw), min(1.0, y1 + 0.6 * bh)
    piece = image[int(wy0 * height) : int(wy1 * height), int(wx0 * width) : int(wx1 * width)]
    ok, encoded = cv2.imencode(".jpg", piece, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return box
    try:
        raw = _vision_json(encoded.tobytes(), REFINE_PROMPT.format(label=label), "{", call, 120)
    except (ClovaV3Error, menu_photo.MenuPhotoError, ValueError):
        return box
    inner = _box(raw.get("box")) if isinstance(raw, dict) else None
    if inner is None:
        return box
    a, b, c, d = inner
    return (
        wx0 + a * (wx1 - wx0),
        wy0 + b * (wy1 - wy0),
        wx0 + c * (wx1 - wx0),
        wy0 + d * (wy1 - wy0),
    )


def photo_column(boxes: list[Box]) -> tuple[float, float] | None:
    """첫 읽기 사진 위치들로 본 사진 열(가로 범위). 사진이 가로로 퍼져 있으면 None(전체)."""
    if not boxes:
        return None
    x0 = max(0.0, min(b[0] for b in boxes) - 0.12)
    x1 = min(1.0, max(b[2] for b in boxes) + 0.02)
    return (x0, x1) if x1 - x0 <= 0.7 else None


def read_photos(
    board: bytes, *, call: Call = chat_v3, column: tuple[float, float] | None = None
) -> list[tuple[str, Box]]:
    """음식 사진만 다시 읽는다 → [(이름표, 위치)]. column이 있으면 그 세로 띠만 잘라 크게 읽는다.

    첫 읽기는 사진을 자주 빠뜨리고(4장 중 1장, 3번 중 2번) 위치도 어긋났다. 전체를 다시 읽어도
    같은 사진을 빠뜨렸지만, 사진 열만 잘라 읽으면 4장을 모두 찾고 위치도 맞았다(3번 모두, 실측
    2026-11-21)."""
    import cv2
    import numpy as np

    data, x0, x1 = board, 0.0, 1.0
    if column is not None:
        image = cv2.imdecode(np.frombuffer(board, np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            width = image.shape[1]
            x0, x1 = column
            ok, encoded = cv2.imencode(
                ".jpg",
                image[:, int(x0 * width) : int(x1 * width)],
                [cv2.IMWRITE_JPEG_QUALITY, 92],
            )
            if ok:
                data = encoded.tobytes()
            else:
                x0, x1 = 0.0, 1.0
    try:
        raw = _vision_json(shrink(data, READ_SIDE), PHOTOS_PROMPT, "[", call, 600)
    except (ClovaV3Error, menu_photo.MenuPhotoError, ValueError):
        return []
    out = []
    for photo in raw if isinstance(raw, list) else []:
        if not isinstance(photo, dict) or not isinstance(photo.get("label"), str):
            continue
        box = _box(photo.get("box"))
        if box is not None and photo["label"].strip():
            a, b, c, d = box
            out.append((photo["label"].strip(), (x0 + a * (x1 - x0), b, x0 + c * (x1 - x0), d)))
    return out


# ---------------------------------------------------------------- 등록과 채우기


@dataclass
class Registered:
    menu_id: str
    item: BoardItem
    filling: list[str]


@dataclass
class BoardResult:
    created: list[str]  # 새로 올린 메뉴 이름
    updated: list[str]  # 가격이 바뀐 기존 메뉴
    unchanged: list[str]
    no_price: list[str]  # 가격이 안 보여 올리지 않은 줄
    jobs: list[Registered]
    # 사진이 안 붙은 메뉴. 사진만 다시 읽어 빠진 사진을 찾을 때 쓴다(fill_all).
    no_photo: list[Registered] = field(default_factory=list)


def register(store: DummyStore, items: list[BoardItem], knowledge: StoreKnowledge) -> BoardResult:
    """읽은 메뉴를 곧바로 저장소에 올린다. 기존 메뉴(가게 지식)와 맞으면 그 이름으로 가격만
    맞춘다."""
    rows, no_price = [], []
    for item in items:
        if item.price is None:
            no_price.append(item.name)
            continue
        known = menu_photo.match_known(item.name, knowledge)
        photo = ["photo"] if item.box else []
        if known:
            rows.append(({"name": known[0].name_ko, "price": item.price, "filling": photo}, item))
            continue
        dish = dish_glossary.lookup(item.name)
        if dish is not None:
            # 외국 음식 사전: 모델 없이 한국어 이름·원어 이름·설명을 바로 쓴다. 한글로 적힌
            # 이름은 사장님 표기를 그대로 둔다(블리니를 사전 표기 블린으로 바꾸지 않음).
            korean = bool(re.search(r"[가-힣]", item.name))
            name = item.name if korean else dish.ko
            renamed = name != item.name
            rows.append(
                (
                    {
                        "name": name,
                        "price": item.price,
                        "name_native": dish.native,
                        "description": dish.description,
                        "ai": ["description"] + (["name"] if renamed else []),
                        "filling": photo,
                    },
                    item,
                )
            )
            continue
        korean = bool(re.search(r"[가-힣]", item.name))
        rows.append(
            (
                {
                    "name": item.name,
                    "price": item.price,
                    "name_native": menu_photo._native(item.name),
                    # 한글 이름은 그대로 두고 설명만, 원어 이름은 한국어 이름·설명을 만든다.
                    "filling": (["description"] if korean else ["name", "description"]) + photo,
                },
                item,
            )
        )
    saved = store.add_board_menus([r for r, _ in rows])
    result = BoardResult([], [], [], no_price, [])
    for (_, item), row in zip(rows, saved, strict=True):
        record = row["record"]
        getattr(result, row["action"]).append(record["name"])
        if record.get("filling"):
            result.jobs.append(Registered(record["id"], item, list(record["filling"])))
        if not item.box and not record.get("photo"):
            result.no_photo.append(Registered(record["id"], item, []))
    return result


def fill_one(
    store: DummyStore,
    job: Registered,
    board: bytes,
    regions: list[Box],
    store_context: str,
    *,
    call: Call = chat_v3,
    cutout=None,
    menu_list: str = "",
    refine: bool = True,
) -> None:
    """메뉴 한 줄의 남은 칸(이름·설명·사진)을 채워 저장소에 쓴다. 실패한 칸은 비운 채 끝낸다.
    refine: 사진 위치를 잘라 다시 물어 바로잡는다(테스트는 끈다)."""
    tasks: list[Callable[[], None]] = []
    if {"name", "description"} & set(job.filling):

        def text() -> None:
            started = time.monotonic()
            read = f"{job.item.name} ({job.item.subtitle})" if job.item.subtitle else job.item.name
            name_ko, description = menu_photo.localize(
                read, store_context, call=call, menu_list=menu_list
            )
            log.info("menu localize %.2fs %s", time.monotonic() - started, job.item.name)
            fields = {}
            if name_ko and "name" in job.filling:
                fields["name"] = name_ko
            if description:
                fields["description"] = description
            store.patch_menu(job.menu_id, fields, by_owner=False, done=("name", "description"))

        tasks.append(text)
    if "photo" in job.filling and job.item.box:

        def photo() -> None:
            started = time.monotonic()
            try:
                box = job.item.box
                if refine:
                    box = refine_box(board, box, job.item.photo_label or job.item.name, call=call)
                    # 다시 물은 위치도 틀릴 수 있다(이름표·장식 띠를 잡음, 2026-11-21 머랭롤).
                    # 아래 이름표로 확인 못 하면 사진 없이 둔다: 엉뚱한 조각보다 일러스트가 낫다.
                    if not labelled(board, box, job.item.name, call):
                        log.info("menu photo dropped for %s: no matching label", job.item.name)
                        store.patch_menu(job.menu_id, {}, by_owner=False, done=("photo",))
                        return
                image, piece = white_photo(board, box, regions, cutout)
            except (ImageCutoutError, ValueError):
                store.patch_menu(job.menu_id, {}, by_owner=False, done=("photo",))
                return
            log.info("menu cutout %.2fs %s", time.monotonic() - started, job.item.name)
            store.patch_menu(
                job.menu_id,
                {},
                by_owner=False,
                done=("photo",),
                photo=image,
                photo_mime="image/jpeg",
                photo_source=piece,
            )

        tasks.append(photo)
    for task in tasks:
        try:
            task()
        except DummyStoreError:
            log.warning("menu fill failed for %s", job.menu_id, exc_info=True)


def fill_all(
    store: DummyStore,
    jobs: list[Registered],
    board: bytes,
    store_context: str,
    *,
    call: Call = chat_v3,
    cutout=None,
    workers: int = WORKERS,
    menu_list: str = "",
    refine: bool = True,
    no_photo: list[Registered] | None = None,
) -> Callable[[], None]:
    """백그라운드에서 돌릴 함수: 메뉴마다 동시에 채운다. 이름·설명과 사진도 서로 기다리지 않는다."""

    def run() -> None:
        started = time.monotonic()
        regions = photo_regions(board) if any(j.item.box for j in jobs) else []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for job in jobs:
                text = [f for f in job.filling if f in ("name", "description")]
                if text:
                    futures.append(
                        pool.submit(
                            fill_one,
                            store,
                            Registered(job.menu_id, job.item, text),
                            board,
                            regions,
                            store_context,
                            call=call,
                            cutout=cutout,
                            menu_list=menu_list,
                            refine=refine,
                        )
                    )
            # 사진은 한 묶음으로: 사진 열을 다시 읽은 뒤 메뉴마다 자른다(이름·설명과 동시에).
            with_photo = [
                Registered(j.menu_id, j.item, ["photo"]) for j in jobs if "photo" in j.filling
            ]
            if with_photo or no_photo:
                futures.append(
                    pool.submit(
                        place_photos,
                        store,
                        with_photo,
                        list(no_photo or []),
                        board,
                        regions,
                        call,
                        cutout,
                        refine,
                    )
                )
            for future in futures:
                try:
                    future.result()
                except Exception:  # noqa: BLE001 - 한 줄 실패가 나머지를 막지 않게
                    log.exception("menu fill crashed")
        # 첫 읽기가 사진을 빠뜨린 메뉴(no_photo)도 이 일이 사진을 찾아 채운다. 사전 메뉴처럼 처음엔
        # 채울 칸이 없어 jobs에 없을 수 있다(2026-11-21 '3 menus'인데 사진은 4장): 함께 센다.
        touched = list({j.menu_id: j for j in [*jobs, *(no_photo or [])]}.values())
        log.info("menu board fill %.2fs for %d menus", time.monotonic() - started, len(touched))
        # 남은 '채우는 중' 표시는 지운다(실패한 칸이 영원히 돌지 않게). 찾은 사진이 예상 못 한
        # 오류로 죽은 메뉴도.
        for job in touched:
            try:
                store.patch_menu(
                    job.menu_id, {}, by_owner=False, done=("name", "description", "photo")
                )
            except DummyStoreError:
                pass

    return run


def photo_runs(board: bytes, column: tuple[float, float]) -> list[Box]:
    """사진 열 안의 음식 사진 칸을 영상으로 찾는다(위에서부터).

    HCX-005가 주는 사진 위치는 돌릴 때마다 달라지고 한 칸씩 밀렸다(2026-11-21 실측).
    그래서 모델에게서는 사진 열(가로 범위)과 위아래 순서만 받고, 정확한 위치는 여기서 잰다:
    열의 가운데 60%에서 줄마다 '종이 바탕색이 아닌 점'의 비율을 보고, 그 비율이 높은 세로
    구간이 사진이다(글자·이름표 줄은 낮다). 틈이 2% 미만인 구간은 하나로 합치고(흰 만두·크림이
    많은 사진), 가로 폭은 같은 열 사진끼리 맞춘다."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(board, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return []
    height, width = image.shape[:2]
    scale = min(1.0, 900 / max(height, width))
    image = cv2.resize(
        image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
    )
    h, w = image.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(image, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    middle = lab[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4].reshape(-1, 3)
    keys, counts = np.unique((middle // 8).astype(np.int32), axis=0, return_counts=True)
    paper = keys[counts.argmax()] * 8 + 4  # 가장 흔한 색 = 종이 바탕
    far = np.linalg.norm(lab - paper, axis=2) > 30
    x0, x1 = int(column[0] * w), int(column[1] * w)
    strip = far[:, x0:x1]
    span = x1 - x0
    if span < 10:
        return []
    rows = strip[:, int(span * 0.2) : int(span * 0.8)].mean(axis=1)
    rows = np.convolve(rows, np.ones(5) / 5, mode="same")
    on = rows > 0.5
    runs: list[list[int]] = []
    y = 0
    while y < h:
        if not on[y]:
            y += 1
            continue
        end = y
        while end < h and on[end]:
            end += 1
        if runs and (y - runs[-1][1]) / h < 0.02:
            runs[-1][1] = end
        else:
            runs.append([y, end])
        y = end
    runs = [r for r in runs if (r[1] - r[0]) / h >= 0.06]
    cells = []
    for top, bottom in runs:
        dense = strip[top:bottom].mean(axis=0) > 0.6
        # 가장 긴 연속 가로 구간(사진은 한 덩어리, 장식은 듬성듬성)
        best, start = (0, 0), None
        for x, on_x in enumerate([*dense, False]):
            if on_x and start is None:
                start = x
            elif not on_x and start is not None:
                if x - start > best[1] - best[0]:
                    best = (start, x)
                start = None
        a, b = best
        if b - a < span * 0.3:
            continue
        if strip[top:bottom, a:b].mean() < 0.7:  # 장식·글자 덩어리: 꽉 차 있지 않다
            continue
        cells.append((top, bottom, a, b))
    if len(cells) >= 2:  # 같은 열 사진은 폭이 같다
        left = int(np.median([c[2] for c in cells]))
        right = int(np.median([c[3] for c in cells]))
        cells = [(t, btm, left, right) for t, btm, _, _ in cells]
    return [((x0 + a) / w, top / h, (x0 + b) / w, bottom / h) for top, bottom, a, b in cells]


LABEL_PROMPT = (
    '음식 사진 바로 아래 이름표를 잘라 낸 그림이다. 적힌 글자 그대로. JSON 하나만: {"label": "..."}'
)


def read_label(board: bytes, box: Box, *, call: Call = chat_v3) -> str:
    """사진 칸 바로 아래(이름표 자리)를 잘라 글자를 읽는다. 못 읽으면 빈 문자열."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(board, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return ""
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    top, bottom = int(y1 * height), int(min(1.0, y1 + 0.08) * height)
    piece = image[top:bottom, int(x0 * width) : int(x1 * width)]
    if piece.size == 0:
        return ""
    pad = max(0, piece.shape[1] // 3 - piece.shape[0])  # 너무 납작하면 400: 위아래 흰 여백
    piece = cv2.copyMakeBorder(piece, pad // 2, pad - pad // 2, 0, 0, cv2.BORDER_REPLICATE)
    ok, encoded = cv2.imencode(".jpg", piece, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return ""
    try:
        raw = _vision_json(encoded.tobytes(), LABEL_PROMPT, "{", call, 60)
    except (ClovaV3Error, menu_photo.MenuPhotoError, ValueError):
        return ""
    label = raw.get("label") if isinstance(raw, dict) else None
    return label.strip() if isinstance(label, str) else ""


def match_runs(
    targets: list[Registered], runs: list[Box], board: bytes, call: Call
) -> dict[int, Box]:
    """찾은 사진 칸을 메뉴에 맞춘다 → {targets 번호: 칸}. 칸마다 아래 이름표를 읽어(동시에)
    이름으로 맞춘다. 첫 읽기의 사진 위치로는 맞추지 않는다: 한 칸씩 밀려 펠메니 이름에 보르시
    칸이 붙었다(2026-11-21 실측)."""
    out: dict[int, Box] = {}
    if not runs or not targets:
        return out
    with ThreadPoolExecutor(max_workers=len(runs)) as pool:
        labels = list(pool.map(lambda box: read_label(board, box, call=call), runs))
    rest = list(range(len(targets)))
    for box, label in zip(runs, labels, strict=True):
        if not label or not rest:
            continue
        key = menu_photo._norm(split_subtitle(label)[0])
        score, best = max((_label_score(key, targets[i].item.name), i) for i in rest)
        if score >= LABEL_MIN:
            out[best] = box
            targets[best].item.photo_label = label
            rest.remove(best)
    return out


def pitch_cells(cells: list[Box]) -> list[Box]:
    """이름표로 맞춘 사진 칸들의 간격으로, 재기가 놓친 칸이 있을 자리를 짐작한다(위에서부터).

    흰 크림·흰 접시 사진은 종이색과 비슷해 photo_runs가 놓친다(2026-11-21 머랭롤). 한 열의 사진
    칸은 같은 크기로 같은 간격이라, 맞춘 칸들의 아래 끝 간격만큼 위아래·사이 빈자리를 후보로
    낸다. 칸 위 끝은 장식 띠와 붙어 흔들리므로 아래 끝(사진 → 이름표 경계)으로 잰다. 후보는
    이름표를 읽어 맞아야만 쓴다(match_runs)."""
    if len(cells) < 2:
        return []
    bottoms = sorted(c[3] for c in cells)
    pitch = min(b - a for a, b in zip(bottoms, bottoms[1:], strict=False))
    if pitch < 0.05:
        return []
    height = min(c[3] - c[1] for c in cells)
    left, right = cells[0][0], cells[0][2]
    out = []
    for k in range(-2, round((bottoms[-1] - bottoms[0]) / pitch) + 3):
        bottom = bottoms[0] + k * pitch
        if bottom - height < 0 or bottom > 1 or any(abs(bottom - b) < pitch / 2 for b in bottoms):
            continue
        out.append((left, bottom - height, right, bottom))
    return out


def labelled(board: bytes, box: Box, name: str, call: Call) -> bool:
    """box 바로 아래 이름표가 이 메뉴 이름인가. 모델이 준 사진 위치를 쓰기 전 확인한다."""
    label = read_label(board, box, call=call)
    key = menu_photo._norm(split_subtitle(label)[0]) if label else ""
    return bool(key) and _label_score(key, name) >= LABEL_MIN


def place_photos(
    store: DummyStore,
    with_photo: list[Registered],
    no_photo: list[Registered],
    board: bytes,
    regions: list[Box],
    call: Call = chat_v3,
    cutout=None,
    refine: bool = True,
    workers: int = WORKERS,
) -> None:
    """흰 배경 사진 채우기. 첫 읽기로 사진 열을 정하고 그 안의 사진 칸을 영상으로 정확히 찾아
    (photo_runs) 메뉴에 맞춘 뒤(match_runs) 메뉴마다 동시에 자른다. 첫 읽기가 빠뜨린 사진도
    칸의 이름표로 찾는다. 재기가 놓친 칸은 맞춘 칸들의 간격으로 자리를 짐작해 이름표로 찾는다
    (pitch_cells). 그래도 못 찾으면 첫 읽기 위치를 잘라 다시 묻고(refine_box), 그 아래 이름표가
    메뉴 이름과 맞을 때만 자른다(fill_one)."""
    targets = with_photo + no_photo
    exact: dict[int, Box] = {}
    if refine and targets:
        column = photo_column([t.item.box for t in with_photo if t.item.box])
        runs = photo_runs(board, column) if column else []
        if runs:
            exact = match_runs(targets, runs, board, call)
        missing = [i for i in range(len(targets)) if i not in exact]
        guesses = pitch_cells(list(exact.values())) if missing else []
        if guesses:
            found = match_runs([targets[i] for i in missing], guesses, board, call)
            for j, box in found.items():
                log.info("menu photo cell found by spacing for %s", targets[missing[j]].item.name)
                exact[missing[j]] = box
    jobs = []
    for i, target in enumerate(targets):
        if i in exact:
            target.item.box = exact[i]
        if i >= len(with_photo):
            if i not in exact:
                continue  # 사진이 없는 메뉴
            log.info("menu photo recovered for %s", target.item.name)
            store.patch_menu(target.menu_id, {}, by_owner=False, start=("photo",))
        if not target.item.box:
            store.patch_menu(target.menu_id, {}, by_owner=False, done=("photo",))
            continue
        jobs.append((Registered(target.menu_id, target.item, ["photo"]), refine and i not in exact))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                fill_one, store, job, board, regions, "", call=call, cutout=cutout, refine=again
            )
            for job, again in jobs
        ]
        for future in futures:
            try:
                future.result()
            except Exception:  # noqa: BLE001 - 한 장 실패가 나머지를 막지 않게
                log.exception("menu photo crashed")


# ---------------------------------------------------------------- 2차: 사진 다듬기 (SEED Omni)

POLISH_TEXT = {
    "no_photo": "{name}에는 아직 사진이 없어요. 음식 사진을 먼저 올려 주세요.",
    "busy": "{name} 사진은 지금 다듬고 있어요. 조금만 기다려 주세요.",
    "started_omni": (
        "{name} 사진을 AI(HyperCLOVA X SEED Omni)로 다듬기 시작했어요. 한 장에 1~2분쯤 걸려요. "
        "다 되면 메뉴 탭 사진이 바로 바뀌어요."
    ),
    "started_studio": (
        "{name} 사진을 배달앱 메뉴 사진처럼 보정하고 있어요(밝기·색·가운데 정렬·그림자). "
        "몇 초면 메뉴 탭 사진이 바뀌어요."
    ),
}


def start_polish(
    store: DummyStore, menu_id: str, *, polish=None
) -> tuple[str, Callable[[], None] | None]:
    """메뉴 사진 다듬기를 시작한다. (사장님께 할 말, 백그라운드에서 돌릴 일 또는 None).

    GPU 서버(SEED Omni, AGENTHAN_IMAGE_POLISH_URL)가 있으면 그것으로 다시 그리고, 없으면 CPU
    스튜디오 보정(tools/image_studio.py)을 한다. 메뉴판에서 자른 원본 조각이 있으면 그걸로 다시
    만든다(흰 배경 사진을 또 보정하면 가장자리가 무뎌진다)."""
    from agenthan.tools import image_polish, image_studio

    record = next((m for m in store.read()["menus"] if m["id"] == menu_id), None)
    if record is None:
        raise DummyStoreError("없는 메뉴입니다.")
    name = record["name"]
    if not record.get("photo"):
        return POLISH_TEXT["no_photo"].format(name=name), None
    if "polish" in (record.get("filling") or []):
        return POLISH_TEXT["busy"].format(name=name), None
    folder = store.path.parent
    source = record.get("photo_source")
    photo = (
        folder / (source if source and (folder / source).is_file() else record["photo"])
    ).read_bytes()
    if polish is None:
        if image_polish.enabled():
            polish, key = image_polish.polish, "started_omni"
        else:

            def polish(data, _name):
                return image_studio.studio(data), "image/jpeg"

            key = "started_studio"
    else:
        key = "started_omni"
    store.patch_menu(menu_id, {}, by_owner=False, start=("polish",))

    def run() -> None:
        started = time.monotonic()
        try:
            image, mime = polish(photo, name)
            store.patch_menu(menu_id, {}, by_owner=False, photo=image, photo_mime=mime)
            log.info("menu polish %.1fs %s (%s)", time.monotonic() - started, name, key)
        except (image_polish.ImagePolishError, ImageCutoutError, DummyStoreError, ValueError):
            log.warning("menu polish failed for %s", name, exc_info=True)
        finally:
            try:
                store.patch_menu(menu_id, {}, by_owner=False, done=("polish",))
            except DummyStoreError:
                pass

    return POLISH_TEXT[key].format(name=name), run


POLISH_WORDS = re.compile(r"(사진|이미지).{0,12}(다듬|예쁘|이쁘|보정|깔끔|고급|AI로|에이아이)")


def polish_target(text: str, menus: list[dict]) -> dict | None:
    """'메도빅 사진 더 예쁘게 해 줘' → 메도빅 메뉴. 다듬기 말이 아니거나 메뉴 이름이 없으면 None."""
    if not POLISH_WORDS.search(text):
        return None
    key = menu_photo._norm(text)
    hits = [m for m in menus if menu_photo._norm(m["name"]) and menu_photo._norm(m["name"]) in key]
    return max(hits, key=lambda m: len(m["name"]), default=None)
