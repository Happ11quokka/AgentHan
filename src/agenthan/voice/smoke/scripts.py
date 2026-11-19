"""스모크 S1: 대본 시나리오를 엔진에 넣고 최종 Order를 본다 (6단계 품질).

대본은 코드에 적어 두지 않고 **가게 데이터로 만든다**(`build_scripts`). 메뉴 이름·단위·꼭 고를 것·
영업시간이 가게 JSON에서 오므로 가게를 바꿔도 같은 스모크가 돈다. 손님 말은 엔진 대사
(`dialog/templates.py`)의 읽는 법(수량·조사·시각)을 빌려 만든다.

대본 9개(가게에 따라 줄어든다: 바로 담을 수 있는 메뉴가 하나뿐이면 두 품목·빼기가 빠지고, 꼭
고를 것이 없으면 옵션 대본이 빠진다):

| id | 보는 것 | NLU 호출 |
|---|---|---|
| single | 한 품목 → 주문 끝(규칙) → 시각 → 확정 | 2 |
| two-items | 한 말에 두 품목 + 수량 | 2 |
| change-qty | 복창 뒤 수량 정정 | 3 |
| remove-item | 담은 품목 빼기 | 3 |
| required-option | 꼭 고를 것 묻고 받기 | 3 |
| big-qty | 큰 수량 확인 → 고친 수량 | 3 |
| asap | "아니요, 지금 바로 갈게요" | 2 |
| cancel | 주문 취소(주문서 없음) | 1 |
| handoff | 사장님 연결(handoff 주문서) | 1 |

합계 NLU 20번. 호출 사이를 `min_gap_s`(기본 0.7초) 이상 띄워 분당 86번을 넘지 않는다(HCX 테스트
키 한도 분당 90). 실패한 대본은 한 번 다시 돌리고, 그때 통과하면 "깜빡임(flaky)"으로 표시하고
통과로 센다. 엔진 시각은 가게 여는 시각 + 30분으로 고정한다(밤에 배포해도 영업시간 규칙에 안
걸리게). 답변기(질문 답)는 부르지 않는다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from agenthan.core.contracts import MenuItem, Order, StoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.pickup import earliest_pickup, today_at
from agenthan.voice.dialog.states import TERMINAL
from agenthan.voice.nlu.base import Act, Nlu, NluContext
from agenthan.voice.store_config import RequiredOption, StoreConfig

MIN_GAP_S = 0.7  # NLU 호출 사이 최소 간격. 60 / 0.7 ≈ 86번/분 < 90
SCRIPT_AFTER_OPEN = timedelta(minutes=30)  # 대본 시각: 가게 여는 시각 + 이만큼
PICKUP_AFTER = (90, 120, 150, 60)  # 픽업 시각 후보: 대본 시각 + 분 (앞에서부터 되는 것)
FIXED_QTY = 2  # 큰 수량을 고쳐 말하는 수

Line = tuple[str, int, tuple[str, ...]]  # (메뉴 id, 수량, 옵션 이름들)
Cart = tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Expected:
    status: Literal["confirmed", "handoff"]
    lines: tuple[Line, ...]
    pickup: datetime | None = None  # None이면 시각은 보지 않는다


@dataclass(frozen=True)
class Script:
    id: str
    title: str
    says: tuple[str, ...]  # 손님 말
    expect: Expected | None  # None이면 주문서가 없어야 한다 (취소)
    carts: tuple[tuple[int, Cart], ...] = ()  # (몇 번째 손님 말 뒤, 그때 장바구니)


# ---------------------------------------------------------------- 대본 만들기


def script_now(config: StoreConfig, today: datetime) -> datetime:
    """대본을 돌릴 엔진 시각: `today`의 가게 날짜, 여는 시각 + 30분."""
    local = today.astimezone(config.tz)
    return today_at(local, config.open_time) + SCRIPT_AFTER_OPEN


def _pickup(config: StoreConfig, now: datetime) -> datetime | None:
    """손님이 말할 픽업 시각. 바로 받는 시각(준비 시간 뒤, 닫기 전, 확인 질문 없이)이고 12시대가
    아닌 것("낮 12시"는 읽는 법이 흔들린다). 없으면 None(대본이 "지금 바로"로 간다)."""
    close = today_at(now, config.close_time)
    for minutes in PICKUP_AFTER:
        at = now + timedelta(minutes=minutes)
        at = at.replace(minute=at.minute - at.minute % 30, second=0, microsecond=0)
        if (at.hour != 12 and earliest_pickup(now, config) <= at <= close
                and at - now <= timedelta(minutes=config.confirm_after_minutes)):
            return at
    return None


def _ask_first(item: MenuItem, config: StoreConfig) -> RequiredOption | None:
    """담으면 엔진이 먼저 물을 꼭 고를 것(기본값 없는 것). 없으면 None."""
    return next((g for g in config.required_options.get(item.id, ()) if not g.default), None)


def _defaults(item: MenuItem, config: StoreConfig) -> tuple[str, ...]:
    return tuple(g.default for g in config.required_options.get(item.id, ()) if g.default)


def _phrase(item: MenuItem, qty: int) -> str:
    """"메도빅 하나", "아메리카노 두 잔"."""
    return templates.line_phrase(item.name_ko, qty, templates.counter_for(item.category))


def _yo(phrase: str) -> str:
    """"두 개" → "두 개요", "한 잔" → "한 잔이요"."""
    return templates.items_noted([phrase]).rstrip(".")


def build_scripts(menu: Sequence[MenuItem], config: StoreConfig, now: datetime) -> list[Script]:
    """가게 메뉴·설정으로 대본을 만든다. 바로 담을 수 있는 메뉴가 없으면 빈 목록."""
    open_menu = [m for m in menu if not m.sold_out]
    plain = [m for m in open_menu if _ask_first(m, config) is None]
    if not plain:
        return []
    a = plain[0]
    b = plain[1] if len(plain) > 1 else None
    at = _pickup(config, now)
    done = "아니요, 그게 다예요"
    yes = "네"
    order_a = f"{_phrase(a, 1)} 주세요"
    # 시각을 말하는 한 마디와 그 결과. 받을 시각이 없는 가게면 "지금 바로"
    when = f"{templates.meridiem_time(at)}에 갈게요" if at else "지금 바로 갈게요"
    pickup = at or earliest_pickup(now, config)

    def line(item: MenuItem, qty: int, *options: str) -> Line:
        return (item.id, qty, (*_defaults(item, config), *options))

    def confirmed(*lines: Line) -> Expected:
        return Expected("confirmed", lines, pickup)

    out = [Script("single", "한 품목 주문", (order_a, done, when, yes), confirmed(line(a, 1)))]
    if b is not None:
        both = f"{templates.i_rang(_phrase(a, 2))} {_phrase(b, 1)} 주세요"
        out.append(Script("two-items", "한 말에 두 품목", (both, "없어요", when, yes),
                          confirmed(line(a, 2), line(b, 1))))
    out.append(Script("change-qty", "복창 뒤 수량 정정",
                      (order_a, done, when, f"아니요, {_yo(_phrase(a, FIXED_QTY))}", yes),
                      confirmed(line(a, FIXED_QTY))))
    if b is not None:
        pair = f"{templates.i_rang(_phrase(a, 1))} {_phrase(b, 1)} 주세요"
        out.append(Script("remove-item", "담은 품목 빼기",
                          (pair, f"{templates.eun_neun(b.name_ko)} 빼 주세요", done, when, yes),
                          confirmed(line(a, 1))))
    asked = next(((m, g) for m in open_menu if (g := _ask_first(m, config)) is not None), None)
    if asked is not None:
        item, group = asked
        choice = group.choices[0].name
        others = [g.choices[0].name for g in config.required_options.get(item.id, ())
                  if not g.default and g is not group]
        out.append(Script("required-option", "꼭 고를 것 묻고 받기",
                          (f"{_phrase(item, 1)} 주세요", _yo(choice), *map(_yo, others),
                           done, when, yes),
                          confirmed(line(item, 1, choice, *others))))
    if config.confirm_qty_from > 0:
        big = config.confirm_qty_from + 2
        unit = templates.counter_for(a.category)
        out.append(Script("big-qty", "큰 수량 확인 뒤 고친 수량",
                          (f"{_phrase(a, big)} 주세요",
                           f"아니요, {_yo(templates.qty_phrase(FIXED_QTY, unit))}",
                           done, when, yes),
                          confirmed(line(a, FIXED_QTY)), carts=((0, ()),)))
    out.append(Script("asap", "아니요, 지금 바로 갈게요",
                      (order_a, "아니요, 지금 바로 갈게요", yes),
                      Expected("confirmed", (line(a, 1),), earliest_pickup(now, config))))
    out.append(Script("cancel", "주문 취소", (order_a, "주문 취소할게요", yes), None))
    out.append(Script("handoff", "사장님 연결", (order_a, "사장님 바꿔 주세요"),
                      Expected("handoff", (line(a, 1),))))
    return out


# ---------------------------------------------------------------- 돌리기


class PacedNlu:
    """NLU 호출을 세고, 호출 사이를 `min_gap_s` 이상 띄운다(분당 한도). 걸린 시간도 남긴다."""

    def __init__(self, nlu: Nlu, *, min_gap_s: float = MIN_GAP_S,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._nlu = nlu
        self._gap = min_gap_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self.calls = 0
        self.ms: list[int] = []

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if self._last is not None and (wait := self._last + self._gap - self._clock()) > 0:
            self._sleep(wait)
        started = self._last = self._clock()
        self.calls += 1
        try:
            return self._nlu.parse(text, ctx)
        finally:
            self.ms.append(round((self._clock() - started) * 1000))


@dataclass
class ScriptRun:
    id: str
    title: str
    ok: bool = True
    flaky: bool = False  # 처음에 실패하고 다시 돌려 통과했다
    problems: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)  # 실패했을 때만 채운다

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title, "ok": self.ok}
        if self.flaky:
            out["flaky"] = True
        if self.problems:
            out |= {"problems": self.problems, "transcript": self.transcript}
        return out


@dataclass
class S1Report:
    nlu: str
    runs: list[ScriptRun]
    nlu_calls: int = 0
    nlu_ms: list[int] = field(default_factory=list)
    nlu_errors: int = 0  # NLU 어댑터가 경고를 남긴 횟수(429·시간 초과 등)
    seconds: float = 0.0
    problem: str = ""  # 대본을 못 만들었을 때

    @property
    def total(self) -> int:
        return len(self.runs)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.runs if r.ok)

    @property
    def flaky(self) -> int:
        return sum(1 for r in self.runs if r.flaky)

    @property
    def ok(self) -> bool:
        return not self.problem and self.total > 0 and self.passed == self.total

    def as_json(self) -> dict[str, Any]:
        ms = sorted(self.nlu_ms)
        return {
            "ok": self.ok, "nlu": self.nlu, "passed": self.passed, "total": self.total,
            "flaky": self.flaky, "nlu_calls": self.nlu_calls, "nlu_errors": self.nlu_errors,
            "nlu_p50_ms": ms[len(ms) // 2] if ms else None,
            "nlu_max_ms": ms[-1] if ms else None,
            "seconds": round(self.seconds, 1),
            **({"problem": self.problem} if self.problem else {}),
            "scripts": [r.as_json() for r in self.runs],
        }


def _lines(order: Order) -> list[tuple[str, int, frozenset[str]]]:
    return sorted((line.menu_id, line.quantity, frozenset(line.options)) for line in order.lines)


def _order_problems(order: Order | None, expect: Expected | None) -> list[str]:
    if expect is None:
        if order is None:
            return []
        return [f"최종 Order: 없어야 하는데 {order.status} 주문서가 나왔다"]
    if order is None:
        return [f"최종 Order: {expect.status} 주문서가 없다"]
    problems = []
    if order.status != expect.status:
        problems.append(f"최종 Order: 상태 {order.status} ≠ {expect.status}")
    want = sorted((menu, qty, frozenset(options)) for menu, qty, options in expect.lines)
    if _lines(order) != want:
        got = [(m, q, sorted(o)) for m, q, o in _lines(order)]
        problems.append(f"최종 Order: 줄 {got} ≠ {[(m, q, sorted(o)) for m, q, o in want]}")
    if expect.pickup is not None:
        got_at = datetime.fromisoformat(order.pickup_time) if order.pickup_time else None
        if got_at != expect.pickup:
            problems.append(f"최종 Order: 픽업 {order.pickup_time or '없음'} ≠ "
                            f"{expect.pickup.isoformat()}")
    return problems


def run_script(script: Script, make_engine: Callable[[str], DialogEngine],
               now: datetime) -> ScriptRun:
    """대본 하나를 새 엔진에 넣는다. 최종 Order·중간 장바구니·끝난 상태를 본다."""
    out = ScriptRun(script.id, script.title)
    transcript: list[str] = []
    carts = dict(script.carts)
    order: Order | None = None
    try:
        engine = make_engine(f"smoke-{script.id}")
        result = engine.start(now)
        transcript += [f"AI: {u}" for u in result.utterances]
        for i, say in enumerate(script.says):
            if order is not None or result.state in TERMINAL:
                out.problems.append(f"손님 말 {i + 1} 전에 대화가 끝났다 ({result.state.value})")
                break
            result = engine.handle(say, now)
            transcript.append(f"손님: {say}")
            transcript += [f"AI: {u}" for u in result.utterances]
            order = result.order
            if i in carts:
                cart = tuple((line.item.id, line.qty) for line in result.cart.lines)
                if cart != carts[i]:
                    out.problems.append(f"손님 말 {i + 1} 뒤 장바구니 {list(cart)} ≠ "
                                        f"{list(carts[i])}")
        else:
            if result.state not in TERMINAL:
                out.problems.append(f"대본이 끝났는데 대화가 안 끝났다 ({result.state.value})")
        out.problems += _order_problems(order, script.expect)
    except Exception as err:  # noqa: BLE001  스모크는 무엇이 터졌는지 적고 다음 대본으로 간다
        out.problems.append(f"예외 {type(err).__name__}: {err}")
    out.ok = not out.problems
    if not out.ok:
        out.transcript = transcript
    return out


class _Warnings(logging.Handler):
    """NLU 어댑터가 남긴 경고(호출 실패) 수."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def run_s1(store: StoreKnowledge, config: StoreConfig, nlu: Nlu, *, today: datetime,
           nlu_name: str = "fake", min_gap_s: float = MIN_GAP_S, retry: bool = True,
           limit: int | None = None) -> S1Report:
    """대본을 만들어 차례로 돌린다. 실패한 대본은 한 번 다시 돌린다(통과하면 flaky)."""
    started = time.monotonic()
    paced = PacedNlu(nlu, min_gap_s=min_gap_s)
    now = script_now(config, today)
    scripts = build_scripts(store.list_menu(), config, now)[:limit]
    report = S1Report(nlu_name, [])
    if not scripts:
        report.problem = "바로 담을 수 있는 메뉴가 없어 대본을 만들지 못했다"
        return report

    def make_engine(order_id: str) -> DialogEngine:
        return DialogEngine(store, paced, config, order_id=order_id)

    watch = _Warnings()
    nlu_log = logging.getLogger("agenthan.voice.nlu")
    nlu_log.addHandler(watch)
    try:
        for script in scripts:
            run = run_script(script, make_engine, now)
            if not run.ok and retry:
                again = run_script(script, make_engine, now)
                if again.ok:
                    again.flaky = True
                    run = again
            report.runs.append(run)
    finally:
        nlu_log.removeHandler(watch)
    report.nlu_calls, report.nlu_ms, report.nlu_errors = paced.calls, paced.ms, watch.count
    report.seconds = time.monotonic() - started
    return report
