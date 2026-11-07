"""Tools for the catalog features handled by agenthan (menu + 15 features).

Review features (review_reply, ai_review) are delegated to review_tools.

Every tool works only on the local dummy store or on fixed dummy reference data.
Nothing is sent to Naver. Invalid arguments never reach storage.
"""

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from agenthan.rag.place_matcher import get_feature, load_features
from agenthan.tools.dummy_store import DummyStore, find_records, normalize
from agenthan.tools.owner_tools import TOOL_NAMES, execute_tool, tool_schema

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")
DUMMY_NOTE = "로컬 더미 데이터만 사용하며 실제 네이버 연동·발송은 하지 않습니다."

# Fixed dummy reference data. Real requirements must be checked on Naver's own pages.
NAVERPAY_GUIDE = {
    "individual": ["사업자등록증", "대표자 신분증", "정산 받을 계좌"],
    "corporation": ["사업자등록증", "법인 등기부등본", "법인 인감증명서", "법인 계좌"],
}
DUMMY_POS = ["연습POS 베이직", "연습POS 플러스", "연습POS 모바일"]
HELP_ARTICLES = [
    {"id": "help-1", "title": "스마트플레이스 처음 시작하기", "keywords": ["시작", "가입", "처음"]},
    {"id": "help-2", "title": "예약 받는 방법 배우기", "keywords": ["예약", "교육"]},
    {"id": "help-3", "title": "리뷰 답글 잘 쓰는 법", "keywords": ["리뷰", "답글"]},
    {
        "id": "help-4",
        "title": "고객센터 문의하기",
        "keywords": ["고객센터", "문의", "상담", "도움"],
    },
    {
        "id": "help-5",
        "title": "비즈니스 스쿨 무료 강의",
        "keywords": ["교육", "강의", "스쿨", "배우"],
    },
]


@dataclass(frozen=True)
class Param:
    id: str
    label: str
    kind: str = "string"  # string | integer | boolean | date | time | day | string_list
    required: bool = True
    enum: tuple[str, ...] = ()
    hint: str = ""
    minimum: int | None = None
    max_items: int | None = None


@dataclass(frozen=True)
class FeatureTool:
    feature_id: str
    name: str
    params: tuple[Param, ...]
    handler: Callable[[DummyStore, dict], dict] = field(repr=False)


def _json_schema(param: Param) -> dict:
    description = param.label + (f" ({param.hint})" if param.hint else "")
    if param.kind == "integer":
        spec = {"type": "integer"}
        if param.minimum is not None:
            spec["minimum"] = param.minimum
    elif param.kind == "boolean":
        spec = {"type": "boolean"}
    elif param.kind == "string_list":
        spec = {"type": "array", "items": {"type": "string"}, "minItems": 1}
        if param.max_items:
            spec["maxItems"] = param.max_items
    else:
        spec = {"type": "string"}
        if param.kind == "date":
            description += " (YYYY-MM-DD)"
        elif param.kind == "time":
            description += " (HH:MM, 24시간)"
        elif param.kind == "day":
            description += " (요일 mon~sun 또는 날짜 YYYY-MM-DD)"
    if param.enum:
        spec["enum"] = list(param.enum)
    spec["description"] = description
    return spec


def _valid(param: Param, value: object) -> bool:
    if param.kind == "integer":
        ok = type(value) is int and (param.minimum is None or value >= param.minimum)
    elif param.kind == "boolean":
        ok = type(value) is bool
    elif param.kind == "string_list":
        ok = (
            isinstance(value, list)
            and bool(value)
            and (param.max_items is None or len(value) <= param.max_items)
            and all(isinstance(v, str) and normalize(v) for v in value)
        )
    elif not isinstance(value, str) or not normalize(value):
        ok = False
    elif param.kind == "date":
        ok = _is_date(value)
    elif param.kind == "time":
        ok = bool(TIME_PATTERN.match(value))
    elif param.kind == "day":
        ok = value in WEEKDAYS or _is_date(value)
    else:
        ok = True
    return ok and (not param.enum or value in param.enum)


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10


def _needs(labels: list[str]) -> dict:
    return {
        "ok": False,
        "error": "needs_input",
        "missing_fields": labels,
        "message": "다음 항목을 확인해 주세요: " + ", ".join(labels),
    }


