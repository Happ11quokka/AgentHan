from __future__ import annotations

from abc import ABC, abstractmethod

from ..schema import ClassifyResult


class Backend(ABC):
    """모든 분류기 백엔드(로컬 베이스라인, HCX 등)가 따르는 공통 인터페이스.
    다른 도구(요약, 보고 루틴 등)는 이 인터페이스에만 의존한다."""

    name: str = "base"

    @abstractmethod
    def classify(self, text: str) -> ClassifyResult: ...

    def classify_many(self, texts: list[str]) -> list[ClassifyResult]:
        return [self.classify(t) for t in texts]
