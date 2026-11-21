"""What the owner screen shows: the place sections and the changes waiting for a yes.

Read-only views over the dummy store (ADR 0002). Writes still happen only in
OwnerAssistant after the owner approves; preview() runs the waiting actions on a copy.
"""

import copy
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from agenthan.agent import menu_photo
from agenthan.agent.owner_assistant import RECORD_TARGETS
from agenthan.illustration import ILLUSTRATION_KEY, illustration_url
from agenthan.tools.dummy_store import FILL_KEY, DummyStore
from agenthan.tools.feature_tools import BUSINESS_FIELDS, execute_feature_tool

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MENU_FIELDS = ("id", "name", "price", "description")
DRAFT_FIELDS = ("name_ko", "name_native", "price", "description", "menu_id")

PhotoUrl = Callable[[dict], str | None]


def _no_photo(_record: dict) -> None:
    return None


def _record_rows(records: list[dict], label_fields: tuple[str, ...]) -> list[dict]:
    """Records a feature changes one at a time (talktalk, order, reservation).

    label: the fields that tell the records apart (owner_assistant.RECORD_TARGETS);
    fields: every other field. The screen shows a change as the label plus the fields
    whose value differs, so a new record kind needs no screen code of its own.
    """
    rows = []
    for record in records:
        rest = {k: copy.deepcopy(v) for k, v in record.items() if k != "id"}
        rows.append(
            {
                "id": record["id"],
                "label": {k: rest.pop(k) for k in label_fields if k in rest},
                "fields": rest,
            }
        )
    return rows


def fill_progress(data: dict, now: float | None = None) -> dict | None:
    """메뉴판 사진 채우기 진행(dummy_store.track_fill): 시작 뒤 지난 초, 이 일의 메뉴, 채울 칸 수.
    화면은 이것으로 걸린 초와 'N/M개 완료'를 그린다. 지난 초를 보내므로 기기 시계가 달라도 맞다.
    채우는 중이 아니면 None."""
    run = data.get(FILL_KEY)
    if not isinstance(run, dict) or type(run.get("started_at")) not in (int, float):
        return None
    live = {m["id"] for m in data.get("menus", [])}
    ids = [i for i in run.get("menu_ids") or [] if i in live]  # 그 사이 지운 메뉴는 빼고 센다
    fields = [f for f in run.get("fields") or [] if f.split("/", 1)[0] in live]
    seconds = max(0.0, (time.time() if now is None else now) - run["started_at"])
    return {
        "started_at": run["started_at"],
        "seconds": round(seconds, 1),
        "menu_ids": ids,
        "fields": len(fields),
    }


def snapshot(data: dict, photo_url: PhotoUrl = _no_photo, now: float | None = None) -> dict:
    """The sections the screen draws, keyed by feature id."""
    hours = data.get("shop", {}).get("hours", {})
    records = {
        feature: _record_rows(data.get(collection, []), fields)
        for feature, (collection, _key, fields) in RECORD_TARGETS.items()
    }
    return {
        "menu": [
            {
                **{k: m.get(k) for k in MENU_FIELDS},
                "photo_url": photo_url(m),
                # 대표 그림(사진이 아님). 사진이 있어도 보내고, 무엇을 보일지는 화면이 정한다.
                "illustration_url": illustration_url(m.get(ILLUSTRATION_KEY)),
                # 메뉴판 사진으로 올린 메뉴: AI가 채운 칸(ai)과 지금 채우는 칸(filling)
                "ai": list(m.get("ai") or []),
                "filling": list(m.get("filling") or []),
            }
            for m in data.get("menus", [])
        ],
        "business_hours": {day: copy.deepcopy(hours[day]) for day in DAYS if day in hours},
        # 최소 주문 금액이 없던 옛 쿠폰은 조건 없음(0)으로 읽는다.
        "coupon": [
            {**copy.deepcopy(c), "min_order": c.get("min_order") or 0}
            for c in data.get("coupons", [])
        ],
        "keywords": list(data.get("shop", {}).get("keywords", [])),
        # 가게 기본 정보: 입력 블록의 처음 값이자, 승인 카드가 바뀌기 전·후를 비교하는 값
        "business_info": {k: data.get("shop", {}).get(k) for k in BUSINESS_FIELDS},
        # 메뉴판 사진 채우기 진행. 가게 정보가 아니라 바뀜(changes)으로 세지 않는다.
        FILL_KEY: fill_progress(data, now),
        **records,
    }


