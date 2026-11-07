"""답글 흐름: 미답글 목록 → 한국어 답글 초안(+사장님 언어 번역) → 사장님 확정 후 등록(더미).

네이버 답글 기능은 권한이 없어 MockReplyStore로 흉내 낸다. 실서비스에서는 이 저장소만
스마트플레이스 연동으로 바꾼다.
답글 정책: 개인정보(마스킹 이름 포함) 금지, 비방·감정적 표현 금지,
리뷰·별점 수정이나 삭제 요구 금지, 자동 등록 금지(사장님 확정 후에만).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .repo import ReviewRepo
from .sentiment import RuleSentiment, prefetch_reviews


# ---------------------------------------------------------------- 답글 상태 (더미 네이버)
class MockReplyStore:
    """review_id → 등록된 답글. 스냅샷에는 답글 여부가 없으므로 데모용 가정을 둔다:
    방문일이 오늘로부터 seed_days일보다 오래된 리뷰는 '이미 답글 있음', 최근 리뷰는 '답글 없음'."""

    def __init__(
        self, path: str | Path | None = None, today: date | None = None, seed_days: int = 14
    ):
        self.path = Path(path) if path else None
        self.today, self.seed_days = today, seed_days
        self.posted: dict[str, dict] = {}
        if self.path and self.path.exists():
            self.posted = json.loads(self.path.read_text(encoding="utf-8"))

    def recent_replies(self, n: int = 3) -> list[str]:
        items = sorted(self.posted.values(), key=lambda x: x.get("posted_at", ""))
        return [x["reply"] for x in items[-n:]]

    def has_reply(self, review: dict) -> bool:
        if review["review_id"] in self.posted:
            return True
        if review.get("has_owner_reply") is not None:  # 실제 데이터에 값이 있으면 그것을 쓴다
            return bool(review["has_owner_reply"])
        return bool(
            self.today
            and review.get("visit_date")
            and review["visit_date"] < (self.today - timedelta(days=self.seed_days)).isoformat()
        )

    def post(self, review_id: str, reply: str) -> dict:
        rec = {"reply": reply, "posted_at": datetime.now().isoformat(timespec="seconds")}
        self.posted[review_id] = rec
        if self.path:
            self.path.write_text(
                json.dumps(self.posted, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return rec


class ReplyAwareRepo(ReviewRepo):
    """리뷰를 돌려줄 때 has_owner_reply를 답글 저장소 기준으로 채운다 → analyze_reviews의
    unanswered도 맞게 나온다."""

    def __init__(self, repo: ReviewRepo, store: MockReplyStore):
        self.repo, self.store = repo, store

    def get_reviews(self, store_id, start=None, end=None):
        return [
            {**r, "has_owner_reply": self.store.has_reply(r)}
            for r in self.repo.get_reviews(store_id, start, end)
        ]

    def __getattr__(self, name):  # rows, _match_store 등은 원래 저장소로
        return getattr(self.repo, name)


# ---------------------------------------------------------------- 고위험 리뷰 (위생·안전·법적 문제)
# 참고: 브런치 '46-smart_cs_automation' 실습의 HIGH_RISK 경로. 단어만 보면 '고소한 맛', '이상한
# 맛'까지 걸려서
# (스냅샷에서 실제 확인) 단어 경계를 좁히고, 감정 판단이 부정·혼합인 리뷰에만 적용한다 (이중 조건).
HIGH_RISK = re.compile(
    r"이물질|머리카락|털이\s?(나|들어|있)|벌레|바퀴|식중독|배탈|설사|복통|구토|토했|"
    r"알레르기|알러지|화상|다쳤|부상|환불|신고(하|할|했)|고소(하겠|할게|할\s|합니|장)|보건소|위생\s?(불량|상태|문제)|"
    r"곰팡이|(?<!이)상한\s?(음식|재료|맛|거|것)|상했|썩"
)


def risk_of(text: str, sentiment: str) -> dict | None:
    words = sorted({m.group(0) for m in HIGH_RISK.finditer(text)})
    if words and sentiment in ("negative", "mixed"):
        return {"level": "high", "words": words}
    return None


# ---------------------------------------------------------------- 미답글 목록
def _review_sentiment(review: dict, sentiment) -> str:
    spans = [h["text"] for f in review.get("features", []) for h in f["highlights"]]
    labels = sentiment.spans_sentiment(spans, review["text"]) if spans else []
    return sentiment.review_sentiment(review, labels)


def _quote(text: str, limit: int = 60) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def get_unanswered_reviews(
    *,
    store_id: str,
    today: date,
    repo: ReviewRepo,
    sentiment=None,
    priority: str | None = None,
    limit: int = 5,
    **_,
) -> dict:
    sentiment = sentiment or RuleSentiment()
    rows = [r for r in repo.get_reviews(store_id, None, today) if r.get("has_owner_reply") is False]
    prefetch_reviews(sentiment, rows)
    items = []
    for r in rows:
        senti = _review_sentiment(r, sentiment)
        items.append(
            {
                "review_id": r["review_id"],
                "visit_date": r["visit_date"],
                "rating": r.get("rating"),
                "sentiment": senti,
                "risk": risk_of(r["text"], senti),
                "quote": _quote(r["text"]),
            }
        )
    if priority == "negative_first":
        rank = {"negative": 1, "mixed": 2, "neutral": 3, "positive": 4}
        # 안정 정렬: 고위험 → 부정 → 혼합 …, 같은 순위 안에서는 최신순 유지
        items.sort(key=lambda x: 0 if x["risk"] else rank.get(x["sentiment"], 3))
    return {
        "total": len(items),
        "priority": priority or "recent",
        "reviews": items[:limit],
        "negative_count": sum(i["sentiment"] in ("negative", "mixed") for i in items),
        "high_risk_count": sum(1 for i in items if i["risk"]),
    }


# ---------------------------------------------------------------- 답글 검사 (정책 가드레일)
_PHONE = re.compile(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}")
_MASKED_NAME = re.compile(r"[가-힣]\*+[가-힣]?|\*+[가-힣]{1,2}님?|[가-힣]{1,2}\*+")
_AGGRESSIVE = re.compile(
    r"거짓말|어이(가)?\s?없|황당|억지|진상|고소|신고하겠|악플|블랙\s?컨슈머|모욕|비방"
)
_BLAME = re.compile(r"그럴\s?리가|허위|악성\s?리뷰|오해하신|오해가\s?있|사실과\s?다르|과장")
_COMPENSATION = re.compile(
    r"환불(해|을)\s?드리|무료로|쿠폰(을)?\s?드리|서비스로\s?드리|보상(해)?\s?드리|할인(해)?\s?드리"
)
_PLACEHOLDER = re.compile(r"\[[^\]]{1,15}\]|OO|○○|XX")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_RRN = re.compile(r"\d{6}[-\s]?[1-4]\d{6}")
_CARD = re.compile(r"\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}")
NAVER_LIMIT = 300  # 네이버 플레이스 답글 권장 길이 (실습 기준)
_REVIEW_EDIT = re.compile(
    r"(리뷰|후기|별점|평점).{0,12}(수정|삭제|지워|내려|올려\s?주|바꿔)|"
    r"(다시|재).{0,10}(평가|리뷰|별점|후기)|평가해\s?주"
)  # '다시 평가해 주실 수 있을까요?'(실제 발생)
# 사실 확인 전에 원인을 추정·단정하는 표현. '원재료가 잘못 사용되었거나 보관 과정에서 문제가
# 발생했을 가능성이 큽니다'(2026-11-07 실제 발생)
_CAUSE = re.compile(
    r"가능성이\s?(크|높|있)|(때문인|때문일|탓인|탓일)\s?(것|수|듯)|원인은|"
    r"잘못\s?(사용|보관|조리|들어가|섞|만들)|(실수|착오)로\s|문제가\s?(있었던|생긴|발생한)\s?것\s?같"
)


# 사장님이 지시에서 약속을 요청했는지 (원어 그대로: 한·영·러·베·중)
PROMISE_INTENT = re.compile(
    r"서비스|쿠폰|할인|환불|무료|증정|사은품|보상|"
    r"free|discount|coupon|refund|on the house|complimentary|"
    r"бесплатн|скидк|купон|возврат|в подарок|"
    r"tặng|miễn phí|giảm giá|hoàn tiền|phiếu|"
    r"免费|赠送|优惠|折扣|退款|送",
    re.I,
)
# 답글 속 약속 문장 (체크리스트 맨 위에 ⚠로 보여 준다)
_PROMISE = re.compile(
    r"(서비스|증정|무료|쿠폰|할인|환불|보상|사은품|음료|디저트).{0,25}(드리|드릴|해\s?드|챙겨)"
)
_APOLOGY = re.compile(r"죄송|사과\s?(드|의)|송구|면목")
# 제안 구조 C-3(허용할 약속 범위)이 정해지기 전 기본값: 사장님이 직접 지시한 약속만 허용하고,
# 게시 전에 ⚠로 확인받는다. 지시하지 않은 약속은 지금처럼 등록을 막는다.
OWNER_PROMISES_ALLOWED = True


def _sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?。])\s+", t.strip()) if s.strip()]


def build_checklist(reply: str, glossary: dict[str, str] | None = None) -> dict:
    """답글에서 사장님이 책임질 사실만 뽑는다: 사과 여부, 약속 문장, 언급한 메뉴(용어집 표기)."""
    promises = [s for s in _sentences(reply) if _PROMISE.search(s) or _COMPENSATION.search(s)]
    menus = [(ko, native) for ko, native in (glossary or {}).items() if ko in reply]
    return {"apology": bool(_APOLOGY.search(reply)), "promises": promises, "menus": menus}


def _first_sentence(t: str) -> str:
    return re.split(r"(?<=[.!?])\s", t.strip(), maxsplit=1)[0]


def check_reply(
    reply: str,
    recent: list[str] | None = None,
    limit: int = NAVER_LIMIT,
    owner_promise: bool = False,
) -> dict:
    """→ {"passed": bool, "issues": [...], "warnings": [...]}. issues가 있으면 등록할 수 없고,
    warnings는 알리기만 한다."""
    issues, warnings = [], []
    if _PHONE.search(reply) or _EMAIL.search(reply) or _RRN.search(reply) or _CARD.search(reply):
        issues.append("전화번호 등 개인정보")
    if _BLAME.search(reply):
        issues.append("손님 탓 표현")
    if _COMPENSATION.search(reply) or _PROMISE.search(reply):
        if owner_promise and OWNER_PROMISES_ALLOWED:
            warnings.append("약속 포함 (사장님 지시, 게시 전 확인)")
        else:
            issues.append("보상·환불 약속 (실제로 줄 수 있는지 확인)")
    if _PLACEHOLDER.search(reply):
        issues.append("채우지 않은 자리표시")
    if len(reply) > limit * 1.3:
        warnings.append(f"길이 {len(reply)}자 (네이버 권장 {limit}자)")
    if recent and _first_sentence(reply) in {_first_sentence(x) for x in recent if x}:
        warnings.append("최근 답글과 같은 첫 문장")
    if _MASKED_NAME.search(reply):
        issues.append("리뷰어 이름(마스킹 포함)")
    if _AGGRESSIVE.search(reply):
        issues.append("비방·감정적 표현")
    if _REVIEW_EDIT.search(reply):
        issues.append("리뷰·별점 수정이나 삭제 요구")
    if _CAUSE.search(reply):
        issues.append("확인 전 원인 추정·단정")
    if not reply.strip():
        issues.append("빈 답글")
    return {"passed": not issues, "issues": issues, "warnings": warnings}


# ---------------------------------------------------------------- 답글 초안
_TEMPLATE = {
    "positive": (
        "소중한 리뷰 남겨 주셔서 감사합니다! 맛있게 드셨다니 저희도 정말 기쁩니다. 다음에도 "
        "만족하실 수 있도록 정성껏 준비하겠습니다."
    ),
    "negative": (
        "불편을 드려 정말 죄송합니다. 말씀해 주신 부분은 꼼꼼히 확인해서 개선하겠습니다. 다음 방문 "
        "때는 더 나은 모습으로 맞이하겠습니다."
    ),
    "mixed": (
        "방문해 주셔서 감사합니다. 좋게 봐주신 점은 계속 지키고, 아쉬우셨던 부분은 바로 "
        "개선하겠습니다."
    ),
    "neutral": "방문해 주셔서 감사합니다. 다음에도 편하게 찾아 주세요.",
    "escalate": (
        "불편을 드려 진심으로 죄송합니다. 말씀해 주신 내용은 지금 사실관계를 확인하고 있습니다. "
        "번거로우시겠지만 매장으로 직접 연락 주시면 자세히 확인해 안내드리겠습니다."
    ),
}


def draft_review_reply(
    *,
    store_id: str,
    today: date,
    repo: ReviewRepo,
    review_ids: list[str] | None,
    sentiment=None,
    writer=None,
    owner_lang: str = "ko",
    glossary: dict | None = None,
    edit_request: str | None = None,
    previous: dict | None = None,
    profile: dict | None = None,
    recent: list[str] | None = None,
    instruction: str | None = None,
    owner_promise: bool = False,
    **_,
) -> dict:
    """review_ids(시스템 값)의 리뷰마다 한국어 답글 초안을 만든다. edit_request가 있으면 previous
    초안을 고친다.
    writer: HCXReviewLLM (없으면 감정별 템플릿 — 테스트·오프라인용)."""
    if not review_ids:
        return {
            "error": "no_target_review",
            "message": "어느 리뷰에 답글을 쓸지 모릅니다. 먼저 미답글 목록을 보여주세요.",
        }
    sentiment = sentiment or RuleSentiment()
    # 사장님이 지시(원어)에서 약속을 요청했으면 그 약속은 허용하고 ⚠로 확인받는다
    owner_promise = owner_promise or bool(
        PROMISE_INTENT.search(f"{instruction or ''} {edit_request or ''}")
    )
    by_id = {r["review_id"]: r for r in repo.get_reviews(store_id, None, today)}
    drafts = []
    for rid in review_ids:
        r = by_id.get(rid)
        if not r:
            drafts.append({"review_id": rid, "error": "review_not_found"})
            continue
        senti = _review_sentiment(r, sentiment)
        risk = risk_of(r["text"], senti)
        route = "escalate" if risk else senti  # 고위험은 짧은 사과 + 사실 확인 + 직접 연락 경로
        prev = (previous or {}).get(rid)
        recent_all = list(recent or []) + [
            d["reply_ko"] for d in drafts if "reply_ko" in d
        ]  # 같은 묶음 안 반복도 방지
        if writer is not None:
            kw = dict(route=route, profile=profile, recent=recent_all[-3:], glossary=glossary)
            if instruction and not edit_request:
                kw["instruction"] = instruction
            reply = writer.write_reply(
                r["text"], senti, edit_request=edit_request, previous=prev, **kw
            )
            chk = check_reply(reply, recent_all, owner_promise=owner_promise)
            fixable = chk["issues"] + [w for w in chk["warnings"] if not w.startswith("약속 포함")]
            if fixable:  # 한 번 고쳐 쓰게 한다
                reply = writer.write_reply(
                    r["text"],
                    senti,
                    previous=reply,
                    edit_request=(f"다음 문제를 고치세요: {', '.join(fixable)}"),
                    **kw,
                )
        else:
            reply = prev if (prev and not edit_request) else _TEMPLATE[route]
        chk = check_reply(reply, recent_all, owner_promise=owner_promise)
        checklist = build_checklist(reply, glossary)
        translated, promises_tr = None, []
        if owner_lang != "ko" and writer is not None:  # 전체 번역 + 약속 문장 번역을 한 번에
            out = writer.translate([reply, *checklist["promises"]], owner_lang, glossary)
            translated, promises_tr = out[0], list(out[1:])
        checklist["promises_translated"] = promises_tr
        drafts.append(
            {
                "draft_id": f"d-{rid}",
                "review_id": rid,
                "review_quote": _quote(r["text"], 80),
                "sentiment": senti,
                "route": route,
                "risk": risk,
                "owner_check": bool(risk),
                "reply_ko": reply,
                "reply_translated": translated,
                "checks": chk,
                "checklist": checklist,
                "owner_promise": owner_promise,
                "revised": bool(edit_request),
            }
        )
    return {
        "drafts": drafts,
        "posted": False,
        "note": "초안입니다. 사장님이 '이대로 올려줘'처럼 확정해야 등록됩니다.",
    }


# ---------------------------------------------------------------- 등록 (더미)
CONFIRM = re.compile(
    r"올려|등록|게시|확정|이대로|그대로|좋아\s?(요)?\s?$|post|publish|submit|go ahead|"
    r"опубликуй|размести|отправь|подтвер",
    re.I,
)


def post_review_reply(
    *, drafts: dict | None, store: MockReplyStore, utterance: str | None, **_
) -> dict:
    """drafts(시스템 값: 직전 초안들)를 등록한다. 사장님 문장에 확정 표현이 없으면 등록하지 않는다
    (자동 등록 금지)."""
    if not drafts:
        return {"error": "no_draft", "message": "등록할 초안이 없습니다."}
    if not utterance or not CONFIRM.search(utterance):
        return {
            "error": "not_confirmed",
            "message": "사장님이 등록을 확정하지 않았습니다. 확정할지 물어보세요.",
        }
    out = []
    for d in drafts.values():
        if not d.get("checks", {}).get("passed"):
            out.append(
                {
                    "review_id": d["review_id"],
                    "posted": False,
                    "issues": d.get("checks", {}).get("issues"),
                }
            )
            continue
        rec = store.post(d["review_id"], d["reply_ko"])
        out.append(
            {
                "review_id": d["review_id"],
                "posted": True,
                "posted_at": rec["posted_at"],
                "dummy": True,
            }
        )
    return {"results": out, "posted_count": sum(o["posted"] for o in out)}
