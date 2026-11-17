"""승인 다음 턴에는 새로 시작하는 계획 루프 예산."""

import json
import time
from dataclasses import dataclass, field


class BudgetExhausted(Exception):
    """코드가 진행 상황과 남은 단계를 안내해야 한다."""


@dataclass
class LoopBudget:
    tool_limit: int = 8
    model_limit: int = 10
    replan_limit: int = 1
    seconds: float = 20
    multi_step_seconds: float = 30
    started: float = field(default_factory=lambda: time.monotonic())
    tools: int = 0
    models: int = 0
    replans: int = 0
    reason: str | None = None
    seen: set[str] = field(default_factory=set)

    @property
    def deadline(self):
        return self.started + self.seconds

    def check(self):
        if time.monotonic() >= self.deadline:
            self.reason = "wall_clock"
            raise BudgetExhausted

    def model(self):
        self.check()
        if self.models >= self.model_limit:
            self.reason = "model_limit"
            raise BudgetExhausted
        self.models += 1

    def tool(self, name: str, arguments: dict):
        self.check()
        key = json.dumps([name, arguments], sort_keys=True, ensure_ascii=False)
        if key in self.seen:
            self.reason = "repeated_tool"
            raise BudgetExhausted
        if self.tools >= self.tool_limit:
            self.reason = "tool_limit"
            raise BudgetExhausted
        self.seen.add(key)
        self.tools += 1

    def plan_steps(self, count: int):
        """여러 단계 계획은 단계마다 HCX 왕복이 늘어 시간 한도만 넓힌다(횟수 한도는 그대로)."""
        if count >= 2:
            self.seconds = max(self.seconds, self.multi_step_seconds)

    def replan(self):
        self.check()
        if self.replans >= self.replan_limit:
            return False
        self.replans += 1
        return True

    def trace(self):
        return {
            "tools": self.tools,
            "models": self.models,
            "replans": self.replans,
            "elapsed_sec": round(time.monotonic() - self.started, 3),
            "exhausted": self.reason is not None,
            "reason": self.reason,
        }
