"""L2 대본 시나리오 (티켓 06, spec §9). 러너는 `scenario_runner.py`, 대본은 `scenarios/*.json`."""

from __future__ import annotations

from datetime import datetime

import pytest
from scenario_runner import SCENARIO_DIR, Scenario, TurnView, forbidden, load_all, run

from agenthan.voice.dialog.cart import Cart
from agenthan.voice.dialog.engine import TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.store_config import KST

SCENARIOS = load_all()

REQUIRED = [  # 티켓 06의 시나리오 10개
    "s01-single-item",
    "s02-multi-items",
    "s03-change-qty",
    "s04-remove-item",
    "s05-required-option",
    "s06-option-and-request",
    "s07-ambiguous-clarify",
    "s08-unknown-and-sold-out",
    "s09-correction",
    "s10-menu-question",
]


def _param(scenario: Scenario) -> object:
    marks = [pytest.mark.xfail(reason=scenario.xfail, strict=True)] if scenario.xfail else []
    return pytest.param(scenario, id=scenario.id, marks=marks)


@pytest.mark.parametrize("scenario", [_param(s) for s in SCENARIOS])
def test_scenario(scenario: Scenario) -> None:
    result = run(scenario)
    transcript = "\n".join(result.transcript)
    assert not result.failures, "\n".join([*result.failures, "", transcript])


def test_scenario_files_are_consistent() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert all(s.path.stem == s.id for s in SCENARIOS)
    assert set(REQUIRED) <= set(ids)
    required = [s for s in SCENARIOS if s.id in REQUIRED]
    assert not any(s.xfail for s in required), "필수 시나리오 10개는 xfail이면 안 된다"
    assert SCENARIO_DIR.is_dir()


# ---------------------------------------------------------------- 금지 규칙이 실제로 잡는지


NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)


def _view(utterances: list[str], *, user: str = "네", state: State = State.ORDERING,
          before: TurnResult | None = None, order_status: str | None = None) -> TurnView:
    from agenthan.core.contracts import Order

    order = Order("o", [], status=order_status) if order_status else None  # type: ignore[arg-type]
    result = TurnResult(utterances, state, Cart(), order)
    return TurnView(user, result, before or TurnResult([], State.ORDERING, Cart(), None))


def test_forbidden_passes_a_normal_turn() -> None:
    assert forbidden(_view(["어떤 걸로 드릴까요?"])) == []


@pytest.mark.parametrize(
    ("utterances", "rule"),
    [
        ([], "대답 없는 턴"),
        (["메도빅 하나요. 더 필요하신 거 있으세요?", "몇 시쯤 오세요?"], "한 턴 두 질문"),
        (["메도빅 하나, 6시 반 픽업, 총 7,500원이에요. 맞으세요?"], "복창 총액"),
        (["가" * 150 + "."], "151자 문장"),
        (["메도빅(꿀 케이크) 하나요."], "괄호·이모지·마크다운"),
        (["메도빅 하나요 🍰"], "괄호·이모지·마크다운"),
        (["**메도빅** 하나요."], "괄호·이모지·마크다운"),
    ],
)
def test_forbidden_catches(utterances: list[str], rule: str) -> None:
    problems = forbidden(_view(utterances))
    assert any(rule in p for p in problems), problems


def test_forbidden_catches_confirmed_without_readback() -> None:
    problems = forbidden(_view(["감사합니다."], state=State.CLOSING, order_status="confirmed"))
    assert any("확인 전 confirmed" in p for p in problems)


def test_forbidden_catches_confirmed_on_content_after_readback() -> None:
    before = TurnResult(["…, 총 0원이에요. 맞으세요?"], State.CONFIRM, Cart(), None)
    view = _view(["감사합니다."], user="네, 6시 반이요", state=State.CLOSING, before=before,
                 order_status="confirmed")
    assert any("확인 전 confirmed" in p for p in forbidden(view))


def test_forbidden_allows_confirmed_after_readback_and_yes() -> None:
    before = TurnResult(["…, 총 0원이에요. 맞으세요?"], State.CONFIRM, Cart(), None)
    view = _view(["감사합니다."], state=State.CLOSING, before=before, order_status="confirmed")
    assert forbidden(view) == []
