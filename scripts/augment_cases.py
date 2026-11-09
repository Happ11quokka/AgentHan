# ruff: noqa: E501 — 프롬프트·문서 문장은 줄을 나누지 않는다
"""기능 매칭(route)·전체 흐름(flow) 케이스 증강: Gemini(agy) 생성 → 코드 검증 → Sonnet(agy) 독립 라벨.

리뷰 기능(review_reply, ai_review)은 근형님 평가를 따르므로 만들지 않는다. 입력은 한국어 위주(ko·ko_simple, en 소수).
Sonnet 정답(route=기능, flow=도구+인자)이 생성 정답과 정확히 같을 때만 채택한다. .env·키는 읽지 않는다.

  .venv/bin/python scripts/augment_cases.py gen --out <폴더>     # 초안 + REPORT.md
  .venv/bin/python scripts/augment_cases.py apply --out <폴더>   # tests/data/*_aug.jsonl·splits.json에 추가
"""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from agenthan.eval.cases import Case, make_splits
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import (
    DELEGATED_FEATURES,
    TOOL_FEATURES,
    all_tool_schemas,
    execute_feature_tool,
)

ROOT = Path(__file__).resolve().parents[1]
ROUTE_FILE = ROOT / "tests/data/place_request_cases.jsonl"
FLOW_FILE = ROOT / "tests/data/tool_selection_cases.jsonl"
ROUTE_AUG = ROOT / "tests/data/place_request_cases_aug.jsonl"
FLOW_AUG = ROOT / "tests/data/tool_selection_cases_aug.jsonl"
SPLITS_FILE = ROOT / "tests/data/splits.json"
CATALOG = ROOT / "src/agenthan/agent/place_features.json"

GEN_MODELS = ["gemini-4-argon-high", "gemini-3.8-flash-high"]
CHECK_MODELS = ["claude-sonnet-5-5-medium"]
LANG_RULE = "언어 비율: ko 50%, ko_simple(외국인 사장님의 쉬운·서툰 한국어) 35%, en 15%."
PER_FEATURE = 3  # route: 리뷰 외 기능마다
PER_TOOL = 2  # flow full: 도구마다
N_ROUTE_UNRELATED = 8
N_FLOW_MISSING = 12
N_FLOW_UNRELATED = 6
N_FLOW_HARD = 10

HARD_PAIRS = [
    ("update_business_info", "update_business_hours"),
    ("save_talktalk_reply", "update_reservation_status"),
    ("update_order_status", "update_reservation_status"),
    ("create_coupon", "register_new_opening"),
    ("get_naverpay_signup_guide", "set_onsite_payment"),
    ("set_onsite_payment", "get_settlement_summary"),
    ("get_statistics", "set_representative_keywords"),
    ("search_help_articles", "get_naverpay_signup_guide"),
    ("list_call_history", "save_talktalk_reply"),
]


def agy(prompt: str, model: str, log: Path, timeout_s: int = 900) -> str:
    """빈 임시 폴더에서 agy 한 번. 레포를 탐색하지 않게 한다."""
    with tempfile.TemporaryDirectory() as cwd:
        argv = [
            "agy",
            "--print-timeout",
            f"{timeout_s}s",
            "--model",
            model,
            "--output-format",
            "json",
            "-p",
            prompt,
        ]
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_s + 60)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "at": time.strftime("%FT%T"),
                    "model": model,
                    "exit": r.returncode,
                    "stdout_tail": r.stdout[-300:],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    obj = json.loads(r.stdout)
    if obj.get("status") != "SUCCESS":
        raise RuntimeError(f"agy {model}: {obj.get('status')}")
    return obj["response"]


def parse_json(text: str) -> list:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = (m.group(1) if m else text).strip()
    start = body.find("[")
    return json.loads(body[start : body.rfind("]") + 1])


def call_json(prompt: str, models: list[str], log: Path, tries: int = 2) -> tuple[list, str]:
    last = None
    for model in models:
        for _ in range(tries):
            try:
                return parse_json(agy(prompt, model, log)), model
            except Exception as e:  # noqa: BLE001 — 다음 시도로 넘어간다
                last = e
                time.sleep(10)
    raise RuntimeError(f"all models failed: {last}")


def norm(t: str) -> str:
    return re.sub(r"[\W_]+", "", t.lower())


def grams(t: str) -> set[str]:
    t = norm(t)
    return {t[i : i + 3] for i in range(max(1, len(t) - 2))}


