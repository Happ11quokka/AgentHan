"""리뷰 도구 호출 벤치마크 실행 (실제 HCX 호출). 더미 가게(fixtures) 기준이라 결과를 공유해도 된다.

    uv run --env-file .env python scripts/run_benchmark.py                 # 전체
    uv run --env-file .env python scripts/run_benchmark.py --only A01 H01  # 일부
    uv run --env-file .env python scripts/run_benchmark.py --category 답글

결과: reports/bench-<날짜시각>.csv (턴별), .json (요약)
"""

import argparse
import csv
import json
import time
from pathlib import Path

from agenthan.agent.review import MenuCatalog, SnapshotRepo
from agenthan.agent.review.agent import ReviewToolbox, run_turn, system_prompt
from agenthan.agent.review.bench import NOW, TODAY, load_goldset, run_case, summarize
from agenthan.agent.review.clova import ClovaClient
from agenthan.agent.review.hcx_llm import HCXReviewLLM, HCXSentiment
from agenthan.agent.review.replies import MockReplyStore
from agenthan.agent.review.report import ReportSettingsStore

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--goldset", default=str(ROOT / "tests" / "goldset" / "review_tools.jsonl"))
ap.add_argument("--chat-model", default="HCX-005", help="도구 선택·답변·답글 모델")
ap.add_argument("--analysis", choices=["rule", "hcx"], default="rule", help="도구 안 감정 판단")
ap.add_argument("--only", nargs="*", help="문항 ID")
ap.add_argument("--category", help="이 글자가 들어간 분류만")
ap.add_argument(
    "--router",
    choices=["rules", "intent", "hybrid"],
    default="rules",
    help="rules: 규칙(기본), intent: HCX 의도 분류, hybrid: 규칙 먼저 + 못 잡은 턴만 분류",
)
ap.add_argument(
    "--split",
    choices=["dev", "holdout"],
    help="dev: 규칙을 고칠 때 본 문항, holdout: 고친 뒤 새로 만든 문항 (규칙 과적합 확인용)",
)
a = ap.parse_args()

client = ClovaClient()
repo = SnapshotRepo(ROOT / "fixtures" / "review_snapshot.jsonl")
catalog = MenuCatalog.load(ROOT / "fixtures" / "review_menus.json")
writer = HCXReviewLLM(a.chat_model, client=client)
extra = {}
if a.analysis == "hcx":
    llm = HCXReviewLLM("HCX-DASH-002", client=client)
    extra = {"llm": llm, "sentiment": HCXSentiment(llm)}


def make_toolbox(store: str) -> ReviewToolbox:
    # 위생 리뷰(9월)가 '답글 없음'이 되도록 돈가스집만 200일 가정
    seed = 200 if store.startswith("별빛돈가스") else 14
    return ReviewToolbox(
        store,
        TODAY,
        repo,
        catalog,
        **extra,
        writer=writer,
        reply_store=MockReplyStore(today=TODAY, seed_days=seed),
        settings_store=ReportSettingsStore(None),
        now=NOW,
        router=a.router,
    )


cases = load_goldset(a.goldset)
if a.only:
    cases = [c for c in cases if c["id"] in a.only]
if a.category:
    cases = [c for c in cases if a.category in c["category"]]
if a.split:
    cases = [c for c in cases if c.get("split", "dev") == a.split]
rows = []
t0 = time.time()
for n, case in enumerate(cases, 1):
    got = run_case(case, client, a.chat_model, make_toolbox, run_turn, system_prompt)
    rows.extend(got)
    mark = "✔" if all(r["pass"] for r in got) else "✘"
    fails = "; ".join(f"t{r['turn']} {r['detail']}" for r in got if not r["pass"])
    print(f"[{n}/{len(cases)}] {mark} {case['id']} {case['category']} {fails}", flush=True)

summary = summarize(rows)
summary.update(
    {
        "goldset": Path(a.goldset).name,
        "split": a.split or "all",
        "router": a.router,
        "chat_model": a.chat_model,
        "analysis": a.analysis,
        "minutes": round((time.time() - t0) / 60, 1),
    }
)
out = ROOT / "reports"
out.mkdir(exist_ok=True)
stem = out / f"bench-{a.router}-{time.strftime('%Y%m%d-%H%M')}"
fields = list(rows[0]) if rows else []
with stem.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    for r in rows:
        w.writerow(
            {
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, list | dict) else v
                for k, v in r.items()
            }
        )
stem.with_suffix(".json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
print(f"saved {stem.relative_to(ROOT)}.csv / .json")
