"""이미 만든 리뷰 벤치마크 요약(reports/bench-*.json)을 팀 평가 --extra-summary용으로 줄인다.

    uv run python scripts/bench_extra_summary.py reports/bench-rules-20261107-1000.json
    uv run python -m agenthan.eval.run --backend replay \\
        --extra-summary reports/bench-rules-20261107-1000.extra.json

run_benchmark.py는 실행할 때 .extra.json을 같이 쓴다. 이 스크립트는 그 전에 만든 결과용이다.
"""

import argparse
import json
from pathlib import Path

from agenthan.agent.review.bench import extra_summary

ap = argparse.ArgumentParser()
ap.add_argument("summary", type=Path, help="run_benchmark.py가 쓴 요약 JSON")
ap.add_argument("-o", "--out", type=Path, help="기본: <입력>.extra.json")
ap.add_argument("--commit", help="측정한 커밋 (모르면 생략)")
a = ap.parse_args()

out = a.out or a.summary.with_suffix(".extra.json")
data = extra_summary(json.loads(a.summary.read_text(encoding="utf-8")), a.commit)
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"saved {out}")
