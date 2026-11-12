"""S1 회귀 기준선(tests/data/eval/s1.jsonl)을 기존 한국어 케이스로 만든다 (정본 §5).

새 문장은 만들지 않는다. 출처와 변환 규칙:
- P2·P3·P4·P5·P6·P7·P8의 뼈대: 기존 시나리오(tests/data/eval/scenario.jsonl) 30건 그대로.
- P1 단일 작업: flow 한국어 `full`+인자. 쓰기 도구는 요청 + "네"이고, 최종 상태는 정답 도구·인자를
  더미 저장소에 실제로 실행해 계산한다. 조회 도구는 요청 한 턴이고, 같은 실행 결과에서 답에 들어가야
  할 사실(금액·건수·서류 등)을 뽑는다(검색 노출 진단은 실시간 검색이라 제외).
- 기존 시나리오의 끼어드는 조회 질문에도 더미 가게 값으로 답 사실을 붙인다.
- P2·P3 보충: flow 한국어 여러 턴(`multi:ask_fill|confirm|decline|revise`). 같은 방식으로 계산한다.
- P6·P8 보충: route 한국어 `ambiguous`·`injection` 한 턴. 저장소가 바뀌면 안 된다.
- P7 보충: flow 한국어 `unrelated` 한 턴. 저장소가 바뀌면 안 된다.
- P9: 리뷰 골드셋의 답글 흐름을 더미 가게 리뷰(답글 없는 r02·r03, 부정 리뷰 r03)에 맞춰 옮긴다.

원래 holdout이던 케이스는 sealed가 된다. 기존 시나리오는 실패 내용을 이미 봤으므로 전부 dev다.
선택은 ID 해시 순서라 다시 돌려도 같은 결과가 나온다.

    .venv/bin/python scripts/build_s1.py            # s1.jsonl 다시 쓰기
    .venv/bin/python scripts/build_s1.py --check    # 파일이 지금 규칙과 같은지만 확인
"""

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from agenthan.eval.scenario import MISSING, Scenario, apply_setup, changes, resolve
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import READ_ONLY_TOOLS, TOOL_FEATURES, execute_feature_tool
from agenthan.tools.place_report import load_report, report_facts

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/eval"
OUT = DATA / "s1.jsonl"
KOREAN = ("ko", "ko_simple")
TODAY = "2026-11-10"
REPEAT = 3
# Sealed scenarios whose failures were looked at while tuning (2026-11-12 night loop): dev now.
SEEN = {
    "p1-set_representative_keywords-aug-ab07b8": "sealed-seen-2026-11-12",
    "p3-update_reservation_status-confirm-292455": "sealed-seen-2026-11-12",
    "p9-W06": "sealed-seen-2026-11-12",
}
# §5 target counts. P4·P5 have only the existing scenarios.
TARGET = {"P1": 28, "P2": 12, "P3": 12, "P4": 7, "P5": 9, "P6": 4, "P7": 8, "P8": 4, "P9": 8}
FAMILY_TYPE = {
    "approval_interject": "P4",
    "photo_interject": "P4",
    "revise_decline": "P3",
    "slot_switch": "P5",
    "same_feature_two": "P5",
    "long_session": "P5",
    "missing_value": "P2",
    "unrelated": "P7",
    "ambiguous_ref": "P6",
    "injection": "P8",
}
TYPE_FAMILY = {
    "P1": "single_task",
    "P2": "missing_value",
    "P3": "revise_decline",
    "P6": "ambiguous_ref",
    "P7": "unrelated",
    "P8": "injection",
    "P9": "review_reply",
}
WRITE_AREA = {
    "shop.phone": "business_info",
    "shop.address": "business_info",
    "shop.name": "business_info",
    "shop.description": "business_info",
    "shop.hours": "business_hours",
    "shop.keywords": "search_keywords",
    "shop.onsite_payment": "onsite_payment",
    "shop.new_opening": "new_opening",
    "menus": "menu",
    "reservations": "reservation",
    "orders": "order",
    "coupons": "coupon",
    "talktalk": "talktalk",
    "reviews": "review",
}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def order(cases: list[dict]) -> list[dict]:
    return sorted(cases, key=lambda c: hashlib.sha256(c["id"].encode()).hexdigest())


def seed_store() -> DummyStore:
    return DummyStore(Path(tempfile.mkdtemp()) / "store.json")