def _not_found(kind: str, records: list[dict], text_key: str) -> dict:
    return {
        "ok": False,
        "error": f"{kind}_not_found",
        "message": "대상을 확인해 주세요.",
        "candidates": [{"id": r["id"], text_key: r.get(text_key)} for r in records],
    }


def _saved(store: DummyStore, collection: str, record_id: str, action: str) -> dict:
    saved = next(r for r in store.read()[collection] if r["id"] == record_id)
    return {"ok": True, "action": action, "record": saved}


# --- handlers: arguments are already validated -------------------------------------


def _business_info(store, args):
    data = store.read()
    previous = data["shop"].get(args["field"])
    data["shop"][args["field"]] = " ".join(args["value"].split())
    store.write(data)
    shop = store.read()["shop"]
    return {
        "ok": True,
        "action": "updated",
        "field": args["field"],
        "previous": previous,
        "record": shop[args["field"]],
    }


def _business_hours(store, args):
    if args.get("closed"):
        hours = {"closed": True}
    elif not args.get("open_time") or not args.get("close_time"):
        return _needs([p.label for p in TOOLS["business_hours"].params[2:]])
    else:
        hours = {"closed": False, "open_time": args["open_time"], "close_time": args["close_time"]}
    data = store.read()
    data["shop"].setdefault("hours", {})[args["day"]] = hours
    store.write(data)
    return {
        "ok": True,
        "action": "updated",
        "day": args["day"],
        "record": store.read()["shop"]["hours"][args["day"]],
    }


def _talktalk(store, args):
    data = store.read()
    matches = find_records(data["talktalk"], args["inquiry"], "message")
    if len(matches) != 1:
        return _not_found("inquiry", matches or data["talktalk"], "message")
    matches[0].update(reply_text=args["reply_text"].strip(), status="saved_not_sent")
    store.write(data)
    return _saved(store, "talktalk", matches[0]["id"], "saved_not_sent")


def _status_updater(collection: str, key: str, text_key: str):
    def handler(store, args):
        data = store.read()
        matches = [r for r in data[collection] if normalize(r["id"]) == normalize(args[key])]
        if len(matches) != 1:
            return _not_found(key.removesuffix("_id"), data[collection], text_key)
        previous = matches[0].get("status")
        matches[0]["status"] = args["status"]
        store.write(data)
        result = _saved(store, collection, matches[0]["id"], "updated")
        result["previous_status"] = previous
        return result

    return handler


def _call_history(store, args):
    calls = store.read()["calls"]
    if args.get("missed_only"):
        calls = [c for c in calls if c.get("missed")]
    return {"ok": True, "action": "read", "records": calls}


def _keywords(store, args):
    keywords = list(dict.fromkeys(" ".join(k.split()) for k in args["keywords"]))
    data = store.read()
    previous = data["shop"].get("keywords", [])
    data["shop"]["keywords"] = keywords
    store.write(data)
    return {
        "ok": True,
        "action": "updated",
        "previous": previous,
        "record": store.read()["shop"]["keywords"],
    }


def _coupon(store, args):
    invalid = []
    if args["discount_type"] == "percent" and args["discount_value"] > 100:
        invalid.append("할인 값")
    if args["end_date"] < args["start_date"]:
        invalid.append("끝나는 날")
    if invalid:
        return _needs(invalid)
    data = store.read()
    record = {"id": f"coupon-{len(data['coupons']) + 1}", **args, "status": "saved_not_published"}
    data["coupons"].append(record)
    store.write(data)
    return _saved(store, "coupons", record["id"], "saved_not_published")


def _new_opening(store, args):
    data = store.read()
    data["shop"]["new_opening"] = {"opening_date": args["opening_date"], "status": "saved"}
    store.write(data)
    return {"ok": True, "action": "saved", "record": store.read()["shop"]["new_opening"]}


def _naverpay(store, args):
    return {
        "ok": True,
        "action": "read",
        "business_type": args["business_type"],
        "documents": NAVERPAY_GUIDE[args["business_type"]],
        "note": "더미 안내입니다. 실제 서류는 네이버페이 가맹 안내에서 확인하세요.",
    }


