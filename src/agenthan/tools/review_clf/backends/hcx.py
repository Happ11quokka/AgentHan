"""HyperCLOVA X(CLOVA Studio) 백엔드 — OpenAI 호환 API 사용.

설계
- 리뷰를 먼저 로컬 분절기로 절(clause) 단위로 자르고 번호를 붙여 보낸다.
  LLM은 "몇 번 절이 어떤 특징인지"만 답하므로, 굵게 표시 구간의 오프셋은 항상 정확하다
  (LLM이 문장을 베껴 쓰다 틀리는 문제를 피함). 이웃한 같은 특징 절은 합친다.
- JSON 형식 지정(response_format)은 HCX-007만 받는다 (HCX-005·DASH는 400). 어느 모델이든
  응답을 pydantic으로 검증하고 실패하면 한 번 재시도한다. 모델별 옵션은 request_options() 참고.

환경 변수
- CLOVASTUDIO_API_KEY (필수), HCX_MODEL (기본 HCX-005), CLOVASTUDIO_BASE_URL (선택)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from agenthan.core.llm import DEFAULT_BASE_URL, make_client
from agenthan.core.llm import request_options as _hcx_request_options

from ..schema import FEATURE_GUIDE, FEATURES, ClassifyResult, FeatureResult, Span
from ..segment import merge_adjacent, split_clauses
from .base import Backend

__all__ = ["DEFAULT_BASE_URL", "HCXBackend", "request_options"]


class _Item(BaseModel):
    clause: int
    features: list[str]

    @field_validator("features")
    @classmethod
    def _known(cls, v):
        return [f for f in v if f in FEATURES]  # 정의 밖 라벨은 버린다


class _Answer(BaseModel):
    items: list[_Item]


JSON_SCHEMA = {
    "name": "review_features",
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clause": {"type": "integer"},
                        "features": {
                            "type": "array",
                            "items": {"type": "string", "enum": FEATURES},
                        },
                    },
                    "required": ["clause", "features"],
                },
            }
        },
        "required": ["items"],
    },
}


def request_options(model: str, max_tokens: int = 1024) -> dict:
    """분류기 요청 옵션: hcx.request_options + 분류기 JSON 스키마 (HCX-007만 사용)."""
    return _hcx_request_options(model, max_tokens, JSON_SCHEMA)


def build_system_prompt() -> str:
    guide = "\n".join(f"- {f}: {d}" for f, d in FEATURE_GUIDE.items())
    return (
        "당신은 네이버 플레이스 방문자 리뷰의 [특징] 분류기입니다. 번호가 붙은 절 목록을 받으면, "
        "각 절이 다음 16개 특징 중 무엇을 언급하는지 고르세요.\n\n"
        f"{guide}\n\n"
        "규칙\n"
        "1. 긍정·부정을 구분하지 않습니다. '불친절해요'도 서비스, '양이 적어요'도 음식량입니다.\n"
        "2. 한 절에 여러 특징을 붙일 수 있습니다. 해당 특징이 없는 절은 결과에서 빼세요.\n"
        "3. 실제로 언급된 것만 고르고 추측하지 마세요. 대부분의 리뷰는 특징이 1~3개입니다.\n"
        '4. 반드시 JSON으로만 답하세요: {"items": [{"clause": 번호, "features": ["특징", ...]}]}'
    )


def format_clauses(text: str):
    clauses = split_clauses(text)
    return clauses, "\n".join(f"[{i}] {c.text}" for i, c in enumerate(clauses))


class HCXBackend(Backend):
    name = "hcx"

    def __init__(
        self,
        model: str | None = None,
        client=None,
        fewshot_path: str | Path | None = None,
        temperature: float = 0.0,
        max_retries: int = 1,
        cache_path: str | Path | None = None,
        min_interval: float | None = None,
    ):
        self.model = model or os.getenv("HCX_MODEL", "HCX-005")
        self.client = client or make_client()
        # 호출 간 최소 간격(초). 테스트 키는 한도가 낮아 기본 1초
        self.min_interval = (
            float(os.getenv("HCX_MIN_INTERVAL", "1.0")) if min_interval is None else min_interval
        )
        self._last_call = 0.0
        # 같은 리뷰를 다시 부르지 않도록 응답을 저장 → 중간에 멈춰도 다시 실행하면 이어서 진행
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[str, dict] = {}
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                self.cache[rec["key"]] = rec["answer"]
        self.temperature = temperature
        self.max_retries = max_retries
        self.system = build_system_prompt()
        self.fewshot = self._load_fewshot(fewshot_path)

    @staticmethod
    def _load_fewshot(path) -> list[dict]:
        path = Path(path) if path else Path(__file__).resolve().parents[5] / "data" / "fewshot.json"
        if not path.exists():
            return []
        msgs = []
        for ex in json.loads(path.read_text(encoding="utf-8")):
            _, listing = format_clauses(ex["text"])
            msgs.append({"role": "user", "content": listing})
            msgs.append(
                {
                    "role": "assistant",
                    "content": json.dumps({"items": ex["items"]}, ensure_ascii=False),
                }
            )
        return msgs

    def _call(self, listing: str) -> _Answer:
        messages = [
            {"role": "system", "content": self.system},
            *self.fewshot,
            {"role": "user", "content": listing},
        ]
        last_err = None
        for _ in range(self.max_retries + 1):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                **request_options(self.model),
            )
            raw = resp.choices[0].message.content or ""
            try:
                return _Answer.model_validate_json(_extract_json(raw))
            except (ValidationError, ValueError) as e:
                last_err = e
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": "형식이 틀렸습니다. 지정한 JSON만 다시 출력하세요.",
                    },
                ]
        raise RuntimeError(f"HCX 응답 파싱 실패: {last_err}")

    def classify(self, text: str) -> ClassifyResult:
        clauses, listing = format_clauses(text)
        res = ClassifyResult(text=text)
        if not clauses:
            return res
        key = hashlib.sha1(f"{self.model}\n{self.system}\n{listing}".encode()).hexdigest()
        if key in self.cache:
            ans = _Answer.model_validate(self.cache[key])
        else:
            ans = self._call(listing)
            if self.cache_path:
                self.cache[key] = ans.model_dump()
                with self.cache_path.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps({"key": key, "answer": self.cache[key]}, ensure_ascii=False)
                        + "\n"
                    )
        flags = {f: [False] * len(clauses) for f in FEATURES}
        for it in ans.items:
            if 0 <= it.clause < len(clauses):
                for f in it.features:
                    flags[f][it.clause] = True
        for f in FEATURES:
            if any(flags[f]):
                spans = merge_adjacent(clauses, flags[f], text)
                res.features.append(FeatureResult(f, [Span(s, e, text[s:e]) for s, e in spans]))
        return res


def _extract_json(raw: str) -> str:
    """```json 펜스나 앞뒤 설명이 붙어 와도 JSON 본문만 꺼낸다."""
    s, e = raw.find("{"), raw.rfind("}")
    if s < 0 or e < s:
        raise ValueError("JSON 없음")
    return raw[s : e + 1]