def short_hash(text: str) -> str:
    return hashlib.sha1(norm(text).encode()).hexdigest()[:6]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------- 프롬프트 ----------

COMMON = """너는 한국 네이버 스마트플레이스 '사장님 비서'의 평가 케이스를 만든다.
사용자는 한국에서 가게를 하는 외국인 사장님(몽골·러시아·베트남·우즈벡·영어권 등)이다. 문장은 사장님이 메신저에
칠 법하게 짧고 구어체로, 띄어쓰기·맞춤법이 조금 틀려도 된다. 실존 가게·인물·전화번호는 쓰지 말고 가상의 값만 쓴다.
{lang_rule}
기존 케이스와 겹치지 않게 상황·말투·어휘를 다양하게 한다. 기능 이름이나 키워드를 그대로 베끼지 말고 사장님 말로 돌려 말한
문장을 섞는다. 기존 예시(따라 하지 말 것):
{examples}
"""

ROUTE_GEN = (
    COMMON
    + """
기능 목록(id: 이름 — 설명):
{features}

할 일: 위 기능마다 정확히 {n}개씩, 그 기능으로 가야 하는 사장님 요청을 만든다. 문장 하나에 기능 하나만 담는다.
출력: JSON 배열 하나만. [{{"expected": "기능 id", "lang": "ko|ko_simple|en", "text": "..."}}]
"""
)

ROUTE_UNRELATED = (
    COMMON
    + """
할 일: 스마트플레이스 가게 관리와 무관한 사장님 메시지 {n}개(잡담, 날씨, 개인 일, 다른 앱·서비스 문의, 일반 상식 등).
가게 이야기가 살짝 섞여도 위 기능 어디에도 해당하지 않아야 한다(예: 직원 생일 선물 추천).
출력: JSON 배열 하나만. [{{"expected": null, "lang": "ko|ko_simple|en", "text": "..."}}]
"""
)

FLOW_FULL = (
    COMMON
    + """
도구 목록(JSON 스키마):
{tools}

더미 가게 데이터(톡톡·주문·예약을 가리킬 때는 이 ID만 쓴다):
{store}

할 일: 각 도구마다 정확히 {n}개씩, 그 도구의 필수 인자를 모두 문장에 담은 요청을 만든다.
expected_args에는 비서가 보내야 할 인자를 스키마 그대로(enum 값, 정수, HH:MM, YYYY-MM-DD) 적는다.
선택 인자는 문장에 분명히 있을 때만 넣는다. "입력 미지원: 보내지 마세요"인 인자는 넣지 않는다.
자유 문장 인자(답장 내용, 소개 문구 등)는 문장 안에 따옴표로 정확한 값을 담아 정답이 하나뿐이게 한다.
출력: JSON 배열 하나만. [{{"expected_tool": "도구", "expected_args": {{...}}, "lang": "...", "text": "..."}}]
"""
)

FLOW_HARD = (
    COMMON
    + """
도구 목록(JSON 스키마):
{tools}

더미 가게 데이터(톡톡·주문·예약을 가리킬 때는 이 ID만 쓴다):
{store}

할 일: 아래 헷갈리는 쌍에서 정답은 한쪽이지만 다른 쪽 어휘가 섞여 헷갈리기 쉬운 요청 {n}개. 필수 인자를 모두 담는다.
쌍: {pairs}
expected_args 규칙은 스키마 그대로, 자유 문장 인자는 따옴표로 정확한 값을 담는다.
출력: JSON 배열 하나만. [{{"expected_tool": "정답 도구", "near_tool": "헷갈리는 도구", "expected_args": {{...}},
"lang": "...", "text": "..."}}]
"""
)

FLOW_MISSING = (
    COMMON
    + """
도구 목록(JSON 스키마):
{tools}

할 일: 어떤 도구를 쓸지는 분명하지만 그 도구의 필수 인자가 하나 이상 빠져 있어 비서가 되물어야 하는 요청 {n}개.
여러 도구에 고르게 퍼뜨린다. 조회 전용 도구처럼 필수 인자가 없는 도구는 쓰지 않는다.
출력: JSON 배열 하나만. [{{"near_tool": "그 도구", "lang": "...", "text": "..."}}]
"""
)

FLOW_UNRELATED = ROUTE_UNRELATED

