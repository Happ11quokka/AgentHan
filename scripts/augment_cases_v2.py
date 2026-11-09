# ruff: noqa: E501 — 프롬프트·문서 문장은 줄을 나누지 않는다
"""2차 증강: 실측에서 약했던 곳 세 가지. 생성·검사 방식은 augment_cases.py와 같다(Gemini 생성 → 코드 검증 → Sonnet 독립 라벨).

1. nofab: 일부 정보는 구체적이라 값을 지어내고 싶어지지만 필수 항목 하나가 빠진 요청 (flow missing, 도구 호출 없어야 함)
2. shopword: 가게·기능 단어가 섞였지만 스마트플레이스와 무관한 질문 (route·flow unrelated)
3. multi: 여러 턴 흐름. 되묻기 → 답 → 승인, 바로 승인, 취소, 말 바꾸기 (flow, 저장소 기대값은 코드가 계산)

  .venv/bin/python scripts/augment_cases_v2.py all --out <폴더>     # 생성 + 검사, REPORT.md
  .venv/bin/python scripts/augment_cases_v2.py apply --out <폴더>   # tests/data/*_aug2.jsonl·splits.json 반영
"""

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

import augment_cases as base  # 같은 scripts 폴더

from agenthan.eval.cases import Case, make_splits
from agenthan.eval.flow import store_diff
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.feature_tools import READ_ONLY_TOOLS, TOOL_FEATURES, execute_feature_tool

ROUTE_OUT = base.ROOT / "tests/data/place_request_cases_aug2.jsonl"
FLOW_OUT = base.ROOT / "tests/data/tool_selection_cases_aug2.jsonl"
# 통계는 평가에서 분기까지만 보므로 흐름 케이스에서 뺀다.
SKIP_TOOLS = {"get_statistics", "list_call_history"}
CONFIRM_WORDS = ["네", "네!", "응", "좋아요", "저장해 주세요", "ok"]
DECLINE_WORDS = ["취소", "아니요", "그만", "아뇨"]
N_SHOPWORD = 22  # route·flow 각각 (검사 탈락분 여유)

NOFAB = (
    base.COMMON
    + """
도구 목록(JSON 스키마):
{tools}

더미 가게 데이터:
{store}

할 일: 아래 도구마다 정확히 {n}개씩, 사장님 요청 일부는 아주 구체적이지만 **필수 인자 하나가 빠져 있어서** 비서가 그럴듯한 값을
지어내고 싶어지는 요청을 만든다. 비서는 도구를 부르지 말고 되물어야 정답이다.
예: 시간은 있는데 요일이 없음 / 답장 문구는 있는데 미답변 톡톡이 둘이라 어느 문의인지 모름 / 상태는 있는데 주문이 둘이라 어느 주문인지 모름 /
할인액은 있는데 기간이 없음 / 메뉴 이름은 있는데 가격이 없음 / 바꿀 항목은 있는데 새 내용이 없음.
더미 데이터의 내용(메시지·품목·날짜)으로 대상을 하나로 특정할 수 있으면 빠진 게 아니므로 그렇게 만들지 않는다.
도구: {tool_names}
출력: JSON 배열 하나만. [{{"near_tool": "도구", "missing": "빠진 필수 인자 이름", "lang": "...", "text": "..."}}]
"""
)

SHOPWORD = (
    base.COMMON
    + """
기능 목록(id: 이름 — 설명):
{features}

할 일: 위 기능의 단어(영업시간, 예약, 정산, 수수료, 메뉴, 전화, 주문, 쿠폰, 키워드, 결제, 리뷰 등)가 섞여 있지만 **네이버
스마트플레이스 가게 관리와는 무관한** 사장님 메시지 {n}개. 은행·관공서·병원 영업시간이나 예약, 세금·노무, 다른 앱 쓰는 법, 장보기,
개인 일, 잡담 등. 기능 목록 어디에도 해당하지 않아야 한다. 어느 기능 단어와 겹치는지 near_feature에 적는다.
출력: JSON 배열 하나만. [{{"near_feature": "기능 id", "lang": "...", "text": "..."}}]
"""
)

