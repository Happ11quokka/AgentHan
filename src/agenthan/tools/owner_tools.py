"""Catalog-derived schemas and a validation boundary for model tool arguments."""

import json

from agenthan.rag.place_matcher import get_feature
from agenthan.tools.dummy_store import DummyStore, normalize, valid_price

TOOL_NAMES = {"menu": "upsert_menu", "review_reply": "save_review_reply"}


def tool_schema(feature_id: str) -> dict:
    feature = get_feature(feature_id)
    if feature_id not in TOOL_NAMES or not feature or not feature["input_supported"]:
        raise ValueError("Unsupported feature")
    properties = {}
    required = []
    for field in feature["input_fields"]:
        spec = {"type": "string", "description": field["name_ko"]}
        if field["id"] == "price":
            spec.update(type="integer", minimum=1)
        if field["id"] == "review_text":
            spec["description"] += " (가게 데이터의 리뷰 id 또는 리뷰 원문 일부)"
        if not field["required"]:
            spec["description"] += " (현재 입력 미지원: 보내지 마세요)"
        properties[field["id"]] = spec
        if field["required"]:
            required.append(field["id"])
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAMES[feature_id],
            "description": feature["name_ko"]
            + ": 로컬 더미 저장만 수행하며 실제 발송은 하지 않습니다.",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def execute_tool(store: DummyStore, feature_id: str, name: str, arguments: object) -> dict:
    """Invalid arguments never reach storage; labels always come from the catalog."""
    if TOOL_NAMES.get(feature_id) != name:
        return {"ok": False, "error": "tool_not_allowed", "message": "허용되지 않은 도구입니다."}
    fields = get_feature(feature_id)["input_fields"]
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = None
    if not isinstance(arguments, dict):
        arguments = {}
    invalid = []
    for field in fields:
        if not field["required"]:
            continue
        value = arguments.get(field["id"])
        valid = (
            valid_price(value)
            if field["id"] == "price"
            else isinstance(value, str) and bool(normalize(value))
        )
        if not valid:
            invalid.append(field["name_ko"])
    if invalid:
        return {
            "ok": False,
            "error": "needs_input",
            "missing_fields": invalid,
            "message": "다음 항목을 확인해 주세요: " + ", ".join(invalid),
        }
    unsupported = [
        f["name_ko"]
        for f in fields
        if not f["required"] and arguments.get(f["id"]) not in (None, "")
    ]
    if unsupported or set(arguments) - {f["id"] for f in fields}:
        return {
            "ok": False,
            "error": "unsupported_fields",
            "unsupported_fields": unsupported,
            "message": "메뉴 이름·가격과 리뷰 내용·답글 내용만 입력할 수 있습니다.",
        }
    if feature_id == "menu":
        return store.upsert_menu(arguments["name"], arguments["price"])
    return store.save_review_reply(arguments["review_text"], arguments["reply_text"])
