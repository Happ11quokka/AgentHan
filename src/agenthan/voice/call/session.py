"""통화 세션: 통화 1건 (spec 모듈 · 통화 세션, 반이중, 엔진 연결). FastAPI를 모른다.

쓰는 법 (WS 라우터, 티켓 04):

    session = CallSession(store=..., config=..., nlu="fake", stt=..., tts=...,
                          clock=SystemClock(), outbound=..., order_sink=...)
    await session.open()        # 송신 작업 시작
    # 읽기 작업 하나에서:
    #   binary → await session.feed_audio(pcm), text → await session.feed_event(dict)
    #   (둘 다 STT·엔진을 기다리지 않는다. 읽기 작업이 밀리지 않게)
    await session.wait_done()   # 확정·handoff 뒤 재생 끝, 손님 call.end, 오류 중 하나
    await session.close()       # STT 닫기, 지연 기록 쓰기, 남은 송신 비우기

작업은 넷이다.
- 송신 작업 1개: 나가는 이벤트·오디오를 큐에서 꺼내 `Outbound`로 보낸다
- 턴 작업 1개: 준비(엔진 생성·STT 연결) → 인사(턴 0) → 턴을 하나씩.
  엔진은 스레드에서 부른다. 만드는 쪽이 `executor`(엔진 전용 풀)를 주면 거기서, 아니면
  이벤트 루프 기본 풀에서 (`asyncio.to_thread`와 같은 곳)
- STT 쓰기 작업 1개: `feed_audio`가 쌓은 프레임을 STT에 쓴다(리뷰 1번). 쓰기가 밀리면
  쌓인 프레임을 `epFlag` 프레임까지 이어 붙여 DATA 하나로 보낸다. 바이트를 버리지 않으므로
  STT 시각과 무음 감지 시각이 그대로 맞는다. `stt_backlog_ms`(3초)만큼 밀리면 `stt_backlog`로 끝낸다
- STT 읽기 작업 1개: 결과를 받아 조각을 합치고, 말 끝이면 턴을 넘긴다

턴 번호: 인사가 0, 손님 말 하나(합친 것)마다 1씩 는다. 나가는 메시지 모두에 `turn`이 있다.

국면(phase): listening(듣는 중) → thinking(엔진·첫 문장 TTS 도는 중) → speaking(봇 말하는 중)
→ listening.
- listening에 말 끝 결과가 오면 바로 다음 턴
- thinking에 온 결과는 모아 두었다가 재생이 끝나면 하나로 합쳐 다음 턴 하나로 넣는다
  (대답 두 번 방지)
- speaking은 첫 오디오를 보낼 때부터다(3단계 티켓 01). 그 뒤에 시작한 말의 결과는 버린다
  (반이중, 말한 시각 기준). 첫 소리 전에 한 말은 결과가 늦게 와도 다음 턴으로 받는다.
  마지막 문장의 `playback.done`이 오면 listening.
  안 오면 보낸 소리 길이 + `play_done_grace_s` 뒤 서버가 스스로 listening으로 간다.
  말 끊기 버튼(`user.interrupt`, 3단계 티켓 05)이 오면 남은 문장 TTS·자막·송신 큐 오디오를
  버리고 바로 listening. `state.interruptible`이 버튼을 받는지 알린다(복창·마무리·사장님 연결 막음)
  speaking에 들어갈 때 붙잡아 둔 조각은 다음 턴으로 넘기고 "말 끝" 표시를 지운다

조각 붙잡기: 끝 후보 결과가 왔는데 그 끝 뒤에 새 말 구간이 시작됐으면 다음 결과와 합친다.
epFlag 응답이 아닌 결과(gap 등)는 무음 감지가 아직 말하는 중이거나 epFlag 응답을 기다리는
중이어도 붙잡는다(티켓 10, `_continues_after`). 그때 턴은 epFlag 응답으로 넘어간다.
`hold_max_s` 안에 새 결과가 없으면 그대로 턴을 넘긴다(`end_by: hold_timeout`,
`_on_hold_timer` 설명).
마지막으로 글을 붙잡은 때부터 `hold_cap_s`를 넘겨 붙잡지는 않는다(리뷰 C4).
버린 결과(반이중·확정 뒤·통화 끝)는 그 턴 기록의 `dropped`에 남긴다.

무응답 (3단계 티켓 04): 봇 재생이 끝나거나 끊기면(`_after_playback`) 무음 타이머를 건다.
`no_input_s`(기다림 요청 뒤에는 `wait_request_s`) 동안 말이 없으면 봇이 먼저 말하는 턴
(`bot_turn`)으로 엔진 `no_input`을 부른다. 엔진이 횟수를 세고 되묻거나 마지막 인사를 정한다.
말소리는 있었는데 글 없는 epFlag 응답만 오면 엔진 `not_heard`로 다시 말해 달라고 한다.
대화가 끝나 서버가 통화를 끝낼 때는 `call.ended {turn, reason, sms}`를 보낸다
(reason: confirmed · handoff · cancelled · no_input. sms: 그때까지 손님에게 보낸 문자 미리보기,
사장님 허브가 만든다, 4단계 티켓 07). 위로 오는 `call.end`는 손님이 끊은 것이다
(리뷰 S13: 같은 이름이 두 뜻이던 것을 나눴다).

엔진 시간 제한(`turn_timeout_s`)을 넘으면 `error`를 내고 통화를 끝낸다. 다음 턴은 시작하지 않는다
(스레드는 취소되지 않아서, 다음 턴이 같은 엔진을 동시에 건드리게 되기 때문, verify-web D5).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from agenthan.core.contracts import Order, OrderSink, StoreKnowledge
from agenthan.voice.call.barge import (
    Action,
    BargeSettings,
    BargeTracker,
    BotWindow,
    Verdict,
    WindowKind,
    is_early_yes,
)
from agenthan.voice.call.defaults import (
    CONT_WAIT_S,
    MODEL_HIGH,
    MODEL_LOW,
    NO_INPUT_S,
    NO_INPUT_SLACK_S,
    NO_INPUT_SPEECH_MAX_S,
    OWNER_WAIT_S,
    WAIT_REQUEST_S,
)
from agenthan.voice.call.frames import (
    BYTES_PER_MS,
    CHUNK_BYTES,
    HEADER,
    SAMPLE_RATE,
    AudioFrame,
    chunk_pcm,
    pack_audio_frame,
)
from agenthan.voice.call.latency import LatencyLog
from agenthan.voice.call.ports import (
    CUSTOMER_FATAL,
    CUSTOMER_RETRY,
    ClientGone,
    Clock,
    NullOwnerChannel,
    Outbound,
    OwnerChannel,
    PortError,
    SttResult,
    SttStream,
    Tts,
    TurnModel,
)
from agenthan.voice.call.sentences import TTS_LIMIT, split_sentences
from agenthan.voice.call.spoken import SpokenTurn
from agenthan.voice.call.timers import POLL_S, Deadline, OneShot
from agenthan.voice.call.trim import TrimSettings, trim_leading_silence
from agenthan.voice.call.turn_end import (
    TurnDecision,
    classify_turn_end,
    combine_turn_end,
    is_backchannel,
    question_kind,
)
from agenthan.voice.call.vad import SilenceDetector, VadSettings
from agenthan.voice.dialog.cart import Cart
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.owner_question import (
    OwnerNote,
    OwnerQuestion,
    OwnerReply,
    OwnerSeen,
)
from agenthan.voice.dialog.rules import apply_rules
from agenthan.voice.dialog.states import TERMINAL, State
from agenthan.voice.nlu.base import Nlu
from agenthan.voice.nlu.prefetch import PrefetchNlu
from agenthan.voice.store_config import StoreConfig

log = logging.getLogger(__name__)

__all__ = ["CUSTOMER_FATAL", "CUSTOMER_RETRY", "CallSession", "SessionSettings", "make_nlu"]

NluChoice = Literal["fake", "hcx"]
Phase = Literal["idle", "listening", "thinking", "speaking", "ended"]
# 턴 모델(Smart Turn)이 보는 최근 오디오: 8초 = 256,000바이트 (원본 입력 규칙)
TURN_AUDIO_BYTES = 8_000 * BYTES_PER_MS
# 턴 모델 전용 스레드 수. 프로세스에 풀 하나, 세션(ONNX) 하나를 모든 통화가 같이 쓴다.
# 2 vCPU 서버라 2개(연구 노트 Q11-3). 엔진 풀은 HCX를 기다리는 스레드로 찰 수 있어 따로 둔다
TURN_THREADS = 2
TURN_THREAD_PREFIX = "phone-turn"
_turn_pool: ThreadPoolExecutor | None = None


def turn_pool() -> ThreadPoolExecutor:
    """프로세스의 턴 모델 풀 하나 (처음 부를 때 만든다). 라우터와 세션 기본값이 같이 쓴다 (C8)."""
    global _turn_pool
    if _turn_pool is None:
        _turn_pool = ThreadPoolExecutor(TURN_THREADS, thread_name_prefix=TURN_THREAD_PREFIX)
    return _turn_pool


@dataclass(frozen=True)
class SessionSettings:
    vad: VadSettings = field(default_factory=VadSettings)
    # 엔진 시간 제한. HCX 한 번(10초) + 재시도 한 번까지. 손님이 더 오래 말없이 기다리지 않게
    # (리뷰 15번. 전에는 HCX 최악을 다 기다리는 40초)
    turn_timeout_s: float = 20.0
    tts_concurrency: int = 2
    sentence_limit: int = TTS_LIMIT
    tts_trim: TrimSettings = field(default_factory=TrimSettings)  # 첫 문장 앞 무음 자르기
    chunk_bytes: int = CHUNK_BYTES  # 내리는 오디오 조각 ≈ 200ms
    stt_backlog_ms: int = 3_000  # STT 쓰기가 이만큼 밀리면 통화를 끝낸다 (stt_backlog)
    stt_batch_ms: int = 1_000  # 밀린 프레임을 DATA 하나로 묶는 최대 길이
    # 붙잡은 뒤 새 결과 없이 이만큼 지나면 턴을 넘긴다 (hold_timeout). STT 결과 대기라서
    # "epFlag 송신 → 글 있는 응답"의 p99로 정한다(Pipecat `ttfs_p99`, 3단계 티켓 01).
    # 임시값 1.8초: 2단계 가짜 휴대폰 실측(2026-11-08 net59k, 녹음 17개 중 무음 감지로 끝난 턴
    # 8개)은 p50 0.56 / p90 1.05 / 최대 1.78초였다. 8개로는 p99를 못 내서 최대값을 덮게 잡았다.
    # 회선 병목이던 실행(`NETWORK-BOUND-invalid`)은 뺐다. 다시 잴 때는 `turn_summary`의
    # `stt_wait_ms`(턴 기록의 `ep_sent` → `stt_final`)를 30턴 이상 모아 p99로 바꾼다.
    # 전 값 1.2초는 짐작이었다
    hold_max_s: float = 1.8
    hold_cap_s: float = 3.0  # 이어진 말이 열려 있어도 마지막 글부터 붙잡기는 여기까지 (리뷰 C4)
    # 말 끝 판정 (3단계 티켓 02). 이어짐이면 마지막 말 끝(마지막 유성 프레임 도착)부터 이만큼까지
    # 기다린다. 말이 다시 시작되면 다음 말 끝부터 다시 센다 (grilling Q3, `PHONE_CONT_WAIT_S`)
    cont_wait_s: float = CONT_WAIT_S
    # 이어짐 대기 전체 상한(대기 중 마지막 글부터, 리뷰 P3). 글 없는 배경 소리가 "말하는 중"을
    # 안 풀어도 여기서 넘긴다. 글이 계속 들어오는 긴 주문은 쪼개지 않는다
    cont_max_s: float = 8.0
    # 미리 해석(이어짐 대기 중 NLU 선호출)의 시간 제한. 넘으면 버리고 엔진이 새로 부른다.
    # HCX p95 1.14초(1단계 티켓 07)에 여유를 두고, 새로 불러도 `turn_timeout_s` 안에 끝나게
    prefetch_timeout_s: float = 5.0
    turn_model_low: float = MODEL_LOW  # Smart Turn 점수 대역 (티켓 03이 점수를 넣는다)
    turn_model_high: float = MODEL_HIGH
    # 말 끝(무음 400ms) 전에 온 epFlag 응답을 말 끝에서 넘길 때 턴 모델 점수를 이만큼까지 기다린다
    # (리뷰 C5). 점수 작업은 같은 순간에 내서 그냥 넘기면 늘 `late`다. 맥 1스레드 약 50ms,
    # 연구 노트 Q11 제안 150ms
    turn_score_wait_s: float = 0.15
    # `playback.done`이 안 와도(AudioContext 멈춤 등) 보낸 소리 길이 + 이만큼 뒤 듣기로 (리뷰 4번)
    play_done_grace_s: float = 3.0
    # 말 끝 + 이만큼까지 봇 목소리가 없으면 `bot.thinking`(생각 중 효과음). PRD 1.5초 (grilling Q13)
    thinking_after_s: float = 1.5
    # 무응답 (3단계 티켓 04, grilling Q6). 봇 재생 끝(`playback.done`·말 끊기)부터 이만큼
    # 말이 없으면 엔진 무응답 진입점이 되묻는다(`PHONE_NO_INPUT_S`). AiCall 5초·Lex 4초보다
    # 조금 길게: 주문 중에는 메뉴를 고르며 생각한다 (`research/s3-turn-end.md` 결정 6)
    no_input_s: float = NO_INPUT_S
    wait_request_s: float = WAIT_REQUEST_S  # "잠깐만요" 뒤 첫 대기 (`PHONE_WAIT_REQUEST_S`, 결정 5)
    # 글 없는 말소리(배경 소리)가 마감을 계속 밀어도 처음 마감 + 이만큼에는 되묻는다 (검증 결정 6)
    no_input_slack_s: float = NO_INPUT_SLACK_S
    # 상한에서도 손님이 말하는 중이면 미룬다(리뷰 P4). 다만 쉼 없는 말 구간이 이만큼을 넘으면
    # 끊기지 않는 배경 소리로 보고 되묻는다(감사 2번). 안 그러면 소음이 `max_call_s`까지 막는다
    no_input_speech_max_s: float = NO_INPUT_SPEECH_MAX_S
    # 사장님 질문 답 대기 (4단계 티켓 06). 질문 카드를 보낸 때부터 센다. 넘으면 "확인해서 문자로
    # 알려 드릴게요" (`PHONE_OWNER_WAIT_S`, 허브 카드 마감과 같은 값)
    owner_wait_s: float = OWNER_WAIT_S
    # 말소리가 이만큼 이상인데 글 없는 epFlag 응답이면 "잘 못 들었어요" (결정 8. 기침 거르기)
    not_heard_min_ms: int = 500
    # 빈 epFlag 응답 뒤 이만큼 글 있는 결과가 없을 때 되묻는다. CLOVA는 글을 gap 결과로 빈 epFlag
    # 응답보다 늦게 낼 수 있다(티켓 10 조각 붙잡기 시험). 그 글이 오면 되묻지 않는다
    not_heard_wait_s: float = 1.0
    # 음성 끼어들기 (티켓 06, `PHONE_VOICE_BARGE_IN`). None이면 꺼짐(반이중 + 버튼). 켜도 브라우저가
    # `call.start.voice_barge_in`(안드로이드 크롬)을 알린 통화만 쓴다
    barge: BargeSettings | None = None
    start_timeout_s: float = 10.0  # 소켓만 열고 `call.start`를 안 보내면 닫는다 (리뷰 9·17번)
    max_call_s: float = 600.0  # 통화 하나의 최대 길이 (턴과 무관). 넘으면 call_too_long


def make_nlu(choice: NluChoice) -> Nlu:
    """설정값 → NLU. `hcx`는 키를 `.env`·환경 변수에서 읽는다 (없으면 `PortError`)."""
    if choice == "fake":
        from agenthan.voice.nlu.fake import FakeNlu

        return FakeNlu()
    if choice == "hcx":
        from agenthan.voice.nlu.hcx import HcxNlu

        try:
            return HcxNlu.from_env()
        except ValueError as err:
            raise PortError(str(err), code="nlu_config", retry=False) from err
    raise PortError(f"모르는 NLU: {choice!r}", code="nlu_config", retry=False)


@dataclass(frozen=True)
class _Utterance:
    """손님 말 하나 (STT 조각을 합친 것)."""

    text: str
    fragments: int  # 합친 STT 결과 수. 2 이상이면 쪼개졌던 것
    epd_type: str
    end_by: str  # vad · button · gap · … 무엇으로 끝났나
    stt_final: float  # 마지막 조각 도착 (단조 초)
    speech_end: float | None = None
    ep_sent: float | None = None
    button: float | None = None
    utterances: int = 1  # 엔진 도는 중에 와서 합친 말 수
    # 봇이 말하는 동안 모아 둔 말(`_pending`)로 연다. 말 끝이 봇 재생 전이라 말 끝 기준 지연을
    # 재지 않는다(3단계 티켓 01). 합치면 마지막 말을 따른다(뒤에 새로 한 말이면 다시 잰다)
    queued: bool = False
    wait_request: bool = False  # 끝말 규칙이 기다림 요청("잠깐만요")으로 봤다 (티켓 04)
    start_ms: int | None = None  # 첫 조각을 말하기 시작한 오디오 시각 (복창 앞 "네" 거르기, P1)

    @staticmethod
    def merge(items: list[_Utterance]) -> _Utterance:
        last = items[-1]
        return _Utterance(
            text=" ".join(u.text for u in items),
            fragments=sum(u.fragments for u in items),
            epd_type=last.epd_type,
            end_by=last.end_by,
            stt_final=last.stt_final,
            speech_end=last.speech_end,
            ep_sent=last.ep_sent,
            button=last.button,
            utterances=sum(u.utterances for u in items),
            queued=last.queued,
            start_ms=items[0].start_ms,
        )


@dataclass(frozen=True)
class _BotTurn:
    """봇이 먼저 말하는 턴 (무음 되묻기·기다림 요청 응답 등, 3단계 티켓 04·05가 쓴다)."""

    reason: str  # 턴 기록 `bot_turn`에 남는다
    act: Callable[[DialogEngine, datetime], TurnResult]  # 엔진 스레드에서 부른다


class _EngineTimeout(Exception):
    pass


class CallSession:
    def __init__(
        self,
        *,
        store: StoreKnowledge,
        config: StoreConfig,
        nlu: Nlu | NluChoice,
        stt: SttStream,
        tts: Tts,
        clock: Clock,
        outbound: Outbound,
        order_sink: OrderSink,
        owner: OwnerChannel | None = None,
        log_dir: Path | None = Path("logs"),
        call_id: str | None = None,
        settings: SessionSettings | None = None,
        executor: Executor | None = None,
        turn_model: TurnModel | None = None,
        turn_executor: Executor | None = None,
    ) -> None:
        self.call_id = call_id or f"call-{uuid.uuid4().hex[:12]}"
        self._store = store
        self._config = config
        self._nlu = nlu
        self._stt = stt
        self._tts = tts
        self._clock = clock
        self._outbound = outbound
        self._sink = order_sink
        self._owner: OwnerChannel = owner or NullOwnerChannel()  # 사장님 쪽 알림 (4단계 티켓 02)
        # 사장님 쪽이 붙은 통화만 근거 없는 메뉴 질문을 사장님께 묻는다 (4단계 티켓 05)
        self._asks_owner = owner is not None
        self._settings = settings or SessionSettings()
        self._executor = executor  # None이면 루프 기본 풀
        # 턴 모델(티켓 03). 없으면(꺼짐·모델 파일 없음) 링 버퍼도 만들지 않고 규칙만 쓴다
        self._turn_model = turn_model
        self._turn_executor = turn_executor
        self._ring: bytearray | None = bytearray() if turn_model is not None else None
        self._score: Future[tuple[float, float]] | None = None  # 마지막 말 끝의 점수 작업
        self._model_info: dict[str, Any] | None = None  # 마지막 판정에 쓴 점수의 출처
        self._log = LatencyLog(log_dir, self.call_id, clock)
        self._vad = SilenceDetector(self._settings.vad)

        self._out: asyncio.Queue[tuple[str, Any, Callable[[], None] | None] | None] = (
            asyncio.Queue()
        )
        self._turns: asyncio.Queue[tuple[int, _Utterance | _BotTurn]] = asyncio.Queue()
        self._stt_q: asyncio.Queue[tuple[bytes, bool, int]] = asyncio.Queue()
        self._stt_backlog = 0  # 큐에 있거나 쓰는 중인 바이트
        self._done = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._writer: asyncio.Task[None] | None = None

        self._engine: DialogEngine | None = None
        self._prefetch: PrefetchNlu | None = None  # 엔진에 넣은 NLU (미리 해석 래퍼)
        self._last: TurnResult | None = None
        self._phase: Phase = "idle"
        self._turn = 0
        self._ready = False  # STT가 연결됐다. 그 전 오디오는 버린다
        self._terminal = False  # 확정·handoff·취소로 대화가 끝났다. 재생이 끝나면 통화 끝
        self._last_seq: dict[int, int] = {}  # 턴 → 마지막 문장 seq
        self._parts: list[SttResult] = []
        self._held: SttResult | None = None  # 붙잡은 끝 후보 결과 (뒤에 말이 이어짐)
        self._hold_text_at: float | None = None  # 마지막으로 글을 붙잡은 때 (`hold_cap_s` 기준)
        self._hold_timer = Deadline(self._on_hold_timer)
        self._score_timer = OneShot(self._release_held_reply)  # 말 끝에서 점수 기다리기 (C5)
        self._pending: list[_Utterance] = []
        # 판정 중 국면 (티켓 02). 국면 값은 listening 그대로 두고(화면은 듣는 중) 이것으로 가른다
        self._cont: _Utterance | None = None  # 이어짐 판정으로 기다리는 말
        self._cont_decision: TurnDecision | None = None
        self._cont_since: float | None = None  # 처음 이어짐 판정을 낸 때 (대기 시간 기록)
        # 이어짐 대기 중 마지막으로 글이 들어온 때. 전체 상한(`cont_max_s`)은 여기부터 센다(리뷰 P3)
        self._cont_text_at: float | None = None
        self._cont_timer = Deadline(self._on_cont_timer)  # 붙잡기 타이머와 따로 둔다
        self._steps: list[dict[str, Any]] = []  # 이 말의 앞선 판정들 (턴 기록 `turn_end_steps`)
        self._commit = False  # "말 끝" 버튼: 다음 DATA에 epFlag
        self._forced = False  # "말 끝" 버튼: 다음 말 끝 결과는 붙잡지 않는다
        self._button_at: float | None = None
        # 마지막 말소리 프레임이 온 때 = 마지막 말 끝. 이어짐 마감·무음 대기·지연 기록이 같이 쓴다
        self._last_voice_at: float | None = None
        self._speech_from: float | None = None  # 지금 말 구간이 시작된 때 (무응답 소음 거르기)
        self._ep_at: float | None = None  # 마지막 epFlag 프레임을 쓰기 큐에 넣은 때
        self._ep_reply_at: float | None = None  # 마지막 epFlag 응답을 받은 때
        self._speak_from_ms = 0  # 이번 봇 소리를 보내기 시작한 오디오 시각 (반이중 기준)
        self._client_start: dict[str, Any] = {}
        self._bot_done_turn: int | None = None  # `bot.done`까지 보낸 마지막 턴
        self._play_timer = OneShot(self._on_play_timeout)
        self._thinking_timer = OneShot(self._on_thinking)
        self._speaking: asyncio.Task[int] | None = None  # 지금 문장을 보내는 작업 (말 끊기가 취소)
        self._spoken: SpokenTurn | None = None  # 마지막으로 말한 턴 (들은 만큼·복창 금액 끝)
        # 무응답 타이머 (티켓 04). 몇 번째 무응답인지는 엔진이 센다. 세션은 시간만 잰다
        self._quiet_timer = Deadline(self._on_quiet_timer)
        self._quiet_since: float | None = None  # 이번 무음 대기를 시작한 때 (재생 끝·끊김)
        self._quiet_delay = 0.0
        self._quiet_turn: int | None = None  # 그 대기를 연 봇 턴
        # 지난 손님 턴이 "잠깐만요"였다. 다음 무음 대기는 `wait_request_s`
        self._wait_requested = False
        self._voiced_ms = 0  # 이번 말 구간의 말소리 프레임 길이
        self._ep_voiced_ms = 0  # 마지막 epFlag 프레임까지의 말소리 길이 (빈 응답 되묻기 기준)
        self._end_reason: str | None = None  # 대화가 끝난 이유 (`call.ended`)
        self._ended_by: str | None = None  # 통화가 끝난 이유 (`_end`, 끝내는 길 전부)
        self._not_heard_timer = OneShot(self._on_not_heard_timer)
        # 음성 끼어들기 (티켓 06). 설정이 켜져 있고 브라우저가 할 수 있다고 알린 통화만 만든다
        self._barge: BargeTracker | None = None
        self._windows: list[BotWindow] = []  # 최근 봇 말 구간들 (늦게 온 글을 말한 시각으로 가른다)
        self._cut_ep = False  # 봇 재생 끝 경계: 다음 DATA에 epFlag (`_close_window`)
        self._stop_sent_at: float | None = None  # 마지막 `bot.stop`을 보낸 때 (멈춤 지연 T7)
        self._listen_rms: dict[str, list[float]] = {"voiced": [], "quiet": []}
        self._broken = False
        self._report_due: int | None = None  # 끊겨서 들은 만큼 보고를 기다리는 턴 (리뷰 C2)
        # 사장님께 묻기 (4단계 티켓 05·06). 엔진 턴 결과로 안 열린 질문 번호와, 받았지만 아직 말하지
        # 못한 사장님 쪽 알림들(답·열람, 받은 때와 함께. 말해도 될 때 봇 턴으로 넣는다)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._owner_open: int | None = None
        self._owner_inbox: list[tuple[OwnerNote, float]] = []
        self._owner_opened_at: float | None = None  # 질문 카드를 보낸 때 (시간 초과 기준)
        self._owner_seen_at: float | None = None  # 사장님이 그 카드를 처음 본 때
        self._owner_seen_said = False  # "사장님이 보고 계세요"를 이 질문에 이미 말했다
        self._owner_timer = OneShot(self._on_owner_timeout)  # 답 대기 시간 초과
        self._owner_retry = OneShot(self._say_owner_reply)  # 손님이 말하는 중이라 미룬 답 다시 보기

    # ------------------------------------------------------------ 바깥 진입점

    async def open(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._writer is None:
            self._writer = asyncio.create_task(self._write_loop(), name=f"{self.call_id}-writer")
            self._spawn(self._watch(), "watch")

    @property
    def done(self) -> bool:
        return self._done.is_set()

    async def wait_done(self) -> None:
        await self._done.wait()

    async def feed_audio(self, pcm: bytes) -> None:
        """올라온 PCM16LE 16kHz mono 프레임 하나 (헤더 없음)."""
        if not self._ready or self.done:
            return
        was_speaking = self._vad.in_speech
        frame_from = self._vad.offset_ms
        ended = self._vad.feed(pcm)
        now = self._clock.monotonic()
        started = self._vad.in_speech and not was_speaking
        if started:
            self._speech_from = now
        if self._ring is not None:
            self._keep_turn_audio(pcm, started=started)
        if self._vad.last_voiced:
            self._last_voice_at = now
            self._voiced_ms += len(pcm) // BYTES_PER_MS
        # 조기 epFlag: 무음 250ms (턴 판정은 `ended`). `_cut_ep`는 봇 재생 끝 경계(티켓 06)
        ep = self._vad.ep_due or self._commit or self._cut_ep
        if ep:
            if self._commit:
                self._vad.end_speech()
            self._commit = self._cut_ep = False
            self._ep_at = now
            self._ep_voiced_ms, self._voiced_ms = self._voiced_ms, 0
        elif not self._vad.in_speech and not self._vad.last_voiced:
            self._voiced_ms = 0  # 말 구간 밖 무음: 짧은 잡음은 쌓지 않는다
        self._stt_q.put_nowait((pcm, ep, self._turn + 1))
        if self._barge is not None:
            if self._phase == "speaking" and self._windows:
                self._barge_frame(frame_from)
            elif self._vad.last_voiced or not self._vad.in_speech:
                # 듣는 동안의 손님 말소리·조용할 때 RMS (실기기 시험에서 문턱을 정하는 근거)
                key = "voiced" if self._vad.last_voiced else "quiet"
                self._listen_rms[key].append(self._vad.last_rms)
        if ended:
            self._score_turn()
            self._on_speech_end()
        self._stt_backlog += len(pcm)
        backlog_ms = self._stt_backlog // BYTES_PER_MS
        record = self._log.get(self._turn)
        if record is not None and backlog_ms > record.fields.get("stt_backlog_max", 0):
            record.fields["stt_backlog_max"] = backlog_ms
        if backlog_ms >= self._settings.stt_backlog_ms:
            self._fail(PortError(f"STT 쓰기가 {backlog_ms}ms 밀림", code="stt_backlog",
                                 customer="회선이 느려 통화를 이어 갈 수 없어요. "
                                          "잠시 후 다시 걸어 주세요."), "stt")
            return
        await asyncio.sleep(0)  # 쓰기 작업에 차례를 준다. STT를 기다리지는 않는다

    def bot_turn(
        self, reason: str, act: Callable[[DialogEngine, datetime], TurnResult]
    ) -> int | None:
        """봇이 먼저 말하는 턴을 새 턴 번호로 연다. 듣는 중일 때만 받는다. 연 턴 번호를 돌려준다.

        `act(engine, now)`가 엔진에서 할 말을 정한다(예: 무응답 진입점). 이 턴도 손님 턴처럼
        국면·턴 번호·지난 오디오 버리기·반이중·재생 끝 처리를 그대로 탄다. 생각 중 효과음은 없다.
        손님 말을 붙잡는 중(조각·말 끝 표시)이면 받지 않는다. 그 말이 먼저다
        """
        if (self.done or self._terminal or self._phase != "listening" or self._parts
                or self._pending or self._cont is not None):
            return None
        self._quiet_timer.cancel()
        if self._ring is not None:
            self._ring.clear()  # 되묻기 앞의 글 없는 소리는 다음 손님 말 점수에 넣지 않는다
            self._drop_score()
        self._turn += 1
        turn = self._turn
        self._phase = "thinking"
        self._flush_before(turn)
        self._log.begin(turn, text="", bot_turn=reason)
        self._emit_state()
        self._turns.put_nowait((turn, _BotTurn(reason, act)))
        return turn

    async def drain_audio(self) -> None:
        """받은 오디오를 STT에 다 쓸 때까지(또는 통화가 끝날 때까지) 기다린다. 테스트용."""
        joined = asyncio.ensure_future(self._stt_q.join())
        ended = asyncio.ensure_future(self._done.wait())
        try:
            await asyncio.wait({joined, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            joined.cancel()
            ended.cancel()

    async def feed_event(self, event: dict[str, Any]) -> None:
        """올라온 JSON 이벤트 하나 (dict로 푼 것)."""
        kind = event.get("type")
        turn = event.get("turn", self._turn)
        seq = event.get("seq")
        if not _is_int(turn) or not (seq is None or _is_int(seq)):
            log.info("값이 틀린 %s 이벤트를 버림 (%s)", kind, self.call_id)  # 리뷰 10번
            return
        if kind == "call.start":
            if self._phase == "idle" and not self.done:
                self._client_start = {k: v for k, v in event.items() if k not in ("type", "turn")}
                barge = self._settings.barge
                if barge is not None and event.get("voice_barge_in") is True:
                    self._barge = BargeTracker(barge)  # 브라우저가 봇 말 중에도 진짜 소리를 보낸다
                self._phase = "thinking"
                self._spawn(self._turn_loop(), "turns")
        elif kind == "user.end_of_speech":
            if self._cont is not None and not (self._vad.in_speech or self._parts
                                               or self._ep_waiting()):
                self._finish_waiting("button")  # 이어짐 대기 중 누름: 기다린 말로 끝낸다
            # 이어서 한 말의 글을 아직 기다리면(리뷰 C1) 아래처럼 그 글로 끝낸다(기다린 말과 합침)
            elif self._phase in ("listening", "thinking"):
                self._commit = True
                self._forced = True
                self._button_at = self._clock.monotonic()
        elif kind == "playback.started":
            self._log.mark(turn, "play_start")
            spoken = self._spoken
            if spoken is not None and spoken.turn == turn and seq is not None:
                spoken.marks.setdefault(seq, self._vad.offset_ms)  # 말한 시각 기준 (티켓 06)
        elif kind == "playback.done":
            self._on_playback_done(turn, seq, event.get("output_latency"))
        elif kind == "user.interrupt":  # 말 끊기 버튼 (티켓 05)
            self._interrupt(turn, "button")
        elif kind == "playback.interrupted":
            self._on_playback_interrupted(turn, seq, event.get("played_ms"))
        elif kind == "client.metrics":
            record = self._log.get(turn)
            if record is not None:
                record.client.update({k: v for k, v in event.items() if k not in ("type", "turn")})
        elif kind == "call.end":  # 손님이 끊었다
            self._end("hangup")

    async def close(self) -> None:
        self._end("hangup")  # 아직 안 끝났으면 손님 쪽이 먼저 사라진 것이다(소켓 끊김·서버 종료)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        try:
            await self._stt.close()
        except Exception:
            log.exception("STT 닫기 실패 (%s)", self.call_id)
        self._flush_log()
        if self._writer is not None:
            self._out.put_nowait(None)
            try:
                await asyncio.wait_for(self._writer, 2.0)
            except (TimeoutError, asyncio.CancelledError):
                self._writer.cancel()

    # ------------------------------------------------------------ 작업들

    def _spawn(self, coro: Any, name: str) -> None:
        self._tasks.append(asyncio.create_task(coro, name=f"{self.call_id}-{name}"))

    async def _watch(self) -> None:
        """`call.start` 대기 시간과 통화 전체 길이를 지킨다."""
        settings = self._settings
        first = min(settings.start_timeout_s, settings.max_call_s)
        await asyncio.sleep(first)
        if self._phase == "idle" and settings.start_timeout_s <= settings.max_call_s:
            self._fail(PortError("call.start가 오지 않음", code="start_timeout"), "call")
            return
        await asyncio.sleep(settings.max_call_s - first)
        self._fail(PortError(f"통화가 {settings.max_call_s:.0f}초를 넘음", code="call_too_long",
                             customer="통화가 너무 길어져서 끊을게요. 다시 걸어 주세요."), "call")

    async def _write_loop(self) -> None:
        while True:
            item = await self._out.get()
            if item is None:
                return
            kind, payload, on_sent = item
            try:
                if kind == "event":
                    await self._outbound.send_event(payload)
                else:
                    await self._outbound.send_audio(payload)
            except ClientGone as err:  # 손님이 끊었다. 보통 일이라 한 줄만 (감사 문제 B)
                log.info("손님 연결이 끊겨 송신을 멈추고 통화를 끝냄 (%s): %s", self.call_id, err)
                self._broken = True
                self._end("hangup")
                return
            except Exception:
                log.info("송신 실패, 통화를 끝냄 (%s)", self.call_id, exc_info=True)
                self._broken = True
                self._end("error")
                return
            if on_sent is not None:
                on_sent()

    async def _turn_loop(self) -> None:
        try:
            await self._setup()
            await self._run_turn(0, None)
            while not self.done:
                turn, utterance = await self._turns.get()
                await self._run_turn(turn, utterance)
        except asyncio.CancelledError:
            raise
        except _EngineTimeout:
            limit = self._settings.turn_timeout_s
            self._fail(PortError(f"엔진이 {limit:.0f}초 안에 답하지 않음", code="engine_timeout"),
                       "engine")
        except Exception as err:
            self._fail(err, "engine")

    async def _setup(self) -> None:
        nlu = self._nlu
        if isinstance(nlu, str):
            nlu = await self._in_thread(make_nlu, nlu)  # hcx는 .env를 읽는다
        self._prefetch = PrefetchNlu(nlu, timeout_s=self._settings.prefetch_timeout_s,
                                     clock=self._clock.monotonic)
        # list_menu()가 동기라 엔진 생성도 스레드에서 (verify-web D5 조건 2)
        self._engine = await self._in_thread(
            DialogEngine, self._store, self._prefetch, self._config, order_id=self.call_id,
            ask_owner=self._asks_owner,
        )
        await self._stt.start()
        self._spawn(self._read_stt(), "stt")
        self._spawn(self._write_stt(), "stt-write")
        self._ready = True
        ready: dict[str, Any] = {"type": "call.ready", "turn": 0, "call": self.call_id,
                                 "sample_rate": SAMPLE_RATE}
        if self._barge is not None:  # 꺼져 있으면 2단계와 같은 모양 (필드 없음)
            ready["voice_barge_in"] = self._barge.mode
        self._emit(ready)
        self._tell_owner(lambda owner: owner.call_started(self.call_id))

    async def _write_stt(self) -> None:
        """STT 쓰기 주체는 이 작업 하나다. `close()`가 이 작업을 취소한 뒤 STT를 닫는다."""
        batch_limit = self._settings.stt_batch_ms * BYTES_PER_MS
        while True:
            pcm, ep, seq = await self._stt_q.get()
            parts, taken = [pcm], 1
            size = len(pcm)
            while not ep and size < batch_limit and not self._stt_q.empty():
                pcm, ep, seq = self._stt_q.get_nowait()  # epFlag 프레임에서 멈춘다
                parts.append(pcm)
                taken += 1
                size += len(pcm)
            try:
                await self._stt.write(b"".join(parts), ep_flag=ep, seq_id=seq)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._fail(err, "stt")
                return
            finally:
                self._stt_backlog -= size
                for _ in range(taken):
                    self._stt_q.task_done()

    async def _read_stt(self) -> None:
        try:
            async for result in self._stt.results():
                self._on_result(result)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._fail(err, "stt")

    # ------------------------------------------------------------ STT 결과 → 턴

    def _on_result(self, r: SttResult) -> None:
        if r.ep_flag:
            self._ep_reply_at = self._clock.monotonic()
        if self._barge is not None:  # 음성 끼어들기: 봇 말 중에 한 말은 판정한다 (티켓 06)
            if self.done or self._terminal:
                self._log_dropped(r)
                return
            window = self._window_of(r)
            if window is not None and not self._barge_take(window, r):
                return
        elif self.done or self._terminal or self._spoken_over(r):
            self._log_dropped(r)  # 반이중: 봇 소리가 나간 뒤에 한 말의 결과는 버린다
            return
        self._hold_timer.cancel()
        if r.text.strip():
            self._parts.append(r)
            if self._held is not None:
                self._hold_text_at = self._clock.monotonic()  # 말이 이어진다. 상한을 다시 센다
        if not r.ends_speech:
            if self._parts and self._held is not None:
                self._hold_timer.arm(self._settings.hold_max_s)  # 아직 끝 후보가 아니다
            return
        if not self._parts:
            if r.ep_flag:
                if not self._forced and r.epd_type != "eof" and not self._vad.in_speech:
                    self._log_empty(r)
                    self._not_heard()
                self._forced = False  # 말 없이 누른 버튼
            return
        forced = self._forced
        if not forced and self._continues_after(r):
            # 말이 이어진다. 다음 결과(보통 epFlag 응답)와 합친다. 안 오면 시간 제한으로 푼다
            self._held = r
            self._hold_text_at = self._clock.monotonic()
            self._hold_timer.arm(self._settings.hold_max_s)
            return
        self._release(r, "button" if forced else ("vad" if r.ep_flag else r.epd_type))

    def _spoken_over(self, r: SttResult) -> bool:
        """봇 소리가 나간 뒤에 시작한 말의 결과인가 (반이중으로 버린다).

        도착 시각이 아니라 말한 시각(오디오 시각)으로 본다. 첫 소리 전에 한 말은 결과가 봇 소리
        뒤에 와도 다음 턴으로 받는다(3단계 티켓 01). 그 전 오디오에는 봇 목소리가 섞일 수 없다
        """
        return self._phase == "speaking" and r.start_ms >= self._speak_from_ms

    def _continues_after(self, r: SttResult) -> bool:
        """말 끝 후보 결과 `r` 뒤에 손님 말이 이어지나 (붙잡을지).

        - 결과 끝 뒤에 새로 시작한 말 구간이 있다(spec 조각 합치기). 어느 결과든 본다
        - (티켓 10) epFlag 응답이 아닌 결과(gap 등)는 다음 둘도 본다. CLOVA는 gapThreshold(700ms)
          보다 짧은 쉼(실측 90ms)에서도 gap 조각을 내므로, gap이 왔다고 말이 끝난 것이 아니다
          - 무음 감지가 아직 말하는 중이다(A1: 조각이 손님 말 중간에 왔다)
          - 써 둔 epFlag의 응답을 아직 못 받았다(C3: 말 끝을 잡은 뒤 앞 조각이 먼저 왔다.
            남은 글은 epFlag 응답에 온다)
          이 경우 턴은 epFlag 응답으로 넘긴다. 배경 소리가 임계값 위로 계속돼 "말하는 중"이
          풀리지 않아도 `hold_cap_s` 안에서 넘긴다(`_on_hold_timer`)
        """
        if self._vad.speech_since(r.end_ms):
            return True
        if r.ep_flag:
            # 조기 epFlag(무음 250ms)의 응답인데 무음이 아직 턴 판정(400ms)에 못 닿았거나 말이 다시
            # 시작됐다. 턴은 무음 감지가 말 끝을 볼 때(`_on_speech_end`)나 다음 응답으로 넘긴다
            return self._vad.in_speech
        return self._vad.in_speech or self._ep_waiting()

    def _on_speech_end(self) -> None:
        """무음이 턴 판정 길이(400ms)에 닿았다. 그 전에 와서 붙잡아 둔 epFlag 응답이면 넘긴다.

        방금 낸 턴 모델 점수가 아직이면 `turn_score_wait_s`까지 기다렸다 넘긴다(리뷰 C5)
        """
        if not self._held_reply_due():
            return
        score = self._score
        if score is not None and not score.done():
            loop = asyncio.get_running_loop()
            self._score_timer.arm(self._settings.turn_score_wait_s)
            score.add_done_callback(
                lambda _: loop.call_soon_threadsafe(self._on_score_ready, score))
            return
        self._release_held_reply()

    def _on_score_ready(self, score: Future[tuple[float, float]]) -> None:
        if self._score_timer.armed and score is self._score:
            self._score_timer.cancel()
            self._release_held_reply()

    def _held_reply_due(self) -> bool:
        """말 끝에 넘길 붙잡은 epFlag 응답이 있나."""
        held = self._held
        return (held is not None and held.ep_flag and bool(self._parts)
                and not self._ep_waiting() and not self._vad.in_speech)

    def _release_held_reply(self) -> None:
        if self.done or not self._held_reply_due():
            return  # 기다리는 사이 말이 다시 시작됐거나 다른 길로 넘어갔다
        held = self._held
        assert held is not None
        self._hold_timer.cancel()
        self._release(held, "vad")

    def _ep_waiting(self) -> bool:
        """마지막으로 쓴 epFlag 프레임의 응답을 아직 못 받았나."""
        if self._ep_at is None:
            return False
        return self._ep_reply_at is None or self._ep_reply_at < self._ep_at

    def _log_dropped(self, r: SttResult, verdict: Verdict | None = None) -> None:
        """버린 결과를 지금 턴 기록의 `dropped`에 남긴다 (티켓 10 #1).

        말 중간 조각으로 턴을 넘긴 뒤 뒷조각이 반이중에 걸려 사라지는 일("나폴" 잘림)을
        기록으로 확인하려고 둔다.
        `reason`: speaking(봇 말하는 중) · terminal(확정·handoff 뒤) · ended(통화 끝).
        음성 끼어들기면 `barge`에 판정(에코·맞장구 등, 티켓 06)을 붙인다
        """
        record = self._log.get(self._turn)
        if record is None:
            return
        reason = "ended" if self.done else ("terminal" if self._terminal else "speaking")
        entry = {
            "text": r.text, "epd_type": r.epd_type, "start_ms": r.start_ms, "end_ms": r.end_ms,
            "ep_flag": r.ep_flag, "reason": reason, "backchannel": is_backchannel(r.text),
        }
        if verdict is not None:
            entry["barge"] = verdict
        record.fields.setdefault("dropped", []).append(entry)

    def _log_empty(self, r: SttResult) -> None:
        """빈 턴: 무음 감지가 말로 봐서 epFlag를 보냈는데 글 없는 응답만 왔다 (지표).

        재연결 때 어댑터가 만든 빈 응답(`eof`)과 말 없이 누른 버튼은 세지 않는다.
        `voiced_ms`는 그 epFlag까지의 말소리 길이다. 길면 `_not_heard`가 되묻는다(티켓 04)
        """
        record = self._log.get(self._turn)
        if record is not None:
            record.fields.setdefault("empty", []).append({
                "start_ms": r.start_ms, "end_ms": r.end_ms, "epd_type": r.epd_type,
                "ep_flag": r.ep_flag, "voiced_ms": self._ep_voiced_ms,
            })

    def _not_heard(self) -> None:
        """말소리는 `not_heard_min_ms` 이상이었는데 글이 없다: 봇이 다시 말해 달라고 한다 (티켓 04).

        듣는 중이고 손님 말을 붙잡거나 판정하는 중이 아닐 때만(`bot_turn`). 실패로 세지 않는다
        (엔진 `not_heard`). 재연결 때 어댑터가 만든 `eof`와 말 없이 누른 버튼은 앞에서 걸렀다
        """
        voiced, self._ep_voiced_ms = self._ep_voiced_ms, 0
        if voiced >= self._settings.not_heard_min_ms:
            self._not_heard_timer.arm(self._settings.not_heard_wait_s)

    def _on_not_heard_timer(self) -> None:
        """그사이 글 있는 결과가 왔거나(붙잡는 중·턴이 열림) 다시 말하는 중이면 되묻지 않는다."""
        if self._vad.in_speech:
            return  # 새 말이 제 결과를 낸다
        self.bot_turn("not_heard", lambda engine, now: engine.not_heard(now))

    def _release(self, r: SttResult, end_by: str) -> None:
        """모아 둔 조각을 손님 말 하나로 만든다. 마지막 끝 후보 결과가 `r`이다."""
        forced = end_by == "button"
        self._forced = False
        utterance = _Utterance(
            text=" ".join(p.text.strip() for p in self._parts),
            fragments=len(self._parts),
            epd_type=r.epd_type,
            end_by=end_by,
            stt_final=self._clock.monotonic(),
            speech_end=self._last_voice_at,
            ep_sent=self._ep_at if r.ep_flag else None,
            button=self._button_at if forced else None,
            start_ms=self._parts[0].start_ms,
        )
        self._clear_hold()
        if self._phase == "listening":
            if not self._drop_early_yes(utterance):
                self._decide(utterance)
        else:
            self._pending.append(utterance)

    def _clear_hold(self) -> None:
        """붙잡아 둔 조각·시간 제한을 모두 지운다."""
        self._hold_timer.cancel()
        self._score_timer.cancel()
        self._parts = []
        self._held = None
        self._hold_text_at = None

    def _on_hold_timer(self) -> None:
        """붙잡기 시간 제한 (리뷰 2번). 붙잡은 뒤 `hold_max_s` 동안 새 결과가 없으면 푼다.

        다만 그때 이어진 말 구간이 아직 열려 있거나 그 구간의 `epFlag` 응답을 기다리는 중이면
        `hold_cap_s`까지 늘린다(마지막 글부터 센다, 리뷰 C4). 그래야 "메도빅 두 개… 랑 아메리카노
        하나요"처럼 긴 뒷말이 쪼개지지 않는다
        """
        held = self._held
        if held is None or not self._parts or self.done:
            return
        now = self._clock.monotonic()
        assert self._hold_text_at is not None
        settings = self._settings
        if self._hold_timer.wait(now, due=now, cap=self._hold_text_at + settings.hold_cap_s,
                                 busy=self._vad.in_speech or self._ep_waiting(),
                                 poll=settings.hold_max_s):
            return
        self._release(held, "hold_timeout")

    # ------------------------------------------------------------ 말 끝 판정 (티켓 02)

    def _decide(self, u: _Utterance) -> None:
        """손님 말 하나(조각을 합친 것)가 끝났나 본다. 이어짐 대기 중이면 기다리던 말과 합친다.

        끝남·기다림 요청이면 바로 턴을 넘기고, 이어짐이면 판정 중 국면에서 기다린다
        (`_wait_more`). 엔진(장바구니 변경)은 넘긴 뒤에만 돈다.
        """
        if self._cont is not None:
            u = _Utterance.merge([self._cont, u])
            self._cont_timer.cancel()
            self._cont = self._cont_decision = None
        decision = self._judge(u.text)
        if u.end_by == "button" and decision.label == "continue":
            decision = replace(decision, label="end", reason="button", path="button")
        if decision.label == "continue":
            self._wait_more(u, decision)
            return
        if decision.verdict.filler_only:  # 버튼으로 끝낸 필러만 있는 말
            self._drop_fillers(u, decision)
            return
        self._dispatch(u, decision)

    def _judge(self, text: str) -> TurnDecision:
        engine = self._engine
        assert engine is not None
        ctx = engine.nlu_context()  # 듣는 중이라 엔진은 쉬고 있다
        verdict = classify_turn_end(
            text, state=ctx.state, question_kind=question_kind(ctx.last_question, ctx.state),
            menu_names=ctx.menu_names, cont_wait_s=self._settings.cont_wait_s,
        )
        settings = self._settings
        score, self._model_info = self._take_score()  # 없으면(꺼짐·늦음·실패) 규칙만
        return combine_turn_end(verdict, score, low=settings.turn_model_low,
                                high=settings.turn_model_high, cont_wait_s=settings.cont_wait_s)

    # ------------------------------------------------------------ 턴 모델 (티켓 03)

    def _keep_turn_audio(self, pcm: bytes, *, started: bool) -> None:
        """최근 8초 링 버퍼에 프레임을 넣는다. 봇이 말하는 동안의 프레임(반이중 0)은 넣지 않는다.

        새 말 구간이 시작되면 앞 말 끝의 점수는 버린다(그 뒤 말 끝에서 다시 본다).
        """
        assert self._ring is not None
        if started:
            self._drop_score()
        if self._phase == "speaking":
            return
        self._ring += pcm
        if len(self._ring) > TURN_AUDIO_BYTES:
            del self._ring[: len(self._ring) - TURN_AUDIO_BYTES]

    def _score_turn(self) -> None:
        """말 끝(무음 400ms)에 한 번, 링 버퍼 사본으로 턴 모델을 돌린다. 결과는 판정 때 꺼낸다.

        무음이 더 길어졌다고 다시 돌리지 않는다(확률이 끝 무음 길이에 따라 오락가락한다,
        연구 노트 Q2). 이어짐 대기 중에는 링 버퍼를 비우지 않아 다음 말 끝에서 턴 전체를 본다.
        """
        model, ring = self._turn_model, self._ring
        if model is None or ring is None or self._phase == "speaking" or not ring:
            return
        executor = self._turn_executor or turn_pool()
        self._drop_score()
        self._score = executor.submit(_timed_score, model, bytes(ring))

    def _drop_score(self) -> None:
        """낡은 점수 작업을 버린다. 아직 줄을 서 있으면 취소해 공유 풀(다른 통화)을 비운다 (C7)."""
        if self._score is not None:
            self._score.cancel()
            self._score = None

    def _take_score(self) -> tuple[float | None, dict[str, Any] | None]:
        """판정에 쓸 점수. 기다리지 않는다: 말 끝(400ms)부터 STT 확정 글(말 끝 + 0.9~1.1초)까지가
        모델의 시간이다. 그때까지 안 끝났으면 `late`, 실패면 `error`로 남기고 규칙만 쓴다.
        """
        if self._turn_model is None:
            return None, None
        future, self._score = self._score, None  # 한 번 쓴 점수는 다른 글에 다시 쓰지 않는다
        if future is None:
            return None, {"src": "none"}  # 말 끝 없이 넘어온 말(버튼·붙잡기 시간 제한)
        if not future.done():
            future.cancel()  # 아직 줄을 서 있으면 공유 풀에서 뺀다 (리뷰 C7)
            return None, {"src": "late"}
        err = future.exception()
        if err is not None:
            log.warning("턴 모델 실패, 이 판정은 규칙만 씀 (%s): %r", self.call_id, err)
            return None, {"src": "error"}
        score, ms = future.result()
        return score, {"src": "model", "ms": round(ms, 1)}

    def _wait_more(self, u: _Utterance, decision: TurnDecision) -> None:
        self._steps.append({"text": u.text, **decision.as_record()})
        self._cont, self._cont_decision = u, decision
        self._cont_text_at = self._clock.monotonic()
        if self._cont_since is None:
            self._cont_since = self._cont_text_at
        self._prefetch_nlu(u.text, decision)
        self._on_cont_timer()

    def _prefetch_nlu(self, text: str, decision: TurnDecision) -> None:
        """기다리는 동안 붙잡은 글로 NLU를 미리 부른다(엔진은 확정 뒤에만 돈다).

        엔진이 NLU를 부르지 않을 글(규칙 단계에 걸림)과 필러만 있는 말은 부르지 않는다.
        """
        engine, prefetch = self._engine, self._prefetch
        if engine is None or prefetch is None or decision.verdict.filler_only:
            return
        if apply_rules(text) is not None:
            return
        prefetch.prefetch(text, engine.nlu_context(), self._submit)

    def _submit(self, fn: Callable[[], None]) -> None:
        self._in_thread(fn).add_done_callback(_retrieve)

    def _on_cont_timer(self) -> None:
        """이어짐 마감: 마지막 말 끝 + `cont_wait_s`. 말하는 중이거나 아직 STT 글을 기다리면 미룬다.

        조각 붙잡기(`_hold_timer`)와 다른 타이머라서 붙잡기가 이 마감을 덮어쓰지 않는다.
        이어진 말의 글을 붙잡는 중이면(조각 붙잡기가 `hold_cap_s` 안에 넘긴다) 미룬다. 전체 상한은
        마지막 글부터 센다. 글이 계속 들어오는 긴 주문은 쪼개지 않고, 글 없는 배경 소리가 "말하는
        중"을 붙잡을 때만 넘긴다 (리뷰 P3)
        """
        if self._cont is None or self.done or self._phase != "listening":
            return
        now = self._clock.monotonic()
        assert self._cont_text_at is not None and self._cont_decision is not None
        last_voice = self._last_voice_at if self._last_voice_at is not None else now
        if self._cont_timer.wait(now, due=last_voice + self._cont_decision.wait_s,
                                 cap=self._cont_text_at + self._settings.cont_max_s,
                                 busy=self._vad.in_speech or self._ep_waiting(),
                                 hold=bool(self._parts)):
            return
        self._finish_waiting("cap")

    def _finish_waiting(self, path: Literal["cap", "button"]) -> None:
        """이어짐 대기를 끝낸다. 말이 안 이어졌으니 기다린 말을 그대로 넘긴다."""
        u, decision = self._cont, self._cont_decision
        assert u is not None and decision is not None
        self._cont_timer.cancel()
        self._cont = self._cont_decision = None
        self._steps.pop()  # 마지막 이어짐 판정은 아래 판정의 근거로 남긴다
        decision = replace(decision, label="end", reason=f"{path}:{decision.reason}", path=path)
        if decision.verdict.filler_only:
            self._drop_fillers(u, decision)
            return
        self._dispatch(u, decision)

    def _drop_fillers(self, u: _Utterance, decision: TurnDecision) -> None:
        """필러만 있는 말("음…")은 엔진에 넘기지 않는다. 무응답처럼 다루고 실패로 세지 않는다.

        지금 턴 기록의 `fillers`에 남긴다.
        """
        steps, self._steps = self._steps, []
        self._cont_since = None
        record = self._log.get(self._turn)
        if record is not None:
            record.fields.setdefault("fillers", []).append(
                {"text": u.text, **decision.as_record(), "steps": steps}
            )

    def _clear_decision(self) -> None:
        self._cont_timer.cancel()
        self._cont = self._cont_decision = None
        self._cont_since = None
        self._steps = []

    def _dispatch(self, u: _Utterance, decision: TurnDecision | None = None) -> None:
        """손님 말 하나를 새 턴으로 넘긴다."""
        self._quiet_timer.cancel()
        self._not_heard_timer.cancel()
        if decision is not None and decision.label == "wait_request":
            u = replace(u, wait_request=True)  # 엔진이 "천천히 말씀하세요"를 하고 오래 기다린다
        self._wait_requested = u.wait_request
        self._turn += 1
        turn = self._turn
        self._phase = "thinking"
        self._flush_before(turn)
        fields: dict[str, Any] = {}
        if u.queued:
            fields["queued"] = True
        if self._barge is not None:  # 이 말을 듣는 동안의 RMS (티켓 06 실기기 시험)
            fields["listen_rms"] = {k: _rms_stats(v) for k, v in self._listen_rms.items()}
            self._listen_rms = {"voiced": [], "quiet": []}
        if decision is not None:
            fields["turn_end"] = decision.as_record()
            if self._model_info is not None:
                fields["turn_model"] = self._model_info
            if self._steps:
                fields["turn_end_steps"] = self._steps
            if self._cont_since is not None:
                fields["cont_wait_ms"] = round((self._clock.monotonic() - self._cont_since) * 1000)
        self._steps, self._cont_since = [], None
        self._model_info = None
        if self._ring is not None:
            self._ring.clear()  # 넘긴 말의 오디오는 다음 말 끝에 넣지 않는다
        self._log.begin(turn, text=u.text, epd_type=u.epd_type, fragments=u.fragments,
                        splits=u.fragments - 1, utterances=u.utterances, end_by=u.end_by,
                        **fields)
        for name, at in (("speech_end", u.speech_end), ("ep_sent", u.ep_sent),
                         ("button", u.button), ("stt_final", u.stt_final)):
            if at is not None:
                self._log.mark(turn, name, at=at)
        self._emit({"type": "stt.final", "turn": turn, "text": u.text, "epd_type": u.epd_type,
                    "fragments": u.fragments, "end_by": u.end_by})
        text = u.text
        self._tell_owner(lambda owner: owner.customer_said(self.call_id, text))
        self._emit_state()
        # 모아 둔 말은 말 끝이 봇 재생 전이라, 재생이 끝나 넘긴 지금부터 센다
        ref = None if u.queued else (u.speech_end or u.button)
        self._arm_thinking(turn, ref)
        self._turns.put_nowait((turn, u))

    # 생각 중 효과음 (3단계 티켓 01, grilling Q13). 말 끝 + `thinking_after_s`까지 봇 목소리가
    # 없으면 `bot.thinking`을 한 번 보낸다. 브라우저가 짧은 비음성 효과음을 낸다. 첫 오디오를
    # 보내거나(`_start_speaking`) 말 없이 턴이 끝나면 타이머를 끈다. 지표는 목소리(`audio_first`·
    # `play_start`)와 따로 `thinking` 표시로 남긴다

    def _arm_thinking(self, turn: int, ref: float | None) -> None:
        now = self._clock.monotonic()
        self._thinking_timer.arm((now if ref is None else ref) + self._settings.thinking_after_s
                                 - now, turn)

    def _on_thinking(self, turn: int) -> None:
        if self.done or turn != self._turn or self._phase != "thinking":
            return
        self._log.mark(turn, "thinking")
        self._emit({"type": "bot.thinking", "turn": turn})

    # ------------------------------------------------------------ 한 턴

    async def _run_turn(self, turn: int, u: _Utterance | _BotTurn | None) -> None:
        engine = self._engine
        assert engine is not None
        if u is None:  # 인사 턴. 마이크 실제 설정값(`call.start`)을 이 줄의 client에 남긴다
            self._log.begin(turn, text="").client.update(self._client_start)
            self._emit_state()
        now = self._clock.now()
        self._log.mark(turn, "engine_start")
        if u is None:
            result = await self._call_engine(lambda: engine.start(now))
        elif isinstance(u, _BotTurn):
            act = u.act
            result = await self._call_engine(lambda: act(engine, now))
        else:
            text, wait = u.text, u.wait_request
            result = await self._call_engine(lambda: engine.handle(text, now, wait_request=wait))
        self._log.mark(turn, "engine_end")
        stats = self._prefetch.take_stats() if self._prefetch is not None else None
        record = self._log.get(turn)
        if stats is not None and record is not None:
            record.fields["nlu_prefetch"] = stats
        self._last = result
        if result.state in TERMINAL:
            self._terminal = True
            self._end_reason = _end_reason(result, u)
        lines = result.cart.to_order_lines()
        self._tell_owner(lambda owner: owner.cart_changed(self.call_id, lines))
        if result.utterances:
            said = " ".join(result.utterances)
            self._tell_owner(lambda owner: owner.bot_said(self.call_id, said))
        if result.owner_question is not None:
            self._open_owner_question(turn, result.owner_question)
        if result.order is not None:
            self._sink.submit(result.order)
            self._emit({"type": "order", "turn": turn, "order": _order_json(result.order),
                        "cart": _cart_json(result.cart), "total": result.cart.total()})
        sentences = [
            s for text in result.utterances
            for s in split_sentences(text, self._settings.sentence_limit)
        ]
        audio_ms: int | None = 0
        if sentences:
            self._last_seq[turn] = len(sentences) - 1
            self._spoken = SpokenTurn(turn, sentences, readback=result.readback)
            audio_ms = await self._speak_until_cut(turn, sentences)  # 첫 소리를 보낼 때 speaking
        if self._turn == turn:  # 끊긴 뒤 벌써 다음 턴이 열렸으면 그 턴의 효과음 타이머다
            self._thinking_timer.cancel()  # 말할 문장이 없던 턴
        done: dict[str, Any] = {"type": "bot.done", "turn": turn, "sentences": len(sentences)}
        if audio_ms is None:  # 말 끊기: 보낸 문장 수만 센다
            done.update(sentences=len(self._spoken.ms) if self._spoken else 0, interrupted=True)
        self._emit(done)
        self._bot_done_turn = turn
        self._log.mark(turn, "bot_done")
        if not sentences:
            self._after_playback(turn)
        elif self._phase == "speaking" and audio_ms is not None:
            # 서버 안전망: 재생 끝 보고가 안 와도 소리 길이 + 여유 뒤에는 듣기로 (리뷰 4번)
            delay = audio_ms / 1000 + self._settings.play_done_grace_s
            self._play_timer.arm(delay, turn)

    def _start_speaking(self, turn: int) -> None:
        """첫 오디오를 보내기 직전. 여기서부터 "봇 말하는 중"이다 (3단계 티켓 01).

        전에는 엔진이 끝나자마자 speaking이 돼서 TTS 합성 중(첫 소리 전)에 한 말이 버려졌다.
        이 오디오 시각 뒤에 시작한 말만 반이중으로 버린다(`_spoken_over`).
        붙잡아 둔 조각은 첫 소리 전에 한 말이므로 버리지 않고 `_pending`(다음 턴)에 넣는다.
        다음 손님 말에 붙지는 않는다(리뷰 3번). 그 뒷글이 늦게 와도 `_pending`에서 합쳐진다
        """
        self._phase = "speaking"
        self._thinking_timer.cancel()
        self._speak_from_ms = self._vad.offset_ms
        if self._spoken is not None and self._spoken.turn == turn:
            self._spoken.from_ms = self._speak_from_ms
        if self._parts:
            self._release(self._held or self._parts[-1], "bot_start")
        self._clear_hold()
        # 판정 중인 말(티켓 02)도 첫 소리 전에 한 말이다. 지금은 늘 비어 있다(엔진은 판정 뒤에만
        # 돌고, 봇이 먼저 말하는 턴은 판정 중이면 열지 않는다). 있으면 다음 턴으로 넘긴다
        if self._cont is not None:
            self._pending.append(self._cont)
        self._clear_decision()
        self._forced = self._commit = False
        self._button_at = None
        if self._barge is not None:
            self._open_window(turn)
        self._emit_state()

    def _in_thread(
        self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> asyncio.Future[Any]:
        """동기 함수를 엔진 풀(없으면 루프 기본 풀)의 스레드에서 돌린다."""
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(self._executor, functools.partial(fn, *args, **kwargs))

    async def _call_engine(self, fn: Callable[[], TurnResult]) -> TurnResult:
        task = self._in_thread(fn)
        done, _ = await asyncio.wait({task}, timeout=self._settings.turn_timeout_s)
        if not done:
            task.add_done_callback(_retrieve)  # 나중에 예외로 끝나도 경고가 남지 않게 (리뷰 16번)
            raise _EngineTimeout  # 스레드는 계속 돈다. 이 엔진은 다시 부르지 않는다
        return task.result()

    async def _speak_until_cut(self, turn: int, sentences: list[str]) -> int | None:
        """`_speak`를 따로 작업으로 돌린다. 말 끊기가 그 작업을 취소하면 `None`.

        `_speak`를 턴 작업 안에서 그냥 await하면 끊긴 뒤에도 남은 문장 TTS가 끝날 때까지 다음 손님
        말의 엔진 호출이 밀리고 `bot.say` 자막이 계속 나간다(`s3-verify-barge-in` 4-3).
        턴 작업 자체를 취소하면 `_turn_loop`가 죽으니 안쪽 작업만 취소한다.
        """
        task = asyncio.create_task(self._speak(turn, sentences), name=f"{self.call_id}-speak")
        self._speaking = task
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            self._speaking = None
        if task.cancelled():
            return None
        return task.result()

    async def _speak(self, turn: int, sentences: list[str]) -> int:
        """문장마다 TTS를 동시에 `tts_concurrency`개까지 부르고, 보내는 건 문장 순서대로.

        보낸 소리 길이(ms)를 돌려준다.
        """
        gate = asyncio.Semaphore(self._settings.tts_concurrency)

        async def synth(seq: int, text: str) -> bytes:
            async with gate:
                if seq == 0:
                    self._log.mark(turn, "tts_start")
                try:
                    pcm = await self._tts.synth(text)
                except PortError:
                    raise
                except Exception as err:
                    log.exception("TTS 실패 (%s)", self.call_id)
                    raise PortError("잠시 후 다시 걸어 주세요.", code="tts") from err
                if seq == 0:
                    self._log.mark(turn, "tts_first")
                return pcm

        jobs = [asyncio.create_task(synth(i, s)) for i, s in enumerate(sentences)]
        audio_bytes = 0
        try:
            for seq, (job, text) in enumerate(zip(jobs, sentences, strict=True)):
                pcm = await job
                if seq == 0:
                    # 첫 문장 앞 무음은 손님이 기다리는 시간일 뿐이라 잘라 보낸다 (`trim.py`)
                    pcm, trimmed_ms = trim_leading_silence(pcm, self._settings.tts_trim)
                    record = self._log.get(turn)
                    if record is not None:
                        record.fields["tts_trim_ms"] = trimmed_ms
                    self._start_speaking(turn)
                audio_bytes += len(pcm)
                if self._spoken is not None and self._spoken.turn == turn:
                    self._spoken.ms.append(len(pcm) // BYTES_PER_MS)  # 들은 만큼 계산용
                self._emit({"type": "bot.say", "turn": turn, "seq": seq, "text": text})
                for i, (chunk, last) in enumerate(chunk_pcm(pcm, self._settings.chunk_bytes)):
                    frame = pack_audio_frame(AudioFrame(turn, seq, chunk, last))
                    on_sent = (lambda: self._log.mark(turn, "audio_first")) if i == 0 else None
                    self._emit_audio(frame, on_sent)
                self._emit({"type": "bot.audio.end", "turn": turn, "seq": seq})
        finally:
            for job in jobs:
                job.cancel()
                job.add_done_callback(_retrieve)  # 먼저 실패로 끝난 뒤 문장도 (리뷰 16번)
        return audio_bytes // BYTES_PER_MS

    def _on_playback_done(self, turn: int, seq: Any, output_latency: Any) -> None:
        if self._phase != "speaking" or turn != self._turn:
            return
        last = self._last_seq.get(turn)
        if seq is not None and last is not None and seq < last:
            return
        if seq is None and self._bot_done_turn != turn:
            return  # seq 없는 보고는 문장을 다 보낸 뒤에만 받는다 (리뷰 11번)
        record = self._log.get(turn)
        if record is not None and output_latency is not None:
            record.fields["output_latency"] = output_latency
        self._after_playback(turn)

    def _on_play_timeout(self, turn: int) -> None:
        if self._phase != "speaking" or turn != self._turn or self.done:
            return
        log.info("playback.done이 오지 않아 듣기로 돌아감 (%s, 턴 %d)", self.call_id, turn)
        record = self._log.get(turn)
        if record is not None:
            record.fields["play_done_timeout"] = True
        self._after_playback(turn)

    # 말 끊기 (3단계 티켓 05). 기본은 반이중이라 봇이 말하는 동안 브라우저는 0 프레임을 보낸다.
    # 버튼을 누르면 브라우저가 재생을 멈추고 지금 턴 이하 오디오를 버리고 마이크를 연 뒤
    # `user.interrupt`와 `playback.interrupted {turn, seq, played_ms}`를 보낸다. 서버는 남은 문장
    # TTS와 `bot.say`를 멈추고, 송신 큐에 남은 그 턴 오디오를 버리고, 듣기로 간다.
    # 소리가 나간 턴은 들었다고 본다(엔진은 되돌리지 않는다, grilling Q12). 복창·마무리·사장님
    # 연결 안내 턴은 막는다(`_interruptible`). 확정·취소로 끝난 턴은 그래서 끊기지 않는다

    def _interrupt(self, turn: int, by: str) -> None:
        """봇 말을 끊는다. 지금 턴이고 끊기를 받는 때만. 두 번 불러도 한 번만 끊는다."""
        if self.done or turn != self._turn or not self._interruptible():
            return
        self._cut(turn, by)

    def _cut(self, turn: int, by: str) -> None:
        """지금 말하는 턴을 끊는다. 음성 끼어들기(`by: voice`)는 버튼 막기와 따로 판정한다."""
        if self._speaking is not None:
            self._speaking.cancel()  # 남은 문장 TTS·`bot.say`. `_speak`의 finally가 TTS를 취소한다
        self._drop_queued(turn)
        record = self._log.get(turn)
        if record is not None:
            record.fields["interrupt"] = {"by": by}
            self._report_due = turn  # 들은 만큼 보고가 올 때까지 이 기록을 닫지 않는다
        self._after_playback(turn, mark="interrupted")

    def _drop_queued(self, turn: int) -> None:
        """송신 큐에 남은 그 턴의 오디오·자막을 버린다. 이미 나간 것은 브라우저가 버린다."""
        kept = []
        while not self._out.empty():
            item = self._out.get_nowait()
            if item is None or not _of_turn(item, turn):
                kept.append(item)
        for item in kept:
            self._out.put_nowait(item)

    def _on_playback_interrupted(self, turn: int, seq: Any, played_ms: Any) -> None:
        """브라우저가 재생을 멈춘 자리. 문장마다 들음·일부·안 들음을 그 턴 기록에 남긴다."""
        self._interrupt(turn, "playback")  # 버튼 신호 없이 온 보고도 같은 자리에서 끊는다
        record = self._log.get(turn)
        spoken = self._spoken
        if record is None or "interrupt" not in record.fields or spoken is None \
                or spoken.turn != turn:
            return  # 끊지 않은 턴(막는 턴·지난 턴)의 보고는 남기지 않는다
        cut = record.fields["interrupt"]
        if cut.get("by") == "voice" and self._stop_sent_at is not None and "report_ms" not in cut:
            # `bot.stop`을 보낸 뒤 브라우저가 멈췄다고 알리기까지 (판정 → 소리 꺼짐의 위 한계, T7)
            cut["report_ms"] = round((self._clock.monotonic() - self._stop_sent_at) * 1000)
        if "heard" in cut and not cut.get("est"):
            return
        if seq is None or not _is_number(played_ms):
            log.info("값이 틀린 playback.interrupted를 버림 (%s)", self.call_id)
            return
        cut.pop("est", None)  # 서버 어림보다 브라우저가 잰 자리가 정확하다
        cut.update(seq=seq, played_ms=round(played_ms), heard=spoken.heard(seq, played_ms))
        if self._report_due == turn:
            self._report_due = None  # 보고가 왔다. 더 기다릴 것이 없다
            if self._turn > turn:
                self._flush_log(before=self._turn)  # 다음 턴이 이미 열렸다. 끊긴 턴 기록을 닫는다

    def _after_playback(self, turn: int, *, mark: str = "play_done") -> None:
        """재생이 끝났거나(`playback.done`·안전망) 끊겼다. 듣기로 가고 무음 타이머를 건다."""
        self._play_timer.cancel()
        self._log.mark(turn, mark)
        if self._barge is not None:
            self._close_window(turn)
        if self._terminal:
            self._say_call_end(turn)
            self._end(self._end_reason or "done")
            return
        self._phase = "listening"
        self._emit_state()
        self._start_quiet(turn)
        pending, self._pending = self._pending, []
        pending = [u for u in pending if not self._drop_early_yes(u)]
        if pending:
            self._decide(replace(_Utterance.merge(pending), queued=True))
        # 받아 둔 사장님 답. 손님 말이 먼저다(그 말을 판정하는 중이면 `bot_turn`이 받지 않는다)
        self._say_owner_reply()

    def _drop_early_yes(self, u: _Utterance) -> bool:
        """최종 복창의 금액 문장이 끝나기 전에 한 "네"·맞장구면 버린다 (grilling Q10, 리뷰 P1).

        복창 첫 문장을 합성하는 동안 한 말은 `_pending`에 있다가 재생이 끝난 뒤 판정된다. 도착
        시각이 아니라 말한 시각(`start_ms`)으로 본다. 정정은 받는다. 버린 말은 지금 턴 기록의
        `dropped`에 `early_yes`로 남긴다
        """
        spoken = self._spoken
        if spoken is None or not spoken.readback or u.start_ms is None or not is_early_yes(u.text):
            return False
        # 반이중은 첫 소리부터 재생 끝까지 마이크가 0이라 그 사이에 시작한 말이 없다. 그래서 첫
        # 소리 전에 시작한 말이 금액 전의 말이다. 재생 중 오디오가 멈춰도(브라우저 멈춤) 재생 뒤의
        # "네"를 버리지 않는다. 음성 끼어들기는 재생 중에도 진짜 소리라 금액 문장 끝과 비교한다
        boundary = spoken.amount_end_ms() if self._barge is not None else spoken.from_ms
        if u.start_ms >= boundary:
            return False
        record = self._log.get(self._turn)
        if record is not None:
            record.fields.setdefault("dropped", []).append({
                "text": u.text, "start_ms": u.start_ms, "reason": "early_yes",
                "backchannel": is_backchannel(u.text),
            })
        return True

    def _say_call_end(self, turn: int) -> None:
        """대화가 끝나 서버가 통화를 끝낸다. 이유를 `call.ended`와 그 턴 기록에 남긴다 (티켓 04)."""
        reason = self._end_reason or "done"
        record = self._log.get(turn)
        if record is not None:
            record.fields["end_reason"] = reason
        # 그때까지 손님에게 보낸 문자(확정 안내 등, 티켓 07). 손님 화면이 "받은 문자"로 바로 보인다
        texts: list[dict[str, Any]] = []
        self._tell_owner(lambda owner: texts.extend(owner.customer_texts(self.call_id)))
        self._emit({"type": "call.ended", "turn": turn, "reason": reason, "sms": texts})

    # 무응답 타이머 (티켓 04, grilling Q6). 봇 재생이 끝나거나 끊긴 때(`_after_playback`)부터
    # `no_input_s`(지난 손님 말이 "잠깐만요"면 `wait_request_s`)를 센다. 손님 말 턴이나 봇 턴이
    # 열리면 끈다. VAD 말 구간 중에는 멈추고, 그 말 끝(`_last_voice_at`)부터 다시 센다. 그 말이
    # 턴이 되면(받아들인 말) 엔진이 횟수를 0으로 돌린다. 글 없는 소리가 계속 와도 처음 마감 +
    # `no_input_slack_s`에는 되묻는다(손님이 말하는 중이면 그 말이 끝나자마자, 리뷰 P4). 쉼 없는
    # 말 구간이 `no_input_speech_max_s`를 넘으면 끊기지 않는 소음으로 보고 상한에서 되묻는다
    # (감사 2번). 무엇을 말할지(되묻기·마지막 인사)는 엔진 `no_input`이 정한다

    def _start_quiet(self, turn: int) -> None:
        self._quiet_timer.cancel()
        if self._owner_open is not None:
            # 사장님 답을 기다리는 중에는 되묻지 않는다 (4단계 티켓 06, V1 AO-6). 답·시간 초과를
            # 말한 봇 턴의 재생이 끝나면 다시 건다
            self._quiet_since = None
            return
        self._quiet_delay = (self._settings.wait_request_s if self._wait_requested
                             else self._settings.no_input_s)
        self._wait_requested = False
        self._quiet_since = self._clock.monotonic()
        self._quiet_turn = turn
        self._quiet_timer.arm(self._quiet_delay)

    def _on_quiet_timer(self) -> None:
        if self.done or self._terminal or self._phase != "listening" or self._quiet_since is None:
            return
        if self._turn != self._quiet_turn:
            return  # 그 사이 다른 턴이 열렸다(그 턴이 끝나면 다시 건다)
        now = self._clock.monotonic()
        start, delay = self._quiet_since, self._quiet_delay
        # 손님 말을 붙잡거나 판정하는 중이면 그 말이 먼저다. 손님이 지금 말하는 중이면 상한이
        # 지나도 끼어들지 않는다(리뷰 P4). 글 없는 소리가 마감을 밀어도 상한에는 되묻는다.
        # 쉼 없는 말 구간이 `no_input_speech_max_s`를 넘으면 소음으로 본다(감사 2번)
        handling = bool(self._parts or self._held is not None or self._cont is not None
                        or self._pending)
        if self._quiet_timer.wait(now, due=max(start, self._last_voice_at or start) + delay,
                                  cap=start + delay + self._settings.no_input_slack_s,
                                  busy=self._ep_waiting(),
                                  hold=handling or self._talking(now)):
            return
        self.bot_turn("no_input", lambda engine, at: engine.no_input(at))

    def _talking(self, now: float) -> bool:
        """손님이 지금 말하는 중인가. 쉼 없이 `no_input_speech_max_s`를 넘긴 말 구간은 소음이다."""
        if not self._vad.in_speech:
            return False
        since = self._speech_from if self._speech_from is not None else now
        return now - since < self._settings.no_input_speech_max_s

    # ------------------------------------------------------------ 음성 끼어들기 (티켓 06)
    # 플래그(`barge`)가 켜져 있고 브라우저가 알린 통화만. 봇이 말하는 동안에도 진짜 마이크 소리가
    # 올라온다. 판정 규칙은 `barge.py`에 있고, 여기서는 봇 말 구간(`BotWindow`)을 오디오 시각으로
    # 기억하고 판정 결과를 `bot.duck`·`bot.unduck`·`bot.stop`으로 내보낸다.
    # - 거르기: STT 글은 도착 시각이 아니라 말한 시각(`start_ms`)으로 봇 말 구간과 비교한다.
    #   구간 안에서 시작한 말이면 판정하고, 진짜 끼어들기만 손님 말로 받는다. 에코·맞장구·빈 글은
    #   버린다(`dropped`에 판정과 함께 남긴다). 재생이 끝나면 그때 말소리가 이어지던 구간(에코)에
    #   epFlag를 붙여 STT 구간을 끊고 무음 감지를 비운다(봇 말 끝 경계)
    # - 멈춤은 버튼과 같은 자리(`_cut`)에서 끊는다. 들은 만큼은 서버가 오디오 시각으로 어림해
    #   두고(`est`), 브라우저 `playback.interrupted`가 오면 그 값으로 바꾼다

    def _window_kind(self, turn: int) -> WindowKind:
        result = self._last
        if turn == 0 or result is None or result.state in TERMINAL:
            return "blocked"  # 인사(에코 제거가 아직 덜 적응)·마무리·사장님 연결·취소
        return "readback" if result.readback else "open"

    def _open_window(self, turn: int) -> None:
        assert self._barge is not None and self._spoken is not None
        window = BotWindow(self._window_kind(turn), self._spoken)
        window.log.update(kind=window.kind, stop_ms=self._barge.stop_ms, events=[], results=[])
        record = self._log.get(turn)
        if record is not None:
            record.fields["barge"] = window.log
        self._windows = [*self._windows[-2:], window]
        self._barge.begin()

    def _close_window(self, turn: int) -> None:
        """재생이 끝났거나 끊겼다. 봇 말 구간을 닫고 에코 바닥(RMS)을 기록한다."""
        barge = self._barge
        window = self._windows[-1] if self._windows else None
        if barge is None or window is None or window.turn != turn or window.to_ms is not None:
            return
        window.to_ms = self._vad.offset_ms
        window.log["rms"] = _rms_stats(barge.rms)
        if self._vad.in_speech:
            # 봇 말 끝 경계: 에코로 열린 STT 구간·무음 감지를 끊는다. 아니면 늦게 온 에코 글이
            # 손님 말이 되고, 에코로 켜진 말 상태가 다음 손님 말을 붙잡는다
            # (`s3-verify-barge-in` 4-4).
            # 손님이 말하던 중이면 앞부분은 구간 안 글로 판정되고, 뒷부분은 조각 붙잡기가 합친다
            self._vad.end_speech()
            self._cut_ep = True

    def _window_of(self, r: SttResult) -> BotWindow | None:
        """그 말을 시작한 봇 말 구간. 봇 말 끝 바로 뒤(`answer_gap_ms`)의 맞장구도 구간 안이다."""
        assert self._barge is not None
        gap = self._barge.answer_gap_ms(r.text)
        for window in reversed(self._windows):
            if window.holds(r.start_ms, gap):
                return window
        return None

    def _barge_frame(self, frame_from: int) -> None:
        barge, window = self._barge, self._windows[-1]
        assert barge is not None
        at = self._vad.offset_ms
        action = barge.frame(self._vad.last_rms, frame_from, at, kind=window.kind,
                             remaining_ms=window.spoken.remaining_ms(at))
        if action is not None:
            self._barge_act(window, action, "sound")

    def _barge_act(self, window: BotWindow, action: Action, by: str) -> None:
        assert self._barge is not None
        on = self._barge.acting
        window.log["events"].append({"act": action, "by": by, "at_ms": self._vad.offset_ms,
                                     **({} if on else {"shadow": True})})
        if not on:
            return  # 그림자 측정: 판정만 남긴다
        if action == "stop":
            self._voice_stop(window, by)
        else:
            self._emit({"type": f"bot.{action}", "turn": window.turn})

    def _voice_stop(self, window: BotWindow, by: str) -> None:
        """음성 끼어들기로 봇 말을 멈춘다. 브라우저가 소리를 먼저 끄도록 `bot.stop`부터 보낸다."""
        turn = window.turn
        if self.done or turn != self._turn or self._phase != "speaking":
            return
        at = self._vad.offset_ms
        self._emit({"type": "bot.stop", "turn": turn})
        self._stop_sent_at = self._clock.monotonic()
        window.to_ms, window.cut = at, True
        window.log["rms"] = _rms_stats(self._barge.rms if self._barge else [])
        seq, played = window.spoken.position(at)
        self._cut(turn, "voice")
        record = self._log.get(turn)
        if record is not None and "interrupt" in record.fields:
            record.fields["interrupt"].update(reason=by, seq=seq, played_ms=played, est=True,
                                              heard=window.spoken.heard(seq, played))

    def _barge_take(self, window: BotWindow, r: SttResult) -> bool:
        """봇 말 중에 시작한 말의 글. 진짜 끼어들기면 참(손님 말로 받는다), 아니면 버린다."""
        barge = self._barge
        assert barge is not None
        verdict = barge.judge(r.text, kind=window.kind,
                              before_amount_end=r.start_ms < window.spoken.amount_end_ms(),
                              bot_texts=window.spoken.texts)
        window.log["results"].append({"start_ms": r.start_ms, "end_ms": r.end_ms,
                                      "text": r.text, "verdict": verdict})
        latest = window is self._windows[-1]
        if latest:
            action = barge.settle(verdict, r.end_ms, self._vad.offset_ms)
            if action is not None:
                self._barge_act(window, action, "text")
        if verdict == "accept" and latest and window.to_ms is None and not barge.stopped:
            # 아직 말하는 중: 글로 멈춘다 (그림자 측정은 소리로 멈춘 셈이면 다시 남기지 않는다)
            barge.stop_by_text(self._vad.offset_ms)
            self._barge_act(window, "stop", "text")
        if verdict != "accept" or not barge.acting:
            self._log_dropped(r, verdict)  # 그림자 측정은 반이중처럼 버린다
            return False
        return True

    # ------------------------------------------------------------ 내보내기·끝내기

    def _emit(self, event: dict[str, Any]) -> None:
        if not self._broken:
            self._out.put_nowait(("event", event, None))

    def _emit_audio(self, frame: bytes, on_sent: Callable[[], None] | None) -> None:
        if not self._broken:
            self._out.put_nowait(("audio", frame, on_sent))

    def _emit_state(self) -> None:
        result = self._last
        self._emit({
            "type": "state",
            "turn": self._turn,
            "phase": self._phase,
            "interruptible": self._interruptible(),
            "dialog": result.state.value if result else None,
            "cart": _cart_json(result.cart if result else Cart()),
            "total": result.cart.total() if result else 0,
        })

    def _interruptible(self) -> bool:
        """지금 말 끊기 버튼을 받나. 봇 소리가 나가는 중이고, 막는 턴이 아닐 때만.

        막는 턴 (3단계 티켓 05, grilling Q10): 최종 복창(끊고 "네"라고 하면 총액을 못 듣고
        확정된다), 마무리·사장님 연결 안내·취소(TERMINAL, 주문이 이미 갔거나 통화가 끝난다).
        복창 턴은 엔진이 `TurnResult.readback`으로 알린다(리뷰 C6). CONFIRM 상태여도 "네, 천천히
        말씀하세요"·"잘 못 들었어요"는 끊을 수 있다. 인사는 막지 않는다
        """
        result = self._last
        return (self._phase == "speaking" and result is not None
                and result.state not in TERMINAL and not result.readback)

    def _flush_before(self, turn: int) -> None:
        """새 턴 `turn`을 열기 전에 앞 턴 기록을 쓴다.

        바로 앞 턴이 끊겼고 브라우저의 `playback.interrupted`(들은 만큼)를 아직 못 받았으면 그
        턴은 보고가 올 때까지 열어 둔다(리뷰 C2). 끊는 순간 대기 중이던 말이 다음 턴을 열기
        때문이다. 보고가 오면 `_on_playback_interrupted`가 닫는다. 끝내 안 오면 그다음 턴을 열 때
        닫는다
        """
        due = self._report_due
        if due is not None and due == turn - 1:
            self._flush_log(before=due)
            return
        self._report_due = None
        self._flush_log(before=turn)

    def _flush_log(self, before: int | None = None) -> None:
        for line in self._log.flush(before):
            self._emit({"type": "metrics", **line})

    def _fail(self, err: BaseException, where: str) -> None:
        if self.done:
            return
        detail = None
        if isinstance(err, PortError):
            code, retry, message, detail = err.code, err.retry, err.customer_message, str(err)
            log.info("통화 오류 %s (%s, %s): %s", code, self.call_id, where, detail)
        else:  # 모르는 예외의 문자열은 키가 들어 있을 수 있어 기록에도 넣지 않는다
            log.exception("통화 세션 오류 (%s, %s)", self.call_id, where, exc_info=err)
            code, retry, message = where, True, CUSTOMER_RETRY
        record = self._log.get(self._turn)
        if record is not None:
            record.fields["error"] = code
            if detail is not None:
                record.fields["error_detail"] = detail
        self._emit({"type": "error", "turn": self._turn, "code": code, "message": message,
                    "retry": retry})
        self._end("error")

    def _end(self, reason: str) -> None:
        """통화를 끝내는 길은 모두 여기를 지난다. 처음 부른 이유가 이 통화가 끝난 이유다.

        이유: confirmed · handoff · cancelled · no_input(대화가 끝남, `call.ended`와 같음) ·
        hangup(손님이 끊음·소켓 끊김) · error(어댑터·엔진 오류, 엔진 시간 초과, 통화 길이 초과).
        여러 번 불러도 된다(`close()`가 한 번 더 부른다). 정리는 매번 하고 이유는 처음 것만 둔다
        """
        first = self._ended_by is None
        if first:
            self._ended_by = reason
        self._quiet_timer.cancel()
        self._not_heard_timer.cancel()
        self._hold_timer.cancel()
        self._thinking_timer.cancel()
        self._cont_timer.cancel()
        self._play_timer.cancel()
        self._score_timer.cancel()
        self._owner_timer.cancel()
        self._owner_retry.cancel()
        self._owner_open = None  # 통화 뒤 사장님 답은 버린다(늦은 답 문자는 허브 몫, 티켓 07)
        self._owner_inbox.clear()
        self._drop_score()  # 줄 선 점수 작업을 공유 풀에서 뺀다 (다른 통화가 늦지 않게, 감사 11번)
        self._phase = "ended"
        self._ready = False
        self._done.set()
        if first:
            self._tell_owner(lambda owner: owner.call_ended(self.call_id, reason))

    # 사장님께 묻기 (4단계 티켓 05·06, spec "ASK_OWNER: 세션"). 기다림은 세션이 맡는다(엔진
    # 스레드에서 기다리지 않는다). 질문은 `OwnerChannel.open_question`으로 열고 바로 돌아온다.
    # 사장님 쪽 알림(답·카드 열람)은 받는 곳(`_owner_replied`)으로 들어와 큐에 쌓인다. 카드를 보낸
    # 때부터 `owner_wait_s`가 지나면 세션이 시간 초과 답을 큐에 넣는다. 큐는 말해도 될 때
    # (`_owner_may_speak`, 무응답 타이머와 같은 조건) 봇이 먼저 말하는 턴으로 엔진에 넣는다. 안
    # 되면 미뤘다가 재생 끝(`_after_playback`)·짧은 재시도(`_owner_retry`)에서 다시 본다. 기다리는
    # 동안 무응답 타이머는 멈춘다(`_start_quiet`). 통화가 끝나면(`_end`) 질문·큐·타이머를 비운다

    def _open_owner_question(self, turn: int, question: OwnerQuestion) -> None:
        self._owner_open = question.number
        self._owner_opened_at = self._clock.monotonic()
        self._owner_seen_at = None
        self._owner_seen_said = False
        self._owner_timer.arm(self._settings.owner_wait_s, question.number)
        record = self._log.get(turn)
        if record is not None:
            record.fields["owner_question"] = {"number": question.number,
                                               "menu_id": question.menu_id,
                                               "opened_at": self._clock.now().isoformat()}
        self._tell_owner(lambda owner: owner.open_question(self.call_id, question,
                                                           self._owner_replied))

    def _owner_replied(self, note: OwnerNote) -> None:
        """사장님 쪽 알림(답·열람)을 받는 곳. 바로 돌아온다. 어느 스레드에서 불려도 된다.

        처리는 루프의 다음 차례에 한다(부른 쪽 안에서 턴을 열지 않게).
        """
        loop = self._loop
        if loop is None or self.done:
            return
        loop.call_soon_threadsafe(self._owner_got, note, self._clock.monotonic())

    def _owner_got(self, note: OwnerNote, at: float) -> None:
        if self.done:
            return
        if (isinstance(note, OwnerSeen) and note.question == self._owner_open
                and self._owner_seen_at is None):
            self._owner_seen_at = at  # 열람 시각은 말하지 못해도(답이 먼저 옴) 답 턴 기록에 남는다
        self._owner_inbox.append((note, at))
        self._say_owner_reply()

    def _on_owner_timeout(self, number: int) -> None:
        """질문 카드를 보낸 뒤 `owner_wait_s`. 허브에 알리고 시간 초과 답을 큐에 넣는다."""
        if self.done or self._owner_open != number:
            return
        self._tell_owner(lambda owner: owner.question_timed_out(self.call_id, number))
        self._owner_got(OwnerReply(number, "timeout"), self._clock.monotonic())

    def _owner_may_speak(self, now: float) -> bool:
        """사장님 답·열람을 지금 말해도 되나. 무응답 타이머가 되묻는 조건과 같다 (V1 판정 1).

        듣는 중이고, 손님 말을 붙잡거나 판정하는 중이 아니고, 손님이 말하는 중(VAD 말 구간)이
        아니고, 말 끝 epFlag 응답을 기다리는 중이 아니다. epFlag 응답이 `hold_cap_s`를 넘게 안
        오면 더 기다리지 않는다(응답이 사라져도 답이 영영 막히지 않게)
        """
        if self._phase != "listening":
            return False
        if self._parts or self._held is not None or self._cont is not None or self._pending:
            return False
        ep_waiting = (self._ep_waiting() and self._ep_at is not None
                      and now - self._ep_at < self._settings.hold_cap_s)
        return not (self._talking(now) or ep_waiting)

    def _say_owner_reply(self) -> None:
        """받은 사장님 답·열람을 말한다. 지금 말하면 안 되면 미룬다.

        열린 질문과 번호가 다른 것(낡은 답·닫힌 질문의 답)은 버린다. 답이 와 있으면 열람은 버린다
        (답을 바로 말한다). 열람은 질문마다 한 번만 말한다.
        """
        self._owner_retry.cancel()
        if self.done:
            self._owner_inbox.clear()
            return
        kept = [(n, at) for n, at in self._owner_inbox if n.question == self._owner_open]
        if len(kept) != len(self._owner_inbox):
            log.info("낡은 사장님 답을 버림 (%s)", self.call_id)
        answers = [(n, at) for n, at in kept if isinstance(n, OwnerReply)]
        if answers:
            kept = answers[:1]
        elif self._owner_seen_said:
            kept = []
        self._owner_inbox = kept[:1]
        if not kept:
            return
        now = self._clock.monotonic()
        if self._phase != "listening":
            return  # 봇이 말하는 중·생각 중. 그 턴의 재생 끝(`_after_playback`)에서 다시 본다
        if not self._owner_may_speak(now):
            self._owner_retry.arm(POLL_S)
            return
        note, at = kept[0]
        if isinstance(note, OwnerReply):
            number, answer = note.question, note.answer
            opened = self.bot_turn(
                "owner_answer",
                lambda engine, when: engine.owner_answered(number, answer, when))
        else:
            number = note.question
            opened = self.bot_turn("owner_seen",
                                   lambda engine, when: engine.owner_seen(number, when))
        if opened is None:
            self._owner_retry.arm(POLL_S)
            return
        self._owner_inbox.clear()
        record = self._log.get(opened)
        since = self._owner_opened_at if self._owner_opened_at is not None else at
        timing = {"received_at": self._clock.now().isoformat(),
                  "wait_ms": round((at - since) * 1000),  # 질문 카드를 보낸 때부터 받은 때까지
                  "deferred_ms": round((now - at) * 1000)}  # 받은 때부터 말을 시작한 때까지
        if isinstance(note, OwnerReply):
            self._owner_open = None
            self._owner_timer.cancel()
            seen = self._owner_seen_at
            if record is not None:
                record.fields["owner_answer"] = {
                    "question": note.question, "answer": note.answer, **timing,
                    "seen_ms": None if seen is None else round((seen - since) * 1000),
                }
        else:
            self._owner_seen_said = True
            if record is not None:
                record.fields["owner_seen"] = {"question": note.question, **timing}

    def _tell_owner(self, note: Callable[[OwnerChannel], None]) -> None:
        """사장님 쪽에 알린다. 실패해도 통화는 잇는다(사장님 화면 탓에 손님 통화가 끊기지 않게)."""
        try:
            note(self._owner)
        except Exception:
            log.exception("사장님 쪽 알림 실패 (%s)", self.call_id)


def _end_reason(result: TurnResult, u: _Utterance | _BotTurn | None) -> str:
    """대화가 끝난 이유 (`call.ended`). 봇이 먼저 말한 턴이면 그 이유(no_input 등)."""
    if isinstance(u, _BotTurn):
        return u.reason
    if result.state is State.CLOSING:
        return "confirmed"
    if result.state is State.FALLBACK:
        return "handoff"
    return "cancelled"


def _timed_score(model: TurnModel, pcm: bytes) -> tuple[float, float]:
    """턴 모델 스레드에서 돈다. (끝남 확률, 걸린 ms). 0~1 밖이거나 숫자가 아니면 실패로 본다."""
    start = time.perf_counter()
    score = float(model.p_complete(pcm))
    if not (math.isfinite(score) and 0.0 <= score <= 1.0):
        raise ValueError(f"턴 모델 점수가 0~1 밖: {score!r}")
    return score, (time.perf_counter() - start) * 1000


def _rms_stats(values: list[float]) -> dict[str, int]:
    """프레임 RMS의 p10·p50·p95·최대 (에코 바닥·손님 말소리·조용할 때, 실기기 시험 T1)."""
    if not values:
        return {"frames": 0}
    ordered = sorted(values)

    def pct(p: float) -> int:
        return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))])

    return {"frames": len(ordered), "p10": pct(0.1), "p50": pct(0.5), "p95": pct(0.95),
            "max": round(ordered[-1])}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    """0 이상의 유한한 수 (파이썬 JSON은 `NaN`·`Infinity`도 받는다)."""
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _of_turn(item: tuple[str, Any, Any], turn: int) -> bool:
    """송신 큐 항목이 그 턴의 봇 소리(오디오 프레임·문장 자막·문장 끝)인가."""
    kind, payload, _ = item
    if kind == "audio":
        return HEADER.unpack_from(payload)[3] == turn
    return payload.get("turn") == turn and payload.get("type") in ("bot.say", "bot.audio.end")


def _retrieve(task: asyncio.Future[Any]) -> None:
    """끝난 작업의 예외를 꺼내 둔다("Task exception was never retrieved" 방지)."""
    if not task.cancelled():
        task.exception()


def _cart_json(cart: Cart) -> list[dict[str, Any]]:
    return [
        {
            "menu_id": line.item.id,
            "name": line.item.name_ko,
            "qty": line.qty,
            "options": [o.name for o in line.options],
            "request": line.request,
            "subtotal": line.subtotal(),
        }
        for line in cart.lines
    ]


def _order_json(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "status": order.status,
        "lines": [
            {"menu_id": line.menu_id, "quantity": line.quantity, "options": list(line.options),
             "request": line.request}
            for line in order.lines
        ],
        "pickup_time": order.pickup_time,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "transcript": list(order.transcript),
    }