MULTI = (
    base.COMMON
    + """
도구 목록(JSON 스키마):
{tools}

더미 가게 데이터(톡톡·주문·예약을 가리킬 때는 이 ID나 내용만 쓴다):
{store}

비서 동작: 필수 정보가 빠지면 되묻는다. 쓰기 도구는 미리보기를 보여주고 사장님이 "네"라고 해야 저장한다. "취소"면 버린다.
조회 도구({read_only})는 승인 없이 바로 실행한다.

할 일: 아래 목록 그대로 여러 턴 대화의 **사장님 발화**를 만든다(승인·취소 한마디는 코드가 붙이므로 쓰지 않는다).
- ask_fill: turns=[필수 정보가 빠진 첫 요청, 비서가 되물은 뒤 빠진 정보만 짧게 답하는 말], args=[null, 두 턴을 합친 최종 인자]
- confirm: turns=[필수 정보를 다 담은 요청], args=[인자]
- decline: turns=[필수 정보를 다 담은 요청], args=[인자]
- revise: turns=[필수 정보를 다 담은 요청, 미리보기를 본 뒤 값 하나를 바꾸는 말(예: "아 가격은 8000원으로")], args=[처음 인자, 바뀐 값을 반영한 전체 인자]
인자는 스키마 그대로(enum 값, 정수, HH:MM, YYYY-MM-DD). 자유 문장 인자는 따옴표로 정확한 값을 담는다. 선택 인자는 분명할 때만.
"입력 미지원: 보내지 마세요"인 인자는 넣지 않는다. 오늘은 2026-11-09(월)이다.

목록(pattern, tool):
{plan}

출력: JSON 배열 하나만, 목록 순서대로. [{{"pattern": "...", "tool": "...", "lang": "...", "turns": ["..."], "args": [null 또는 {{...}}]}}]
"""
)

MULTI_CHECK = """너는 평가 정답을 독립적으로 다는 검수자다. 각 케이스는 사장님 발화가 순서대로 있는 한 대화다.
각 발화 직후에 비서가 지금 호출해야 할 도구와 인자를 정한다. 앞 발화들의 정보는 이어서 쓴다.
규칙: 필수 인자가 하나라도 빠졌으면 tool=null(되물어야 함). 쓰기 도구를 부르면 미리보기가 나가고 아직 저장되지 않은 상태로 다음 발화가 온다.
값을 바꾸는 발화가 오면 바뀐 값을 반영한 **전체 인자**로 다시 부른다. 인자는 스키마 그대로, 선택 인자는 분명할 때만.
"입력 미지원: 보내지 마세요"인 인자는 넣지 않는다. 오늘은 2026-11-09(월)이다.

도구 목록(JSON 스키마):
{tools}

더미 가게 데이터:
{store}

케이스:
{cases}

출력: JSON 배열 하나만. [{{"id": "...", "turns": [{{"tool": "도구 또는 null", "args": {{...}} 또는 null}}], "reason": "한 줄"}}]
"""


def plan_multi(tools: list[str]) -> list[tuple[str, str]]:
    writes = [t for t in tools if t not in READ_ONLY_TOOLS]
    reads = [t for t in tools if t in READ_ONLY_TOOLS]
    plan = [("ask_fill", t) for t in writes + reads]
    plan += [("confirm", t) for t in writes]
    plan += [("decline", t) for t in writes[::2]]
    plan += [("revise", t) for t in writes[1::2]]
    return plan


