"""비서 답 아래 맥락 칩(next_actions)의 정본: 이번 턴에 한 일 + 가게 데이터 → 칩.

규칙은 RULES 표 한 곳에만 둔다. 문구는 고정 문구이고 모델을 부르지 않는다.
칩은 누르기만 해서는 가게 정보를 바꾸지 않는다. 쓰기 기능 칩(coupon.create·keywords.edit)은
입력 폼이나 질문으로 이어지고, 저장은 기존 미리보기·승인을 거친다. 버튼이 없는 기능은
``chat.say``(그 말을 대화 한 턴으로 보냄)로 연결한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from agenthan.tools.feature_tools import TOOL_FEATURES

MAX_CHIPS = 3
# 네이버 플레이스 대표키워드는 5개까지 넣을 수 있다. 그보다 적으면 더 넣어 보자고 한다.
KEYWORD_ENOUGH = 5


@dataclass(frozen=True)
class Facts:
    """칩 조건이 보는 가게 데이터."""

    keywords: tuple[str, ...] = ()
    # 오늘 이후로 끝나는(아직 쓸 수 있는) 쿠폰 제목.
    live_coupons: tuple[str, ...] = ()
    # 답글이 없는 낮은 별점(3점 이하) 리뷰 id. 별점 없는 리뷰는 세지 않는다.
    low_unanswered: tuple[str, ...] = ()
    report_enabled: bool = False


@dataclass(frozen=True)
class Rule:
    after: str  # 이번 턴에 한 일: review | statistics | coupon
    label: str
    action_id: str
    args: Callable[[Facts], dict] = field(default=lambda _: {})
    # 이 조건이 참이면 칩을 뺀다(이미 설정됨·대상 없음 등).
    skip: Callable[[Facts], bool] = field(default=lambda _: False)


def _say(text: str) -> Callable[[Facts], dict]:
    return lambda _: {"text": text}


RULES: tuple[Rule, ...] = (
    # 리뷰 요약 뒤
    Rule(
        "review",
        "매일 리뷰 요약 받기",
        "chat.say",
        _say("매일 리뷰 요약해서 보내 줘"),
        skip=lambda f: f.report_enabled,
    ),
    Rule(
        "review",
        "불만 리뷰 답글 초안",
        "review.draft_reply",
        lambda f: {"review_id": f.low_unanswered[0]},
        skip=lambda f: not f.low_unanswered,
    ),
    Rule("review", "이번 주 통계 보기", "stats.week"),
    # 통계(유입 경로·조회) 뒤
    Rule(
        "statistics",
        "대표 키워드 추가",
        "keywords.edit",
        skip=lambda f: len(f.keywords) >= KEYWORD_ENOUGH,
    ),
    Rule("statistics", "쿠폰 만들기", "coupon.create", skip=lambda f: bool(f.live_coupons)),
    Rule("statistics", "리뷰 요약 보기", "review.summary"),
    # 쿠폰 만든 뒤
    Rule("coupon", "이번 주 통계 보기", "stats.week"),
    Rule(
        "coupon",
        "매주 리뷰 요약 받기",
        "chat.say",
        _say("매주 리뷰 요약해서 보내 줘"),
        skip=lambda f: f.report_enabled,
    ),
    Rule("coupon", "리뷰 요약 보기", "review.summary"),
)

# 버튼 턴: 버튼이 한 일.
BUTTON_DONE = {"review.summary": "review", "stats.week": "statistics", "stats.day": "statistics"}


def facts(shop: dict, report_settings: dict | None, today: date) -> Facts:
    coupons = [
        c
        for c in shop.get("coupons", [])
        if str(c.get("end_date") or "9999-12-31") >= today.isoformat()
        and c.get("status") not in ("ended", "deleted")
    ]
    low = [
        r["review_id"]
        for r in shop.get("reviews", [])
        if isinstance(r.get("rating"), int) and r["rating"] <= 3 and not r.get("has_owner_reply")
    ]
    return Facts(
        keywords=tuple(shop.get("shop", {}).get("keywords", [])),
        live_coupons=tuple(str(c.get("title", "")) for c in coupons),
        low_unanswered=tuple(low),
        report_enabled=bool((report_settings or {}).get("enabled")),
    )


def done_in(trace: dict, action_id: str | None = None) -> list[str]:
    """이번 턴에 한 일(규칙 표의 after 값). 버튼 id와 trace의 실제 실행 결과만 본다."""
    done = [BUTTON_DONE[action_id]] if action_id in BUTTON_DONE else []
    # 통계 한 기능만 묻는 말은 계획 모드가 기존 통계 설명기에 바로 넘긴다(trace에 호출 없음).
    if trace.get("feature") == "statistics" and not trace.get("pending_approval"):
        done.append("statistics")
    for call, result in zip(trace.get("calls", []), trace.get("results", []), strict=False):
        name = call.get("name")
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        feature = "review" if name == "ask_review_agent" else TOOL_FEATURES.get(name)
        if name == "create_coupon" and (
            call.get("approved") or str(result.get("action", "")).startswith("saved")
        ):
            # 승인 뒤 실제 저장(계획 묶음 승인은 approved 표시, 기존 승인 경로는 saved_*).
            done.append("coupon")
        elif feature in ("review", "statistics") and result.get("action") != "pending_approval":
            done.append(feature)
    return list(dict.fromkeys(done))


def merge(existing: list[dict], done: list[str], f: Facts) -> list[dict]:
    """기존 칩 뒤에 맥락 칩을 붙인다. 승인 대기 칩이 있으면 그대로 둔다."""
    if any(a.get("action_id") == "task.approve" for a in existing):
        return existing
    taken = {_key(a) for a in existing} | {a.get("label") for a in existing}
    chips: list[dict] = []
    for rule in RULES:
        if len(chips) >= MAX_CHIPS:
            break
        if rule.after not in done or rule.skip(f):
            continue
        chip = {"action_id": rule.action_id, "label": rule.label, "args": rule.args(f)}
        if _key(chip) in taken or rule.label in taken:
            continue
        chips.append(chip)
        taken |= {_key(chip), rule.label}
    return [*existing, *chips]


def _key(chip: dict):
    """같은 기능을 하는 칩인지 가르는 열쇠. 말로 보내는 칩은 보낼 말로 가른다."""
    if chip.get("action_id") == "chat.say":
        return ("chat.say", (chip.get("args") or {}).get("text"))
    return chip.get("action_id")