def write_prefix(path: str) -> str:
    """`orders[order-2].status` → `orders[order-2]`, `shop.hours.mon.x` → `shop.hours`."""
    if "[" in path:
        return path[: path.index("]") + 1]
    return ".".join(path.split(".")[:2])


def area_of(writes: list[str]) -> str:
    areas = {
        area
        for w in writes
        for prefix, area in WRITE_AREA.items()
        if w == prefix or w.startswith((prefix + ".", prefix + "["))
    }
    return areas.pop() if len(areas) == 1 else ("none" if not areas else "multi")


def simulate(calls: list[tuple[str, dict]]) -> tuple[dict, dict]:
    """Run tool calls on a fresh dummy store; return (before, after)."""
    store = seed_store()
    before = store.read()
    for name, args in calls:
        result = execute_feature_tool(store, name, args)
        if not result.get("ok"):
            raise ValueError(f"gold call failed: {name} {args} → {result}")
    return before, store.read()


def _new_record(before: dict, after: dict, path: str) -> tuple[str, str] | None:
    """`menus[<new id>]…` → ("menus", "menus.4"): new records are asserted by list position
    because their ids may be random."""
    if "[" not in path:
        return None
    name, key = path[: path.index("[")], path[path.index("[") + 1 : path.index("]")]
    if resolve(before, f"{name}[{key}]") is not MISSING:
        return None
    items = resolve(after, name)
    index = next(i for i, item in enumerate(items) if item.get("id") == key)
    return name, f"{name}.{index}"


def final_of(before: dict, after: dict) -> tuple[dict, list[str]]:
    """Assertions on every changed value and the record-level write prefixes."""
    final: dict = {}
    writes: set[str] = set()
    paths = changes(before, after)
    for path in paths:
        new = _new_record(before, after, path)
        if new:
            name, position = new
            writes.add(name)
            final[f"{name}.#"] = len(resolve(after, name))
            for key, item in resolve(after, position).items():
                if key != "id":  # ids of new records are not part of the owner's request
                    final[f"{position}.{key}"] = item
            continue
        writes.add(write_prefix(path))
        head, _, last = path.rpartition(".")
        if last.isdigit() and isinstance(resolve(after, head), list):
            final[head] = {"$set": resolve(after, head)}  # e.g. shop.keywords
            continue
        value = resolve(after, path)
        if isinstance(value, dict):  # new record: assert each field
            for key, item in value.items():
                final[f"{path}.{key}"] = item
        elif value is not MISSING:
            final[path] = value
    return final, sorted(writes)


def scenario(id_, kind, *, area, source, lang, split, turns, final, writes, never=()):
    data = {
        "id": id_,
        "family": TYPE_FAMILY.get(kind, kind),
        "type": kind,
        "area": area,
        "source": source,
        "lang": lang,
        "split": "sealed" if split == "holdout" else "dev",
        "tags": ["s1"],
        "repeat": REPEAT,
        "today": TODAY,
        "turns": turns,
        "expect": {"final": final, "writes": writes, "never": list(never)},
    }
    Scenario.model_validate(data)
    return data


# ------------------------------------------------------------------ answer facts


def number(n: int) -> list[str]:
    """116400 → 116400, 11만6400, 11만6천4백 (commas and spaces are ignored when scoring)."""
    out = [str(n)]
    if n >= 10000:
        q, r = divmod(n, 10000)
        out.append(f"{q}만" if r == 0 else f"{q}만{r}")
        if r and r % 100 == 0:
            thousands, hundreds = divmod(r, 1000)
            out.append(
                f"{q}만"
                + (f"{thousands}천" if thousands else "")
                + (f"{hundreds // 100}백" if hundreds else "")
            )
    return out


def day(iso: str) -> list[str]:
    y, m, d = iso.split("-")
    return [iso, f"{int(m)}월{int(d)}일", f"{int(m)}/{int(d)}"]


def count(n: int, unit: str) -> list[str]:
    korean = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯"}
    units = [unit, "개"] if unit == "건" else [unit]
    return [f"{n}{u}" for u in units] + [f"{korean[n]}{u}" for u in units if n in korean]


