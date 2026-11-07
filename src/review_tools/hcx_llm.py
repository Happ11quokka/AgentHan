"""리뷰 도구용 HCX 호출: 구간 감정 판단, 요약(인사이트), topic 해석 보조.

분류기 HCX 백엔드와 같은 방식: 모델별 요청 옵션(request_options), 호출 간격, 응답 캐시, JSON 검증 +
1회 재시도.
CLOVA Studio에 접속할 수 있는 환경과 CLOVASTUDIO_API_KEY가 필요하다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from naver_review_clf.backends.hcx import DEFAULT_BASE_URL, _extract_json, request_options
from naver_review_clf.schema import FEATURE_GUIDE

from .sentiment import RuleSentiment

LABELS = ["positive", "negative", "neutral"]

SENTIMENT_SYSTEM = (
    "당신은 식당·카페 리뷰의 감정 판단기입니다. 리뷰 전문과 번호가 붙은 구절 목록을 받으면, 각 "
    "구절이 리뷰 맥락에서 "
    "가게에 대해 긍정(positive)인지 부정(negative)인지, 평가가 없으면 중립(neutral)인지 판단하세요."
    "\n"
    "규칙\n"
    "1. 구절이 문장의 일부이면 그 문장이 뜻하는 평가로 판단합니다 (예: 전문이 '주차가능이라고해서 "
    "왔는데 주차권은 사서 쓰라네요'이면 "
    "구절 '주차가능이라고해서'는 negative). 단, 다른 문장의 평가를 옮겨오지 마세요.\n"
    "2. 부정 단어가 없어도 불만이면 negative입니다 (예: '바퀴벌레 기어다녀요', '직원이 우기시네요',"
    " '예전 같지 않아요').\n"
    "3. 디저트의 '많이 안 달아요', '느끼하지 않아요'는 칭찬이므로 positive입니다.\n"
    "4. '품절이었어요'처럼 아쉬움을 말하면 negative, 방문 사실만 말하면 neutral입니다.\n"
    '5. 반드시 다음 JSON으로만 답하세요: {"items": [{"i": 번호, "label": "positive|negative|'
    'neutral"}]}'
)

# 리뷰 전문은 500자까지만 맥락으로 넣는다 (참고: 47-area_review_analysis 실습의 prepare 단계)
MAX_CONTEXT = 500

# 구절 번호는 묶음 전체에 이어서 붙인다. 리뷰마다 0부터 세게 했더니 HCX가 번호를 이어서 매겨
# 절반 가까운 리뷰가 어긋났다 (2026-11-07 실측: 묶음 판단 101건, 단건 재판단 91건)
BATCH_SYSTEM = (
    "당신은 식당·카페 리뷰의 감정 판단기입니다. 여러 리뷰를 받습니다. 각 리뷰는 [R번호] 전문과 그 "
    "아래 번호가 붙은 구절로 "
    "되어 있고, 구절 번호는 묶음 전체에서 이어집니다. 각 구절이 자기 리뷰의 맥락에서 가게에 대해 "
    "긍정(positive)인지 "
    "부정(negative)인지, 평가가 없으면 중립(neutral)인지 판단하세요.\n"
    "규칙\n"
    "1. 구절이 문장의 일부이면 그 문장이 뜻하는 평가로 판단합니다. 다른 리뷰의 내용과 섞지 마세요."
    "\n"
    "2. 부정 단어가 없어도 불만이면 negative입니다 (예: '바퀴벌레 기어다녀요', '직원이 우기시네요',"
    " '예전 같지 않아요').\n"
    "3. 디저트의 '많이 안 달아요', '느끼하지 않아요'는 칭찬이므로 positive입니다.\n"
    "4. '품절이었어요'처럼 아쉬움을 말하면 negative, 방문 사실만 말하면 neutral입니다.\n"
    "5. 모든 구절 번호에 하나씩 답하세요. label은 positive, negative, neutral 중 하나만 씁니다.\n"
    '반드시 다음 JSON으로만 답하세요: {"items": [{"i": 구절번호, "label": "positive|negative|'
    'neutral"}]}'
)

REPORT_IDEAS_SYSTEM = (
    "당신은 가게 사장님을 돕는 리뷰 분석가입니다. 정기 보고용 집계(JSON)를 보고 두 가지를 쓰세요.\n"
    "improvements: 불만 항목을 근거로 이번 주에 사장님이 할 수 있는 구체적인 개선 행동 최대 3개 "
    "(각 40자 이내)\n"
    "marketing: 칭찬 항목을 근거로 새소식·리뷰 요청·메뉴 소개에 활용할 홍보 포인트 최대 2개 (각 "
    "40자 이내)\n"
    "규칙: 집계에 있는 항목과 대표 리뷰만 근거로 쓰고, 숫자나 사실을 지어내지 마세요. 불만이 "
    "없으면 improvements는 빈 배열.\n"
    '반드시 다음 JSON으로만 답하세요: {"improvements": ["..."], "marketing": ["..."]}'
)

INSIGHT_SYSTEM = (
    "당신은 가게 사장님을 돕는 리뷰 분석가입니다. 리뷰 집계 결과(JSON)를 보고 사장님이 바로 이해할 "
    "핵심 인사이트를 "
    "최대 3개, 각각 한 문장(40자 이내)으로 쓰세요.\n"
    "규칙: (1) 숫자는 집계에 있는 것만 씁니다. (2) '조건에_맞는_리뷰'는 조건으로 걸러진 수입니다. "
    "전체 손님 중 비율을 말할 때는 "
    "'기간_전체_리뷰'를 분모로 쓰세요. 초점이 '불만만'이면 걸러진 리뷰가 모두 불만인 것은 "
    "당연하므로 '대부분이 부정적'이라고 쓰지 마세요. "
    "(3) 불만은 '불만_많은_항목'에 있는 항목만 불만이라고 쓰세요. 칭찬이 더 많은 항목을 불만이라고 "
    "쓰지 마세요. "
    "(4) 불만이 있으면 가장 많은 불만과 개선 방향을 포함하세요. (5) 리뷰_감정의 mixed는 칭찬과 "
    "불만이 섞인 리뷰입니다.\n"
    '반드시 다음 JSON으로만 답하세요: {"insights": ["...", "..."]}'
)


KO_LABELS = {"긍정": "positive", "부정": "negative", "중립": "neutral"}


class _Item(BaseModel):
    i: int
    label: str

    @field_validator("label")
    @classmethod
    def _norm(cls, v):  # HCX가 가끔 한국어 라벨로 답한다 (2026-11-07 실측)
        v = v.strip().lower()
        return KO_LABELS.get(v, v)


def _repair_json(raw: str) -> str:
    """흔한 형식 오류 보정: 따옴표 없는 키 {i:8} → {"i":8} (2026-11-07 실측)."""
    return re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', raw)


class _Labels(BaseModel):
    items: list[_Item]


class _BItem(BaseModel):
    r: int
    i: int
    label: str

    @field_validator("label")
    @classmethod
    def _norm(cls, v):
        v = v.strip().lower()
        return KO_LABELS.get(v, v)


class _BLabels(BaseModel):
    items: list[_BItem]


class _ReportIdeas(BaseModel):
    improvements: list[str] = []
    marketing: list[str] = []


class _Insights(BaseModel):
    insights: list[str]


class _Reply(BaseModel):
    reply: str


class _Translations(BaseModel):
    t: list[str]


REPLY_SYSTEM = (
    "당신은 식당·카페 사장님 대신 네이버 플레이스 리뷰에 답글을 쓰는 비서입니다. 한국어로 2~4문장, "
    "정중하고 따뜻하게 쓰세요.\n"
    "규칙: (1) 긍정 리뷰는 손님이 언급한 구체적인 점을 짚어 감사하고 재방문을 자연스럽게 권합니다. "
    "(2) 부정·혼합 리뷰는 변명 없이 사과하고, 지적한 점을 어떻게 개선할지 구체적으로 말합니다. "
    "(3) 손님 이름·전화번호 등 개인정보를 쓰지 않습니다 (홍*동처럼 가린 이름도 금지). "
    "(4) 손님을 탓하거나 비꼬지 않고 감정적 표현을 쓰지 않습니다. "
    "(5) 리뷰나 별점을 수정·삭제하거나 다시 평가해 달라고 요청하지 않습니다. 보상·할인을 약속하지 "
    "않습니다. "
    "서명('OO 드림')이나 인사말 머리('안녕하세요, 고객님')는 넣지 않습니다. "
    "(6) 리뷰에 없는 사실을 지어내지 않습니다.\n"
    '반드시 다음 JSON으로만 답하세요: {"reply": "답글"}'
)
REPLY_SCHEMA = None  # 아래에서 정의

# 경로별 작성 방법 (참고: 46-smart_cs_automation 실습의 ROUTE_GUIDE)
ROUTE_GUIDE = {
    "positive": "감사 → 손님이 칭찬한 구체적인 점을 짚기 → 대표 메뉴 안에서 자연스럽게 재방문 권유",
    "negative": (
        "공감과 사과 → 불편한 점을 구체적으로 짚기(변명 금지) → 어떻게 개선할지 → 다시 기회를 "
        "청하기"
    ),
    "mixed": "좋게 봐주신 점에 감사 → 아쉬운 점 사과와 개선 → 재방문 권유",
    "neutral": "방문 감사 → 짧은 재방문 권유",
    "escalate": (
        "위생·안전 관련 리뷰입니다. 짧게 진심으로 사과 → 지금 사실관계를 확인하고 있다고 안내 → "
        "매장으로 직접 연락해 "
        "달라고 정중히 요청. 원인을 단정하지 말고(예: '저희 잘못입니다', '그럴 리 없습니다' 모두 "
        "금지), 보상·환불을 약속하지 말고, "
        "전화번호나 [연락처] 같은 칸을 쓰지 마세요."
    ),
}


class _Topic(BaseModel):
    menus: list[str] = []
    aspects: list[str] = []


def _schema(name, props, required):
    return {"name": name, "schema": {"type": "object", "properties": props, "required": required}}


SENTIMENT_SCHEMA = _schema(
    "labels",
    {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "label": {"type": "string", "enum": LABELS},
                },
                "required": ["i", "label"],
            },
        }
    },
    ["items"],
)
REPLY_SCHEMA = _schema("reply", {"reply": {"type": "string"}}, ["reply"])
TRANS_SCHEMA = _schema("t", {"t": {"type": "array", "items": {"type": "string"}}}, ["t"])
INSIGHT_SCHEMA = _schema(
    "insights",
    {"insights": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
    ["insights"],
)


class HCXReviewLLM:
    def __init__(
        self,
        model: str | None = None,
        client=None,
        cache_path: str | Path | None = None,
        min_interval: float | None = None,
    ):
        self.model = model or os.getenv("HCX_MODEL", "HCX-DASH-002")
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=os.environ["CLOVASTUDIO_API_KEY"],
                base_url=os.getenv("CLOVASTUDIO_BASE_URL", DEFAULT_BASE_URL),
                max_retries=int(os.getenv("HCX_MAX_RETRIES", "8")),
            )
        self.client = client
        self.min_interval = (
            float(os.getenv("HCX_MIN_INTERVAL", "1.0")) if min_interval is None else min_interval
        )
        self._last = 0.0
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[str, dict] = {}
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                self.cache[rec["key"]] = rec["value"]

    # ---------- 공통 호출 ----------
    def _ask(self, system: str, user: str, model_cls, schema: dict, max_tokens: int = 512):
        key = hashlib.sha1(f"{self.model}\n{system}\n{user}".encode()).hexdigest()
        if key in self.cache:
            return model_cls.model_validate(self.cache[key])
        opts = request_options(self.model, max_tokens)
        if (
            "response_format" in opts
        ):  # HCX-007: 이 호출의 스키마로 바꿔 끼운다 (스키마가 없으면 프롬프트만)
            if schema:
                opts["response_format"] = {"type": "json_schema", "json_schema": schema}
            else:
                opts.pop("response_format")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        err = None
        for _ in range(2):  # JSON이 틀리면 1회 재시도
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            raw = (
                self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0, **opts
                )
                .choices[0]
                .message.content
                or ""
            )
            try:
                body = _extract_json(raw)
                try:
                    val = model_cls.model_validate_json(body)
                except ValidationError:
                    val = model_cls.model_validate_json(_repair_json(body))
                break
            except (ValidationError, ValueError) as e:
                err = e
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": "형식이 틀렸습니다. 지정한 JSON만 다시 출력하세요.",
                    },
                ]
        else:
            raise RuntimeError(f"HCX 응답 파싱 실패: {err}")
        if self.cache_path:
            self.cache[key] = val.model_dump()
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps({"key": key, "value": self.cache[key]}, ensure_ascii=False) + "\n"
                )
        return val

    # ---------- 기능 ----------
    def sentiments(self, texts: list[str], context: str | None = None) -> list[str]:
        """구절 목록(+리뷰 전문) → 같은 순서의 positive/negative/neutral. 빠진 번호나 모르는 라벨은
        neutral."""
        if not texts:
            return []
        listing = "\n".join(f"[{i}] {' '.join(t.split())}" for i, t in enumerate(texts))
        if context:
            listing = (
                f"리뷰 전문: {' '.join(context.split())[:MAX_CONTEXT]}\n\n판단할 구절:\n{listing}"
            )
        ans = self._ask(SENTIMENT_SYSTEM, listing, _Labels, SENTIMENT_SCHEMA)
        out = ["neutral"] * len(texts)
        for it in ans.items:
            if 0 <= it.i < len(texts) and it.label in LABELS:
                out[it.i] = it.label
        return out

    def write_reply(
        self,
        review_text: str,
        sentiment: str,
        edit_request: str | None = None,
        previous: str | None = None,
        route: str | None = None,
        profile: dict | None = None,
        recent: list[str] | None = None,
    ) -> str:
        """한국어 답글. edit_request(사장님 언어 그대로 가능)가 있으면 previous를 그 요청대로
        고친다.
        profile: 업체명·업종·대표 메뉴 / recent: 최근 답글(같은 첫 문장·표현 반복 금지)."""
        user = ""
        if profile:
            user += (
                f"가게: {profile.get('name', '')} ({profile.get('category', '')}), "
                f"대표 메뉴: {', '.join(profile.get('menus', [])[:5]) or '정보 없음'}\n"
            )
        user += "플랫폼: 네이버 플레이스, 친근한 존댓말, 300자 이내\n"
        user += f"작성 방법: {ROUTE_GUIDE.get(route or sentiment, ROUTE_GUIDE['neutral'])}\n"
        if recent:
            user += (
                "최근 답글 (첫 문장과 표현을 반복하지 마세요):\n"
                + "\n".join(f"- {x}" for x in recent[-3:])
                + "\n"
            )
        user += f"리뷰 감정: {sentiment}\n리뷰 전문: {' '.join(review_text.split())}"
        if previous:
            user += f"\n\n지금 초안: {previous}"
        if edit_request:
            user += (
                f"\n\n사장님 수정 요청(다른 언어일 수 있음): {edit_request}\n"
                "요청을 반영해 한국어 답글을 다시 쓰세요. 요청이 규칙(개인정보·비방·리뷰 수정 요구·"
                "보상 약속)에 어긋나면 그 부분은 반영하지 마세요."
            )
        return self._ask(REPLY_SYSTEM, user, _Reply, REPLY_SCHEMA, max_tokens=400).reply.strip()

    def translate(
        self, texts: list[str], lang: str, glossary: dict | None = None
    ) -> list[str | None]:
        """한국어 → 사장님 언어. 원문에 없는 내용을 더하지 않게 하고, 메뉴명은 용어집대로."""
        names = {"ru": "Russian", "en": "English", "ja": "Japanese", "zh": "Chinese"}.get(
            lang, lang
        )
        gloss = (
            ("Use these menu names: " + "; ".join(f"{k} = {v}" for k, v in glossary.items()) + ". ")
            if glossary
            else ""
        )
        system = (
            f"Translate each Korean text into {names}, faithfully and naturally. {gloss}"
            "Do not add, remove or guess anything. "
            'Answer only JSON: {"t": ["translation 0", "translation 1", ...]}'
        )
        try:
            ans = self._ask(
                system,
                "\n".join(f"[{i}] {t}" for i, t in enumerate(texts)),
                _Translations,
                TRANS_SCHEMA,
                800,
            )
            out = [x.strip() or None for x in ans.t]
            return out[: len(texts)] + [None] * (len(texts) - len(out))
        except Exception:
            return [None] * len(texts)

    def sentiments_batch(self, pairs: list[tuple[str, list[str]]]) -> list[list[str] | None]:
        """여러 리뷰를 한 번에 판단. pairs: [(리뷰 전문, [구절...])]. 답이 빠진 리뷰는 None (호출한
        쪽이 단건으로 다시 판단)."""
        blocks, owner = [], []  # owner[전체 번호] = (리뷰 번호, 리뷰 안 번호)
        for ri, (ctx, spans) in enumerate(pairs):
            c = " ".join((ctx or "").split())[:MAX_CONTEXT]
            lines = []
            for si, t in enumerate(spans):
                lines.append(f"  [{len(owner)}] {' '.join(t.split())}")
                owner.append((ri, si))
            blocks.append(f"[R{ri}] {c}\n" + "\n".join(lines))
        try:
            ans = self._ask(
                BATCH_SYSTEM, "\n\n".join(blocks), _Labels, SENTIMENT_SCHEMA, max_tokens=1500
            )
        except RuntimeError:
            return [None] * len(pairs)
        got: dict[tuple[int, int], str] = {}
        for it in ans.items:
            if 0 <= it.i < len(owner) and it.label in LABELS:
                got[owner[it.i]] = it.label
        out = []
        for ri, (_, spans) in enumerate(pairs):
            labels = [got.get((ri, si)) for si in range(len(spans))]
            out.append(
                None if any(x is None for x in labels) else labels
            )  # 하나라도 빠지면 그 리뷰는 다시
        return out

    def report_ideas(self, summary: dict) -> dict:
        ans = self._ask(
            REPORT_IDEAS_SYSTEM,
            json.dumps(summary, ensure_ascii=False),
            _ReportIdeas,
            None,
            max_tokens=500,
        )
        return {
            "improvements": [x.strip() for x in ans.improvements if x.strip()][:3],
            "marketing": [x.strip() for x in ans.marketing if x.strip()][:2],
        }

    def insights(self, summary: dict) -> list[str]:
        ans = self._ask(
            INSIGHT_SYSTEM, json.dumps(summary, ensure_ascii=False), _Insights, INSIGHT_SCHEMA
        )
        return [s.strip() for s in ans.insights if s.strip()][:3]

    def resolve_topic(self, topic: str, menus: list[str], aspects: list[str]) -> dict:
        guide = "\n".join(f"- {a}: {FEATURE_GUIDE.get(a, '')}" for a in aspects)
        system = (
            "사장님이 말한 표현이 가게의 어느 메뉴, 어느 리뷰 특징을 가리키는지 고르세요. 목록에 "
            "없으면 빈 배열로 두세요. "
            f"메뉴 목록: {menus}\n특징 목록:\n{guide}\n"
            '반드시 다음 JSON으로만 답하세요: {"menus": [...], "aspects": [...]}'
        )
        schema = _schema(
            "topic",
            {
                "menus": {"type": "array", "items": {"type": "string"}},
                "aspects": {"type": "array", "items": {"type": "string"}},
            },
            ["menus", "aspects"],
        )
        ans = self._ask(system, topic, _Topic, schema)
        return {
            "menus": [m for m in ans.menus if m in menus],
            "aspects": [a for a in ans.aspects if a in aspects],
        }


class HCXSentiment(RuleSentiment):
    """RuleSentiment와 같은 인터페이스. 구간 판단만 HCX로 하고 리뷰 단위 합산 규칙은 그대로 쓴다."""

    name = "hcx"

    def __init__(self, llm: HCXReviewLLM, batch_size: int | None = None):
        self.llm = llm
        self.fallback = RuleSentiment()
        self.failures = 0  # HCX 응답을 끝내 못 읽어 규칙으로 대신 판단한 리뷰 수
        self.batch_size = int(os.getenv("HCX_BATCH", "10")) if batch_size is None else batch_size
        self._pre: dict[str, list[str]] = {}
        self.stats = {"batch_calls": 0, "batched_reviews": 0, "batch_misses": 0}

    @staticmethod
    def _key(texts, context):
        return hashlib.sha1(
            json.dumps([context or "", texts], ensure_ascii=False).encode()
        ).hexdigest()

    def prefetch(self, pairs: list[tuple[str, list[str]]]) -> None:
        """리뷰 여러 건을 batch_size씩 묶어 한 번에 판단해 둔다 (호출 수 약 1/batch_size).
        참고: 47-area_review_analysis 실습의 analyze_batch. batch_size<=1이면 아무것도 하지 않는다.
        """
        todo = [(c, sp) for c, sp in pairs if sp and self._key(sp, c) not in self._pre]
        if self.batch_size <= 1 or not todo:
            return
        missed = self._run_batches(todo, self.batch_size)
        if (
            len(missed) >= 3
        ):  # 빠진 리뷰가 많으면 그것끼리 작은 묶음으로 한 번 더 (2026-11-07: 단건 재판단 53건)
            missed = self._run_batches(missed, max(2, self.batch_size // 2))
        self.stats["batch_misses"] += len(missed)  # 남은 것은 spans_sentiment가 단건으로 판단

    def _run_batches(self, items, size):
        missed = []
        for k in range(0, len(items), size):
            chunk = items[k : k + size]
            res = self.llm.sentiments_batch(chunk)
            self.stats["batch_calls"] += 1
            for (c, sp), labels in zip(chunk, res, strict=False):
                if labels is None:
                    missed.append((c, sp))
                else:
                    self._pre[self._key(sp, c)] = labels
                    self.stats["batched_reviews"] += 1
        return missed

    def span_sentiment(self, text: str) -> str:
        return self.spans_sentiment([text])[0]

    def spans_sentiment(
        self, texts: list[str], context: str | None = None
    ) -> list[str]:  # 리뷰 한 건의 구간을 한 번에
        hit = self._pre.get(self._key(texts, context))
        if hit is not None:
            return hit
        try:
            return self.llm.sentiments(texts, context)
        except (
            RuntimeError
        ):  # 형식 오류가 재시도 후에도 남으면 그 리뷰만 규칙으로 판단하고 계속 진행
            self.failures += 1
            return self.fallback.spans_sentiment(texts, context)