ROUTE_CHECK = """너는 평가 정답을 독립적으로 다는 검수자다. 각 사장님 요청이 아래 기능 중 어디로 가야 하는지 하나 고른다.
가게 관리와 무관하면 null. 둘 이상으로 해석되면 "ambiguous".

기능 목록:
{features}

케이스:
{cases}

출력: JSON 배열 하나만. [{{"id": "...", "expected": "기능 id 또는 null 또는 ambiguous", "reason": "한 줄"}}]
"""

FLOW_CHECK = """너는 평가 정답을 독립적으로 다는 검수자다. 각 사장님 요청에 대해 비서가 지금 바로 호출할 도구와 인자를 정한다.
규칙: 필수 인자가 빠졌으면 tool=null(되물어야 함). 가게 관리와 무관하면 tool=null. 둘 이상으로 해석되면 tool=null.
인자는 스키마 그대로(enum 값, 정수, HH:MM, YYYY-MM-DD), 선택 인자는 문장에 분명할 때만 넣는다.
"입력 미지원: 보내지 마세요"인 인자는 넣지 않는다. 오늘은 2026-11-09(월)이다.

도구 목록(JSON 스키마):
{tools}

더미 가게 데이터:
{store}

케이스:
{cases}

출력: JSON 배열 하나만. [{{"id": "...", "tool": "도구 또는 null", "args": {{...}} 또는 null,
"null_reason": "missing|unrelated|ambiguous|null", "reason": "한 줄"}}]
"""


# ---------- 생성 ----------


def context():
    catalog = [
        f
        for f in json.loads(CATALOG.read_text(encoding="utf-8"))
        if f["id"] not in DELEGATED_FEATURES
    ]
    features = "\n".join(f"- {f['id']}: {f['name_ko']} — {f['summary_ko']}" for f in catalog)
    schemas = [
        t
        for t in all_tool_schemas()
        if TOOL_FEATURES[t["function"]["name"]] not in DELEGATED_FEATURES
    ]
    tools = json.dumps([t["function"] for t in schemas], ensure_ascii=False)
    with tempfile.TemporaryDirectory() as d:
        s = DummyStore(Path(d) / "s.json").read()
    store = json.dumps(
        {
            "shop": {k: s["shop"][k] for k in ("name", "phone", "keywords", "onsite_payment")},
            "menus": [{"id": m["id"], "name": m["name"], "price": m["price"]} for m in s["menus"]],
            "talktalk": s["talktalk"],
            "orders": s["orders"],
            "reservations": s["reservations"],
            "calls": s["calls"],
        },
        ensure_ascii=False,
    )
    return catalog, features, schemas, tools, store


