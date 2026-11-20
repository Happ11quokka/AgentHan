"""Owner-screen pilot seed from a store data folder (data/clean, fixtures/dummy_store).

The folder (Part B format: store.json, menus.json, reviews.json) gives the shop, its menus
and reviews. What it does not have (talktalk, orders, reservations, calls, coupons,
settlements, statistics) comes from the made-up pilot shop, with menu names swapped for this
shop's menus so the screen still tells one story. Real data never leaves data/.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA_DIR_ENV = "AGENTHAN_DATA_DIR"  # the same switch as the store knowledge RAG
REPO_ROOT = Path(__file__).resolve().parents[3]
KST = timezone(timedelta(hours=9))
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = {
    "월": ("mon",),
    "화": ("tue",),
    "수": ("wed",),
    "목": ("thu",),
    "금": ("fri",),
    "토": ("sat",),
    "일": ("sun",),
    "매일": DAYS,
    "평일": DAYS[:5],
    "주말": DAYS[5:],
}
# Crawl notes in Naver menu descriptions, not menu text: "Best 표시", "(네이버 …)".
CRAWL_NOTE = re.compile(r"\([^)]*네이버[^)]*\)|\b(?:best!?|new)\s*표시", re.IGNORECASE)


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _read(folder: Path, name: str, default):
    f = folder / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default


def _hours(store: dict) -> dict:
    hours = {day: {"closed": True} for day in DAYS}
    for row in store.get("business_hours") or []:
        if not row.get("start") or not row.get("end"):
            continue
        for day in DAY_LABELS.get(str(row.get("day", "")).strip(), ()):
            hours[day] = {"closed": False, "open_time": row["start"], "close_time": row["end"]}
    for label in store.get("regular_closed_days") or []:
        for day in DAY_LABELS.get(str(label).strip(), ()):
            hours[day] = {"closed": True}
    return hours


def _description(text: str) -> str:
    text = CRAWL_NOTE.sub("", text or "")
    return re.sub(r"^[\s,/]+|[\s,/]+$", "", re.sub(r"\s*,\s*(?=,|$)", "", text)).strip()


def _popularity(menu: dict) -> int:
    return ((menu.get("extra") or {}).get("popularity") or {}).get("review_mentions") or 0


# Search-only menu fields (Part B): kept in the shop file for the store knowledge RAG
# (rag.ShopDataRepository reads them), hidden from model prompts (owner_assistant.HIDDEN_KEYS).
SEARCH_FIELDS = ("family", "tags", "popularity", "gift", "taste")


def _menu(m: dict) -> dict:
    row = {
        "id": m["id"],
        "name": m["name_ko"],
        "price": m["price"],
        "aliases": list(m.get("aliases") or []),
    }
    if m.get("name_native"):
        row["name_native"] = m["name_native"]
    if description := _description(m.get("description", "")):
        row["description"] = description
    for key in ("category", "options", "allergens"):
        if m.get(key):
            row[key] = m[key]
    extra = m.get("extra") or {}
    for key in SEARCH_FIELDS:
        if extra.get(key):
            row[key] = extra[key]
    return row


def _visit_date(r: dict) -> str:
    visited = (r.get("extra") or {}).get("visit_datetime")
    when = visited or r["created_at"]
    return datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(KST).date().isoformat()


def _review(r: dict) -> dict:
    reply = (r.get("extra") or {}).get("owner_reply")
    row = {
        "review_id": r["id"],
        "visit_date": _visit_date(r),
        "rating": r.get("rating"),
        "text": r["text"],
        "features": [],
        "has_owner_reply": bool(reply),
    }
    if reply:
        text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
        row["owner_reply"] = {"reply": text, "status": "published"}
    return row


def _introduction(store: dict, menus: list[dict]) -> str:
    near = (store.get("subway") or [""])[0]
    top = ", ".join(m["name_ko"] for m in sorted(menus, key=_popularity, reverse=True)[:3])
    head = f"{near} 근처 {store.get('category', '가게')}" if near else store.get("category", "")
    return f"{head}. 대표 메뉴: {top}".strip(". ")


def _story(pilot: dict, menus: list[dict], shop: dict, store: dict) -> dict:
    """The pilot's made-up records, retold with this shop's menus and hours."""
    by_pop = sorted(menus, key=_popularity, reverse=True)
    drinks = [m for m in by_pop if m.get("category") in ("Coffee", "Tea", "Ade", "음료")] or by_pop
    foods = [m for m in by_pop if m not in drinks] or by_pop
    name = lambda items, i: items[i % len(items)]["name_ko"]  # noqa: E731
    whole = max(menus, key=lambda m: m["price"])["name_ko"]
    sun = shop["hours"]["sun"]
    sunday = (
        f"네, 일요일도 {sun['open_time']}~{sun['close_time']}에 열어요!"
        if not sun["closed"]
        else "일요일은 쉽니다."
    )
    takeout = "포장" in (store.get("conveniences") or [])
    talktalk = [
        {"id": "talk-1", "message": f"{whole} 예약이 가능한가요?"},
        {
            "id": "talk-2",
            "message": f"{name(foods, 0)} 단체 주문도 받으시나요? 회사 행사로 20조각 정도요.",
        },
        {"id": "talk-3", "message": "주차 가능한가요?"},
        {"id": "talk-4", "message": "일요일에도 여나요?", "reply_text": sunday},
        {"id": "talk-5", "message": "웨이팅 예약 같은 건 없나요? 토요일 저녁에 가려고요."},
        {
            "id": "talk-6",
            "message": f"{name(foods, 1)} 포장 되나요?",
            "reply_text": "네, 포장 가능합니다." if takeout else "매장에서 드시는 것만 돼요.",
        },
    ]
    for t in talktalk:
        t.setdefault("reply_text", "")
        t["status"] = "answered" if t["reply_text"] else "unanswered"
    orders = [
        (f"{name(foods, 0)} 2조각, {name(drinks, 0)} 2잔", "pickup", "new"),
        (f"{name(foods, 2)} 1개, {name(foods, 1)} 1조각", "pickup", "new"),
        (f"{whole} 1개", "pickup", "new"),
        (f"{name(foods, 3)} 4개, {name(drinks, 1)} 2잔", "delivery", "accepted"),
        (f"{name(foods, 4)} 1개, {name(foods, 5)} 1개", "delivery", "completed"),
        (f"{name(foods, 6)} 2개, {name(drinks, 2)} 1잔", "pickup", "completed"),
    ]
    coupon = dict(pilot["coupons"][0], title=f"평일 오후 {name(drinks, 0)} 1,000원 할인")
    return {
        "talktalk": talktalk,
        "orders": [
            {"id": f"order-{i}", "items": items, "type": kind, "status": status}
            for i, (items, kind, status) in enumerate(orders, 1)
        ],
        "coupons": [coupon],
    }


def pilot_seed(folder: str | Path, pilot: dict, today: date) -> dict:
    """The pilot store for the shop in `folder`, with records up to `today` only."""
    folder = resolve(folder)
    store = _read(folder, "store.json", {})
    raw_menus = [m for m in _read(folder, "menus.json", []) if m.get("price")]
    if not raw_menus:
        raise ValueError(f"{folder}/menus.json에 가격 있는 메뉴가 없습니다.")
    shop = {
        "id": str(store.get("place_id") or folder.name),
        "name": store.get("name") or folder.name,
        "address": store.get("road_address") or store.get("address") or "",
        "phone": store.get("phone") or "",
        "introduction": _introduction(store, raw_menus),
        "hours": _hours(store),
        "keywords": list(store.get("keywords") or []),
        "onsite_payment": False,
        "new_opening": None,
        "category": store.get("category") or "",
    }
    reviews = [_review(r) for r in _read(folder, "reviews.json", []) if r.get("text")]
    reviews = [r for r in reviews if r["visit_date"] <= today.isoformat()]
    return {
        **pilot,
        "shop": shop,
        "menus": [_menu(m) for m in raw_menus],
        "families": _read(folder, "families.json", []),
        "reviews": reviews,
        **_story(pilot, raw_menus, shop, store),
    }
