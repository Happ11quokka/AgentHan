"""의도 분류 라우터 (실험 버전, `ReviewToolbox(router="intent")`).

규칙 라우터(`ReviewToolbox.route`)는 표현을 정규식으로 알아듣기 때문에 새 표현이 나올 때마다
단어를 더해야 하고 규칙끼리 부딪혔다 (벤치마크 holdout 83~94%, docs/review-clf.md).
이 버전은 HCX에게 **의도만** 고르게 하고, 의도 → 도구는 시스템이 정한다.

1) classify(): HCX-007 + JSON 스키마(responseFormat)로 의도 1개와 슬롯(답글 대상)을 받는다
2) plan(): 의도 → 실행 계획
   - 답글·보고 흐름(목록·작성·수정·등록·지금 보고서)은 시스템이 바로 실행
   - 분석·보고 설정은 그 도구 하나만 HCX에게 보여 주고 지정(toolChoice)해 인자만 채우게 한다
   - 관계없음은 도구 없이 답한다 (재요청·시스템 직접 실행 없음)
3) 안전장치는 규칙 그대로: 등록은 post_review_reply가 확정 표현·초안 유무를 다시 확인하고,
   거짓 '올렸습니다'·미래 날짜·기간 보정은 run_turn·dispatch가 맡는다
"""

from __future__ import annotations

import json
import re

from .clova import ClovaError, model_options
from .report_parse import parse_report_request
from .specs import ANALYZE_REVIEWS, SET_REVIEW_REPORT

INTENT_MODEL = "HCX-007"  # responseFormat(JSON 스키마)은 HCX-007만 된다 (2026-11-08 실측)

INTENTS = {
    "list_unanswered": "답글을 아직 안 단 리뷰가 있는지, 몇 개인지, 목록을 보고 싶다",
    "draft_reply": "리뷰에 답글을 새로 써 달라",
    "edit_draft": "방금 보여 준 답글 초안을 고쳐 달라 (내용 추가·빼기·말투·길이·다시 쓰기)",
    "post_reply": "방금 보여 준 답글 초안을 그대로 올려 달라 (확정)",
    "hold": "초안을 아직 올리지 말고 기다려 달라, 나중에 하겠다, 생각해 보겠다",
    "analyze": (
        "리뷰·손님 반응·평가를 보고 싶다 (기간, 불만·칭찬, 메뉴, 맛·가격·서비스 같은 항목 포함)"
    ),
    "report_now": "리뷰 보고서를 지금 바로 받아 보고 싶다",
    "report_settings": (
        "리뷰 보고를 정기적으로 받는 설정 (주기·요일·시간·알림·끄기·주기 추천, 제안 수락)"
    ),
    "other": "리뷰와 관계없는 말 (인사, 감사, 날씨, 매출, 메뉴 등록, 쿠폰, 직원, 영업시간 등)",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "target": {
            "type": "string",
            "enum": ["first", "negative", "all_listed", "none"],
            "description": "답글을 쓸 대상. 안 좋은·불만·위험한 리뷰면 negative, 전부면 all_listed",
        },
        "negative_first": {
            "type": "boolean",
            "description": "미답글 목록에서 안 좋은·급한 리뷰를 먼저 보고 싶으면 true",
        },
    },
    "required": ["intent", "target", "negative_first"],
}


def _prompt(state: str) -> str:
    lines = "\n".join(f"- {k}: {v}" for k, v in INTENTS.items())
    return (
        "가게 사장님이 리뷰 비서에게 한 말의 의도를 하나만 고르세요.\n"
        f"{lines}\n"
        "판단 기준:\n"
        "- '답글 안 단 리뷰 몇 개?', '답글 달아야 할 리뷰 있어?'처럼 묻는 말은 list_unanswered\n"
        "- 리뷰와 관련된 단어(배달·직원·가격)가 있어도 리뷰 내용을 묻는 게 아니면 other\n"
        "- 초안이 없으면 edit_draft·hold가 될 수 없다. "
        "그때 '올려줘'는 post_reply로 두면 시스템이 초안이 없다고 안내한다\n"
        f"지금 상태: {state}\n"
        'JSON으로만 답하세요. 예: {"intent": "analyze", "target": "none", "negative_first": false}'
    )


def _state(toolbox) -> str:
    return ", ".join(
        [
            f"미답글 목록을 보여 줌: {'예' if toolbox.listed else '아니오'}",
            f"확인 기다리는 답글 초안: {'있음' if toolbox.drafts else '없음'}",
            "보고 주기 제안에 대한 답을 기다림: "
            + ("예" if toolbox.pending_suggestion else "아니오"),
        ]
    )


