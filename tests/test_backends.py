"""API 키 없이 돌아가는 테스트: 분절기, 베이스라인, HCX 백엔드(가짜 client로 응답 파싱 검증)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agenthan.tools.review_clf import FEATURES, get_backend
from agenthan.tools.review_clf.backends.hcx import HCXBackend
from agenthan.tools.review_clf.segment import split_clauses

ROOT = Path(__file__).resolve().parents[1]

TEXT = "밥과 된장국도 맛있고 음식도 빨리 나와서 좋았어요\n직원분들도 친절하세요!"


def test_segment_offsets():
    for c in split_clauses(TEXT):
        assert TEXT[c.start : c.end] == c.text


@pytest.mark.skipif(
    not (ROOT / "models" / "baseline.joblib").exists(),
    reason="models/baseline.joblib 없음 (카톡으로 받거나 scripts/train_baseline.py로 학습)",
)
def test_baseline():
    r = get_backend("baseline").classify(TEXT)
    assert set(r.labels) <= set(FEATURES)
    assert {"맛", "서비스"} <= set(r.labels)
    for fr in r.features:
        for h in fr.highlights:
            assert TEXT[h.start : h.end] == h.text


class FakeClient:
    """첫 응답은 깨진 JSON → 재시도 후 정상 JSON (strict 무시 상황 재현)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content=self.replies.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_hcx_parsing_and_retry():
    # 절: [0]밥과 된장국도 맛있고 [1]음식도 빨리 나와서 [2]좋았어요 [3]직원분들도 친절하세요!
    good = json.dumps(
        {
            "items": [
                {"clause": 0, "features": ["맛"]},
                {"clause": 1, "features": ["대기시간"]},
                {"clause": 2, "features": ["대기시간", "없는특징"]},
                {"clause": 3, "features": ["서비스"]},
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient(["이건 JSON이 아닙니다", "```json\n" + good + "\n```"])
    r = HCXBackend(client=client, model="HCX-005").classify(TEXT)
    assert len(client.calls) == 2  # 재시도 1회
    assert "response_format" not in client.calls[0]  # HCX-005는 response_format을 거절함
    assert client.calls[0]["max_tokens"] == 1024
    d = {f.feature: [h.text for h in f.highlights] for f in r.features}
    assert d["대기시간"] == ["음식도 빨리 나와서 좋았어요"]  # 이웃 절 병합
    assert d["맛"] == ["밥과 된장국도 맛있고"]
    assert "없는특징" not in d


def test_hcx007_options():
    from agenthan.tools.review_clf.backends.hcx import request_options

    o = request_options("HCX-007")
    assert o["response_format"]["type"] == "json_schema" and "max_tokens" not in o
    assert o["extra_body"] == {"reasoning_effort": "none"}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
