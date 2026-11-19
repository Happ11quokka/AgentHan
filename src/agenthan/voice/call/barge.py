"""음성 끼어들기 판정 (3단계 spec "음성 끼어들기", 티켓 06). 시각은 모두 오디오 시각(ms)이다.

봇이 말하는 동안 올라온 진짜 마이크 소리와 그 STT 글로 정한다. 세션은 결과(줄이기·멈춤·되돌림)를
이벤트로 내보내기만 한다.

- 소리: 봇 말 중 `threshold` 이상인 소리가 `duck_ms`(0.25초) 이어지면 줄이기(`bot.duck`).
  `stop_ms`(1.0초) 이상 이어지면 멈춤(`bot.stop`). 한국어 맞장구는 중앙값 0.5초·최대 2.4초라
  (Young & Lee 2004, `s3-papers` 적용 3) 소리만으로 멈추는 문턱은 1.0초 아래로 두지 않는다
- 글: 맞장구·빈 글·에코 글이면 되돌림(`bot.unduck`), 그 밖의 글이면 멈춤
- 위치 규칙(`s3-papers` 적용 4): 남은 봇 말이 `min_remaining_ms`(2초)보다 짧으면 소리만으로는
  멈추지 않는다(Cao 2025). 인사·마무리·사장님 연결(`blocked`)은 아무것도 하지 않는다. 최종
  복창(`readback`)은 줄이기만 하고 소리로 멈추지 않는다. 금액 문장 끝 전에 시작한 "네"·맞장구는
  무시하고, 정정 글이면 멈춘다
- 적응 문턱(`s3-papers` 적용 6, Selfridge 2013): 헛 끼어들기(줄였는데 진짜가 아님, 멈췄는데
  진짜가 아님)마다 그 통화의 멈춤 문턱을 `stop_step_ms`씩 올린다(`stop_max_ms`까지)
- 끼어들기를 정리한 뒤(멈춤·되돌림) `backoff_ms`(1초) 동안은 새로 줄이지 않는다(Vapi
  `backoffSeconds`)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from agenthan.voice.call.spoken import SpokenTurn
from agenthan.voice.call.turn_end import is_backchannel
from agenthan.voice.dialog.rules import yes_or_no

BargeMode = Literal["off", "shadow", "on"]
# 봇 말 구간의 종류. blocked: 인사·마무리·사장님 연결 / readback: 최종 복창 / open: 나머지
WindowKind = Literal["blocked", "readback", "open"]
Action = Literal["duck", "unduck", "stop"]
# 봇 말 중에 시작한 말의 STT 글 판정
Verdict = Literal["accept", "empty", "echo", "backchannel", "early_yes", "blocked"]


@dataclass(frozen=True)
class BargeSettings:
    """값은 시작값이다. 실기기 시험(`phone-s3-turn-management/device-check.md`)으로 맞춘다.

    `mode`: shadow는 판정과 기록만 하고 줄이기·멈춤을 보내지 않는다(그림자 측정, 연구 노트 T1~T3).
    봇 말 중 글은 반이중처럼 버린다. on은 실제로 줄이고 멈춘다.
    """

    mode: Literal["shadow", "on"] = "on"
    # 봇 말 중 소리로 볼 RMS(PCM16). 기본은 무음 감지와 같다. 에코가 크면 올린다
    # (`PHONE_BARGE_THRESHOLD`, 시험 T1의 에코 p95를 보고 정한다)
    threshold: float = 1_500.0
    duck_ms: int = 250  # 이만큼 소리가 이어지면 줄이기 (`PHONE_BARGE_DUCK_MS`)
    stop_ms: int = 1_000  # 이만큼 이어지면 멈춤 (`PHONE_BARGE_STOP_MS`)
    stop_step_ms: int = 250  # 헛 끼어들기마다 멈춤 문턱을 이만큼 올린다
    stop_max_ms: int = 2_500  # 한국어 맞장구 최대 2.4초를 덮는 데까지만 올린다
    gap_ms: int = 300  # 소리 사이 이만큼 짧은 쉼은 같은 소리로 본다(낱말 사이 쉼)
    # 소리가 끝난 뒤 이만큼 글이 없으면 되돌린다. LiveKit `false_interruption_timeout`
    # (손님 말 끝부터 2초)과 같다. CLOVA 글은 말 끝 + 0.6~0.9초에 온다
    unduck_after_ms: int = 2_000
    backoff_ms: int = 1_000  # 끼어들기를 정리한 뒤 다시 줄이지 않는 시간
    min_remaining_ms: int = 2_000  # 남은 봇 말이 이보다 짧으면 소리만으로는 멈추지 않는다
    # 봇 말 끝(재생 끝 보고를 받은 오디오 시각) + 이만큼 안에 시작한 맞장구는 봇 말 중으로 본다.
    # vox.ai #54는 "질문 끝 + 0.3초"다. 우리 재생 끝 보고는 브라우저가 마지막 소리 뒤
    # 150ms + outputLatency에 보내므로(`micReopenDelayMs`) 그만큼 뺐다
    answer_gap_ms: int = 150
    # 봇 문장과 글자 두 개짜리 조각이 이 비율 이상 겹치면 에코 글로 본다
    echo_overlap: float = 0.6


def is_early_yes(text: str) -> bool:
    """최종 복창의 금액 문장이 끝나기 전에 한 말이면 무시할 말인가: 예 또는 맞장구 (grilling Q10).

    정정("아니 두 개요")은 받는다. 음성 끼어들기 판정과 반이중의 대기 말(리뷰 P1)이 같이 쓴다
    """
    t = text.strip()
    return bool(t) and (yes_or_no(t) == "yes" or is_backchannel(t))


@dataclass
class BotWindow:
    """봇 말 구간 하나 (음성 끼어들기). 시각은 모두 오디오 시각이다(`SpokenTurn` 참고)."""

    kind: WindowKind
    spoken: SpokenTurn
    to_ms: int | None = None  # 재생 끝 보고·끊긴 때. None이면 아직 말하는 중
    cut: bool = False  # 음성 끼어들기로 멈췄다
    log: dict[str, Any] = field(default_factory=dict)  # 그 턴 기록의 `barge`

    @property
    def turn(self) -> int:
        return self.spoken.turn

    def holds(self, start_ms: int, gap_ms: int = 0) -> bool:
        """이 구간에 시작한 말인가. 끝이 열려 있으면 지금도 말하는 중이다."""
        return start_ms >= self.spoken.from_ms and (
            self.to_ms is None or start_ms < self.to_ms + gap_ms)


_NOT_TEXT = re.compile(r"[^0-9A-Za-z가-힣]+")


def is_echo(text: str, bot_texts: list[str], min_overlap: float) -> bool:
    """STT 글이 봇 문장에서 온 에코인가. 글의 두 글자 조각 중 봇 문장에도 있는 것의 비율로 본다.

    우리 대사는 템플릿이라 봇이 무엇을 말했는지 정확히 안다(Twilio `echoPromptFiltering`과 같은
    생각). 한 글자 말("네")은 여기서 가르지 않는다(맞장구 판정이 맡는다)
    """
    grams = _bigrams(text)
    if not grams:
        return False
    bot: set[str] = set()
    for t in bot_texts:
        bot.update(_bigrams(t))
    return sum(g in bot for g in grams) / len(grams) >= min_overlap


def _bigrams(text: str) -> list[str]:
    t = _NOT_TEXT.sub("", text)
    return [t[i:i + 2] for i in range(len(t) - 1)]


@dataclass
class BargeTracker:
    """통화 하나의 끼어들기 상태. 봇 말 구간마다 `begin`을 부른다."""

    settings: BargeSettings
    false_barges: int = 0  # 헛 끼어들기 수 (멈춤 문턱을 올린다)
    ducked: bool = False
    stopped: bool = False  # 이 구간을 음성으로 멈췄다
    barge_from_ms: int | None = None  # 줄이기를 부른 소리의 시작
    _run_start: int | None = None
    _last_voiced: int | None = None
    _run_end: int | None = None  # 줄인 뒤 소리가 끝난 때 (되돌림 시간 제한)
    _backoff_until: float = -math.inf
    rms: list[float] = field(default_factory=list)  # 이 구간 프레임 RMS (에코 바닥 측정)

    @property
    def mode(self) -> Literal["shadow", "on"]:
        return self.settings.mode

    @property
    def acting(self) -> bool:
        """켜짐(on)이면 줄이고 멈춘다. 그림자(shadow)면 판정만 남긴다."""
        return self.settings.mode == "on"

    def answer_gap_ms(self, text: str) -> int:
        """봇 말 끝 뒤 이만큼 안에 시작한 이 말은 봇 말 중으로 본다. 맞장구만 여유가 있다."""
        return self.settings.answer_gap_ms if is_backchannel(text) else 0

    @property
    def stop_ms(self) -> int:
        s = self.settings
        return min(s.stop_ms + s.stop_step_ms * self.false_barges, max(s.stop_max_ms, s.stop_ms))

    def begin(self) -> None:
        self.ducked = self.stopped = False
        self.barge_from_ms = self._run_start = self._last_voiced = self._run_end = None
        self.rms = []

    def frame(self, rms: float, start_ms: int, end_ms: int, *, kind: WindowKind,
              remaining_ms: float) -> Action | None:
        """봇 말 중 프레임 하나. 할 일이 있으면 돌려준다."""
        s = self.settings
        self.rms.append(rms)
        if rms >= s.threshold:
            if self._run_start is None or (
                self._last_voiced is not None and start_ms - self._last_voiced > s.gap_ms
            ):
                self._run_start = start_ms
            self._last_voiced = end_ms
            self._run_end = None
        elif self._run_start is not None and self._last_voiced is not None \
                and end_ms - self._last_voiced > s.gap_ms:
            self._run_start = None
            self._run_end = self._last_voiced
        if kind == "blocked" or self.stopped:
            return None
        if self._run_start is not None:
            assert self._last_voiced is not None
            length = self._last_voiced - self._run_start
            if not self.ducked and length >= s.duck_ms and self._run_start >= self._backoff_until:
                self.ducked = True
                self.barge_from_ms = self._run_start
                return "duck"
            if (self.ducked and kind == "open" and length >= self.stop_ms
                    and remaining_ms >= s.min_remaining_ms):
                self.stopped = True
                self._backoff_until = end_ms + s.backoff_ms
                return "stop"
        elif self.ducked and self._run_end is not None \
                and end_ms - self._run_end >= s.unduck_after_ms:
            self._false(end_ms)
            return "unduck"
        return None

    def stop_by_text(self, at_ms: int) -> None:
        """끼어든 글로 멈췄다."""
        self.stopped = True
        self._backoff_until = at_ms + self.settings.backoff_ms

    def judge(self, text: str, *, kind: WindowKind, before_amount_end: bool,
              bot_texts: list[str]) -> Verdict:
        """봇 말 중에 시작한 말의 STT 글. 상태는 바꾸지 않는다(`settle`가 바꾼다)."""
        t = text.strip()
        if kind == "blocked":
            return "blocked"
        if not t:
            return "empty"
        if is_echo(t, bot_texts, self.settings.echo_overlap):
            return "echo"
        if kind == "readback":
            if is_early_yes(t):
                # 금액을 다 듣기 전의 "네"는 확정이 아니다(1단계 불변식). 다 들은 뒤면 답이다
                return "early_yes" if before_amount_end else (
                    "accept" if yes_or_no(t) == "yes" else "backchannel")
            return "accept"
        if is_backchannel(t):
            return "backchannel"
        return "accept"

    def settle(self, verdict: Verdict, end_ms: int, at_ms: int) -> Action | None:
        """판정한 글로 줄이기·멈춤을 정리한다. 줄이기를 부른 소리와 겹치는 글만 정리한다.

        진짜가 아니면(맞장구·빈 글·에코) 헛 끼어들기로 세고, 줄여 둔 상태면 되돌린다.
        """
        if self.barge_from_ms is None or end_ms <= self.barge_from_ms:
            return None
        if verdict == "accept" or not (self.ducked or self.stopped):
            return None  # 진짜 끼어들기의 멈춤은 세션이 국면을 보고 정한다
        was_ducked_only = self.ducked and not self.stopped
        self._false(at_ms)
        return "unduck" if was_ducked_only else None

    def _false(self, at_ms: int) -> None:
        self.false_barges += 1
        self.ducked = False
        self.barge_from_ms = None
        self._backoff_until = at_ms + self.settings.backoff_ms
