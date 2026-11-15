"""가게 지식 검색(RagStoreKnowledge) 정확도 채점.

    uv run python scripts/eval_rag.py                         # 더미: goldset + holdout
    uv run python scripts/eval_rag.py --data data/clean       # 실제 데이터 (커밋 금지 폴더)
    uv run python scripts/eval_rag.py --files holdout.jsonl   # 파일 하나만
    uv run python scripts/eval_rag.py --vector                # CLOVA 임베딩도 켜기 (키 필요)

한 줄 형식: {"query", "expected_ids", "primary_id", "type", "channel"} + 선택 "match", "uncertain"

채점 규칙
- 정답이 빈 목록(none 유형 등): 결과가 비었으면 1, 아니면 0 (Recall@1·@3·MRR 모두)
- "match": "any": 상위 k개 결과가 비어 있지 않고 전부 expected_ids 안이면 Recall@k = 1
- 보통 질문: Recall@k = (상위 k개 중 정답 수) / min(k, 정답 수)
  → 정답이 4개(메도빅 묶음)여도 1등이 정답이면 Recall@1 = 1
- MRR: primary_id가 있으면 그 메뉴의 순위, 없으면 첫 정답의 순위로 1/순위. 못 찾으면 0
- "uncertain": true인 질문은 전체 점수에서 빼고 따로 보여 준다
- 틀린 질문: Recall@3 < 1 이거나 MRR < 1
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from agenthan.rag import RagStoreKnowledge
from agenthan.rag.repository import resolve_data_dir

SEARCH_K = 10
METRICS = ("R@1", "R@3", "MRR")


def score(row: dict, got: list[str]) -> dict[str, float]:
    exp = row["expected_ids"]
    if not exp:
        ok = float(not got)
        return {"R@1": ok, "R@3": ok, "MRR": ok}
    out: dict[str, float] = {}
    for k in (1, 3):
        top = got[:k]
        if row.get("match") == "any":
            out[f"R@{k}"] = float(bool(top) and all(i in exp for i in top))
        else:
            out[f"R@{k}"] = len(set(top) & set(exp)) / min(k, len(exp))
    target = row.get("primary_id")
    if target:
        rank = got.index(target) + 1 if target in got else 0
    else:
        rank = next((n for n, i in enumerate(got, 1) if i in exp), 0)
    out["MRR"] = 1.0 / rank if rank else 0.0
    return out


def mean(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return dict.fromkeys(METRICS, float("nan"))
    return {m: sum(r["score"][m] for r in rows) / len(rows) for m in METRICS}


def fmt(m: dict[str, float]) -> str:
    return "  ".join(f"{k} {v:.3f}" for k, v in m.items())


def evaluate(kb: RagStoreKnowledge, path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        got = [h.item.id for h in kb.search_menu(row["query"], k=SEARCH_K)]
        row["got"] = got
        row["score"] = score(row, got)
        rows.append(row)
    return rows


def report(name: str, rows: list[dict]) -> list[str]:
    main = [r for r in rows if not r.get("uncertain")]
    unsure = [r for r in rows if r.get("uncertain")]
    lines = [
        f"== {name}: {len(rows)}개 (uncertain {len(unsure)}개 따로)",
        f"  전체      {fmt(mean(main))}",
    ]
    if unsure:
        lines.append(f"  uncertain {fmt(mean(unsure))}")
    for field in ("type", "channel"):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in main:
            groups[r.get(field, "?")].append(r)
        lines.append(f"  [{field}별]")
        for g in sorted(groups):
            lines.append(f"    {g:<12} {len(groups[g]):>3}개  {fmt(mean(groups[g]))}")
    wrong = [r for r in rows if r["score"]["R@3"] < 1 or r["score"]["MRR"] < 1]
    lines.append(f"  [틀린 질문 {len(wrong)}개]")
    for r in wrong:
        flag = " (uncertain)" if r.get("uncertain") else ""
        lines.append(f"    ✗ [{r['type']}/{r['channel']}] {r['query']}{flag}")
        lines.append(f"        정답 {r['expected_ids']} (1순위 {r.get('primary_id')})")
        lines.append(f"        결과 {r['got'][:5]}   {fmt(r['score'])}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--data", help="데이터 폴더 (기본: AGENTHAN_DATA_DIR, 없으면 fixtures/dummy_store)"
    )
    ap.add_argument("--files", nargs="*", default=["goldset.jsonl", "holdout.jsonl"])
    ap.add_argument("--vector", action="store_true", help="CLOVA 임베딩 벡터 검색도 켠다")
    args = ap.parse_args()

    data_dir = resolve_data_dir(args.data)
    kb = RagStoreKnowledge.from_dir(data_dir, embed=None if args.vector else False)
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"데이터: {data_dir}")
    summary = []
    for name in args.files:
        path = Path(name) if Path(name).is_absolute() else data_dir / name
        if not path.exists():
            print(f"(없음: {path})")
            continue
        rows = evaluate(kb, path)
        main_rows = [r for r in rows if not r.get("uncertain")]
        summary.append((name, len(rows), mean(main_rows)))
        print("\n".join(report(name, rows)))
    if len(summary) > 1:
        print("== 요약 (uncertain 제외)")
        for name, n, m in summary:
            print(f"  {name:<16} {n:>3}개  {fmt(m)}")


if __name__ == "__main__":
    main()