def classify(client, utterance: str, toolbox, history: list[dict] | None = None) -> dict:
    """→ {"intent", "target", "negative_first"}. 실패하면 {"intent": None, "error": ...}."""
    msgs = [{"role": "system", "content": _prompt(_state(toolbox))}]
    # 직전 비서 답변 한 개만 맥락으로 (확인 질문에 대한 '응'을 알아듣도록)
    last = next((m for m in reversed(history or []) if m.get("role") == "assistant"), None)
    if last and last.get("content"):
        msgs.append({"role": "assistant", "content": last["content"][:300]})
    msgs.append({"role": "user", "content": utterance})
    try:
        msg = client.chat(INTENT_MODEL, msgs, **model_options(INTENT_MODEL, 256, SCHEMA))
        out = json.loads(msg.get("content") or "{}")
    except (ClovaError, ValueError) as e:
        return {"intent": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    if out.get("intent") not in INTENTS:
        return {"intent": None, "error": f"unknown intent {out.get('intent')!r}"}
    return out


def _force(spec: dict) -> dict:
    return {"type": "function", "function": {"name": spec["function"]["name"]}}


def plan(intent: dict, utterance: str, toolbox) -> dict:
    """의도 → 실행 계획.
    {"routed": (도구, 인자)}  시스템이 바로 실행
    {"hold": True}            초안 대기 안내
    {"specs": [...], "tool_choice": ...}  HCX가 그 도구 인자만 채운다 (specs=[]면 도구 없이 답)
    분류에 실패하면 None → 규칙 라우터로 처리한다."""
    kind = intent.get("intent")
    if kind is None:
        return None
    target = (
        intent.get("target")
        if intent.get("target") in ("first", "negative", "all_listed")
        else None
    )
    if kind in ("edit_draft", "post_reply", "hold") and not toolbox.drafts:
        # 초안이 없으면 고칠·미룰 것이 없다. '올려줘'는 post가 '초안이 없어요'로 안내한다
        kind = {"edit_draft": "draft_reply", "hold": "other"}.get(kind, kind)
    if kind == "list_unanswered":
        return {
            "routed": (
                "get_unanswered_reviews",
                {"priority": "negative_first"} if intent.get("negative_first") else {},
            )
        }
    if kind == "draft_reply":
        return {"routed": ("draft_review_reply", {"target": target or "first"})}
    if kind == "edit_draft":
        return {"routed": ("draft_review_reply", {"edit_request": utterance})}
    if kind == "post_reply":
        return {"routed": ("post_review_reply", {})}  # 확정 표현은 post_review_reply가 다시 본다
    if kind == "hold":
        return {"hold": True}
    if kind == "report_now":
        return {"routed": ("run_review_report", {})}
    if kind == "report_settings":
        # 인자(주기·요일·시간)는 이미 검증된 파서로 꺼낸다. 못 꺼내면 HCX가 그 도구 인자만 채운다
        rep = parse_report_request(
            utterance, None if toolbox.drafts else toolbox.pending_suggestion
        )
        if rep and rep[0] != "now":
            k, args = rep
            return {
                "routed": ("set_review_report", {"action": k} if k in ("off", "suggest") else args)
            }
        return {"specs": [SET_REVIEW_REPORT], "tool_choice": _force(SET_REVIEW_REPORT)}
    if kind == "analyze":
        return {"specs": [ANALYZE_REVIEWS], "tool_choice": _force(ANALYZE_REVIEWS)}
    return {"specs": [], "tool_choice": None}  # other: 도구 없이 답한다


# 혼합 라우터(router="hybrid"): 규칙이 먼저, 규칙이 못 잡은 턴만 분류한다.
# 분류는 규칙이 약한 곳만 맡는다
# (2026-11-08 비교: intent는 '도구 없음'·보고 의도 100%, 리뷰 세부 의도는 약함)
# - other: 리뷰 단서도 없으면 도구 없이 답한다 (규칙의 '배달 메뉴 가격' → 분석 같은 오탐 방지)
# - report_now·report_settings: 보고 흐름 ('보고서로 정리해줘'처럼 분석 형식을 말하면 제외)
# - hold: 초안 대기 중 미루는 말
# 나머지(분석·목록·작성·수정·등록)는 규칙 버전의 HCX 흐름(도구 5개 + 재요청·시스템 직접 실행)
_ANALYSIS_FORMAT = re.compile(r"형식|(으)?로\s?정리|정리해|요약해|분석")


def hybrid_plan(intent: dict, utterance: str, toolbox) -> dict | None:
    kind = intent.get("intent")
    if kind == "other" and not toolbox.looks_like_review_question(utterance):
        return {"specs": [], "tool_choice": None}
    if kind == "hold" and toolbox.drafts:
        return {"hold": True}
    if kind in ("report_now", "report_settings") and not _ANALYSIS_FORMAT.search(utterance):
        return plan(intent, utterance, toolbox)
    return None