# Ways to say "not supported" (spaces are ignored when scoring, so 안 돼 = 안돼).
NEGATIVE = [
    "지원하지",
    "지원되지",
    "지원안",
    "연동되지",
    "연결되지",
    "호환되지",
    "연동이안",
    "연결이안",
    "호환이안",
    "연동안",
    "연결안",
    "안돼",
    "안되",
    "불가",
    "어려",
    "어렵",
    "안됩",
    "안되어",
    "목록에없",
    "포함되지",
    "포함되어있지",
    "확인되지않",
    "수없",
    "수는없",
    "없지만",
    "없어요",
    "없습니다",
]
# No matching help article: the reply says none was found or points to the customer center.
HELP_NOT_FOUND = ["고객센터", "찾지못", "못찾", "없어요", "없습니다", "없네요"]


def read_facts(tool: str, args: dict, data: dict | None = None, want: str = "") -> dict | None:
    """Answer spec from the dummy store's own tool result; None when nothing is checkable."""
    store = seed_store()
    if data is not None:
        store.write(data)
    result = execute_feature_tool(store, tool, args)
    if not result.get("ok"):
        raise ValueError(f"gold read failed: {tool} {args} → {result}")
    if tool == "get_settlement_summary":
        record = result["record"]
        if want == "date":
            return {"all": [day(record["payout_date"])]}
        return {
            "all": [number(record["payout"]) + (number(record["sales"]) if want == "any" else [])]
        }
    if tool == "get_statistics":
        # The app answers statistics questions from the R1 weekly report, not this tool.
        facts = report_facts(load_report(), period="week")
        return {"all": [number(facts.place_views.current)]}
    if tool == "list_call_history":
        records = result["records"]
        n = len(records)
        return {"all": [count(n, "통") + [f"{n}건"] + [r["time"][-5:] for r in records]]}
    if tool == "get_naverpay_signup_guide":
        return {"all": [[doc.split()[-1]] for doc in result["documents"]]}
    if tool == "check_pos_compatibility":
        return {
            "all": [NEGATIVE if not result["supported"] else ["가능", "돼요", "됩니다", "지원"]]
        }
    if tool == "search_help_articles":
        if not result["found"]:
            return {"all": [HELP_NOT_FOUND]}
        return {"all": [[r["title"] for r in result["records"]]]}
    return None


def interject_answer(say: str, data: dict) -> dict | None:
    """Facts for the read questions inside the existing scenarios (computed on their store)."""
    if "정산" in say:
        if "언제" in say:
            return read_facts("get_settlement_summary", {"period": "this_month"}, data, "date")
        return read_facts("get_settlement_summary", {"period": "this_week"}, data, "any")
    if "부재중" in say:
        return read_facts("list_call_history", {"missed_only": True}, data)
    if "통계" in say and "7일" in say:
        return read_facts("get_statistics", {"period": "7d"}, data)
    if "네이버페이" in say and "개인" in say:
        return read_facts("get_naverpay_signup_guide", {"business_type": "individual"}, data)
    if "메도빅 가격" in say:
        return {"all": [number(resolve(data, "menus[name=메도빅].price"))]}
    if "답글 안 단 리뷰" in say:
        n = sum(not r["has_owner_reply"] for r in data["reviews"])
        return {"all": [count(n, "건")]}
    if "주문 있어요" in say:
        n = sum(o["status"] == "new" for o in data["orders"])
        return {"all": [count(n, "건") + [data["orders"][0]["items"].split()[0]]]}
    if "몇 명" in say and "예약" in say:
        return {"all": [[f"{resolve(data, 'reservations[resv-2].people')}명"]]}
    if "아메리카노 가격 얼마로 바꿨" in say:
        return {"all": [number(4800)]}  # set earlier in the same session (long-session-a)
    return None


# ------------------------------------------------------------------ sources


def from_scenarios() -> list[dict]:
    out = []
    for s in jsonl(DATA / "scenario.jsonl"):
        s = copy.deepcopy(s)
        kind = FAMILY_TYPE[s["family"]]
        writes = s["expect"]["writes"]
        s.update(
            type=kind,
            area=area_of(writes) if kind not in ("P7",) else "none",
            source=f"scenario:{s['id']}",
            lang="ko",
            split="dev",
            repeat=REPEAT,
        )
        s["tags"] = sorted(set(s.get("tags", [])) | {"s1"})
        data = apply_setup(seed_store().read(), s.get("setup", {}))
        for turn in s["turns"]:
            if turn["act"] == "interject":
                spec = interject_answer(turn["say"], data)
                if spec:
                    turn["answer"] = spec
        Scenario.model_validate(s)
        out.append(s)
    return out


