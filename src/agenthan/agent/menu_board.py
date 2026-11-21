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

AI가 채운 칸은 ai에 남아 메뉴 탭에 "AI 추측"으로 보이고, 사장님이 고치면 지워진다.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from agenthan.agent import menu_photo
from agenthan.core.contracts import StoreKnowledge
from agenthan.core.llm import ClovaV3Error, chat_v3, image_content
from agenthan.tools.dummy_store import DummyStore, DummyStoreError
from agenthan.tools.image_cutout import ImageCutoutError, cutout_on_white

log = logging.getLogger(__name__)

READ_SIDE = 1280  # 메뉴판 읽기용 긴 변 (07-menu-photo-speed.md 3번)
WORKERS = 4  # 설명·사진 채우기 동시 실행 수 (테스트 키 분당 60회 안쪽)

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
        name = " ".join(row["name"].split())
        key = menu_photo._norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(
            BoardItem(name, menu_photo.parse_price(row.get("price")), _box(row.get("box")))
        )
    if kind == "menu_board" and not items:
        kind = "other"
    items = items[: menu_photo.MAX_ITEMS]
    attach_photos(items, raw.get("photos") if kind == "menu_board" else None)
    return kind, items


LABEL_MIN = 0.6  # 사진 이름표 ↔ 메뉴 이름 글자 유사도 (Медовник↔Медовик 0.93)


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
        label = menu_photo._norm(photo["label"])
        if box is None or not label:
            continue
        scores = [
            (difflib.SequenceMatcher(None, label, menu_photo._norm(item.name)).ratio(), i)
            for i, item in enumerate(items)
            if i not in taken
        ]
        score, best = max(scores, default=(0.0, -1))
        if score >= LABEL_MIN:
            items[best].box = box
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


def white_photo(board: bytes, box: Box, regions: list[Box], cutout=None) -> bytes:
    """메뉴판의 음식 사진 칸을 잘라 흰 배경 정사각형으로. 배경을 못 지우면 자른 사진에
    흰 여백만 둘러 쓴다(메뉴판 사진은 이미 음식 사진이라 그대로도 쓸 만하다).

    윤곽선 칸에 맞춰졌으면 칸 테두리에 딱 맞게 자른다: 메뉴판 바탕이 섞이지 않아야 GrabCut이
    사진 속 식탁·나무 바탕을 배경으로 잡는다(여백 2%면 바탕이 남았다, 실측 2026-11-21).
    512px·5회: 사진 한 장 0.2~0.5초."""
    snapped = snap(box, regions)
    piece = crop(board, snapped, 0.0 if snapped in regions else 0.02)
    return (cutout or cutout_on_white)(piece, work_side=512, iterations=5).image


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
) -> None:
    """메뉴 한 줄의 남은 칸(이름·설명·사진)을 채워 저장소에 쓴다. 실패한 칸은 비운 채 끝낸다."""
    tasks: list[Callable[[], None]] = []
    if {"name", "description"} & set(job.filling):

        def text() -> None:
            started = time.monotonic()
            name_ko, description = menu_photo.localize(job.item.name, store_context, call=call)
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
                image = white_photo(board, job.item.box, regions, cutout)
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
) -> Callable[[], None]:
    """백그라운드에서 돌릴 함수: 메뉴마다 동시에 채운다. 이름·설명과 사진도 서로 기다리지 않는다."""

    def run() -> None:
        started = time.monotonic()
        regions = photo_regions(board) if any(j.item.box for j in jobs) else []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for job in jobs:
                # 이름·설명과 사진을 따로 던져야 사진(CPU)과 HCX 대기가 겹친다.
                for part in ("text", "photo"):
                    keep = (
                        [f for f in job.filling if f in ("name", "description")]
                        if part == "text"
                        else [f for f in job.filling if f == "photo"]
                    )
                    if keep:
                        piece = Registered(job.menu_id, job.item, keep)
                        futures.append(
                            pool.submit(
                                fill_one,
                                store,
                                piece,
                                board,
                                regions,
                                store_context,
                                call=call,
                                cutout=cutout,
                            )
                        )
            for future in futures:
                try:
                    future.result()
                except Exception:  # noqa: BLE001 - 한 줄 실패가 나머지를 막지 않게
                    log.exception("menu fill crashed")
        log.info("menu board fill %.2fs for %d menus", time.monotonic() - started, len(jobs))
        # 남은 '채우는 중' 표시는 지운다(실패한 칸이 영원히 돌지 않게).
        for job in jobs:
            try:
                store.patch_menu(
                    job.menu_id, {}, by_owner=False, done=("name", "description", "photo")
                )
            except DummyStoreError:
                pass

    return run


