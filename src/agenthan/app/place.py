"""What the owner screen shows: the place sections and the changes waiting for a yes.

Read-only views over the dummy store (ADR 0002). Writes still happen only in
OwnerAssistant after the owner approves; preview() runs the waiting actions on a copy.
"""

import copy
import tempfile
from pathlib import Path

from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import execute_feature_tool

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MENU_FIELDS = ("id", "name", "price", "description")


def snapshot(data: dict) -> dict:
    """The sections the screen draws, keyed by feature id."""
    hours = data.get("shop", {}).get("hours", {})
    return {
        "menu": [{k: m.get(k) for k in MENU_FIELDS} for m in data.get("menus", [])],
        "business_hours": {day: copy.deepcopy(hours[day]) for day in DAYS if day in hours},
        "coupon": copy.deepcopy(data.get("coupons", [])),
    }


def _keyed(section: object) -> dict:
    if isinstance(section, dict):
        return section
    return {item["id"]: item for item in section}


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


def preview(data: dict, feature: str, actions: list[dict]) -> dict:
    """Run the waiting actions on a copy of the store and compare the screen sections."""
    with tempfile.TemporaryDirectory() as folder:
        store = DummyStore(Path(folder) / "store.json")
        store.write(copy.deepcopy(data))
        for action in actions:
            result = execute_feature_tool(store, action["name"], action["arguments"])
            if not result.get("ok"):
                message = result.get("message", "미리보기를 만들지 못했습니다.")
                return {"feature": feature, "after": None, "changes": {}, "error": message}
        after = snapshot(store.read())
    return {"feature": feature, "after": after, "changes": changes(snapshot(data), after)}