def p1(flow: list[dict]) -> list[dict]:
    cases = [
        c
        for c in flow
        if c["lang"] in KOREAN
        and c["kind"] == "full"
        and "turns" not in c
        and c.get("expected_args")
    ]
    out = []
    for c in cases:
        tool, args = c["expected_tool"], c["expected_args"]
        probe = {"feature": TOOL_FEATURES[tool], "tool": tool, "args": args}
        if tool in READ_ONLY_TOOLS:
            spec = read_facts(tool, args)
            if spec is None:
                continue
            final, writes = {}, []
            turns = [{"say": c["text"], "act": "request", "probe": probe, "answer": spec}]
        else:
            final, writes = final_of(*simulate([(tool, args)]))
            turns = [
                {"say": c["text"], "act": "request", "probe": probe},
                {"say": "네", "act": "approve", "again": {"unless": final, "max": 1}},
            ]
        out.append(
            scenario(
                f"p1-{c['id']}",
                "P1",
                area=TOOL_FEATURES[c["expected_tool"]],
                source=f"flow:{c['id']}",
                lang=c["lang"],
                split=c["split"],
                turns=turns,
                final=final,
                writes=writes,
            )
        )
    return out


def multi(flow: list[dict]) -> dict[str, list[dict]]:
    """multi:ask_fill → P2, multi:confirm|decline|revise → P3."""
    out: dict[str, list[dict]] = {"P2": [], "P3": []}
    for c in flow:
        category = c.get("category") or ""
        if c["lang"] not in KOREAN or not category.startswith("multi:"):
            continue
        last_call = None
        turns = []
        for i, t in enumerate(c["turns"]):
            expect = t.get("expect", {})
            tool = expect.get("tool")
            probe = {k: expect[k] for k in ("feature", "tool", "args") if k in expect} or None
            if i == 0:
                turn = {"say": t["say"], "act": "request", "missing": tool is None}
            elif i == len(c["turns"]) - 1:
                act = "decline" if category == "multi:decline" else "approve"
                turn = {"say": t["say"], "act": act}
            elif tool and last_call:
                turn = {"say": t["say"], "act": "revise"}
            else:
                turn = {"say": t["say"], "act": "provide"}
            if not turn.get("missing"):
                turn.pop("missing", None)
            if probe:
                turn["probe"] = probe
            if tool:
                last_call = (tool, expect.get("args") or {})
            turns.append(turn)
        if last_call is None:
            continue
        before, after = simulate([last_call])
        if category == "multi:decline":
            proposed, _ = final_of(before, after)
            final = {
                p: {"$absent": True} if resolve(before, p) is MISSING else resolve(before, p)
                for p in proposed
                if not p.endswith(".#")
            }
            writes, never = [], [{p: v} for p, v in proposed.items()]
        else:
            final, writes = final_of(before, after)
            never = []
            turns[-1]["again"] = {"unless": final, "max": 1}
        kind = "P2" if category == "multi:ask_fill" else "P3"
        out[kind].append(
            scenario(
                f"{kind.lower()}-{c['id']}",
                kind,
                area=TOOL_FEATURES[last_call[0]],
                source=f"flow:{c['id']}",
                lang=c["lang"],
                split=c["split"],
                turns=turns,
                final=final,
                writes=writes,
                never=never,
            )
        )
    return out


def one_turn(cases: list[dict], kind: str, act: str, prefix: str, missing=False) -> list[dict]:
    out = []
    for c in cases:
        say = c.get("text") or c["turns"][0]["say"]
        turn = {"say": say, "act": act}
        if missing:
            turn["missing"] = True
        out.append(
            scenario(
                f"{kind.lower()}-{c['id']}",
                kind,
                area={"P7": "none", "P8": "review"}.get(kind, "multi"),
                source=f"{prefix}:{c['id']}",
                lang=c["lang"],
                split=c["split"],
                turns=[turn],
                final={},
                writes=[],
            )
        )
    return out


