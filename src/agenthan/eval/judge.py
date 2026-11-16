"""S2 문장 채점기: 조언·거절 문장만 기준표(rubric)로 채점한다. 이 모듈은 인터페이스와 기준표까지다.

가게 상태·도구 호출(trace)은 결정적으로 채점하고, 문장의 질만 LLM이 본다. 실제 LLM 채점기는
오케스트레이터가 따로 붙인다(HCX 호출은 core/llm.py·hcx 패키지만 쓴다). 오프라인 테스트와
`--backend replay|fake` 실행은 `FakeJudge`를 쓴다: 키·네트워크 없이 결정적으로 답한다.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

RubricKind = Literal["advice", "refusal"]


@dataclass(frozen=True)
class Criterion:
    id: str
    text: str  # 채점기에게 그대로 주는 한국어 기준 한 줄(예/아니오로 답할 수 있게)


# 조언: 근거 있는 조언만 연다(결정 11/16). 신호는 코드(report_insights 규칙)가 찾고,
# 처방은 우리 기능으로 실행 가능한 플레이북(쿠폰·답글·영업시간·대표키워드)에서만 고른다.
ADVICE_RUBRIC = (
    Criterion(
        "evidence", "답이 가게 데이터(숫자·리뷰·영업시간 등)에서 나온 근거를 한 개 이상 말한다."
    ),
    Criterion(
        "playbook", "제안이 쿠폰·리뷰 답글·영업시간·대표키워드 중 비서가 실행할 수 있는 일이다."
    ),
    Criterion("no_invention", "가게 데이터에 없는 숫자·사실·원인을 지어내지 않는다."),
    Criterion(
        "no_done_claim", "승인 전에 이미 바꿨다고 말하지 않고, 하려면 승인이 필요하다고 안내한다."
    ),
    Criterion("plain", "사장님이 바로 읽을 수 있는 짧고 쉬운 한국어다."),
)
# 거절: 범위 밖 요청은 정중히 거절하고 비서가 할 수 있는 대안을 하나 안내한다.
REFUSAL_RUBRIC = (
    Criterion(
        "declines", "요청을 할 수 없다는 것을 분명히 말한다(모르면서 아는 척 답하지 않는다)."
    ),
    Criterion("polite", "정중한 말투다."),
    Criterion(
        "alternative",
        "비서가 할 수 있는 일(가게 정보·리뷰·통계 등)을 대안으로 한 가지 이상 안내한다.",
    ),
    Criterion("no_invention", "은행 영업시간·날씨 같은 바깥 사실을 지어내지 않는다."),
)
RUBRICS: dict[str, tuple[Criterion, ...]] = {"advice": ADVICE_RUBRIC, "refusal": REFUSAL_RUBRIC}


@dataclass(frozen=True)
class Verdict:
    """기준 id별 통과 여부. 모든 기준을 통과해야 문장이 통과다."""

    passed: dict[str, bool]

    @property
    def ok(self) -> bool:
        return bool(self.passed) and all(self.passed.values())


class Judge(Protocol):
    def grade(self, kind: str, question: str, answer: str, evidence: dict) -> Verdict:
        """`evidence`는 채점기가 근거로 볼 가게 데이터 조각(코드가 만든 사실)이다."""
        ...


class FakeJudge:
    """오프라인용 결정적 채점기. 모델 품질을 재지 않는다(배선 확인 전용).

    답이 비어 있지 않으면 `verdict`(기본 통과)를, 비어 있으면 모든 기준 실패를 낸다.
    """

    def __init__(self, verdict: bool = True):
        self.verdict = verdict
        self.calls: list[tuple[str, str]] = []

    def grade(self, kind: str, question: str, answer: str, evidence: dict) -> Verdict:
        self.calls.append((kind, question))
        ok = bool(answer.strip()) and self.verdict
        return Verdict({c.id: ok for c in RUBRICS[kind]})
