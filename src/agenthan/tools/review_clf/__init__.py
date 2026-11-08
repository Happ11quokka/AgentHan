"""네이버 플레이스 리뷰 [특징] 분류 복제기.

사용 예
    from agenthan.tools.review_clf import get_backend
    clf = get_backend("baseline")          # 로컬 모델 (키 불필요)
    clf = get_backend("hcx")               # HyperCLOVA X (CLOVASTUDIO_API_KEY 필요)
    result = clf.classify("음식이 빨리 나오고 직원분들도 친절하세요")
    result.labels        # ['서비스', '대기시간']
    result.to_dict()     # 특징별 굵게 표시 구간(start, end, text) 포함
"""

from pathlib import Path

from .schema import FEATURE_GUIDE, FEATURES, ClassifyResult, FeatureResult, Span

_ROOT = Path(__file__).resolve().parents[4]


def get_backend(name: str = "baseline", **kwargs):
    if name == "baseline":
        from .backends.baseline import BaselineBackend

        return BaselineBackend.load(kwargs.get("path", _ROOT / "models" / "baseline.joblib"))
    if name == "hcx":
        from .backends.hcx import HCXBackend

        return HCXBackend(**kwargs)
    raise ValueError(f"알 수 없는 백엔드: {name}")


__all__ = ["FEATURES", "FEATURE_GUIDE", "ClassifyResult", "FeatureResult", "Span", "get_backend"]
