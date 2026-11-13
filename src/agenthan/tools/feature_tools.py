"""Tools for the catalog features handled by agenthan (17 features, one tool each).

The review feature (review) is delegated to agenthan.agent.review.

Every tool works only on the local dummy store or on fixed dummy reference data.
Nothing is sent to Naver. Invalid arguments never reach storage.
"""

import copy
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from agenthan.agent.place_matcher import get_feature, load_features
from agenthan.tools.dummy_store import DummyStore, find_records, normalize

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Day groups the owner says in one word; the handler stores each weekday separately.
DAY_GROUPS = {
    "weekdays": WEEKDAYS[:5],
    "weekend": WEEKDAYS[5:],
    "everyday": WEEKDAYS,
}
# One period vocabulary for every read tool that takes a period.
PERIODS = ("today", "yesterday", "this_week", "last_week", "this_month", "last_month")
PERIOD_HINT = (
    "today=오늘, yesterday=어제, this_week=이번 주, last_week=지난주, "
    "this_month=이번 달, last_month=지난달"
)
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")
DUMMY_NOTE = "로컬 더미 데이터만 사용하며 실제 네이버 연동·발송은 하지 않습니다."

# Fixed dummy reference data. Real requirements must be checked on Naver's own pages.
NAVERPAY_GUIDE = {
    "individual": ["사업자등록증", "대표자 신분증", "정산 받을 계좌"],
    "corporation": ["사업자등록증", "법인 등기부등본", "법인 인감증명서", "법인 계좌"],
}
DUMMY_POS = ["연습POS 베이직", "연습POS 플러스", "연습POS 모바일"]
# topics: words that pick the article; generic learning words are in HELP_GENERIC.
HELP_ARTICLES = [
    {"id": "help-1", "title": "스마트플레이스 처음 시작하기", "topics": ["시작", "가입", "처음"]},
    {"id": "help-2", "title": "예약 받는 방법 배우기", "topics": ["예약"]},
    {"id": "help-3", "title": "리뷰 답글 잘 쓰는 법", "topics": ["리뷰", "답글"]},
    {
        "id": "help-4",
        "title": "고객센터 문의하기",
        "topics": ["고객센터", "문의", "상담", "도움말", "상담원"],
    },
    {"id": "help-5", "title": "비즈니스 스쿨 무료 강의", "topics": ["스쿨", "무료 강의"]},
]


@dataclass(frozen=True)
class Param:
    id: str
    label: str
    # string | integer | boolean | date | time | day | day_list | string_list | entry_list
    kind: str = "string"
    required: bool = True
    enum: tuple[str, ...] = ()
    hint: str = ""
    minimum: int | None = None
    max_items: int | None = None
    # entry_list: the fields of one entry (an object in the list).
    fields: tuple["Param", ...] = ()


@dataclass(frozen=True)
class FeatureTool:
    feature_id: str
    name: str
    params: tuple[Param, ...]
    handler: Callable[[DummyStore, dict], dict] = field(repr=False)


# One entry of update_business_hours: days that get the same hours, or that close.
HOURS_ENTRY = (
    Param(
        "days",
        "바꿀 요일·날짜 목록",
        kind="day_list",
        max_items=31,
        hint="임시 휴무 기간은 날짜를 모두 나열",
    ),
    Param(
        "closed",
        "그날 쉬는지 여부",
        kind="boolean",
        required=False,
        hint="쉬는 날이면 true, 생략하면 영업하는 날",
    ),
    Param(
        "open_time",
        "여는 시간",
        kind="time",
        required=False,
        hint="쉬지 않으면 필수, 지금 값이 있으면 생략 가능",
    ),
    Param(
        "close_time",
        "닫는 시간",
        kind="time",
        required=False,
        hint="쉬지 않으면 필수, 지금 값이 있으면 생략 가능",
    ),
    Param("break_start", "쉬는 시간(브레이크) 시작", kind="time", required=False),
    Param("break_end", "쉬는 시간(브레이크) 끝", kind="time", required=False),
)