# ---------------------------------------------------------------- 2차: 사진 다듬기 (SEED Omni)

POLISH_TEXT = {
    "off": (
        "사진 다듬기(HyperCLOVA X SEED Omni)는 GPU 서버를 준비하고 있어요. "
        "지금은 메뉴판에서 잘라 만든 흰 배경 사진을 쓰고 있어요."
    ),
    "no_photo": "{name}에는 아직 사진이 없어요. 음식 사진을 먼저 올려 주세요.",
    "busy": "{name} 사진은 지금 다듬고 있어요. 조금만 기다려 주세요.",
    "started": (
        "{name} 사진을 AI(HyperCLOVA X SEED Omni)로 다듬기 시작했어요. 한 장에 1~2분쯤 걸려요. "
        "다 되면 메뉴 탭 사진이 바로 바뀌어요."
    ),
}


def start_polish(
    store: DummyStore, menu_id: str, *, polish=None
) -> tuple[str, Callable[[], None] | None]:
    """메뉴 사진 다듬기를 시작한다. (사장님께 할 말, 백그라운드에서 돌릴 일 또는 None)."""
    from agenthan.tools import image_polish

    record = next((m for m in store.read()["menus"] if m["id"] == menu_id), None)
    if record is None:
        raise DummyStoreError("없는 메뉴입니다.")
    name = record["name"]
    if not record.get("photo"):
        return POLISH_TEXT["no_photo"].format(name=name), None
    if "polish" in (record.get("filling") or []):
        return POLISH_TEXT["busy"].format(name=name), None
    if polish is None and not image_polish.enabled():
        return POLISH_TEXT["off"], None
    store.patch_menu(menu_id, {}, by_owner=False, start=("polish",))
    photo = (store.path.parent / record["photo"]).read_bytes()

    def run() -> None:
        started = time.monotonic()
        try:
            image, mime = (polish or image_polish.polish)(photo, name)
            store.patch_menu(menu_id, {}, by_owner=False, photo=image, photo_mime=mime)
            log.info("menu polish %.1fs %s", time.monotonic() - started, name)
        except (image_polish.ImagePolishError, DummyStoreError):
            log.warning("menu polish failed for %s", name, exc_info=True)
        finally:
            try:
                store.patch_menu(menu_id, {}, by_owner=False, done=("polish",))
            except DummyStoreError:
                pass

    return POLISH_TEXT["started"].format(name=name), run


POLISH_WORDS = re.compile(r"(사진|이미지).{0,12}(다듬|예쁘|이쁘|보정|깔끔|고급|AI로|에이아이)")


def polish_target(text: str, menus: list[dict]) -> dict | None:
    """'메도빅 사진 더 예쁘게 해 줘' → 메도빅 메뉴. 다듬기 말이 아니거나 메뉴 이름이 없으면 None."""
    if not POLISH_WORDS.search(text):
        return None
    key = menu_photo._norm(text)
    hits = [m for m in menus if menu_photo._norm(m["name"]) and menu_photo._norm(m["name"]) in key]
    return max(hits, key=lambda m: len(m["name"]), default=None)
