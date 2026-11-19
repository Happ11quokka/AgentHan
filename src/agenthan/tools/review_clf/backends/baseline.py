"""로컬 베이스라인: 절 단위 문자 n-gram TF-IDF + 특징별 로지스틱 회귀.

API 키·네트워크 없이 바로 돌릴 수 있어서, 다른 도구를 붙여 검증할 때 '네이버 대용'으로 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from ..schema import FEATURES, ClassifyResult, FeatureResult, Span
from ..segment import merge_adjacent, split_clauses
from .base import Backend

# 임계값 탐색 구간 (기본 인자로 함수 호출을 쓰지 않으려고 모듈 상수로 둔다)
THRESHOLD_GRID = np.arange(0.3, 0.96, 0.05)


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def clause_examples(rows: list[dict], weak_negatives: bool = False):
    """리뷰 → (절 텍스트, 특징, 라벨, 가중치).

    - 리뷰에 특징 f 라벨이 관측됐으면: f 하이라이트와 겹치는 절 = 1, 나머지 절 = 0 (확실한 라벨)
    - 관측되지 않은 특징은 알 수 없으므로 기본적으로 제외 (weak_negatives=True면 낮은 가중치의
    0으로 사용)
    """
    texts, feats, ys, ws = [], [], [], []
    for r in rows:
        clauses = split_clauses(r["text"])
        negs = set(r.get("negatives", []))
        for f in FEATURES:
            spans = r["labels"].get(f)
            if spans is None and f in negs:
                spans = []  # 확실한 음성: 모든 절이 0
            for c in clauses:
                if spans is not None:
                    hit = any(
                        _overlap(c.start, c.end, s, e) >= 0.5 * (c.end - c.start)
                        or _overlap(c.start, c.end, s, e) >= 0.5 * (e - s)
                        for s, e in spans
                    )
                    texts.append(c.text)
                    feats.append(f)
                    ys.append(int(hit))
                    ws.append(1.0)
                elif weak_negatives:
                    texts.append(c.text)
                    feats.append(f)
                    ys.append(0)
                    ws.append(0.3)
    return texts, feats, np.array(ys), np.array(ws)


class BaselineBackend(Backend):
    name = "baseline"

    def __init__(self, vectorizer=None, models=None, thresholds=None):
        self.vectorizer = vectorizer
        self.models: dict[str, LogisticRegression] = models or {}
        self.thresholds: dict[str, float] = thresholds or {f: 0.5 for f in FEATURES}

    # ---------- 학습 ----------
    def fit(self, rows: list[dict], weak_negatives: bool = True, C: float = 4.0):
        texts, feats, ys, ws = clause_examples(rows, weak_negatives)
        self.vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(1, 4),
            min_df=2,
            sublinear_tf=True,
            max_features=200_000,
        )
        self.vectorizer.fit(texts)
        X = self.vectorizer.transform(texts)
        feats = np.array(feats)
        for f in FEATURES:
            m = feats == f
            if ys[m].sum() == 0:
                continue
            clf = LogisticRegression(C=C, max_iter=2000, class_weight="balanced")
            clf.fit(X[m], ys[m], sample_weight=ws[m])
            self.models[f] = clf
        return self

    def tune_thresholds(self, rows: list[dict], grid=THRESHOLD_GRID):
        """검증 매장에서 특징별로 절 단위 F1이 최대가 되는 임계값을 고른다."""
        texts, feats, ys, _ = clause_examples(rows, weak_negatives=False)
        X = self.vectorizer.transform(texts)
        feats = np.array(feats)
        for f, clf in self.models.items():
            m = feats == f
            if ys[m].sum() == 0:
                continue
            p = clf.predict_proba(X[m])[:, 1]
            best = max(grid, key=lambda t: _f1(ys[m], p >= t))
            self.thresholds[f] = float(best)
        return self

    # ---------- 추론 ----------
    def classify(self, text: str) -> ClassifyResult:
        clauses = split_clauses(text)
        res = ClassifyResult(text=text)
        if not clauses:
            return res
        X = self.vectorizer.transform([c.text for c in clauses])
        for f in FEATURES:
            clf = self.models.get(f)
            if clf is None:
                continue
            p = clf.predict_proba(X)[:, 1]
            flags = list(p >= self.thresholds[f])
            if not any(flags):
                continue
            spans = merge_adjacent(clauses, flags, text)
            res.features.append(
                FeatureResult(
                    feature=f,
                    highlights=[Span(s, e, text[s:e]) for s, e in spans],
                    score=float(p.max()),
                )
            )
        return res

    def save(self, path: str | Path):
        import sklearn

        joblib.dump(
            {
                "vectorizer": self.vectorizer,
                "models": self.models,
                "thresholds": self.thresholds,
                "sklearn_version": sklearn.__version__,
            },
            path,
            compress=3,
        )

    @classmethod
    def load(cls, path: str | Path) -> BaselineBackend:
        import warnings

        import sklearn

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # 버전 불일치는 아래에서 직접 확인한다
            d = joblib.load(path)
        saved = d.get("sklearn_version", "1.9.1")  # 버전 기록 전에 저장된 모델은 1.9.1
        if saved != sklearn.__version__:
            # scikit-learn 버전이 다르면 언피클은 되어도 predict_proba에서 깨질 수 있다 (1.9.1 → 1.
            # 7.2에서 확인)
            raise RuntimeError(
                f"모델은 scikit-learn {saved}로 저장됐고 현재는 {sklearn.__version__}입니다. "
                "`python scripts/train_baseline.py`로 현재 환경에서 다시 학습하세요 (약 1분 20초, "
                "지표 동일)."
            )
        return cls(d["vectorizer"], d["models"], d["thresholds"])


def _f1(y, pred) -> float:
    tp = int((y & pred).sum())
    fp = int((~y.astype(bool) & pred).sum())
    fn = int((y.astype(bool) & ~pred).sum())
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)
