"""특징 구간(굵게 표시된 절)별 긍정·부정 판단.

분류기는 긍정·부정을 구분하지 않으므로 여기서 판단한다.
- RuleSentiment: API 키 없이 쓰는 오프라인 규칙. 정확도가 낮으니 개발·테스트용
- 실서비스는 HCX 구현을 같은 인터페이스(span_sentiment)로 붙인다
"""

from __future__ import annotations

import re

NEG = [
    r"맛\s?없",
    r"노맛",
    r"별로",
    r"아쉽",
    r"아쉬",
    r"실망",
    r"최악",
    r"불친절",
    r"비싸",
    r"사악",
    r"느끼",
    r"짜서",
    r"짜요",
    r"짰",
    r"싱거",
    r"눅눅",
    r"퍼지",
    r"늦게",
    r"느려",
    r"오래\s?걸",
    r"기다리라",
    r"더럽",
    r"지저분",
    r"머리카락",
    r"이물질",
    r"작아졌",
    r"적고",
    r"적어",
    r"적네",
    r"이상한",
    r"평범",
    r"품절",
    r"솔드아웃",
    r"없어서",
    r"안\s?되어",
    r"못\s?먹",
    r"불편",
    r"시끄럽",
    r"좁아",
    r"비추",
    r"탄\s?부분",
    r"bad",
    r"rude",
    r"expensive",
    r"dirty",
    r"плох",
    r"дорог",
    r"грязн",
]
POS = [
    r"맛있",
    r"맛나",
    r"맛잇",
    r"좋",
    r"친절",
    r"최고",
    r"추천",
    r"만족",
    r"신선",
    r"쫄깃",
    r"부드럽",
    r"달달",
    r"깨끗",
    r"빨리",
    r"빠르",
    r"푸짐",
    r"넉넉",
    r"예쁘",
    r"이쁘",
    r"귀여",
    r"저렴",
    r"가성비",
    r"굿",
    r"짱",
    r"또\s?(올|갈|방문)",
    r"재방문",
    r"대기\s?없이",
    r"기다리지\s?않",
    r"편해",
    r"편하",
    r"good",
    r"great",
    r"delicious",
    r"tasty",
    r"friendly",
    r"вкусн",
    r"отличн",
    r"хорош",
]
# 부정 표현을 뒤집는 경우: '많이 안 달고' 같은 디저트 칭찬, '친절까지는 안 바라지만'은 그대로 둔다
NEUTRALIZE = [
    r"안\s?달",
    r"달지\s?않",
    r"짜지\s?않",
    r"느끼하지\s?않",
    r"비싸지\s?않",
    r"아쉬움\s?없",
]


def _count(patterns, text) -> int:
    return sum(len(re.findall(p, text, flags=re.I)) for p in patterns)


class RuleSentiment:
    name = "rule"

    def span_sentiment(self, text: str) -> str:
        """→ positive | negative | neutral"""
        neutral_hits = _count(NEUTRALIZE, text)
        for p in NEUTRALIZE:  # '안 달고'의 '안'을 부정으로 세지 않게 먼저 지운다
            text = re.sub(p, " ", text, flags=re.I)
        neg = _count(NEG, text)
        for p in NEG:  # '불친절'의 '친절'을 긍정으로 다시 세지 않게 지운다
            text = re.sub(p, " ", text, flags=re.I)
        pos = _count(POS, text) + neutral_hits
        if neg > pos:
            return "negative"
        if pos > neg:
            return "positive"
        return "neutral"

    def spans_sentiment(self, texts: list[str], context: str | None = None) -> list[str]:
        return [self.span_sentiment(t) for t in texts]  # 규칙은 맥락을 쓰지 않는다

    def review_sentiment(self, review: dict, span_labels: list[str]) -> str:
        """구간 판단 + 별점으로 리뷰 전체를 positive | negative | mixed | neutral 로."""
        labels = set(span_labels)
        if not labels - {"neutral"}:
            labels = set(self.spans_sentiment([review["text"]]))
        r = review.get("rating")
        if labels >= {"positive", "negative"}:
            return "mixed"
        if "negative" in labels:
            return "negative"
        if "positive" in labels:
            # 별점이 낮은데 긍정 표현만 잡혔다면 숨은 불만이 있다고 본다
            return "mixed" if r is not None and r <= 2 else "positive"
        if r is not None:
            return "negative" if r <= 2 else "positive" if r >= 4 else "neutral"
        return "neutral"


def prefetch_reviews(sentiment, reviews: list[dict]) -> None:
    """HCX 감정 판단이면 리뷰들을 묶어서 미리 판단해 둔다 (규칙 방식이면 아무것도 안 함)."""
    if not hasattr(sentiment, "prefetch"):
        return
    pairs = []
    for r in reviews:
        spans = [h["text"] for f in r.get("features", []) for h in f["highlights"]]
        pairs.append((r["text"], spans) if spans else ("", [r["text"]]))
    sentiment.prefetch(pairs)