def _onsite_payment(store, args):
    data = store.read()
    previous = data["shop"].get("onsite_payment", False)
    data["shop"]["onsite_payment"] = args["enabled"]
    store.write(data)
    return {
        "ok": True,
        "action": "updated",
        "previous": previous,
        "record": store.read()["shop"]["onsite_payment"],
    }


def _settlement(store, args):
    record = store.read()["settlements"].get(args["period"])
    if record is None:
        return {"ok": False, "error": "no_data", "message": "해당 기간의 정산 자료가 없습니다."}
    return {"ok": True, "action": "read", "period": args["period"], "record": record}


def _pos(store, args):
    key = normalize(args["pos_name"])
    supported = [p for p in DUMMY_POS if key in normalize(p) or normalize(p) in key]
    return {
        "ok": True,
        "action": "read",
        "supported": bool(supported),
        "matches": supported,
        "supported_pos": DUMMY_POS,
    }


def _statistics(store, args):
    record = store.read()["statistics"].get(args["period"])
    if record is None:
        return {"ok": False, "error": "no_data", "message": "해당 기간의 통계가 없습니다."}
    return {"ok": True, "action": "read", "period": args["period"], "record": record}


def _help(store, args):
    key = normalize(args["query"])
    found = [
        a
        for a in HELP_ARTICLES
        if any(normalize(k) in key for k in a["keywords"]) or key in normalize(a["title"])
    ]
    return {"ok": True, "action": "read", "records": found or HELP_ARTICLES[3:4]}


_TOOL_LIST = [
    FeatureTool(
        "business_info",
        "update_business_info",
        (
            Param(
                "field",
                "바꿀 업체 정보 항목",
                enum=("name", "address", "phone", "introduction"),
                hint="name=상호, address=주소, phone=전화번호, introduction=가게 소개",
            ),
            Param("value", "새 내용"),
        ),
        _business_info,
    ),
    FeatureTool(
        "business_hours",
        "update_business_hours",
        (
            Param("day", "바꿀 요일 또는 날짜", kind="day"),
            Param(
                "closed",
                "그날 쉬는지 여부",
                kind="boolean",
                required=False,
                hint="쉬는 날이면 true, 생략하면 영업하는 날",
            ),
            Param("open_time", "여는 시간", kind="time", required=False, hint="쉬지 않으면 필수"),
            Param("close_time", "닫는 시간", kind="time", required=False, hint="쉬지 않으면 필수"),
        ),
        _business_hours,
    ),
    FeatureTool(
        "talktalk",
        "save_talktalk_reply",
        (
            Param("inquiry", "답할 톡톡 문의", hint="가게 데이터의 톡톡 id 또는 문의 원문 일부"),
            Param("reply_text", "톡톡 답장 내용"),
        ),
        _talktalk,
    ),
    FeatureTool(
        "order",
        "update_order_status",
        (
            Param("order_id", "주문 id", hint="가게 데이터의 주문 id"),
            Param(
                "status",
                "바꿀 주문 상태",
                enum=("accepted", "rejected", "ready", "completed"),
                hint="accepted=접수, rejected=거절, ready=준비 완료, completed=전달 완료",
            ),
        ),
        _status_updater("orders", "order_id", "items"),
    ),
    FeatureTool(
        "reservation",
        "update_reservation_status",
        (
            Param("reservation_id", "예약 id", hint="가게 데이터의 예약 id"),
            Param(
                "status",
                "바꿀 예약 상태",
                enum=("confirmed", "cancelled", "no_show"),
                hint="confirmed=확정, cancelled=취소, no_show=노쇼",
            ),
        ),
        _status_updater("reservations", "reservation_id", "date"),
    ),
    FeatureTool(
        "smartcall",
        "list_call_history",
        (Param("missed_only", "부재중 전화만 볼지 여부", kind="boolean", required=False),),
        _call_history,
    ),
    FeatureTool(
        "search_keywords",
        "set_representative_keywords",
        (Param("keywords", "대표키워드 목록", kind="string_list", max_items=5, hint="최대 5개"),),
        _keywords,
    ),
    FeatureTool(
        "coupon",
        "create_coupon",
        (
            Param("title", "쿠폰 이름"),
            Param(
                "discount_type",
                "할인 방식",
                enum=("amount", "percent"),
                hint="amount=원 할인, percent=% 할인",
            ),
            Param("discount_value", "할인 값", kind="integer", minimum=1, hint="원 또는 %"),
            Param("start_date", "시작하는 날", kind="date"),
            Param("end_date", "끝나는 날", kind="date"),
        ),
        _coupon,
    ),
    FeatureTool(
        "new_opening",
        "register_new_opening",
        (Param("opening_date", "오픈한 날", kind="date"),),
        _new_opening,
    ),
    FeatureTool(
        "naverpay_signup",
        "get_naverpay_signup_guide",
        (
            Param(
                "business_type",
                "사업자 종류",
                enum=("individual", "corporation"),
                hint="individual=개인, corporation=법인",
            ),
        ),
        _naverpay,
    ),
    FeatureTool(
        "onsite_payment",
        "set_onsite_payment",
        (Param("enabled", "매장결제 사용 여부", kind="boolean"),),
        _onsite_payment,
    ),
    FeatureTool(
        "settlement",
        "get_settlement_summary",
        (
            Param(
                "period",
                "정산 기간",
                enum=("this_week", "this_month", "last_month"),
                hint="this_week=이번 주, this_month=이번 달, last_month=지난달",
            ),
        ),
        _settlement,
    ),
    FeatureTool(
        "place_plus",
        "check_pos_compatibility",
        (Param("pos_name", "POS 이름"),),
        _pos,
    ),
    FeatureTool(
        "statistics",
        "get_statistics",
        (Param("period", "통계 기간", enum=("7d", "30d"), hint="7d=최근 7일, 30d=최근 30일"),),
        _statistics,
    ),
    FeatureTool(
        "learning_support",
        "search_help_articles",
        (Param("query", "찾을 교육·도움말 주제"),),
        _help,
    ),
]
TOOLS = {t.feature_id: t for t in _TOOL_LIST}
# Handled by review_tools (ReviewToolbox + run_turn), not by a tool in this module.
DELEGATED_FEATURES = frozenset({"review_reply", "ai_review"})
# Every other catalog feature has exactly one tool name.
TOOL_FEATURES = {**{v: k for k, v in TOOL_NAMES.items()}, **{t.name: k for k, t in TOOLS.items()}}


