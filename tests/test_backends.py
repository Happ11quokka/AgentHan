"""API 키 없이 돌아가는 테스트: 분절기, 베이스라인, HCX 백엔드(가짜 client로 응답 파싱 검증)."""

import json
from pathlib import Path

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
    reason="baseline 모델은 레포에 없다 (실제 리뷰로 학습). 팀에서 받아 models/에 둔다",
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

    def chat(self, model, messages, **kw):  # ClovaClient.chat과 같은 모양
        self.calls.append({"model": model, "messages": messages, **kw})
        return {"role": "assistant", "content": self.replies.pop(0)}


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
    assert "responseFormat" not in client.calls[0]  # HCX-005는 responseFormat을 거절함(400)
    assert client.calls[0]["maxTokens"] == 1024
    d = {f.feature: [h.text for h in f.highlights] for f in r.features}
    assert d["대기시간"] == ["음식도 빨리 나와서 좋았어요"]  # 이웃 절 병합
    assert d["맛"] == ["밥과 된장국도 맛있고"]
    assert "없는특징" not in d


def test_hcx007_options():
    from agenthan.tools.review_clf.backends.hcx import request_options

    o = request_options("HCX-007")
    assert o["responseFormat"]["type"] == "json" and "maxTokens" not in o
    assert o["maxCompletionTokens"] == 1024 and o["thinking"] == {"effort": "none"}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
