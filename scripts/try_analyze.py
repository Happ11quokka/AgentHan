"""analyze_reviews를 스냅샷으로 실행해 본다 (LLM이 넘길 인자를 직접 입력).

python scripts/try_analyze.py --store 달빛모찌 --period 30d --focus complaint
python scripts/try_analyze.py --store 달빛모찌 --period all --topic 딸기모찌
"""

import argparse
import json
from datetime import date
from pathlib import Path

from agenthan.agent.review import MenuCatalog, SnapshotRepo, analyze_reviews

ROOT = Path(__file__).resolve().parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--store", required=True)
ap.add_argument("--today", default="2026-11-07", help="스냅샷 기준일")
for k in ["period", "start_date", "end_date", "focus", "topic", "format"]:
    ap.add_argument(f"--{k}")
ap.add_argument(
    "--llm",
    choices=["none", "hcx"],
    default="none",
    help="hcx: 감정 판단·요약을 HCX로 (본인 컴퓨터에서)",
)
ap.add_argument("--model", default="HCX-DASH-002")
ap.add_argument("--snapshot", default=str(ROOT / "fixtures" / "review_snapshot.jsonl"))
ap.add_argument("--menus", default=str(ROOT / "fixtures" / "review_menus.json"))
a = ap.parse_args()

args = {
    k: getattr(a, k)
    for k in ["period", "start_date", "end_date", "focus", "topic", "format"]
    if getattr(a, k)
}
extra = {}
if a.llm == "hcx":
    from agenthan.agent.review.hcx_llm import HCXReviewLLM, HCXSentiment

    llm = HCXReviewLLM(a.model, cache_path=ROOT / "models" / f"hcx_review_cache_{a.model}.jsonl")
    extra = {"llm": llm, "sentiment": HCXSentiment(llm)}
res = analyze_reviews(
    store_id=a.store,
    today=date.fromisoformat(a.today),
    repo=SnapshotRepo(a.snapshot),
    catalog=MenuCatalog.load(a.menus),
    **args,
    **extra,
)
print(json.dumps(res, ensure_ascii=False, indent=2))
