"""스모크 S2: 가짜 휴대폰으로 서버 WS에 통화를 건다 (6단계 품질).

`fake_phone`(2단계 티켓 07)을 그대로 쓴다: 손님 말을 100ms 프레임으로 실제 속도로 올리고, 봇
소리는 길이만큼 '재생'하고 `playback.started/done`을 보낸다. 통화가 주문 확정까지 가는지와
서버가 보낸 턴 기록(`metrics`)의 말 끝 → 첫 소리(`end_to_voice_ms`)를 본다.

손님 목소리:
- 서버가 실제 STT(`PHONE_STT=clova`)면 손님 말을 **그때그때** CLOVA Voice로 합성해 쓴다(봇과 다른
  화자, `PHONE_SMOKE_SPEAKER`). 합성음은 파일로 남기지 않는다(CLOVA Voice 결과 재사용 금지).
  저장소에는 녹음이 없다(목소리는 개인정보라 `private/`에만 둔다)
- 서버가 가짜 STT면 소리 내용은 쓰이지 않으므로 사인파를 보낸다(가짜 STT는 `epFlag`마다
  `PHONE_FAKE_STT_REPLIES`의 다음 말을 낸다)

통화 대본도 가게 데이터로 만든다(`plan_calls`). 픽업은 "지금 바로 갈게요"라서 서버 시각이
언제든 영업시간 안이면 된다. 영업시간 밖이면 주문을 끝까지 받을 수 없으므로 사장님 연결 통화
("사장님 바꿔 주세요")로 바꿔 음성 경로(STT → 엔진 → TTS)만 본다.

스모크 표시: `smoke_key`(`PHONE_SMOKE_KEY`)를 `call.start`에 실어 보낸다. 서버가 같은 열쇠를 갖고
있으면 그 통화는 사장님 화면·주문 받는 곳에 안 가고 턴 기록에 `smoke: true`가 남는다
(`call.ready.smoke`로 확인, 결과의 `marked`).
"""

from __future__ import annotations

import asyncio
import math
import struct
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from agenthan.core.contracts import MenuItem
from agenthan.voice.call.frames import BYTES_PER_MS
from agenthan.voice.call.turn_summary import spread
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.pickup import earliest_pickup, today_at
from agenthan.voice.fake_phone.audio import speech_end_s
from agenthan.voice.fake_phone.client import CallRun, Clip, run_call
from agenthan.voice.smoke.scripts import Line, _ask_first, _defaults, _phrase
from agenthan.voice.store_config import StoreConfig

Voice = Callable[[str], Awaitable[bytes]]  # 손님 말 → PCM16LE 16kHz mono
CALL_TIMEOUT_S = 180.0  # 통화 하나의 상한 (실제 속도, HCX 최악 포함)
OUT_OF_HOURS = "영업시간 밖이라 주문 통화 대신"
LEAD_MS, TAIL_MS = 300, 400  # 손님 말 앞뒤 무음


@dataclass(frozen=True)
class CallPlan:
    id: str
    kind: Literal["order", "handoff"]
    says: tuple[str, ...]
    expect_status: str  # 주문서 상태: confirmed · handoff
    expect_lines: tuple[Line, ...] | None = None  # None이면 줄은 보지 않는다 (가짜 STT)
    note: str = ""


