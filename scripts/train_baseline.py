"""베이스라인 학습 → 임계값 튜닝 → 테스트 매장 평가 → 모델 저장."""

import hashlib
import json
import sys
from pathlib import Path

from naver_review_clf import dataset
from naver_review_clf.backends.baseline import BaselineBackend
from naver_review_clf.evaluate import evaluate, format_table

ROOT = Path(__file__).resolve().parents[1]
data = ROOT / "data" / "reviews.jsonl"
if not data.exists():
    dataset.build(ROOT / "data" / "naver_reviews_raw.xlsx", data)

train_all = dataset.load(data, "train")
test = dataset.load(data, "test")


# 학습 매장 중 일부를 임계값 튜닝용으로 분리 (매장 단위)
def is_val(r):
    return int(hashlib.md5(("v" + r["store"]).encode()).hexdigest(), 16) % 5 == 0


train = [r for r in train_all if not is_val(r)]
val = [r for r in train_all if is_val(r)]
print(f"train {len(train)} / val {len(val)} / test {len(test)} reviews")

# 기본: 정답이 확실한 (양성 + 날짜로 확인된 음성) 라벨만 사용. --weak 이면 모르는 쌍도 약한
# 음성으로 추가
weak = "--weak" in sys.argv
m = BaselineBackend().fit(train, weak_negatives=weak).tune_thresholds(val)
# 최종 모델은 튜닝된 임계값을 유지한 채 학습 매장 전체로 다시 학습
final = BaselineBackend(thresholds=m.thresholds).fit(train_all, weak_negatives=weak)

metrics = evaluate(final, test)
print(format_table(metrics))
(ROOT / "models").mkdir(exist_ok=True)
final.save(ROOT / "models" / "baseline.joblib")
(ROOT / "models" / "baseline_metrics.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=2)
)
print("saved models/baseline.joblib")
