"""What the owner screen shows: the place sections and the changes waiting for a yes.

Read-only views over the dummy store (ADR 0002). Writes still happen only in
OwnerAssistant after the owner approves; preview() runs the waiting actions on a copy.
"""

import copy
import tempfile
from collections.abc import Callable
from pathlib import Path

from agenthan.agent import menu_photo
from agenthan.agent.owner_assistant import RECORD_TARGETS
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import execute_feature_tool

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


def snapshot(data: dict, photo_url: PhotoUrl = _no_photo) -> dict:
    """The sections the screen draws, keyed by feature id."""
    hours = data.get("shop", {}).get("hours", {})
    records = {
        feature: _record_rows(data.get(collection, []), fields)
        for feature, (collection, _key, fields) in RECORD_TARGETS.items()
    }
    return {
        "menu": [
            {**{k: m.get(k) for k in MENU_FIELDS}, "photo_url": photo_url(m)}
            for m in data.get("menus", [])
        ],
        "business_hours": {day: copy.deepcopy(hours[day]) for day in DAYS if day in hours},
        "coupon": copy.deepcopy(data.get("coupons", [])),
        "keywords": list(data.get("shop", {}).get("keywords", [])),
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
                # 음식 사진: 이름은 사장님이 카드에 입력한다. AI 추측은 자리표시자로만(#93).
                "is_food": d.is_food,
                "name_guess": d.name_read if d.is_food else None,
            }
        )
    ready = all(d.complete for d in drafts)
    actions = [_save_action(data, d) for d in drafts] if ready else []
    return {"kind": "photo", "drafts": rows, **preview(data, "menu", actions, photo_url)}