def plan_calls(menu: Sequence[MenuItem], config: StoreConfig, now: datetime,
               n: int = 1) -> list[CallPlan]:
    """통화 `n`건(1~3)의 대본. 1: 주문 · 2: + 사장님 연결 · 3: + 두 품목 주문."""
    plain = [m for m in menu if not m.sold_out and _ask_first(m, config) is None]
    handoff = CallPlan("handoff", "handoff", ("사장님 바꿔 주세요",), "handoff", ())
    earliest = earliest_pickup(now, config)
    in_hours = today_at(now, config.open_time) <= earliest <= today_at(now, config.close_time)
    if not plain or not in_hours:
        note = OUT_OF_HOURS if plain else "바로 담을 수 있는 메뉴가 없어 주문 통화 대신"
        return [replace(handoff, note=note)]
    a = plain[0]
    tail = ("지금 바로 갈게요", "네")
    plans = [CallPlan("order", "order",
                      (f"{_phrase(a, 1)} 주세요", "아니요, 그게 다예요", *tail), "confirmed",
                      ((a.id, 1, _defaults(a, config)),))]
    if n >= 2:
        plans.append(handoff)
    if n >= 3 and len(plain) > 1:
        b = plain[1]
        plans.append(CallPlan(
            "order-two", "order",
            (f"{templates.i_rang(_phrase(a, 1))} {_phrase(b, 1)} 주세요", "없어요", *tail),
            "confirmed", ((a.id, 1, _defaults(a, config)), (b.id, 1, _defaults(b, config)))))
    return plans


def fake_stt_plan(replies: Sequence[str]) -> CallPlan:
    """가짜 STT 서버용: 서버가 낼 말 수만큼 말소리를 보낸다. 주문 줄은 서버 대본이 정한다."""
    return CallPlan("order", "order", tuple(replies), "confirmed", None,
                    note="가짜 STT: 서버 대본(PHONE_FAKE_STT_REPLIES)대로")


# ---------------------------------------------------------------- 손님 소리


def _silence(ms: int) -> bytes:
    return bytes(ms * BYTES_PER_MS)


def tone_clip(clip_id: str, speech_ms: int = 800) -> Clip:
    """사인파 말소리 하나 (가짜 STT용). 무음 감지 문턱(RMS 1,500)을 넘는다."""
    n = speech_ms * 16
    wave = struct.pack(f"<{n}h", *(int(6_000 * math.sin(2 * math.pi * 440 * i / 16_000))
                                   for i in range(n)))
    return Clip(clip_id, _silence(LEAD_MS) + wave + _silence(TAIL_MS),
                speech_end_s=(LEAD_MS + speech_ms) / 1000)


async def voice_clip(clip_id: str, text: str, voice: Voice) -> Clip:
    pcm = _silence(LEAD_MS) + await voice(text) + _silence(TAIL_MS)
    return Clip(clip_id, pcm, speech_end_s=speech_end_s(pcm))


# ---------------------------------------------------------------- 결과


@dataclass
class CallReport:
    id: str
    kind: str
    ok: bool = True
    flaky: bool = False
    problems: list[str] = field(default_factory=list)
    note: str = ""
    status: str = ""  # 주문서 상태. 없으면 ""
    end_reason: str | None = None
    lines: list[list[Any]] = field(default_factory=list)  # [[메뉴 id, 수량], …]
    voice_ms: list[int] = field(default_factory=list)  # 손님 턴마다 말 끝 → 첫 소리
    routes: list[str] = field(default_factory=list)
    heard: list[str] = field(default_factory=list)  # 서버가 받아 적은 손님 말 (합성음·사인파)
    marked: bool = False  # 서버가 스모크 통화로 받았다
    seconds: float = 0.0

    def as_json(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items() if v not in ("", [], None, False) or k == "ok"}


@dataclass
class S2Report:
    calls: list[CallReport]
    max_p50_ms: int | None = None
    seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.calls)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.calls if c.ok)

    @property
    def flaky(self) -> int:
        return sum(1 for c in self.calls if c.flaky)

    @property
    def voice(self) -> dict[str, int | None]:
        return spread([ms for c in self.calls for ms in c.voice_ms])

    @property
    def slow(self) -> bool:
        p50 = self.voice["p50_ms"]
        return self.max_p50_ms is not None and p50 is not None and p50 > self.max_p50_ms

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.passed == self.total and not self.slow

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok, "passed": self.passed, "total": self.total, "flaky": self.flaky,
            "voice": self.voice, "marked": all(c.marked for c in self.calls),
            "seconds": round(self.seconds, 1), "calls": [c.as_json() for c in self.calls],
        }
        if self.slow:
            out["problem"] = f"말 끝 → 첫 소리 P50이 {self.max_p50_ms}ms를 넘었다"
        return out