def _object_schema(params: tuple[Param, ...]) -> dict:
    return {
        "type": "object",
        "properties": {p.id: _json_schema(p) for p in params},
        "required": [p.id for p in params if p.required],
        "additionalProperties": False,
    }


def _json_schema(param: Param) -> dict:
    description = param.label + (f" ({param.hint})" if param.hint else "")
    if param.kind == "entry_list":
        spec = {"type": "array", "items": _object_schema(param.fields), "minItems": 1}
    elif param.kind == "integer":
        spec = {"type": "integer"}
        if param.minimum is not None:
            spec["minimum"] = param.minimum
    elif param.kind == "boolean":
        spec = {"type": "boolean"}
    elif param.kind in ("string_list", "day_list"):
        spec = {"type": "array", "items": {"type": "string"}, "minItems": 1}
        if param.max_items:
            spec["maxItems"] = param.max_items
        if param.kind == "day_list":
            description += (
                " (요일 mon~sun, 날짜 YYYY-MM-DD, 또는 weekdays=평일·weekend=주말·everyday=매일)"
            )
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


def _invalid(params: tuple[Param, ...], arguments: dict) -> list[str]:
    """Labels of the given or required values that do not fit; inside an entry list, the
    labels of the entry fields that do not fit (여는 시간), not the list's own label."""
    labels: list[str] = []
    for p in params:
        value = arguments.get(p.id)
        if not p.required and value is None:
            continue
        if p.kind != "entry_list" or not isinstance(value, list) or not value:
            if not _valid(p, value):
                labels.append(p.label)
            continue
        for entry in value:
            if not isinstance(entry, dict) or set(entry) - {f.id for f in p.fields}:
                inner = [p.label]
            else:
                inner = _invalid(p.fields, entry)
            labels.extend(label for label in inner if label not in labels)
    return labels


