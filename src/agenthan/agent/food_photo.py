"""음식 사진에서 검토가 필요한 메뉴 초안을 만든다."""

from dataclasses import dataclass
from typing import Literal

from agenthan.agent import menu_photo
from agenthan.core.contracts import StoreKnowledge
from agenthan.core.llm import ClovaV3Error, chat_v3, image_content
from agenthan.tools.image_cutout import cutout_on_white

PROMPT = (
    '음식 사진을 보고 JSON 하나만 답하세요: {"name_ko": "한국어 이름", '
    '"name_native": "원어 이름 또는 빈 문자열", "candidates": ["다른 후보 최대 2개"], '
    '"description": "한 문장 설명", "box": [x0, y0, x1, y1]}. '
    "box는 음식 영역의 0~1 정규화 좌표입니다. 모르면 null로 답하세요."
)
CLASSIFY_PROMPT = "사진을 분류해 menu_board, food, other 중 한 단어만 답하세요."


@dataclass
class FoodGuess:
    name_ko: str
    name_native: str
    candidates: list[str]
    description: str
    box: tuple[float, float, float, float] | None


def _vision(data, prompt, call):
    if not data or len(data) > menu_photo.MAX_IMAGE_BYTES:
        raise menu_photo.MenuPhotoError("사진이 비었거나 10MB를 넘어요.")
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
        "maxTokens": 600,
        "temperature": 0.1,
    }
    try:
        return menu_photo._content(call(menu_photo.VISION_MODEL, body)).strip()
    except ClovaV3Error as error:
        raise menu_photo.MenuPhotoError(str(error)) from None


def classify_photo(data, *, call=chat_v3) -> Literal["menu_board", "food", "other"]:
    result = _vision(data, CLASSIFY_PROMPT, call)
    return result if result in ("menu_board", "food", "other") else "other"


def read_food_photo(data, store_context="", *, call=chat_v3) -> FoodGuess:
    try:
        raw = menu_photo._json_block(_vision(data, PROMPT + "\n가게: " + store_context, call), "{")
    except ValueError:
        raise menu_photo.MenuPhotoError("음식 사진을 읽지 못했어요. 다시 시도해 주세요.") from None
    if not isinstance(raw, dict):
        raise menu_photo.MenuPhotoError("음식 사진을 읽지 못했어요. 다시 시도해 주세요.")

    def string(key):
        value = raw.get(key)
        return " ".join(value.split()) if isinstance(value, str) else ""

    candidates = raw.get("candidates")
    candidates = (
        [" ".join(v.split()) for v in candidates if isinstance(v, str) and v.strip()][:2]
        if isinstance(candidates, list)
        else []
    )
    box = raw.get("box")
    if (
        not isinstance(box, (list, tuple))
        or len(box) != 4
        or any(type(v) not in (int, float) for v in box)
        or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)
        or (box[2] - box[0]) * (box[3] - box[1]) < 0.05
    ):
        box = None
    else:
        box = tuple(box)
    return FoodGuess(
        string("name_ko"), string("name_native"), candidates, string("description"), box
    )


def draft_from_food(
    data, knowledge: StoreKnowledge, *, store_context="", call=chat_v3, cutout=cutout_on_white
):
    guess = read_food_photo(data, store_context, call=call)
    result = cutout(data, box=guess.box)
    draft = menu_photo.MenuDraft(
        name_read=guess.name_ko,
        name_ko=guess.name_ko,
        name_native=guess.name_native,
        price=None,
        description=guess.description,
        notes=["food_guess", "price_missing"],
        photo=result.image,
        photo_mime=result.mime,
        candidates=guess.candidates,
        is_food=True,
    )
    if not result.ok:
        draft.notes.append("cutout_failed")
    for name in [guess.name_ko, *guess.candidates]:
        known = menu_photo.match_known(name, knowledge)
        if known:
            draft.possible_menu_id = known[0].id
            draft.possible_menu_name = known[0].name_ko
            break
    return draft


def text_json(prompt, *, call=chat_v3):
    try:
        raw = menu_photo._json_block(
            menu_photo._content(
                call(
                    menu_photo.TEXT_MODEL,
                    {
                        "messages": [{"role": "user", "content": prompt}],
                        "maxTokens": 250,
                        "temperature": 0.1,
                    },
                )
            ),
            "{",
        )
        return raw if isinstance(raw, dict) else {}
    except (ClovaV3Error, menu_photo.MenuPhotoError, ValueError):
        return {}