def feature_tool_schema(feature_id: str) -> dict:
    """Schema for any non-delegated feature; menu comes from owner_tools."""
    if feature_id in TOOL_NAMES:
        return tool_schema(feature_id)
    tool = TOOLS.get(feature_id)
    feature = get_feature(feature_id)
    if tool is None or feature is None:
        raise ValueError("Unsupported feature")
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": f"{feature['name_ko']}: {feature['summary_ko']} {DUMMY_NOTE}",
            "parameters": {
                "type": "object",
                "properties": {p.id: _json_schema(p) for p in tool.params},
                "required": [p.id for p in tool.params if p.required],
                "additionalProperties": False,
            },
        },
    }


def all_tool_schemas() -> list[dict]:
    """All 16 agenthan tools in catalog order (review features are delegated)."""
    return [
        feature_tool_schema(f["id"]) for f in load_features() if f["id"] not in DELEGATED_FEATURES
    ]


def execute_feature_tool(store: DummyStore, name: str, arguments: object) -> dict:
    """Run any of the 16 tools by name after validating its arguments."""
    feature_id = TOOL_FEATURES.get(name)
    if feature_id is None:
        return {"ok": False, "error": "tool_not_allowed", "message": "허용되지 않은 도구입니다."}
    if feature_id in TOOL_NAMES:
        return execute_tool(store, feature_id, name, arguments)
    tool = TOOLS[feature_id]
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            arguments = None
    if not isinstance(arguments, dict):
        arguments = {}
    arguments = copy.deepcopy(arguments)
    known = {p.id for p in tool.params}
    if set(arguments) - known:
        return {
            "ok": False,
            "error": "unsupported_fields",
            "unsupported_fields": sorted(set(arguments) - known),
            "message": "이 도구에서 받지 않는 항목이 있습니다.",
        }
    invalid = [
        p.label
        for p in tool.params
        if (p.required or arguments.get(p.id) is not None) and not _valid(p, arguments.get(p.id))
    ]
    if invalid:
        return _needs(invalid)
    return tool.handler(store, arguments)
