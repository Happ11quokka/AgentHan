"""통화 세션: 통화 1건 (spec 모듈 · 통화 세션, 반이중, 엔진 연결). FastAPI를 모른다.

쓰는 법 (WS 라우터, 티켓 04):

    session = CallSession(store=..., config=..., nlu="fake", stt=..., tts=...,
                          clock=SystemClock(), outbound=..., order_sink=...)
    await session.open()        # 송신 작업 시작
    # 읽기 작업 하나에서:
    #   binary → await session.feed_audio(pcm), text → await session.feed_event(dict)
    #   (둘 다 STT·엔진을 기다리지 않는다. 읽기 작업이 밀리지 않게)
    await session.wait_done()   # 확정·handoff 뒤 재생 끝, call.end, 오류 중 하나
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
  speaking에 들어갈 때 붙잡아 둔 조각은 다음 턴으로 넘기고 "말 끝" 표시를 지운다

조각 붙잡기: 끝 후보 결과가 왔는데 그 끝 뒤에 새 말 구간이 시작됐으면 다음 결과와 합친다.
epFlag 응답이 아닌 결과(gap 등)는 무음 감지가 아직 말하는 중이거나 epFlag 응답을 기다리는
중이어도 붙잡는다(티켓 10, `_continues_after`). 그때 턴은 epFlag 응답으로 넘어간다.
`hold_max_s` 안에 새 결과가 없으면 그대로 턴을 넘긴다(`end_by: hold_timeout`, `_arm_hold` 앞 주석).
처음 붙잡은 때부터 `hold_cap_s`를 넘겨 붙잡지는 않는다.
버린 결과(반이중·확정 뒤·통화 끝)는 그 턴 기록의 `dropped`에 남긴다.

엔진 시간 제한(`turn_timeout_s`)을 넘으면 `error`를 내고 통화를 끝낸다. 다음 턴은 시작하지 않는다
(스레드는 취소되지 않아서, 다음 턴이 같은 엔진을 동시에 건드리게 되기 때문, verify-web D5).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agenthan.core.contracts import Order, OrderSink, StoreKnowledge
from agenthan.voice.call.frames import (
    BYTES_PER_MS,
    CHUNK_BYTES,
    SAMPLE_RATE,
    AudioFrame,
    chunk_pcm,
    pack_audio_frame,
)
from agenthan.voice.call.latency import LatencyLog
from agenthan.voice.call.ports import (
    CUSTOMER_FATAL,
    CUSTOMER_RETRY,
    Clock,
    Outbound,
    PortError,
    SttResult,
    SttStream,
    Tts,
)
from agenthan.voice.call.sentences import TTS_LIMIT, split_sentences
from agenthan.voice.call.trim import TrimSettings, trim_leading_silence
from agenthan.voice.call.vad import SilenceDetector, VadSettings
from agenthan.voice.dialog.cart import Cart
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import TERMINAL
from agenthan.voice.nlu.base import Nlu
from agenthan.voice.store_config import StoreConfig

log = logging.getLogger(__name__)

__all__ = ["CUSTOMER_FATAL", "CUSTOMER_RETRY", "CallSession", "SessionSettings", "make_nlu"]

NluChoice = Literal["fake", "hcx"]
Phase = Literal["idle", "listening", "thinking", "speaking", "ended"]


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
    hold_cap_s: float = 3.0  # 이어진 말이 아직 열려 있어도 붙잡기는 여기까지
    # `playback.done`이 안 와도(AudioContext 멈춤 등) 보낸 소리 길이 + 이만큼 뒤 듣기로 (리뷰 4번)
    play_done_grace_s: float = 3.0
    # 말 끝 + 이만큼까지 봇 목소리가 없으면 `bot.thinking`(생각 중 효과음). PRD 1.5초 (grilling Q13)
    thinking_after_s: float = 1.5
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
        )


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
        log_dir: Path | None = Path("logs"),
        call_id: str | None = None,
        settings: SessionSettings | None = None,
        executor: Executor | None = None,
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
        self._settings = settings or SessionSettings()
        self._executor = executor  # None이면 루프 기본 풀
        self._log = LatencyLog(log_dir, self.call_id, clock)
        self._vad = SilenceDetector(self._settings.vad)

        self._out: asyncio.Queue[tuple[str, Any, Callable[[], None] | None] | None] = (
            asyncio.Queue()
        )
        self._turns: asyncio.Queue[tuple[int, _Utterance]] = asyncio.Queue()
        self._stt_q: asyncio.Queue[tuple[bytes, bool, int]] = asyncio.Queue()
        self._stt_backlog = 0  # 큐에 있거나 쓰는 중인 바이트
        self._done = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._writer: asyncio.Task[None] | None = None

        self._engine: DialogEngine | None = None
        self._last: TurnResult | None = None
        self._phase: Phase = "idle"
        self._turn = 0
        self._ready = False  # STT가 연결됐다. 그 전 오디오는 버린다
        self._terminal = False  # 확정·handoff·취소로 대화가 끝났다. 재생이 끝나면 통화 끝
        self._last_seq: dict[int, int] = {}  # 턴 → 마지막 문장 seq
        self._parts: list[SttResult] = []
        self._held: SttResult | None = None  # 붙잡은 끝 후보 결과 (뒤에 말이 이어짐)
        self._hold_at: float | None = None  # 마지막으로 붙잡은 때
        self._hold_since: float | None = None  # 처음 붙잡은 때 (`hold_cap_s` 기준)
        self._hold_timer: asyncio.TimerHandle | None = None
        self._pending: list[_Utterance] = []
        self._commit = False  # "말 끝" 버튼: 다음 DATA에 epFlag
        self._forced = False  # "말 끝" 버튼: 다음 말 끝 결과는 붙잡지 않는다
        self._button_at: float | None = None
        self._voice_at: float | None = None
        self._ep_at: float | None = None  # 마지막 epFlag 프레임을 쓰기 큐에 넣은 때
        self._ep_reply_at: float | None = None  # 마지막 epFlag 응답을 받은 때
        self._speak_from_ms = 0  # 이번 봇 소리를 보내기 시작한 오디오 시각 (반이중 기준)
        self._client_start: dict[str, Any] = {}
        self._bot_done_turn: int | None = None  # `bot.done`까지 보낸 마지막 턴
        self._play_timer: asyncio.TimerHandle | None = None
        self._thinking_timer: asyncio.TimerHandle | None = None
        self._broken = False

    # ------------------------------------------------------------ 바깥 진입점

    async def open(self) -> None:
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
        ended = self._vad.feed(pcm)
        now = self._clock.monotonic()
        if self._vad.last_voiced:
            self._voice_at = now
        ep = self._vad.ep_due or self._commit  # 조기 epFlag: 무음 250ms (턴 판정은 `ended`)
        if ep:
            if self._commit:
                self._vad.end_speech()
            self._commit = False
            self._ep_at = now
        self._stt_q.put_nowait((pcm, ep, self._turn + 1))
        if ended:
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
                self._phase = "thinking"
                self._spawn(self._turn_loop(), "turns")
        elif kind == "user.end_of_speech":
            if self._phase in ("listening", "thinking"):
                self._commit = True
                self._forced = True
                self._button_at = self._clock.monotonic()
        elif kind == "playback.started":
            self._log.mark(turn, "play_start")
        elif kind == "playback.done":
            self._on_playback_done(turn, seq, event.get("output_latency"))
        elif kind == "client.metrics":
            record = self._log.get(turn)
            if record is not None:
                record.client.update({k: v for k, v in event.items() if k not in ("type", "turn")})
        elif kind == "call.end":
            self._end()

    async def close(self) -> None:
        self._end()
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
            except Exception:
                log.info("송신 실패, 통화를 끝냄 (%s)", self.call_id, exc_info=True)
                self._broken = True
                self._end()
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
        # list_menu()가 동기라 엔진 생성도 스레드에서 (verify-web D5 조건 2)
        self._engine = await self._in_thread(
            DialogEngine, self._store, nlu, self._config, order_id=self.call_id
        )
        await self._stt.start()
        self._spawn(self._read_stt(), "stt")
        self._spawn(self._write_stt(), "stt-write")
        self._ready = True
        self._emit({"type": "call.ready", "turn": 0, "call": self.call_id,
                    "sample_rate": SAMPLE_RATE})

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
        if self.done or self._terminal or self._spoken_over(r):
            self._log_dropped(r)  # 반이중: 봇 소리가 나간 뒤에 한 말의 결과는 버린다
            return
        self._cancel_hold()
        if r.text.strip():
            self._parts.append(r)
        if not r.ends_speech:
            if self._parts and self._held is not None:
                self._arm_hold(self._settings.hold_max_s)  # 아직 끝 후보가 아니다. 계속 기다린다
            return
        if not self._parts:
            if r.ep_flag:
                self._forced = False  # 말 없이 누른 버튼
            return
        forced = self._forced
        if not forced and self._continues_after(r):
            # 말이 이어진다. 다음 결과(보통 epFlag 응답)와 합친다. 안 오면 시간 제한으로 푼다
            self._held = r
            self._hold_at = self._clock.monotonic()
            if self._hold_since is None:
                self._hold_since = self._hold_at
            self._arm_hold(self._settings.hold_max_s)
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
        """무음이 턴 판정 길이(400ms)에 닿았다. 그 전에 와서 붙잡아 둔 epFlag 응답이면 넘긴다."""
        held = self._held
        if held is None or not held.ep_flag or not self._parts or self._ep_waiting():
            return
        self._cancel_hold()
        self._release(held, "vad")

    def _ep_waiting(self) -> bool:
        """마지막으로 쓴 epFlag 프레임의 응답을 아직 못 받았나."""
        if self._ep_at is None:
            return False
        return self._ep_reply_at is None or self._ep_reply_at < self._ep_at

    def _log_dropped(self, r: SttResult) -> None:
        """버린 결과를 지금 턴 기록의 `dropped`에 남긴다 (티켓 10 #1).

        말 중간 조각으로 턴을 넘긴 뒤 뒷조각이 반이중에 걸려 사라지는 일("나폴" 잘림)을
        기록으로 확인하려고 둔다.
        `reason`: speaking(봇 말하는 중) · terminal(확정·handoff 뒤) · ended(통화 끝)
        """
        record = self._log.get(self._turn)
        if record is None:
            return
        reason = "ended" if self.done else ("terminal" if self._terminal else "speaking")
        record.fields.setdefault("dropped", []).append({
            "text": r.text, "epd_type": r.epd_type, "start_ms": r.start_ms, "end_ms": r.end_ms,
            "ep_flag": r.ep_flag, "reason": reason,
        })

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
            speech_end=self._voice_at,
            ep_sent=self._ep_at if r.ep_flag else None,
            button=self._button_at if forced else None,
        )
        self._clear_hold()
        if self._phase == "listening":
            self._dispatch(utterance)
        else:
            self._pending.append(utterance)

    # 붙잡기 시간 제한 (리뷰 2번). 붙잡은 뒤 `hold_max_s` 동안 새 결과가 없으면 푼다. 다만 그때
    # 이어진 말 구간이 아직 열려 있거나 그 구간의 `epFlag` 응답을 기다리는 중이면 `hold_cap_s`까지
    # 늘린다. 그래야 "메도빅 두 개… 랑 아메리카노 하나요"처럼 긴 뒷말이 쪼개지지 않는다

    def _arm_hold(self, delay: float) -> None:
        self._hold_timer = asyncio.get_running_loop().call_later(delay, self._on_hold_timer)

    def _cancel_hold(self) -> None:
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None

    def _clear_hold(self) -> None:
        """붙잡아 둔 조각·시간 제한을 모두 지운다."""
        self._cancel_hold()
        self._parts = []
        self._held = None
        self._hold_at = self._hold_since = None

    def _on_hold_timer(self) -> None:
        self._hold_timer = None
        held = self._held
        if held is None or not self._parts or self.done:
            return
        now = self._clock.monotonic()
        assert self._hold_since is not None and self._hold_at is not None
        left = self._settings.hold_cap_s - (now - self._hold_since)
        if left > 0 and (self._vad.in_speech or self._ep_waiting()):
            self._arm_hold(min(self._settings.hold_max_s, left))
            return
        self._release(held, "hold_timeout")

    def _dispatch(self, u: _Utterance, *, queued: bool = False) -> None:
        """손님 말 하나를 새 턴으로 넘긴다. `queued`: 봇이 말하는 동안 모아 둔 말(`_pending`)."""
        self._turn += 1
        turn = self._turn
        self._phase = "thinking"
        self._flush_log(before=turn)
        record = self._log.begin(turn, text=u.text, epd_type=u.epd_type, fragments=u.fragments,
                                 splits=u.fragments - 1, utterances=u.utterances, end_by=u.end_by)
        if queued:
            record.fields["queued"] = True
        for name, at in (("speech_end", u.speech_end), ("ep_sent", u.ep_sent),
                         ("button", u.button), ("stt_final", u.stt_final)):
            if at is not None:
                self._log.mark(turn, name, at=at)
        self._emit({"type": "stt.final", "turn": turn, "text": u.text, "epd_type": u.epd_type,
                    "fragments": u.fragments, "end_by": u.end_by})
        self._emit_state()
        # 모아 둔 말은 말 끝이 봇 재생 전이라, 재생이 끝나 넘긴 지금부터 센다
        ref = None if queued else (u.speech_end or u.button)
        self._arm_thinking(turn, ref)
        self._turns.put_nowait((turn, u))

    # 생각 중 효과음 (3단계 티켓 01, grilling Q13). 말 끝 + `thinking_after_s`까지 봇 목소리가
    # 없으면 `bot.thinking`을 한 번 보낸다. 브라우저가 짧은 비음성 효과음을 낸다. 첫 오디오를
    # 보내거나(`_start_speaking`) 말 없이 턴이 끝나면 타이머를 끈다. 지표는 목소리(`audio_first`·
    # `play_start`)와 따로 `thinking` 표시로 남긴다

    def _arm_thinking(self, turn: int, ref: float | None) -> None:
        self._cancel_thinking()
        now = self._clock.monotonic()
        delay = max(0.0, (now if ref is None else ref) + self._settings.thinking_after_s - now)
        self._thinking_timer = asyncio.get_running_loop().call_later(
            delay, self._on_thinking, turn
        )

    def _cancel_thinking(self) -> None:
        if self._thinking_timer is not None:
            self._thinking_timer.cancel()
            self._thinking_timer = None

    def _on_thinking(self, turn: int) -> None:
        self._thinking_timer = None
        if self.done or turn != self._turn or self._phase != "thinking":
            return
        self._log.mark(turn, "thinking")
        self._emit({"type": "bot.thinking", "turn": turn})

    # ------------------------------------------------------------ 한 턴

    async def _run_turn(self, turn: int, u: _Utterance | None) -> None:
        engine = self._engine
        assert engine is not None
        if u is None:  # 인사 턴. 마이크 실제 설정값(`call.start`)을 이 줄의 client에 남긴다
            self._log.begin(turn, text="").client.update(self._client_start)
            self._emit_state()
        now = self._clock.now()
        self._log.mark(turn, "engine_start")
        if u is None:
            result = await self._call_engine(lambda: engine.start(now))
        else:
            result = await self._call_engine(lambda: engine.handle(u.text, now))
        self._log.mark(turn, "engine_end")
        self._last = result
        if result.state in TERMINAL:
            self._terminal = True
        if result.order is not None:
            self._sink.submit(result.order)
            self._emit({"type": "order", "turn": turn, "order": _order_json(result.order),
                        "cart": _cart_json(result.cart), "total": result.cart.total()})
        sentences = [
            s for text in result.utterances
            for s in split_sentences(text, self._settings.sentence_limit)
        ]
        audio_ms = 0
        if sentences:
            self._last_seq[turn] = len(sentences) - 1
            audio_ms = await self._speak(turn, sentences)  # 첫 소리를 보낼 때 speaking
        self._cancel_thinking()  # 말할 문장이 없던 턴
        self._emit({"type": "bot.done", "turn": turn, "sentences": len(sentences)})
        self._bot_done_turn = turn
        self._log.mark(turn, "bot_done")
        if not sentences:
            self._after_playback(turn)
        elif self._phase == "speaking":
            # 서버 안전망: 재생 끝 보고가 안 와도 소리 길이 + 여유 뒤에는 듣기로 (리뷰 4번)
            delay = audio_ms / 1000 + self._settings.play_done_grace_s
            self._play_timer = asyncio.get_running_loop().call_later(
                delay, self._on_play_timeout, turn
            )

    def _start_speaking(self, turn: int) -> None:
        """첫 오디오를 보내기 직전. 여기서부터 "봇 말하는 중"이다 (3단계 티켓 01).

        전에는 엔진이 끝나자마자 speaking이 돼서 TTS 합성 중(첫 소리 전)에 한 말이 버려졌다.
        이 오디오 시각 뒤에 시작한 말만 반이중으로 버린다(`_spoken_over`).
        붙잡아 둔 조각은 첫 소리 전에 한 말이므로 버리지 않고 `_pending`(다음 턴)에 넣는다.
        다음 손님 말에 붙지는 않는다(리뷰 3번). 그 뒷글이 늦게 와도 `_pending`에서 합쳐진다
        """
        self._phase = "speaking"
        self._cancel_thinking()
        self._speak_from_ms = self._vad.offset_ms
        if self._parts:
            self._release(self._held or self._parts[-1], "bot_start")
        self._clear_hold()
        self._forced = self._commit = False
        self._button_at = None
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
        self._play_timer = None
        if self._phase != "speaking" or turn != self._turn or self.done:
            return
        log.info("playback.done이 오지 않아 듣기로 돌아감 (%s, 턴 %d)", self.call_id, turn)
        record = self._log.get(turn)
        if record is not None:
            record.fields["play_done_timeout"] = True
        self._after_playback(turn)

    def _cancel_play_timer(self) -> None:
        if self._play_timer is not None:
            self._play_timer.cancel()
            self._play_timer = None

    def _after_playback(self, turn: int) -> None:
        self._cancel_play_timer()
        self._log.mark(turn, "play_done")
        if self._terminal:
            self._end()
            return
        self._phase = "listening"
        self._emit_state()
        if self._pending:
            merged = _Utterance.merge(self._pending)
            self._pending = []
            self._dispatch(merged, queued=True)

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
            "dialog": result.state.value if result else None,
            "cart": _cart_json(result.cart if result else Cart()),
            "total": result.cart.total() if result else 0,
        })

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
        self._end()

    def _end(self) -> None:
        self._cancel_hold()
        self._cancel_thinking()
        self._cancel_play_timer()
        self._phase = "ended"
        self._ready = False
        self._done.set()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