def generate(out: Path, log: Path) -> None:
    catalog, features, schemas, tools, store = context()
    route_old, flow_old = load_jsonl(ROUTE_FILE), load_jsonl(FLOW_FILE)
    ex_route = "\n".join(f"- {r['text']}" for r in route_old[::6])
    ex_flow = "\n".join(f"- {r['text']}" for r in flow_old[::6])
    common = {"lang_rule": LANG_RULE}
    jobs = [
        (
            "route",
            "full",
            ROUTE_GEN.format(**common, examples=ex_route, features=features, n=PER_FEATURE),
        ),
        (
            "route",
            "unrelated",
            ROUTE_UNRELATED.format(**common, examples=ex_route, n=N_ROUTE_UNRELATED),
        ),
        (
            "flow",
            "full",
            FLOW_FULL.format(**common, examples=ex_flow, tools=tools, store=store, n=PER_TOOL),
        ),
        (
            "flow",
            "hard",
            FLOW_HARD.format(
                **common,
                examples=ex_flow,
                tools=tools,
                store=store,
                n=N_FLOW_HARD,
                pairs=", ".join(f"{a}↔{b}" for a, b in HARD_PAIRS),
            ),
        ),
        (
            "flow",
            "missing",
            FLOW_MISSING.format(**common, examples=ex_flow, tools=tools, n=N_FLOW_MISSING),
        ),
        (
            "flow",
            "unrelated",
            FLOW_UNRELATED.format(**common, examples=ex_flow, n=N_FLOW_UNRELATED),
        ),
    ]
    raw = []
    for mode, kind, prompt in jobs:
        rows, model = call_json(prompt, GEN_MODELS, log)
        for r in rows:
            r.update(_mode=mode, _kind=kind, source=f"aug:{model}")
        raw += rows
        print(f"[gen] {mode}/{kind}: {len(rows)}개 ({model})")
    (out / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 검증 ----------


def run_tool(tool: str, args: dict) -> dict:
    with tempfile.TemporaryDirectory() as d:
        return execute_feature_tool(DummyStore(Path(d) / "s.json"), tool, args)


def to_case(r: dict) -> tuple[str, dict]:
    """초안 → 저장 형식. 문제 목록과 함께."""
    text = str(r.get("text", "")).strip()
    lang = r.get("lang")
    h = short_hash(text)
    if r["_mode"] == "route":
        feat = r.get("expected")
        rid = f"route-aug-{feat or 'none'}-{h}"
        return "route", {
            "id": rid,
            "text": text,
            "lang": lang,
            "expected": feat,
            "source": r["source"],
        }
    kind = {"hard": "full"}.get(r["_kind"], r["_kind"])
    tool = r.get("expected_tool") if kind == "full" else None
    near = r.get("near_tool")
    base = tool or near or "none"
    case = {
        "id": f"{base}-aug-{h}",
        "kind": kind,
        "lang": lang,
        "text": text,
        "expected_tool": tool,
        "near_tool": near if kind != "unrelated" else None,
        "source": r["source"],
    }
    if tool:
        case["expected_args"] = r.get("expected_args") or {}
    if r["_kind"] == "hard":
        case["note"] = f"헷갈리는 쌍: {near}"
    return "flow", case


def issues_of(mode: str, c: dict, features: set[str], required: dict) -> list[str]:
    out = []
    if c["lang"] not in ("ko", "ko_simple", "en"):
        out.append("bad lang")
    if not c["text"]:
        out.append("empty text")
    if mode == "route":
        if c["expected"] is not None and c["expected"] not in features:
            out.append(f"unknown feature {c['expected']}")
        return out
    tool, near = c.get("expected_tool"), c.get("near_tool")
    for t in (tool, near):
        if t and t not in required:
            out.append(f"unknown tool {t}")
    if c["kind"] == "missing":
        if not near or near not in required:
            out.append("missing needs near_tool")
        elif not required[near]:
            out.append("near_tool has no required args")
    if tool and tool in required:
        res = run_tool(tool, c["expected_args"])
        if not res.get("ok"):
            out.append(f"args rejected: {str(res.get('error') or res.get('missing_fields'))[:80]}")
    try:
        Case.model_validate(c)
    except Exception as e:  # noqa: BLE001
        out.append(f"schema: {str(e)[:80]}")
    return out


def check(out: Path, log: Path) -> None:
    catalog, features, schemas, tools, store = context()
    feature_ids = {f["id"] for f in catalog}
    required = {
        t["function"]["name"]: t["function"]["parameters"].get("required", []) for t in schemas
    }
    raw = json.loads((out / "raw.json").read_text(encoding="utf-8"))
    old = [
        r for f in (ROUTE_FILE, FLOW_FILE, ROUTE_AUG, FLOW_AUG) if f.exists() for r in load_jsonl(f)
    ]
    seen = {norm(o["text"]) for o in old}
    seen_g = [grams(o["text"]) for o in old]
    kept: dict[str, list] = {"route": [], "flow": []}
    rejected = []
    for r in raw:
        mode, c = to_case(r)
        iss = issues_of(mode, c, feature_ids, required)
        key, g = norm(c["text"]), grams(c["text"])
        if key in seen:
            iss.append("duplicate")
        elif (near := max((len(g & s) / len(g | s) for s in seen_g), default=0)) > 0.7:
            iss.append(f"near-duplicate {near:.2f}")
        if iss:
            rejected.append({**c, "issues": iss})
            continue
        seen.add(key)
        seen_g.append(g)
        kept[mode].append(c)

    # Sonnet 독립 라벨 (생성 정답은 보여 주지 않는다)
    labels = {}
    for mode, prompt_t in (("route", ROUTE_CHECK), ("flow", FLOW_CHECK)):
        rows = kept[mode]
        for i in range(0, len(rows), 15):
            batch = [{"id": c["id"], "text": c["text"]} for c in rows[i : i + 15]]
            prompt = prompt_t.format(
                features=features,
                tools=tools,
                store=store,
                cases=json.dumps(batch, ensure_ascii=False),
            )
            res, model = call_json(prompt, CHECK_MODELS, log)
            for x in res:
                for k in ("expected", "tool"):  # 문자열 "null"·"none"을 null로
                    if str(x.get(k)).lower() in ("null", "none", ""):
                        x[k] = None
                labels[x.get("id")] = {**x, "model": model}
            print(f"[check] {mode} {i}: {len(res)}개 ({model})")

    final: dict[str, list] = {"route": [], "flow": []}
    for mode, rows in kept.items():
        for c in rows:
            lab = labels.get(c["id"])
            why = None
            if lab is None:
                why = "sonnet label missing"
            elif mode == "route" and lab.get("expected") != c["expected"]:
                why = f"sonnet feature={lab.get('expected')}"
            elif mode == "flow":
                if lab.get("tool") != c["expected_tool"]:
                    why = f"sonnet tool={lab.get('tool')} ({lab.get('null_reason')})"
                elif c["expected_tool"] and (lab.get("args") or {}) != c["expected_args"]:
                    why = f"sonnet args={json.dumps(lab.get('args'), ensure_ascii=False)}"
            if why:
                rejected.append({**c, "issues": [why], "sonnet_reason": (lab or {}).get("reason")})
            else:
                c["source"] += f"+check:{lab['model']}"
                final[mode].append(c)

    for mode in final:  # 기존 파일처럼 기능·도구 순으로
        final[mode].sort(
            key=lambda c: (
                str(c.get("expected", c.get("expected_tool") or c.get("near_tool"))),
                c["id"],
            )
        )
    dump(out / "route_aug.jsonl", final["route"])
    dump(out / "flow_aug.jsonl", final["flow"])
    dump(out / "rejected.jsonl", rejected)
    report(out, final, rejected)


def dump(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def report(out: Path, final: dict, rejected: list) -> None:
    lines = [
        "# 케이스 증강 초안",
        "",
        f"- route 채택 {len(final['route'])} / flow 채택 {len(final['flow'])} / 탈락 {len(rejected)}",
        f"- route 기능: {dict(Counter(c['expected'] for c in final['route']))}",
        f"- flow kind: {dict(Counter(c['kind'] for c in final['flow']))}",
        f"- flow 도구: {dict(Counter(c['expected_tool'] or c['near_tool'] for c in final['flow']))}",
        f"- lang: {dict(Counter(c['lang'] for m in final.values() for c in m))}",
        f"- 탈락 사유: {dict(Counter(i.split(' ')[0] + ' ' + i.split(' ')[1].split('=')[0] if ' ' in i else i for r in rejected for i in r['issues']))}",
        "",
        "## 탈락",
        "",
        "| id | 문장 | 사유 |",
        "|---|---|---|",
    ]
    for r in rejected:
        lines.append(
            f"| {r['id']} | {r['text'].replace('|', '/')} | {'; '.join(r['issues']).replace('|', '/')} |"
        )
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- 반영 ----------


def apply(out: Path) -> None:
    """증강 파일에 새 케이스를 붙이고, 증강 파일마다 make_splits로 다시 배정한다(M0 배정은 그대로)."""
    from agenthan.eval.cases import load_cases

    splits = json.loads(SPLITS_FILE.read_text(encoding="utf-8"))
    added = 0
    for src, dst in (("route_aug.jsonl", ROUTE_AUG), ("flow_aug.jsonl", FLOW_AUG)):
        old = load_jsonl(dst) if dst.exists() else []
        ids = {r["id"] for r in old}
        new = [r for r in load_jsonl(out / src) if r["id"] not in ids]
        added += len(new)
        dump(dst, old + new)
        cases = load_cases(dst)
        splits["splits"].update(make_splits(cases))
        print(
            f"{dst.name}: +{len(new)} → {len(cases)}",
            Counter(splits["splits"][c.id] for c in cases),
        )
    splits["splits"] = dict(sorted(splits["splits"].items()))
    note = " 증강 파일(*_aug.jsonl, scripts/augment_cases.py)은 M0 배정을 바꾸지 않고 파일마다 같은 규칙으로 배정한다."
    if note not in splits["note"]:
        splits["note"] += note
    SPLITS_FILE.write_text(
        json.dumps(splits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"added {added}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["gen", "check", "apply", "all"])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    log = a.out / "agy_calls.jsonl"
    if a.step in ("gen", "all"):
        generate(a.out, log)
    if a.step in ("check", "all"):
        check(a.out, log)
    if a.step == "apply":
        apply(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