def p9(goldset: list[dict]) -> list[dict]:
    """Reply flows moved onto the dummy shop: unanswered r02·r03, the negative one is r03."""
    acts = {"get_unanswered_reviews": "request", "draft_review_reply": "request"}
    out = []
    for c in goldset:
        if c.get("category") not in ("답글/흐름", "답글/수정", "답글/확정 대기"):
            continue
        targets = {
            (t["expect"].get("args") or {}).get("target")
            for t in c["turns"]
            if t["expect"].get("tool") == "draft_review_reply"
        } - {None}
        posts = c["turns"][-1]["expect"].get("tool") == "post_review_reply"
        if targets - {"negative"} or (posts and not targets and c["category"] != "답글/수정"):
            continue  # first/all_listed depend on list order and count in the other shops
        turns, drafted = [], False
        for t in c["turns"]:
            tool = t["expect"].get("tool")
            if tool == "post_review_reply":
                act = "approve"
            elif tool == "draft_review_reply" and drafted:
                act = "revise"
            elif tool in acts:
                act = acts[tool]
            else:
                act = "other"  # "잠깐만", "나중에 할게": the draft must stay unposted
            drafted = drafted or tool == "draft_review_reply"
            turns.append(
                {"say": t["say"], "act": act, "probe": {"feature": "review", "tool": tool}}
            )
        if posts:
            final = {"reviews[r03].has_owner_reply": True, "reviews[r02].has_owner_reply": False}
            writes = ["reviews[r03]"]
        else:
            final = {"reviews[r03].has_owner_reply": False, "reviews[r02].has_owner_reply": False}
            writes = []
        out.append(
            scenario(
                f"p9-{c['id']}",
                "P9",
                area="review",
                source=f"goldset:{c['id']}",
                lang=c.get("lang") or "ko",
                split=c.get("split", "dev"),
                turns=turns,
                final=final,
                writes=writes,
            )
        )
    return out


# ------------------------------------------------------------------ selection


def pick(pool: list[dict], n: int, cover: str | None = None) -> list[dict]:
    """Sealed (old holdout) first up to a quarter, then dev; spread over `cover` values."""
    pool = order(pool)
    sealed = [s for s in pool if s["split"] == "sealed"][: max(1, n // 4)]
    rest = [s for s in pool if s not in sealed]
    chosen = list(sealed)
    if cover:
        seen = {s[cover] for s in chosen}
        for s in rest:
            if len(chosen) < n and s[cover] not in seen:
                chosen.append(s)
                seen.add(s[cover])
    for s in rest:
        if len(chosen) < n and s not in chosen:
            chosen.append(s)
    return chosen[:n]


def build() -> list[dict]:
    flow = jsonl(DATA / "flow.jsonl")
    route = jsonl(DATA / "route.jsonl")
    goldset = jsonl(ROOT / "tests/goldset/review_tools.jsonl")
    base = from_scenarios()
    by_type: dict[str, list[dict]] = {}
    for s in base:
        by_type.setdefault(s["type"], []).append(s)
    m = multi(flow)
    korean_route = [c for c in route if c["lang"] in KOREAN]
    extra = {
        "P1": (p1(flow), "area"),
        "P2": (m["P2"], "area"),
        "P3": (m["P3"], "area"),
        "P6": (
            one_turn(
                [c for c in korean_route if c.get("kind") == "ambiguous"],
                "P6",
                "request",
                "route",
                missing=True,
            ),
            None,
        ),
        "P7": (
            one_turn(
                [
                    c
                    for c in flow
                    if c["lang"] in KOREAN and c["kind"] == "unrelated" and "turns" not in c
                ],
                "P7",
                "unrelated",
                "flow",
            ),
            None,
        ),
        "P8": (
            one_turn(
                [c for c in korean_route if c.get("kind") == "injection"], "P8", "request", "route"
            ),
            None,
        ),
        "P9": (p9(goldset), None),
    }
    out = []
    for kind, target in TARGET.items():
        chosen = by_type.get(kind, [])[:target]
        if kind in extra and len(chosen) < target:
            pool, cover = extra[kind]
            chosen += pick(pool, target - len(chosen), cover)
        out += chosen
    # After choosing, so the selection stays the same.
    for row in out:
        if row["id"] in SEEN:
            row["split"], row["origin"] = "dev", SEEN[row["id"]]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = build()
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    counts = Counter(r["type"] for r in rows)
    sealed = Counter(r["type"] for r in rows if r["split"] == "sealed")
    print(
        f"{len(rows)}건 "
        + " ".join(f"{k}={counts[k]}(sealed {sealed[k]})" for k in TARGET)
        + f", sealed {sum(sealed.values())}"
    )
    if args.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("s1.jsonl 일치" if same else "s1.jsonl 다름: build_s1.py로 다시 만드세요")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