def _valid(param: Param, value: object) -> bool:
    if param.kind == "entry_list":
        return False  # Checked field by field in _invalid.
    if param.kind == "integer":
        ok = type(value) is int and (param.minimum is None or value >= param.minimum)
    elif param.kind == "boolean":
        ok = type(value) is bool
    elif param.kind in ("string_list", "day_list"):
        ok = (
            isinstance(value, list)
            and bool(value)
            and (param.max_items is None or len(value) <= param.max_items)
            and all(isinstance(v, str) and normalize(v) for v in value)
            and (
                param.kind == "string_list"
                or all(v in WEEKDAYS or v in DAY_GROUPS or _is_date(v) for v in value)
            )
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


def _find_menu(menus: list[dict], name: str) -> dict | None:
    key = normalize(name)
    return next(
        (m for m in menus if key in [normalize(m["name"]), *map(normalize, m.get("aliases", []))]),
        None,
    )


def _menu(store, args):
    data = store.read()
    action = args.get("action") or "save"
    record = _find_menu(data["menus"], args["name"])
    if action != "save":
        if record is None:
            return _not_found("menu", data["menus"], "name")
        if action == "delete":
            data["menus"] = [m for m in data["menus"] if m["id"] != record["id"]]
            store.write(data)
            return {
                "ok": True,
                "action": "deleted",
                "record": {"id": record["id"], "name": record["name"]},
            }
        previous = record.get("sold_out", False)
        record["sold_out"] = action == "sold_out"
        store.write(data)
        result = _saved(store, "menus", record["id"], "updated")
        result["previous_sold_out"] = previous
        return result
    if record is None:
        if args.get("price") is None:
            return _needs(["가격 (원)"])
        if args.get("new_name"):
            return _not_found("menu", data["menus"], "name")
        record = {
            "id": str(uuid.uuid4()),
            "name": " ".join(args["name"].split()),
            "price": args["price"],
            "aliases": [],
        }
        if args.get("description"):
            record["description"] = " ".join(args["description"].split())
        data["menus"].append(record)
        store.write(data)
        return _saved(store, "menus", record["id"], "created")
    changes = [k for k in ("price", "new_name", "description") if args.get(k) is not None]
    if not changes:
        return _needs(["가격 (원)"])
    previous = {k: record.get(k) for k in ("name", "price", "description")}
    if args.get("new_name"):
        clash = _find_menu(data["menus"], args["new_name"])
        if clash is not None and clash["id"] != record["id"]:
            return {
                "ok": False,
                "error": "name_taken",
                "message": "같은 이름의 메뉴가 이미 있습니다.",
                "candidates": [{"id": clash["id"], "name": clash["name"]}],
            }
        record["name"] = " ".join(args["new_name"].split())
    if args.get("price") is not None:
        record["price"] = args["price"]
    if args.get("description"):
        record["description"] = " ".join(args["description"].split())
    store.write(data)
    result = _saved(store, "menus", record["id"], "updated")
    result["previous"] = previous
    result["previous_price"] = previous["price"]
    return result


BUSINESS_FIELDS = ("name", "address", "phone", "introduction")


def _business_info(store, args):
    given = {k: " ".join(args[k].split()) for k in BUSINESS_FIELDS if args.get(k)}
    if not given:
        return _needs(["바꿀 업체 정보 항목"])
    data = store.read()
    previous = {k: data["shop"].get(k) for k in given}
    data["shop"].update(given)
    store.write(data)
    shop = store.read()["shop"]
    return {
        "ok": True,
        "action": "updated",
        "previous": previous,
        "record": {k: shop[k] for k in given},
    }


def _expand_days(days: list[str]) -> list[str]:
    out: list[str] = []
    for day in days:
        for one in DAY_GROUPS.get(day, [day]):
            if one not in out:
                out.append(one)
    return out


def _hours_entry(hours: dict, entry: dict) -> tuple[list[str], list[str]] | dict:
    """Apply one entry (days with one set of hours, or closed) to the hours in place.
    Returns (changed days, closed days kept closed) or a needs-input result."""
    break_given = [entry.get("break_start"), entry.get("break_end")]
    if any(break_given) and not all(break_given):
        return _needs(["쉬는 시간 시작", "쉬는 시간 끝"])
    days = _expand_days(entry["days"])
    grouped = {d for g in entry["days"] if g in DAY_GROUPS for d in DAY_GROUPS[g]}
    skipped = []
    for day in days:
        old = hours.get(day) or {}
        if (
            day in grouped
            and day not in entry["days"]
            and old.get("closed")
            and not entry.get("closed")
            and not (entry.get("open_time") and entry.get("close_time"))
        ):
            skipped.append(day)  # 매일 브레이크: a closed day stays closed.
            continue
        if entry.get("closed"):
            hours[day] = {"closed": True}
            continue
        open_time = entry.get("open_time") or (None if old.get("closed") else old.get("open_time"))
        close_time = entry.get("close_time") or (
            None if old.get("closed") else old.get("close_time")
        )
        if not open_time or not close_time:
            return _needs(["여는 시간", "닫는 시간"])
        record = {"closed": False, "open_time": open_time, "close_time": close_time}
        if all(break_given):
            record.update(break_start=break_given[0], break_end=break_given[1])
        elif not old.get("closed") and old.get("break_start"):
            record.update(break_start=old["break_start"], break_end=old.get("break_end"))
        hours[day] = record
    return [d for d in days if d not in skipped], skipped


def _business_hours(store, args):
    """Each entry gives its days their own hours (월 10~21시, 금 11~22시 in one call); a
    later entry wins for a day two entries name."""
    data = store.read()
    hours = data["shop"].setdefault("hours", {})
    days: list[str] = []
    skipped: list[str] = []
    for entry in args["entries"]:
        applied = _hours_entry(hours, entry)
        if isinstance(applied, dict):
            return applied
        days += [d for d in applied[0] if d not in days]
        skipped += [d for d in applied[1] if d not in skipped]
    skipped = [d for d in skipped if d not in days]
    store.write(data)
    saved = store.read()["shop"]["hours"]
    result = {
        "ok": True,
        "action": "updated",
        "days": days,
        "record": {day: saved[day] for day in days},
    }
    if skipped:
        result["kept_closed"] = skipped
    return result


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
        if args.get("reason"):
            matches[0]["reason"] = " ".join(args["reason"].split())
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
    if not any(args.get(k) for k in ("keywords", "add", "remove")):
        return _needs(["더하거나 뺄 대표키워드"])
    data = store.read()
    previous = data["shop"].get("keywords", [])
    current = [" ".join(k.split()) for k in (args.get("keywords") or previous)]
    remove = {normalize(k) for k in args.get("remove") or []}
    absent = [k for k in args.get("remove") or [] if normalize(k) not in map(normalize, current)]
    if absent:
        return {
            "ok": False,
            "error": "keyword_not_found",
            "message": "지금 대표키워드에 없는 단어입니다: " + ", ".join(absent),
            "current": previous,
        }
    current = [k for k in current if normalize(k) not in remove]
    for k in args.get("add") or []:
        k = " ".join(k.split())
        if normalize(k) not in map(normalize, current):
            current.append(k)
    current = list(dict.fromkeys(current))
    if len(current) > 5:
        return {
            "ok": False,
            "error": "too_many_keywords",
            "message": "대표키워드는 최대 5개입니다. 사장님이 목록을 정해 준 것이면 keywords로 "
            "전체를 바꾸고, 더하는 것이면 뺄 키워드를 사장님께 물어보세요.",
            "current": previous,
            "after": current,
        }
    data["shop"]["keywords"] = current
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


def _search_diagnosis(store, args):
    # The diagnosis text is built by the assistant (live search, report facts); this only
    # marks the read so the tool registry stays one feature, one tool.
    return {"ok": True, "action": "read"}


# Words that say "I want to learn" without a topic; a query of only these gets the general
# education entry (비즈니스 스쿨). Any other word must match an article's topic.
HELP_GENERIC = re.compile(
    r"교육|강의|수업|배우|배울|공부|듣|싶|찾아|알려|주세요|줘|관련|방법|하는|법|있|뭐|어디|좀|"
    r"스마트플레이스|플레이스|네이버|사용|쓰는"
)


def _help(store, args):
    key = normalize(args["query"])
    found = [
        a
        for a in HELP_ARTICLES
        if any(normalize(k) in key for k in a["topics"]) or key in normalize(a["title"])
    ]
    if not found and all(HELP_GENERIC.search(word) for word in key.split()):
        found = [a for a in HELP_ARTICLES if a["id"] == "help-5"]
    if found:
        return {"ok": True, "action": "read", "found": True, "records": found}
    # Nothing matches: say so and point to the customer center (not a matching article).
    return {
        "ok": True,
        "action": "read",
        "found": False,
        "records": [],
        "fallback": HELP_ARTICLES[3:4],
        "message": "맞는 교육·도움말을 찾지 못했습니다. 고객센터 문의를 안내하세요.",
    }


_TOOL_LIST = [
    FeatureTool(
        "business_info",
        "update_business_info",
        (
            Param("name", "새 상호(가게 이름)", required=False),
            Param("address", "새 주소", required=False),
            Param("phone", "새 전화번호", required=False),
            Param("introduction", "새 가게 소개글", required=False),
        ),
        _business_info,
    ),
    FeatureTool(
        "menu",
        "upsert_menu",
        (
            Param("name", "메뉴 이름", hint="바꾸거나 지울 메뉴는 지금 이름"),
            Param(
                "action",
                "할 일",
                required=False,
                enum=("save", "delete", "sold_out", "on_sale"),
                hint="save=등록·수정(기본), delete=메뉴 삭제, sold_out=품절, on_sale=품절 해제",
            ),
            Param(
                "price",
                "가격 (원)",
                kind="integer",
                required=False,
                minimum=1,
                hint="새 메뉴면 필수",
            ),
            Param("new_name", "바꿀 새 메뉴 이름", required=False),
            Param("description", "메뉴 설명", required=False, hint="사장님이 말한 문장 그대로"),
        ),
        _menu,
    ),
    FeatureTool(
        "business_hours",
        "update_business_hours",
        (
            Param(
                "entries",
                "요일별 영업시간",
                kind="entry_list",
                hint="같은 시간을 받는 요일·날짜마다 항목 하나. 요일마다 시간이 다르면 항목을 "
                "나눈다",
                fields=HOURS_ENTRY,
            ),
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
            Param("reason", "거절 사유", required=False, hint="사장님이 말했을 때만"),
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
                hint="confirmed=확정(예약 받기), cancelled=취소, no_show=노쇼",
            ),
            Param("reason", "취소 사유", required=False, hint="사장님이 말했을 때만"),
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
        (
            Param(
                "add",
                "더할 대표키워드",
                kind="string_list",
                required=False,
                max_items=5,
                hint="지금 키워드는 두고 '추가', '하나 더'라고 할 때만",
            ),
            Param(
                "remove",
                "뺄 대표키워드",
                kind="string_list",
                required=False,
                max_items=5,
                hint="지금 키워드 중 빼라고 한 것",
            ),
            Param(
                "keywords",
                "새 대표키워드 전체 목록",
                kind="string_list",
                required=False,
                max_items=5,
                hint="사장님이 목록을 정해 줄 때('이 세 개로', '~로 바꿔·넣어·해'). 최대 5개",
            ),
        ),
        _keywords,
    ),
    FeatureTool(
        "search_diagnosis",
        "diagnose_search_visibility",
        (),
        _search_diagnosis,
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
                enum=PERIODS,
                hint=PERIOD_HINT,
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
# Handled by agenthan.agent.review (ReviewToolbox + run_turn), not by a tool in this module.
DELEGATED_FEATURES = frozenset({"review"})
# Every other catalog feature has exactly one tool name.
TOOL_FEATURES = {t.name: k for k, t in TOOLS.items()}
# Tools that only read. Every other tool writes the store and needs the owner's approval.
READ_ONLY_TOOLS = frozenset(
    {
        "list_call_history",
        "get_naverpay_signup_guide",
        "get_settlement_summary",
        "check_pos_compatibility",
        "diagnose_search_visibility",
        "get_statistics",
        "search_help_articles",
    }
)


def needs_approval(tool: str | None) -> bool:
    """Unknown tools are conservatively treated as writes; no call needs no approval."""
    return tool is not None and tool not in READ_ONLY_TOOLS


def feature_tool_schema(feature_id: str) -> dict:
    """Schema for any non-delegated feature."""
    tool = TOOLS.get(feature_id)
    feature = get_feature(feature_id)
    if tool is None or feature is None:
        raise ValueError("Unsupported feature")
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": f"{feature['name_ko']}: {feature['summary_ko']} {DUMMY_NOTE}",
            "parameters": _object_schema(tool.params),
        },
    }


def all_tool_schemas() -> list[dict]:
    """All 17 agenthan tools in catalog order (review features are delegated)."""
    return [
        feature_tool_schema(f["id"]) for f in load_features() if f["id"] not in DELEGATED_FEATURES
    ]


def execute_feature_tool(store: DummyStore, name: str, arguments: object) -> dict:
    """Run any of the 17 tools by name after validating its arguments."""
    feature_id = TOOL_FEATURES.get(name)
    if feature_id is None:
        return {"ok": False, "error": "tool_not_allowed", "message": "허용되지 않은 도구입니다."}
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
    invalid = _invalid(tool.params, arguments)
    if invalid:
        return _needs(invalid)
    return tool.handler(store, arguments)
