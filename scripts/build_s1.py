"""S1 회귀 기준선(tests/data/eval/s1.jsonl)을 기존 한국어 케이스로 만든다 (정본 §5).

새 문장은 만들지 않는다. 출처와 변환 규칙:
- P2·P3·P4·P5·P6·P7·P8의 뼈대: 기존 시나리오(tests/data/eval/scenario.jsonl) 30건 그대로.
- P1 단일 작업: flow 한국어 `full`+인자(쓰기 도구) → 요청 + "네". 최종 상태는 정답 도구·인자를
  더미 저장소에 실제로 실행해 계산한다.
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

from agenthan.eval.scenario import MISSING, Scenario, changes, resolve
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import READ_ONLY_TOOLS, TOOL_FEATURES, execute_feature_tool

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/eval"
OUT = DATA / "s1.jsonl"
KOREAN = ("ko", "ko_simple")
TODAY = "2026-11-10"
REPEAT = 3
# §5 target counts. P4·P5 have only the existing scenarios.
TARGET = {"P1": 16, "P2": 12, "P3": 12, "P4": 7, "P5": 9, "P6": 4, "P7": 8, "P8": 4, "P9": 8}
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
        and c["expected_tool"] not in READ_ONLY_TOOLS
    ]
    out = []
    for c in cases:
        final, writes = final_of(*simulate([(c["expected_tool"], c["expected_args"])]))
        probe = {
            "feature": TOOL_FEATURES[c["expected_tool"]],
            "tool": c["expected_tool"],
            "args": c["expected_args"],
        }
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
