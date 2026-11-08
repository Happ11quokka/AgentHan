"""테스트 매장으로 백엔드 평가.

python scripts/evaluate.py --backend baseline
CLOVASTUDIO_API_KEY=... python scripts/evaluate.py --backend hcx --limit 200 --model HCX-005
"""

import argparse
import json
import random
from pathlib import Path

from agenthan.tools.review_clf import dataset, get_backend
from agenthan.tools.review_clf.evaluate import evaluate, format_table

ROOT = Path(__file__).resolve().parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="baseline", choices=["baseline", "hcx"])
ap.add_argument("--model", default=None, help="HCX 모델명 (기본 HCX_MODEL 환경변수 또는 HCX-005)")
ap.add_argument("--limit", type=int, default=None, help="API 비용을 줄이려면 표본 수 제한")
ap.add_argument("--out", default=None)
a = ap.parse_args()

rows = dataset.load(ROOT / "data" / "reviews.jsonl", "test")
if a.limit:
    random.Random(0).shuffle(rows)  # 같은 seed → 백엔드 간 같은 표본으로 비교 가능
    rows = rows[: a.limit]
kw = {}
if a.backend == "hcx":
    if a.model:
        kw["model"] = a.model
    # 응답 캐시: 중간에 멈춰도 같은 명령을 다시 실행하면 이어서 진행 (이미 받은 리뷰는 다시
    # 호출하지 않음)
    kw["cache_path"] = ROOT / "models" / f"hcx_cache_{a.model or 'default'}.jsonl"
m = evaluate(get_backend(a.backend, **kw), rows, progress=a.backend == "hcx")
print(format_table(m))
if a.out:
    Path(a.out).write_text(json.dumps(m, ensure_ascii=False, indent=2))
