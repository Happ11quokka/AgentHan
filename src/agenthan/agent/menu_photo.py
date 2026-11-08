"""A-1 메뉴 사진 → 메뉴 초안 (PRD §6.1, 그림 5).

1. 사진 읽기: HCX-005 이미지 입력으로 메뉴 이름·가격만 뽑는다. 이미지와 도구 호출은 한 요청에
   같이 못 쓰므로 이 단계는 도구 없이 따로 부른다.
2. 가게 지식 조회: 읽은 이름을 `StoreKnowledge`의 이름·원어 이름·별칭과 맞춘다.
   HCX-005는 키릴 문자를 비슷한 다른 단어로 잘못 읽는 일이 잦아서
   (Медовик → Медовник, 2026-11-08 실측) 글자 유사도로 한 번 더 맞춘다.
   맞으면 기존 메뉴의 한국어 이름·설명을 그대로 쓴다. 안 맞은 키릴 이름은 check_spelling으로
   사장님께 철자 확인을 받는다. 가게 메뉴 목록을 사진 읽기 힌트로 주면 없는 메뉴까지 목록 이름으로
   바꿔 써서(Сырники → Блины) 쓰지 않는다.
3. 한국어 이름·설명: 기존 메뉴가 아니면 메뉴 하나씩 HCX-005로 만든다(여러 개를 한 번에 보내면 순서가
   뒤섞였다). 번역이 아니라 원어 발음을 살린 이름이다(취두부 쌀국수를 "쌀국수"로 줄이지 않음).

저장은 여기서 하지 않는다. 사장님이 승인한 뒤에만 비서가 저장한다(HITL).
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from agenthan.core.contracts import MenuItem, StoreKnowledge
from agenthan.core.llm import ClovaV3Error, chat_v3, image_content

VISION_MODEL = "HCX-005"
TEXT_MODEL = "HCX-005"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ITEMS = 30
FUZZY_MIN = 0.8  # 글자 유사도. Медовник↔Медовик 0.93, Народный↔Наполеон 0.5

READ_PROMPT = (
    "메뉴판 사진이다. 보이는 메뉴마다 이름과 가격을 읽어라.\n"
    "- name: 사진에 적힌 글자 그대로. 번역하거나 다른 단어로 바꾸지 마라.\n"
    "- price: 원 단위 정수. 안 보이면 null.\n"
    "- 메뉴판이 아니거나 읽을 메뉴가 없으면 [].\n"
    '출력은 JSON 배열만: [{"name": "...", "price": 7500}]'
)
LOCALIZE_SYSTEM = (
    "너는 외국인 사장님 가게의 메뉴 하나를 네이버 플레이스용 한국어로 등록하는 도우미다.\n"
    "- name_ko: 반드시 한글. 원어 발음을 살린 표기(Медовик→메도빅, Пельмени→펠메니). "
    "다른 음식으로 바꾸지 않는다.\n"
    "- 재료·특징을 빼서 뭉개지 않는다(臭豆腐米粉→취두부 쌀국수).\n"
    "- description_ko: 한국 손님이 알아듣게 한 문장. 확실하지 않으면 빈 문자열.\n"
    '- 출력은 JSON 하나만: {"name_ko": "...", "description_ko": "..."}'
)

CYRILLIC = re.compile(r"[А-Яа-яЁё]")

Call = Callable[[str, dict], dict]


class MenuPhotoError(Exception):
    """사장님께 그대로 보여도 되는 오류."""


@dataclass
class MenuDraft:
    """플레이스 메뉴 폼 한 줄. `to_form()`이 오른쪽 폼 채우기(form.patch) 값이다."""

    name_read: str  # 사진에서 읽은 그대로
    price: int | None
    name_ko: str = ""
    name_native: str = ""
    description: str = ""
    menu_id: str | None = None  # 가게 지식에 이미 있는 메뉴
    matched_by: str | None = None  # exact | alias | fuzzy | search
    # generated, check_spelling, price_missing, name_missing
    notes: list[str] = field(default_factory=list)

    photo: bytes | None = None
    photo_mime: str | None = None
    candidates: list[str] = field(default_factory=list)
    is_food: bool = False
    possible_menu_id: str | None = None
    possible_menu_name: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.name_ko.strip()) and isinstance(self.price, int) and self.price > 0

    def to_form(self) -> dict:
        return {
            k: v
            for k, v in asdict(self).items()
            if k in ("name_ko", "name_native", "price", "description", "menu_id")
        }


def detect_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise MenuPhotoError("JPG·PNG·WEBP 사진만 읽을 수 있어요.")


def _content(result: dict) -> str:
    content = (result.get("message") or {}).get("content")
    if not isinstance(content, str):
        raise MenuPhotoError("사진을 읽지 못했어요. 다시 시도해 주세요.")
    return content


def _json_block(text: str, opener: str) -> object:
    """```json 감싸기나 앞뒤 말을 걷어 내고 첫 JSON 값을 읽는다."""
    start = text.find(opener)
    if start < 0:
        raise ValueError("no json")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    return value


def parse_price(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[\s,.₩원]|KRW|won", "", value, flags=re.I)
        if digits.isdigit() and int(digits) > 0:
            return int(digits)
    return None


def read_menu_photo(data: bytes, *, call: Call = chat_v3) -> list[tuple[str, int | None]]:
    """사진 → [(사진에 적힌 이름, 가격 또는 None)]."""
    if not data:
        raise MenuPhotoError("사진이 비어 있어요.")
    if len(data) > MAX_IMAGE_BYTES:
        raise MenuPhotoError("사진이 너무 커요. 10MB 이하로 보내 주세요.")
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    image_content(data, detect_mime(data)),
                    {"type": "text", "text": READ_PROMPT},
                ],
            }
        ],
        "maxTokens": 1024,
        "temperature": 0.1,
    }
    try:
        raw = _json_block(_content(call(VISION_MODEL, body)), "[")
    except ClovaV3Error as error:
        raise MenuPhotoError(str(error)) from None
    except ValueError:
        raise MenuPhotoError(
            "사진에서 메뉴를 읽지 못했어요. 더 밝고 반듯한 사진으로 다시 보내 주세요."
        ) from None
    out: list[tuple[str, int | None]] = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            out.append((" ".join(name.split()), parse_price(row.get("price"))))
    return out[:MAX_ITEMS]


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def match_known(name: str, knowledge: StoreKnowledge) -> tuple[MenuItem, str] | None:
    """읽은 이름 → 가게 지식의 기존 메뉴. 정확·별칭 일치 → 글자 유사도 → 검색 순."""
    key = _norm(name)
    if not key:
        return None
    menu = knowledge.list_menu()
    for item in menu:
        if key in (_norm(item.name_ko), _norm(item.name_native)) and key:
            return item, "exact"
    for item in menu:
        if key in {_norm(a) for a in item.aliases}:
            return item, "alias"
    best, best_score = None, 0.0
    for item in menu:
        for label in (item.name_native, item.name_ko, *item.aliases):
            if label:
                score = difflib.SequenceMatcher(None, key, _norm(label)).ratio()
                if score > best_score:
                    best, best_score = item, score
    if best is not None and best_score >= FUZZY_MIN:
        return best, "fuzzy"
    hits = knowledge.search_menu(name, k=1)
    if hits and hits[0].score >= FUZZY_MIN:
        return hits[0].item, "search"
    return None


def localize(name: str, store_context: str = "", *, call: Call = chat_v3) -> tuple[str, str]:
    """원어 이름 하나 → (한글 메뉴명, 한 문장 설명). 한글이 아니면 이름은 빈 문자열."""
    body = {
        "messages": [
            {"role": "system", "content": LOCALIZE_SYSTEM},
            {
                "role": "user",
                "content": f"가게: {store_context or '알 수 없음'}\n메뉴 원어 이름: {name}",
            },
        ],
        "maxTokens": 300,
        "temperature": 0.1,
    }
    try:
        raw = _json_block(_content(call(TEXT_MODEL, body)), "{")
    except (ClovaV3Error, MenuPhotoError, ValueError):
        return "", ""
    if not isinstance(raw, dict):
        return "", ""
    name_ko = raw.get("name_ko") if isinstance(raw.get("name_ko"), str) else ""
    description = raw.get("description_ko") if isinstance(raw.get("description_ko"), str) else ""
    if not re.search(r"[가-힣]", name_ko):
        name_ko = ""
    return " ".join(name_ko.split()), description.strip()


def _native(name: str) -> str:
    """한글이 섞이지 않은 이름만 원어 이름으로 둔다."""
    return "" if re.search(r"[가-힣]", name) else name


def draft_menus(
    data: bytes,
    knowledge: StoreKnowledge,
    *,
    store_context: str = "",
    call: Call = chat_v3,
) -> list[MenuDraft]:
    """사진 → 메뉴 초안 목록. 저장하지 않는다."""
    drafts: list[MenuDraft] = []
    for name, price in read_menu_photo(data, call=call):
        draft = MenuDraft(name_read=name, price=price)
        known = match_known(name, knowledge)
        if known:
            item, how = known
            draft.menu_id, draft.matched_by = item.id, how
            draft.name_ko = item.name_ko
            draft.name_native = item.name_native or _native(name)
            draft.description = item.description
            if price is None:
                draft.price = item.price
        else:
            draft.name_native = _native(name)
            if re.search(r"[가-힣]", name) and not draft.name_native:
                draft.name_ko = name
            else:
                draft.name_ko, draft.description = localize(name, store_context, call=call)
                draft.notes.append("generated")
            if CYRILLIC.search(name):
                # 가게 지식과 안 맞은 키릴 이름은 사진을 잘못 읽었을 수 있다(라틴·한글은 정확했다)
                draft.notes.append("check_spelling")
        if draft.price is None:
            draft.notes.append("price_missing")
        if not draft.name_ko:
            draft.notes.append("name_missing")
        drafts.append(draft)
    return drafts
