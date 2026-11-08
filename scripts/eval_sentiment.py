"""감정 판단 평가: 규칙(rule) vs HCX를 같은 리뷰로 비교한다.

    python scripts/eval_sentiment.py --backend rule
    python scripts/eval_sentiment.py --backend hcx --model HCX-DASH-002     # 본인 컴퓨터에서

기준 1 (별점 대비, 별점은 판단에 쓰지 않음): 별점 2 이하 리뷰를 부정·혼합으로 잡은 비율(높을수록
좋음),
         별점 4.5 이상 리뷰를 부정·혼합으로 잘못 본 비율(낮을수록 좋음)
기준 2 (사람 라벨, data/sentiment_gold.csv의 label 열이 채워져 있을 때): 구절 단위 정확도
표본: 테스트 매장의 별점 있는 리뷰 중 별점 3.5 이하 전부 + 4.5 이상 무작위 100건 (seed 0, 백엔드 간
동일)
"""

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path

from agenthan.agent.review import RuleSentiment, SnapshotRepo
from agenthan.agent.review.analyze import _spans
from agenthan.agent.review.sentiment import prefetch_reviews

ROOT = Path(__file__).resolve().parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="rule", choices=["rule", "hcx"])
ap.add_argument("--model", default="HCX-DASH-002")
ap.add_argument("--high", type=int, default=100)
ap.add_argument(
    "--batch",
    type=int,
    default=1,
    help="HCX에서 리뷰 몇 개씩 묶어 판단할지 (1 = 리뷰마다 따로, 기존 방식)",
)
a = ap.parse_args()

if a.backend == "hcx":
    from agenthan.agent.review.hcx_llm import HCXReviewLLM, HCXSentiment

    sent = HCXSentiment(
        HCXReviewLLM(a.model, cache_path=ROOT / "models" / f"hcx_review_cache_{a.model}.jsonl"),
        batch_size=a.batch,
    )
else:
    sent = RuleSentiment()

rows = [
    r
    for r in SnapshotRepo(ROOT / "data" / "snapshots" / "test_baseline.jsonl").rows
    if r["rating"] is not None
]
low = [r for r in rows if r["rating"] <= 3.5]
high = [r for r in rows if r["rating"] >= 4.5]
random.Random(0).shuffle(high)
sample = low + high[: a.high]
print(f"표본 {len(sample)}건 (별점 3.5 이하 {len(low)}, 4.5 이상 {min(a.high, len(high))})")

prefetch_reviews(sent, sample)  # --batch > 1이면 여기서 묶어서 판단
buckets = {"low(<=2)": Counter(), "mid(2.5~3.5)": Counter(), "high(>=4.5)": Counter()}
misses = []
for i, r in enumerate(sample):
    if a.backend == "hcx" and i % 25 == 0:
        print(f"  {i}/{len(sample)}", flush=True)
    sp = _spans(r, sent)
    rv = sent.review_sentiment({**r, "rating": None}, [s["sentiment"] for s in sp])
    b = "low(<=2)" if r["rating"] <= 2 else "mid(2.5~3.5)" if r["rating"] <= 3.5 else "high(>=4.5)"
    buckets[b][rv] += 1
    if b == "low(<=2)" and rv not in ("negative", "mixed"):
        misses.append(" ".join(r["text"].split())[:80])

out = {"backend": a.backend, "model": a.model if a.backend == "hcx" else None, "n": len(sample)}
for b, c in buckets.items():
    n = sum(c.values())
    out[b] = {
        "n": n,
        "counts": dict(c),
        "neg_or_mixed": round((c["negative"] + c["mixed"]) / n, 3) if n else None,
    }
    print(f"{b:<14} {n:>4}건  부정·혼합 {out[b]['neg_or_mixed']:.0%}  {dict(c)}")
out["low_star_misses"] = misses
if hasattr(sent, "failures"):
    out["hcx_parse_failures"] = sent.failures
    out["batch"] = a.batch
    print(f"HCX 응답을 읽지 못해 규칙으로 대신 판단한 리뷰: {sent.failures}건")

gold = ROOT / "data" / "sentiment_gold.csv"
if gold.exists():
    lab = {
        "긍정": "positive",
        "부정": "negative",
        "중립": "neutral",
        "p": "positive",
        "n": "negative",
        "0": "neutral",
    }
    full = {
        r["review_id"]: r["text"]
        for r in SnapshotRepo(ROOT / "data" / "snapshots" / "test_baseline.jsonl").rows
    }
    # '제외'(한 단어 조각 등 판단 불가)는 빼고, 구절은 리뷰 전문을 맥락으로 판단 (사람도 맥락을
    # 보고 라벨링함)
    rows_g = [
        g
        for g in csv.DictReader(gold.open(encoding="utf-8-sig"))
        if g.get("label", "").strip() in lab
    ]
    if rows_g:
        if hasattr(sent, "prefetch"):
            sent.prefetch([(full.get(g["review_id"]), [g["text"]]) for g in rows_g])
        pred = [sent.spans_sentiment([g["text"]], full.get(g["review_id"]))[0] for g in rows_g]
        acc = sum(p == lab[g["label"].strip()] for p, g in zip(pred, rows_g, strict=False)) / len(
            rows_g
        )
        neg_gold = [
            p for p, g in zip(pred, rows_g, strict=False) if lab[g["label"].strip()] == "negative"
        ]
        neg_recall = sum(p == "negative" for p in neg_gold) / len(neg_gold) if neg_gold else None
        out["gold"] = {
            "n": len(rows_g),
            "accuracy": round(acc, 3),
            "negative_recall": round(neg_recall, 3) if neg_recall is not None else None,
            "errors": [
                {"no": g["no"], "text": g["text"], "gold": lab[g["label"].strip()], "pred": p}
                for p, g in zip(pred, rows_g, strict=False)
                if p != lab[g["label"].strip()]
            ],
        }
        print(
            f"사람 라벨 {len(rows_g)}개 구절: 정확도 {acc:.0%}, 부정 구절 재현율 {neg_recall:.0%}"
        )
if hasattr(sent, "stats"):
    out["batch_stats"] = sent.stats
    print(
        f"묶음 호출 {sent.stats['batch_calls']}회, 묶음으로 판단 {sent.stats['batched_reviews']}건,"
        f" 단건 재판단 {sent.stats['batch_misses']}건"
    )
name = f"sentiment_{a.backend}" + (f"_b{a.batch}" if a.backend == "hcx" and a.batch > 1 else "")
(ROOT / "models" / f"{name}.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"saved models/{name}.json")