def _keyed(section: object) -> dict:
    if isinstance(section, dict):
        return section
    # Records are keyed by id; a list of words (keywords) by the word itself.
    return {item if isinstance(item, str) else item["id"]: item for item in section}


def changes(before: dict, after: dict) -> dict:
    """Per section, the keys (record ids or days) that were added, updated or removed."""
    result = {}
    for name in before:
        if name == FILL_KEY:  # 진행 표시: 승인할 바뀜이 아니다
            continue
        old, new = _keyed(before[name]), _keyed(after[name])
        diff = {
            "added": [k for k in new if k not in old],
            "updated": [k for k in new if k in old and new[k] != old[k]],
            "removed": [k for k in old if k not in new],
        }
        if any(diff.values()):
            result[name] = diff
    return result


def preview(data: dict, feature: str, actions: list[dict], photo_url: PhotoUrl = _no_photo) -> dict:
    """Run the waiting actions on a copy of the store and compare the screen sections."""
    with tempfile.TemporaryDirectory() as folder:
        store = DummyStore(Path(folder) / "store.json")
        store.write(copy.deepcopy(data))
        for action in actions:
            result = execute_feature_tool(store, action["name"], action["arguments"])
            if not result.get("ok"):
                message = result.get("message", "미리보기를 만들지 못했습니다.")
                return {"feature": feature, "after": None, "changes": {}, "error": message}
        after = snapshot(store.read(), photo_url)
    return {
        "feature": feature,
        "after": after,
        "changes": changes(snapshot(data, photo_url), after),
    }


def _save_action(data: dict, draft) -> dict:
    """The upsert_menu call that saving this complete draft amounts to (photo aside)."""
    record = menu_photo.store_record(draft, data.get("menus", []))
    if record is not None and draft.is_food:
        # Attaching a photo keeps the saved name and price.
        return {
            "name": "upsert_menu",
            "arguments": {"name": record["name"], "price": record["price"]},
        }
    arguments = {"name": record["name"] if record else draft.name_ko, "price": draft.price}
    if draft.description:
        arguments["description"] = draft.description
    return {"name": "upsert_menu", "arguments": arguments}


def photo_preview(
    data: dict, drafts: list, draft_photo_url: Callable[[int], str], photo_url: PhotoUrl = _no_photo
) -> dict:
    """Menu drafts from a photo waiting for a yes, and what saving them changes.

    Nothing is saved until every draft has a name and a price, so until then `changes` is
    empty. `after` leaves the photos out: a new menu has no id until it is saved.
    """
    menus = data.get("menus", [])
    rows = []
    for i, d in enumerate(drafts):
        change, previous = menu_photo.store_change(d, menus)
        record = menu_photo.store_record(d, menus)
        rows.append(
            {
                **{k: getattr(d, k) for k in DRAFT_FIELDS},
                "change": change,
                "previous_price": previous,
                "menu_name": record["name"] if record else None,  # the saved menu it matches
                "notes": list(d.notes),
                "photo_url": draft_photo_url(i) if d.photo is not None else None,
                # 음식 사진: 이름은 사장님이 카드에 입력한다.
                # 사진으로 짐작한 이름은 자리표시자로만(#93).
                "is_food": d.is_food,
                "name_guess": d.name_read if d.is_food else None,
            }
        )
    ready = all(d.complete for d in drafts)
    actions = [_save_action(data, d) for d in drafts] if ready else []
    return {"kind": "photo", "drafts": rows, **preview(data, "menu", actions, photo_url)}
