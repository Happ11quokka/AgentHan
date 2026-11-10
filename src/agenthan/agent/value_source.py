"""Value-source gate: a write runs only with values the owner said or a lookup confirmed.

For each argument of a write tool, find where the value came from:
- owner: it appears in what the owner said during this task (Korean number, date, time
  and weekday expressions are converted first, so 만 오천 원, 내일, 저녁 8시 still count);
- lookup: a record id the owner pointed at (by id, or by a description only one record fits);
- rule: an enum or flag the owner's words map to (확정 → confirmed, 켜 → true).
Anything else is unconfirmed: the assistant must ask instead of previewing it.
"""

import re
import unicodedata
from datetime import date, timedelta

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_WORDS = {
    "mon": r"월요일|월욜|monday|\bmon\b",
    "tue": r"화요일|화욜|tuesday|\btue\b",
    "wed": r"수요일|수욜|wednesday|\bwed\b",
    "thu": r"목요일|목욜|thursday|\bthu\b",
    "fri": r"금요일|금욜|friday|\bfri\b",
    "sat": r"토요일|토욜|saturday|\bsat\b",
    "sun": r"일요일|일욜|sunday|\bsun\b",
}
# enum / boolean values → owner words that mean them.
ENUM_WORDS = {
    "confirmed": r"확정|승인|confirm",
    "cancelled": r"취소|cancel",
    "no_show": r"노\s*쇼|안\s*왔|no.?show",
    "accepted": r"접수|수락|받아|받을게|accept",
    "rejected": r"거절|못\s*받|reject",
    "ready": r"준비|ready",
    "completed": r"완료|전달|가져갔|complete",
    "amount": r"\d\s*원|천\s*원|만\s*원|원\s*할인|won",
    "percent": r"%|퍼센트|프로|percent",
    "individual": r"개인",
    "corporation": r"법인",
    "this_week": r"이번\s*주|this week",
    "this_month": r"이번\s*달|이달|this month",
    "last_month": r"지난\s*달|저번\s*달|last month",
    "7d": r"7\s*일|일주일|이번\s*주|최근|week",
    "30d": r"30\s*일|한\s*달|month",
    "name": r"상호|가게\s*이름|가게명|이름",
    "address": r"주소|이사",
    "phone": r"전화|번호",
    "introduction": r"소개",
}
BOOL_WORDS = {
    "enabled": (r"켜|사용|쓸게|쓰고|받을|on\b|enable", r"끄|꺼|안\s*쓸|안\s*받|off\b|disable"),
    "closed": (r"쉬|휴무|닫|안\s*열|closed", r"열|영업"),
    "missed_only": (r"부재", r"전부|모두|다\s*보여"),
}
DIGITS = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7}
DIGITS |= {"팔": 8, "구": 9, "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}
UNITS = {"십": 10, "백": 100, "천": 1000}
NUMBER = re.compile(r"[\d,]+(?:\.\d+)?|[영공일이삼사오육칠팔구십백천만한두세네]+")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\s'\"‘’“”.,!?~·()\[\]-]+", "", text)


def korean_number(token: str) -> int | None:
    """일만 오천 → 15000, 삼천오백 → 3500, 12,000 → 12000."""
    token = token.replace(",", "").replace(" ", "")
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total, section, digit = 0, 0, None
    for ch in token:
        if ch.isdigit():
            digit = (digit or 0) * 10 + int(ch)
        elif ch in DIGITS:
            digit = DIGITS[ch]
        elif ch in UNITS:
            section += (digit if digit is not None else 1) * UNITS[ch]
            digit = None
        elif ch == "만":
            total += (section + (digit or 0) or 1) * 10000
            section, digit = 0, None
        else:
            return None
    return total + section + (digit or 0)


def numbers(text: str) -> set[int]:
    """Every number the text states, including mixed forms (1만 2천, 만 오천 원)."""
    found: set[int] = set()
    compact = re.sub(r"(?<=[\d만천백십])\s+(?=[\d영공일이삼사오육칠팔구십백천만])", "", text)
    for match in re.finditer(r"[\d,]*\d[\d,]*|[\d영공일이삼사오육칠팔구십백천만]+", compact):
        token = match.group()
        if not re.search(r"\d|[십백천만]", token):
            continue  # Bare 일/이/사 are usually words (일요일, 이름, 사진).
        value = korean_number(token)
        if value is not None:
            found.add(value)
    return found


def _dates(text: str, today: date) -> set[str]:
    out = set(re.findall(r"\d{4}-\d{2}-\d{2}", text))
    for m in re.finditer(r"(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일(?!\s*(?:동안|간|요일))", text):
        month = int(m.group(1)) if m.group(1) else today.month
        day = int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                out.add(date(year, month, day).isoformat())
            except ValueError:
                pass
    for word, delta in (("오늘", 0), ("내일", 1), ("모레", 2), ("글피", 3)):
        if word in text:
            out.add((today + timedelta(days=delta)).isoformat())
    return out


def _times(text: str) -> set[str]:
    out = set(re.findall(r"\b(?:[01]\d|2[0-4]):[0-5]\d\b", text))
    for m in re.finditer(
        r"(새벽|아침|오전|낮|점심|오후|저녁|밤)?\s*(\d{1,2}|[한두세네다섯여섯일곱여덟아홉열]+)\s*시"
        r"\s*(반|\d{1,2}\s*분)?",
        text,
    ):
        hour = _hour(m.group(2))
        if hour is None:
            continue
        minute = 30 if m.group(3) == "반" else int(re.sub(r"\D", "", m.group(3) or "0") or 0)
        hours = {hour}
        if m.group(1) in ("오후", "저녁", "밤") or (m.group(1) == "낮" and hour < 6):
            hours = {hour + 12 if hour < 12 else hour}
        elif m.group(1) is None and hour <= 12:
            hours = {hour, hour + 12}  # 8시 may be 08:00 or 20:00; both count as said.
        out.update(f"{h:02d}:{minute:02d}" for h in hours if h <= 24)
    return out


def _hour(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    words = ["한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "열한", "열두"]
    return words.index(token) + 1 if token in words else None


def _record_words(record: dict) -> set[str]:
    words = set()
    for key, value in record.items():
        if key in ("id", "status", "reply_text"):
            continue
        if isinstance(value, str):
            words.update(w for w in re.split(r"[\s,()]+", value) if len(w) >= 2)
    return words


MEALS = ("아침", "점심", "저녁")


def _meal(time: str) -> str:
    hour = int(time[:2])
    return "점심" if 11 <= hour <= 14 else "저녁" if hour >= 17 else "아침"


def candidates(records: list[dict], said: str, today: date) -> tuple[list[dict], bool]:
    """Records the owner's words point at, and whether the owner stated any condition.

    A record id said outright wins. Otherwise every condition the owner stated (date, clock
    time or meal, people count, words of an item or message) must hold for a record that
    has that field; a field the record lacks does not narrow it. Nothing is assumed: with no
    condition stated, every record is returned and stated is False."""
    said_ids = [r for r in records if r.get("id") and _norm(r["id"]) in _norm(said)]
    if said_ids:
        return said_ids, True
    dates = _dates(said, today)
    times = _times(said)
    meals = [m for m in MEALS if m in said]
    people = {int(n) for n in re.findall(r"(?<!\d)(\d{1,3})\s*(?:명|분|인)", said)}
    said_norm = _norm(said)

    def words(record: dict) -> set[str]:
        return {w for w in _record_words(record) if not w[0].isdigit() and _norm(w) in said_norm}

    by_words = any(words(r) for r in records)
    stated = bool(dates or times or meals or people or by_words)
    fits = []
    for record in records:
        if dates and "date" in record and record["date"] not in dates:
            continue
        if "time" in record and (times or meals):
            if times and record["time"] not in times:
                continue
            if not times and _meal(record["time"]) not in meals:
                continue
        if people and "people" in record and record["people"] not in people:
            continue
        if by_words and not words(record):
            continue
        fits.append(record)
    return fits, stated


def _pointed(records: list[dict], value: object, said: str, today: date) -> bool:
    """The record id was said, or it is the only record every stated condition fits."""
    if not isinstance(value, str):
        return False
    if _norm(value) in _norm(said):
        return True
    fits, stated = candidates(records, said, today)
    return stated and len(fits) == 1 and fits[0].get("id") == value


def confirmed(
    param_id: str,
    kind: str,
    value: object,
    said: str,
    store: dict,
    *,
    today: date | None = None,
    enum: tuple[str, ...] = (),
) -> str | None:
    """The source of one argument value (owner / lookup / rule), or None if unconfirmed."""
    today = today or date.today()
    if value is None:
        return "rule"
    if param_id in ("order_id", "reservation_id"):
        collection = "orders" if param_id == "order_id" else "reservations"
        return "lookup" if _pointed(store.get(collection, []), value, said, today) else None
    if param_id == "inquiry":
        talks = store.get("talktalk", [])
        if isinstance(value, str) and value and _norm(value) in _norm(said):
            return "owner"
        match = [t for t in talks if t.get("id") == value or value in t.get("message", "")]
        if len(match) == 1 and (_pointed(talks, match[0]["id"], said, today)):
            return "lookup"
        return None
    if kind == "boolean" or isinstance(value, bool):
        yes, no = BOOL_WORDS.get(param_id, (r"(?!)", r"(?!)"))
        return "rule" if re.search(yes if value else no, said, re.I) else None
    if enum:
        return "rule" if re.search(ENUM_WORDS.get(str(value), r"(?!)"), said, re.I) else None
    if kind == "integer" or (isinstance(value, int) and not isinstance(value, bool)):
        return "owner" if value in numbers(said) else None
    if kind == "date":
        return "owner" if value in _dates(said, today) else None
    if kind == "time":
        return "owner" if value in _times(said) else None
    if kind == "day":
        if value in WEEKDAYS:
            return "owner" if re.search(DAY_WORDS[value], said, re.I) else None
        return "owner" if value in _dates(said, today) else None
    if kind == "string_list":
        ok = isinstance(value, list) and all(
            isinstance(v, str) and _norm(v) in _norm(said) for v in value
        )
        return "owner" if ok else None
    if isinstance(value, str):
        if param_id == "value" and re.fullmatch(r"[\d\s()+-]{7,}", value):
            digits = re.sub(r"\D", "", value)
            return "owner" if digits and digits in re.sub(r"\D", "", said) else None
        if _norm(value) and _norm(value) in _norm(said):
            return "owner"
        # Owner-dictated text the model wrapped in a greeting: the quoted part must be kept.
        quoted = re.findall(r"['‘“\"]([^'’”\"]{4,})['’”\"]", said)
        if quoted and any(_norm(q) in _norm(value) for q in quoted):
            return "owner"
        return None
    return None


def check(
    params: list[tuple[str, str, tuple[str, ...], str]],
    arguments: dict,
    said: str,
    store: dict,
    *,
    today: date | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Sources of every given argument, and labels of the ones the owner did not confirm.

    params: (id, kind, enum, label) of the tool's parameters."""
    sources: dict[str, str] = {}
    unconfirmed: list[str] = []
    for param_id, kind, enum, label in params:
        if param_id not in arguments:
            continue
        value = arguments[param_id]
        if param_id == "closed" and value is False and arguments.get("open_time"):
            source = "rule"  # Opening hours given: the day is open.
        else:
            source = confirmed(param_id, kind, value, said, store, today=today, enum=enum)
        if source is None:
            unconfirmed.append(label)
        else:
            sources[param_id] = source
    return sources, unconfirmed