def judge(plan: CallPlan, run: CallRun) -> CallReport:
    """통화 기록 → 결과. 주문서 상태·줄, 오류 이벤트, 지연을 본다."""
    out = CallReport(plan.id, plan.kind, note=plan.note, end_reason=run.end_reason)
    out.marked = any(e.get("type") == "call.ready" and e.get("smoke") is True for e in run.events)
    out.heard = [text for clip in run.clips for text in clip.texts]
    guest = [m for m in run.metrics if "end_by" in m]
    out.voice_ms = [m["end_to_voice_ms"] for m in guest if "end_to_voice_ms" in m]
    out.routes = [m["route"] for m in guest if "route" in m]
    if run.error is not None:
        out.problems.append(f"오류 이벤트 {run.error.get('code')}")
    order = (run.order or {}).get("order")
    if order is None:
        out.problems.append(f"주문서가 없음 (끝난 이유 {run.end_reason or '없음'})")
    else:
        out.status = order["status"]
        out.lines = [[line["menu_id"], line["quantity"]] for line in order["lines"]]
        if out.status != plan.expect_status:
            out.problems.append(f"주문서 상태 {out.status} ≠ {plan.expect_status}")
        if plan.expect_lines is not None:
            want = sorted((m, q, sorted(o)) for m, q, o in plan.expect_lines)
            got = sorted((line["menu_id"], line["quantity"], sorted(line["options"]))
                         for line in order["lines"])
            if got != want:
                out.problems.append(f"주문 줄 {got} ≠ {want}")
    if not run.closed_by_server:
        out.problems.append("서버가 통화를 끝내지 않음")
    out.ok = not out.problems
    return out


async def run_plan(url: str, plan: CallPlan, *, voice: Voice | None, pace: float,
                   smoke_key: str, no_turn_timeout_s: float, origin: str = "") -> CallReport:
    started = time.monotonic()
    try:
        if voice is None:
            clips = [tone_clip(f"{plan.id}-{i}") for i in range(len(plan.says))]
        else:
            clips = [await voice_clip(f"{plan.id}-{i}", say, voice)
                     for i, say in enumerate(plan.says)]
        run = await asyncio.wait_for(
            run_call(url, clips, pace=pace, no_turn_timeout_s=no_turn_timeout_s,
                     start={"smoke": smoke_key} if smoke_key else None, origin=origin),
            CALL_TIMEOUT_S * max(pace, 0.05))
        report = judge(plan, run)
    except Exception as err:  # noqa: BLE001  서버에 못 붙음·합성 실패·시간 초과도 결과로 적는다
        report = CallReport(plan.id, plan.kind, ok=False, note=plan.note,
                            problems=[f"예외 {type(err).__name__}: {err}"])
    report.seconds = round(time.monotonic() - started, 1)
    return report


async def run_s2(url: str, plans: Sequence[CallPlan], *, voice: Voice | None, pace: float = 1.0,
                 smoke_key: str = "", retry: bool = True, no_turn_timeout_s: float = 6.0,
                 max_p50_ms: int | None = None, origin: str = "") -> S2Report:
    """통화를 하나씩 건다(동시에 걸지 않는다). 실패한 통화는 한 번 다시 건다(통과하면 flaky)."""
    started = time.monotonic()
    report = S2Report([], max_p50_ms)
    for plan in plans:
        kw: dict[str, Any] = {"voice": voice, "pace": pace, "smoke_key": smoke_key,
                              "no_turn_timeout_s": no_turn_timeout_s, "origin": origin}
        call = await run_plan(url, plan, **kw)
        if not call.ok and retry:
            again = await run_plan(url, plan, **kw)
            if again.ok:
                again.flaky = True
                call = again
        report.calls.append(call)
    report.seconds = time.monotonic() - started
    return report
