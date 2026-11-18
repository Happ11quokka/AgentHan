"""NLU 정답셋 실측 비교 (5단계 티켓 08): 지금 프롬프트 vs 튜닝 모델, 두 가게, 정확도와 응답 시간.

실제 HCX를 부른다(키 필요, 호출 한도 때문에 호출 사이 1.1초). 정답셋은 tests/voice의 JSON.

    uv run python -m agenthan.voice.nlu.tuning_eval                 # 지금 프롬프트
    uv run python -m agenthan.voice.nlu.tuning_eval --task <taskId> # 튜닝 모델

채점은 골드셋 테스트(`tests/voice/test_hcx_gold.py`)와 같다: act 타입 순서가 맞으면 타입 정답,
거기에 menu_text(한쪽이 다른 쪽을 포함)와 qty까지 맞으면 슬롯 정답.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.catalog import menu_catalog
from agenthan.voice.nlu.hcx import HcxNlu
from agenthan.voice.store_config import StoreConfig

GOLDS = (Path("tests/voice/nlu_gold.json"), Path("tests/voice/nlu_gold_pho.json"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _types(acts: Sequence[Act]) -> list[str]:
    got = [a.type for a in acts if a.type not in ("yes", "no")]
    return got or [a.type for a in acts]


def types_ok(item: dict[str, Any], acts: Sequence[Act]) -> bool:
    got = _types(acts)
    return got == [a["type"] for a in item["expect"]] or got in item.get("also_ok", [])


def slots_ok(item: dict[str, Any], acts: Sequence[Act]) -> bool:
    got = [a for a in acts if a.type not in ("yes", "no")] or list(acts)
    if len(got) != len(item["expect"]):
        return False
    for want, act in zip(item["expect"], got, strict=True):
        if "menu_text" in want:
            w, g = _norm(want["menu_text"]), _norm(act.menu_text)
            if not g or (w not in g and g not in w):
                return False
        if "qty" in want and act.qty != want["qty"]:
            return False
    return True


def run_gold(path: Path, nlu: HcxNlu, *, pause_s: float = 1.1) -> dict[str, Any]:
    from agenthan.core.fakes import FakeStoreKnowledge  # 개발 도구라 더미 가게를 함수 안에서만

    gold = json.loads(path.read_text(encoding="utf-8"))
    fixture = Path(gold["fixture"]) if gold.get("fixture") else None
    store = (
        FakeStoreKnowledge.from_fixture(fixture) if fixture else FakeStoreKnowledge.from_fixture()
    )
    catalog = menu_catalog(store.list_menu(), StoreConfig.from_fixture(fixture).required_options)
    rows, times = [], []
    for item in gold["items"]:
        ctx = item.get("ctx", {})
        context = NluContext(
            menu_names=tuple(gold["menu_names"]),
            cart_summary=ctx.get("cart_summary", ""),
            last_question=ctx.get("last_question", ""),
            state=ctx.get("state", "ORDERING"),
            menu_catalog=catalog,
        )
        start = time.perf_counter()
        acts = nlu.parse(item["text"], context)
        times.append((time.perf_counter() - start) * 1000)
        ok = types_ok(item, acts)
        rows.append(
            {
                "id": item["id"],
                "text": item["text"],
                "type_ok": ok,
                "slot_ok": ok and slots_ok(item, acts),
                "got": [{k: v for k, v in a.__dict__.items() if v not in ("", None)} for a in acts],
            }
        )
        time.sleep(pause_s)
    n = len(rows)
    return {
        "gold": path.name,
        "n": n,
        "type_acc": sum(r["type_ok"] for r in rows) / n,
        "slot_acc": sum(r["slot_ok"] for r in rows) / n,
        "p50_ms": round(statistics.median(times)),
        "p90_ms": round(sorted(times)[int(n * 0.9) - 1]),
        "misses": [r for r in rows if not r["slot_ok"]],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NLU 정답셋 실측 비교")
    parser.add_argument("--task", default="", help="튜닝 작업 id. 없으면 지금 프롬프트")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSON (private/ 아래 권장)")
    args = parser.parse_args(argv)
    nlu = HcxNlu.from_env(task_id=args.task)
    label = f"튜닝 {args.task}" if args.task else "지금 프롬프트"
    results = [run_gold(path, nlu) for path in GOLDS]
    for r in results:
        print(
            f"[{label}] {r['gold']}: 타입 {r['type_acc']:.0%} · 슬롯 {r['slot_acc']:.0%} "
            f"({r['n']}개) · 응답 p50 {r['p50_ms']}ms p90 {r['p90_ms']}ms"
        )
        for m in r["misses"]:
            print(f"    {m['id']} {m['text']!r} → {m['got']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"label": label, "results": results}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
