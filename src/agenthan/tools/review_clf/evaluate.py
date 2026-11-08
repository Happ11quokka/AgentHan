"""백엔드에 상관없이 '네이버와 얼마나 같게 분류하는가'를 측정한다.

지표 (특징 f별, 테스트 매장 기준)
- 리뷰 재현율: 네이버가 f로 분류한 리뷰 중 우리도 f를 붙인 비율
- 리뷰 정밀도: 우리가 f를 붙인 리뷰 중 네이버도 f인 비율 (확실한 음성 라벨이 있는 리뷰만 집계)
- 구간 정밀도: 우리가 f로 표시한 구간 중 네이버 하이라이트와 겹치는 비율 (음성 리뷰의 예측 구간은
모두 오답)
- 구간 재현율: 네이버 f 하이라이트 중 우리 f 구간과 겹치는 비율
  정답을 모르는(수집되지도, 음성으로 확인되지도 않은) 리뷰/특징 쌍은 계산에서 제외
"""

from __future__ import annotations

from collections import defaultdict

from .schema import FEATURES


def _ov(a, b) -> bool:
    return min(a[1], b[1]) - max(a[0], b[0]) > 0


def evaluate(backend, rows: list[dict], progress: bool = False) -> dict:
    c = defaultdict(lambda: defaultdict(int))
    for i, r in enumerate(rows):
        if progress and i % 50 == 0:
            print(f"  {i}/{len(rows)}", flush=True)
        res = backend.classify(r["text"])
        pred = {fr.feature: [(h.start, h.end) for h in fr.highlights] for fr in res.features}
        c["_all"]["n"] += 1
        c["_all"]["pred_features"] += len(pred)
        c["_all"]["gold_features"] += len(r["labels"])
        for f in r.get("negatives", []):
            if f in pred:  # 네이버가 붙이지 않은 게 확실한 특징을 예측
                c[f]["review_fp"] += 1
                c[f]["pred_spans"] += len(pred[f])
        for f, gold in r["labels"].items():
            k = c[f]
            k["reviews"] += 1
            k["review_hit"] += f in pred
            p = pred.get(f, [])
            k["pred_spans"] += len(p)
            k["pred_ok"] += sum(any(_ov(ps, g) for g in gold) for ps in p)
            k["gold_spans"] += len(gold)
            k["gold_ok"] += sum(any(_ov(g, ps) for ps in p) for g in gold)
    out = {}
    tot = defaultdict(int)
    for f in FEATURES:
        k = c.get(f)
        if not k:
            continue
        for kk, v in k.items():
            tot[kk] += v
        out[f] = _metrics(k)
    out["_micro"] = _metrics(tot)
    a = c["_all"]
    # 과잉 예측 점검용: 리뷰당 예측 특징 수 vs 관측된(최소) 정답 특징 수
    out["_sanity"] = {
        "pred_features_per_review": a["pred_features"] / max(a["n"], 1),
        "observed_gold_features_per_review": a["gold_features"] / max(a["n"], 1),
    }
    return out


def _metrics(k) -> dict:
    rr = k["review_hit"] / k["reviews"] if k["reviews"] else 0
    hp = k["review_hit"] + k["review_fp"]
    rp = k["review_hit"] / hp if hp else 0
    rf = 2 * rp * rr / (rp + rr) if rp + rr else 0
    sp = k["pred_ok"] / k["pred_spans"] if k["pred_spans"] else 0
    sr = k["gold_ok"] / k["gold_spans"] if k["gold_spans"] else 0
    f1 = 2 * sp * sr / (sp + sr) if sp + sr else 0
    return {
        "n_reviews": k["reviews"],
        "review_precision": rp,
        "review_recall": rr,
        "review_f1": rf,
        "span_precision": sp,
        "span_recall": sr,
        "span_f1": f1,
    }


def format_table(m: dict) -> str:
    """리뷰 단위: 이 리뷰에 특징 f가 있는가 / 구간 단위: 굵게 표시 위치까지 맞췄는가"""
    lines = [
        f"{'특징':<6}{'정답수':>6}{'리뷰P':>8}{'리뷰R':>8}{'리뷰F1':>8}{'구간P':>8}{'구간R':>8}{'구간F1':>8}"
    ]
    for f, v in m.items():
        if f == "_sanity":
            lines.append(
                f"리뷰당 예측 특징 수 {v['pred_features_per_review']:.2f} "
                f"(관측된 최소 정답 {v['observed_gold_features_per_review']:.2f})"
            )
            continue
        lines.append(
            f"{f:<6}{v['n_reviews']:>7}"
            + "".join(
                f"{v[k]:>9.1%}"
                for k in [
                    "review_precision",
                    "review_recall",
                    "review_f1",
                    "span_precision",
                    "span_recall",
                    "span_f1",
                ]
            )
        )
    return "\n".join(lines)