def generate(out: Path, log: Path) -> None:
    catalog, features, schemas, tools_json, store = base.context()
    names = [t["function"]["name"] for t in schemas if t["function"]["name"] not in SKIP_TOOLS]
    required = {
        t["function"]["name"]: t["function"]["parameters"].get("required", []) for t in schemas
    }
    nofab_tools = [n for n in names if required[n]]
    old = [
        r for f in sorted(base.ROOT.glob("tests/data/*cases*.jsonl")) for r in base.load_jsonl(f)
    ]
    examples = "\n".join(f"- {r.get('text') or r['turns'][0]['say']}" for r in old[::12])
    common = {"lang_rule": base.LANG_RULE, "examples": examples}
    plan = plan_multi(names)
    jobs = [
        (
            "nofab",
            NOFAB.format(
                **common, tools=tools_json, store=store, n=2, tool_names=", ".join(nofab_tools)
            ),
        ),
        ("shopword_route", SHOPWORD.format(**common, features=features, n=N_SHOPWORD)),
        ("shopword_flow", SHOPWORD.format(**common, features=features, n=N_SHOPWORD)),
        (
            "multi",
            MULTI.format(
                **common,
                tools=tools_json,
                store=store,
                read_only=", ".join(sorted(READ_ONLY_TOOLS)),
                plan="\n".join(f"- {p}, {t}" for p, t in plan),
            ),
        ),
    ]
    raw = []
    for kind, prompt in jobs:
        rows, model = base.call_json(prompt, base.GEN_MODELS, log)
        for r in rows:
            r.update(_kind=kind, source=f"aug2:{model}")
        raw += rows
        print(f"[gen] {kind}: {len(rows)}개 ({model})")
    (out / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 케이스 만들기 ----------


def fresh_store(folder: str) -> DummyStore:
    return DummyStore(Path(folder) / "s.json")


def expected_store(tool: str, args: dict, *, applied: bool) -> dict:
    """저장 후(applied) 또는 취소 후(원래 값) 기대값. 새로 붙은 레코드는 id(무작위) 빼고 잎 값만."""
    with tempfile.TemporaryDirectory() as d:
        store = fresh_store(d)
        before = store.read()
        res = execute_feature_tool(store, tool, args)
        after = store.read()
    if not res.get("ok") or res.get("action") == "read":
        return {}
    expect: dict = {}
    for path in store_diff(before, after):
        old, new = before, after
        for part in path.split("."):
            old = old[int(part)] if isinstance(old, list) else old.get(part)
            new = new[int(part)] if isinstance(new, list) else new.get(part)
        if (
            isinstance(old, list)
            and isinstance(new, list)
            and len(new) > len(old)
            and all(isinstance(x, dict) for x in new)
        ):
            if not applied:
                expect[path] = old  # 취소면 목록이 그대로여야 한다
                continue
            for i in range(len(old), len(new)):
                for k, v in new[i].items():
                    if k != "id":
                        expect[f"{path}.{i}.{k}"] = v
        else:
            expect[path] = new if applied else old
    return expect


def build(r: dict) -> tuple[str, dict | None, list[str]]:
    kind, text = r["_kind"], str(r.get("text", "")).strip()
    lang = r.get("lang")
    if kind == "shopword_route":
        rid = f"route-aug2-none-{base.short_hash(text)}"
        return (
            "route",
            {
                "id": rid,
                "text": text,
                "lang": lang,
                "expected": None,
                "note": f"단어 겹침: {r.get('near_feature')}",
                "source": r["source"],
            },
            [],
        )
    if kind == "shopword_flow":
        return (
            "flow",
            {
                "id": f"none-aug2-{base.short_hash(text)}",
                "kind": "unrelated",
                "lang": lang,
                "text": text,
                "expected_tool": None,
                "near_tool": None,
                "note": f"단어 겹침: {r.get('near_feature')}",
                "source": r["source"],
            },
            [],
        )
    if kind == "nofab":
        tool = r.get("near_tool")
        return (
            "flow",
            {
                "id": f"{tool}-nofab-{base.short_hash(text)}",
                "kind": "missing",
                "lang": lang,
                "text": text,
                "expected_tool": None,
                "near_tool": tool,
                "note": f"빠진 항목: {r.get('missing')}",
                "source": r["source"],
            },
            [],
        )
    # multi
    tool, pattern = r.get("tool"), r.get("pattern")
    says = [str(s).strip() for s in r.get("turns") or []]
    args = r.get("args") or []
    issues = []
    want = {"ask_fill": 2, "confirm": 1, "decline": 1, "revise": 2}.get(pattern)
    if tool not in TOOL_FEATURES or want is None or len(says) != want or len(args) != want:
        return "flow", None, [f"bad multi shape {pattern}/{tool}/{len(says)}/{len(args)}"]
    feature = TOOL_FEATURES[tool]
    write = tool not in READ_ONLY_TOOLS
    key = base.short_hash(" / ".join(says))
    turns = []
    for i, (say, a) in enumerate(zip(says, args, strict=True)):
        expect: dict = {"tool": tool if a else None}
        if a:
            expect["args"] = a
        if i == 0:
            expect["feature"] = feature
        turns.append({"say": say, "expect": expect})
    final = args[-1] or {}
    if write and pattern in ("ask_fill", "confirm", "revise"):
        word = CONFIRM_WORDS[int(key, 16) % len(CONFIRM_WORDS)]
        turns.append({"say": word, "expect": {"store": expected_store(tool, final, applied=True)}})
    elif pattern == "decline":
        word = DECLINE_WORDS[int(key, 16) % len(DECLINE_WORDS)]
        turns.append({"say": word, "expect": {"store": expected_store(tool, final, applied=False)}})
    for t in turns:
        if "store" in t["expect"] and not t["expect"]["store"]:
            issues.append("empty store expectation")
    case = {
        "id": f"{tool}-{pattern}-{key}",
        "kind": "full",
        "lang": lang,
        "category": f"multi:{pattern}",
        "near_tool": tool,
        "turns": turns,
        "source": r["source"],
    }
    return "flow", case, issues


def code_issues(mode: str, c: dict, required: dict, features: set[str]) -> list[str]:
    out = []
    if c.get("lang") not in ("ko", "ko_simple", "en"):
        out.append("bad lang")
    if mode == "route":
        return out
    near = c.get("near_tool")
    if near and near not in required:
        out.append(f"unknown tool {near}")
    if c["kind"] == "missing" and near and not required.get(near):
        out.append("near_tool has no required args")
    for t in c.get("turns", []):
        a = t["expect"].get("args")
        if a is not None:
            res = base.run_tool(t["expect"]["tool"], a)
            if not res.get("ok"):
                out.append(
                    f"args rejected: {str(res.get('error') or res.get('missing_fields'))[:60]}"
                )
    try:
        Case.model_validate(c)
    except Exception as e:  # noqa: BLE001
        out.append(f"schema: {str(e)[:80]}")
    return out


def check(out: Path, log: Path) -> None:
    catalog, features, schemas, tools_json, store = base.context()
    feature_ids = {f["id"] for f in catalog}
    required = {
        t["function"]["name"]: t["function"]["parameters"].get("required", []) for t in schemas
    }
    raw = json.loads((out / "raw.json").read_text(encoding="utf-8"))
    old = [
        r for f in sorted(base.ROOT.glob("tests/data/*cases*.jsonl")) for r in base.load_jsonl(f)
    ]
    first = [r.get("text") or r["turns"][0]["say"] for r in old]
    seen = {base.norm(t) for t in first}
    seen_g = [base.grams(t) for t in first]
    kept: dict[str, list] = {"route": [], "flow": []}
    rejected = []
    for r in raw:
        mode, c, iss = build(r)
        if c is None:
            rejected.append(
                {"id": "?", "text": json.dumps(r, ensure_ascii=False)[:80], "issues": iss}
            )
            continue
        iss += code_issues(mode, c, required, feature_ids)
        text = c.get("text") or c["turns"][0]["say"]
        key, g = base.norm(text), base.grams(text)
        if key in seen or any(k["id"] == c["id"] for k in kept[mode]):
            iss.append("duplicate")
        elif (near := max((len(g & s) / len(g | s) for s in seen_g), default=0)) > 0.7:
            iss.append(f"near-duplicate {near:.2f}")
        if iss:
            rejected.append({**c, "text": text, "issues": iss})
            continue
        seen.add(key)
        seen_g.append(g)
        kept[mode].append(c)

    labels: dict[str, dict] = {}

    def run(prompt_t, rows, fmt, size=12, retry=True):
        for i in range(0, len(rows), size):
            batch = [fmt(c) for c in rows[i : i + size]]
            prompt = prompt_t.format(
                features=features,
                tools=tools_json,
                store=store,
                cases=json.dumps(batch, ensure_ascii=False),
            )
            res, model = base.call_json(prompt, base.CHECK_MODELS, log)
            for x in res:
                for k in ("expected", "tool"):
                    if str(x.get(k)).lower() in ("null", "none", ""):
                        x[k] = None
                for t in x.get("turns") or []:
                    if str(t.get("tool")).lower() in ("null", "none", ""):
                        t["tool"] = None
                labels[x.get("id")] = {**x, "model": model}
            print(f"[check] {len(res)}개 ({model})")
        lost = [c for c in rows if c["id"] not in labels]
        if retry and lost:  # 응답이 잘린 묶음은 작게 나눠 한 번 더 묻는다
            print(f"[check] 라벨 빠짐 {len(lost)}개 다시")
            run(prompt_t, lost, fmt, size=4, retry=False)

    single = [c for c in kept["flow"] if "category" not in c]
    multi = [c for c in kept["flow"] if "category" in c]
    run(base.ROUTE_CHECK, kept["route"], lambda c: {"id": c["id"], "text": c["text"]})
    run(base.FLOW_CHECK, single, lambda c: {"id": c["id"], "text": c["text"]})
    run(
        MULTI_CHECK,
        multi,
        lambda c: {
            "id": c["id"],
            "turns": [t["say"] for t in c["turns"] if "store" not in t["expect"]],
        },
    )

    final: dict[str, list] = {"route": [], "flow": []}
    for mode, rows in kept.items():
        for c in rows:
            lab = labels.get(c["id"])
            why = None
            if lab is None:
                why = "sonnet label missing"
            elif mode == "route":
                if lab.get("expected") is not None:
                    why = f"sonnet feature={lab.get('expected')}"
            elif "category" not in c:
                if lab.get("tool") is not None:
                    why = f"sonnet tool={lab.get('tool')}"
                elif c["kind"] == "missing" and lab.get("null_reason") != "missing":
                    why = f"sonnet null_reason={lab.get('null_reason')}"
            else:
                mine = [t["expect"] for t in c["turns"] if "store" not in t["expect"]]
                theirs = lab.get("turns") or []
                if len(theirs) != len(mine):
                    why = f"sonnet turns={len(theirs)}"
                for i, (m, s) in enumerate(zip(mine, theirs, strict=False)):
                    if why:
                        break
                    if s.get("tool") != m.get("tool"):
                        why = f"turn{i} sonnet tool={s.get('tool')}"
                    elif m.get("tool") and (s.get("args") or {}) != m.get("args"):
                        why = f"turn{i} sonnet args={json.dumps(s.get('args'), ensure_ascii=False)}"
            if why:
                text = c.get("text") or " / ".join(t["say"] for t in c["turns"])
                rejected.append(
                    {**c, "text": text, "issues": [why], "sonnet_reason": (lab or {}).get("reason")}
                )
            else:
                c["source"] += f"+check:{lab['model']}"
                final[mode].append(c)

    base.dump(out / "route_aug2.jsonl", final["route"])
    base.dump(out / "flow_aug2.jsonl", final["flow"])
    base.dump(out / "rejected.jsonl", rejected)
    flow = final["flow"]
    lines = [
        "# 2차 증강 초안",
        "",
        f"- route(shopword) {len(final['route'])} / flow {len(flow)} / 탈락 {len(rejected)}",
        f"- flow 종류: {dict(Counter(c.get('category') or c['kind'] for c in flow))}",
        f"- flow 도구: {dict(Counter(c.get('near_tool') or 'none' for c in flow))}",
        f"- lang: {dict(Counter(c['lang'] for m in final.values() for c in m))}",
        "",
        "## 탈락",
        "",
        "| id | 문장 | 사유 |",
        "|---|---|---|",
    ]
    for r in rejected:
        lines.append(
            f"| {r['id']} | {str(r.get('text', '')).replace('|', '/')[:120]} | {'; '.join(r['issues']).replace('|', '/')} |"
        )
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[2])


def apply(out: Path) -> None:
    from agenthan.eval.cases import load_cases

    splits = json.loads(base.SPLITS_FILE.read_text(encoding="utf-8"))
    for src, dst in (("route_aug2.jsonl", ROUTE_OUT), ("flow_aug2.jsonl", FLOW_OUT)):
        old = base.load_jsonl(dst) if dst.exists() else []
        ids = {r["id"] for r in old}
        new = [r for r in base.load_jsonl(out / src) if r["id"] not in ids]
        base.dump(dst, old + new)
        cases = load_cases(dst)
        splits["splits"].update(make_splits(cases))
        print(
            f"{dst.name}: +{len(new)} → {len(cases)}",
            Counter(splits["splits"][c.id] for c in cases),
        )
    splits["splits"] = dict(sorted(splits["splits"].items()))
    base.SPLITS_FILE.write_text(
        json.dumps(splits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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
