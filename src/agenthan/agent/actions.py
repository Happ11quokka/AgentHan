"""사장님 화면 버튼과 실행 대상의 정본."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    target: str
    fixed: dict = field(default_factory=dict)
    inputs: tuple[str, ...] = ()
    missing: str = "form"


ACTIONS = {
    a.id: a
    for a in (
        Action("review.summary", "리뷰 요약", "analyze_reviews", {"period": "30d"}, ("period",)),
        Action(
            "review.draft_reply",
            "리뷰 답글 초안",
            "draft_review_reply",
            inputs=("review_id",),
            missing="conversation",
        ),
        Action("stats.week", "주간 통계", "report_explainer", {"period": "week"}),
        Action("stats.day", "일간 통계", "report_explainer", {"period": "day"}),
        Action(
            "coupon.create",
            "쿠폰 만들기",
            "create_coupon",
            inputs=("title", "discount_type", "discount_value", "start_date", "end_date"),
        ),
        Action("hours.edit", "영업시간 수정", "update_business_hours", inputs=("entries",)),
        Action("task.approve", "저장", "approve", inputs=("task_id", "version")),
        Action("task.cancel", "취소", "cancel", inputs=("task_id",)),
    )
}
