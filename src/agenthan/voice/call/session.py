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
  엔진은 `asyncio.to_thread`로 부른다
- STT 쓰기 작업 1개: `feed_audio`가 쌓은 프레임을 STT에 쓴다(리뷰 1번). 쓰기가 밀리면
  쌓인 프레임을 `epFlag` 프레임까지 이어 붙여 DATA 하나로 보낸다. 바이트를 버리지 않으므로
  STT 시각과 무음 감지 시각이 그대로 맞는다. `stt_backlog_ms`(3초)만큼 밀리면 `stt_backlog`로 끝낸다
- STT 읽기 작업 1개: 결과를 받아 조각을 합치고, 말 끝이면 턴을 넘긴다

턴 번호: 인사가 0, 손님 말 하나(합친 것)마다 1씩 는다. 나가는 메시지 모두에 `turn`이 있다.

국면(phase): listening(듣는 중) → thinking(엔진 도는 중) → speaking(봇 말하는 중) → listening.
- listening에 말 끝 결과가 오면 바로 다음 턴
- thinking에 온 결과는 모아 두었다가 재생이 끝나면 하나로 합쳐 다음 턴 하나로 넣는다
  (대답 두 번 방지)
- speaking에 온 결과는 버린다(반이중). 마지막 문장의 `playback.done`이 오면 listening

엔진 시간 제한(`turn_timeout_s`)을 넘으면 `error`를 내고 통화를 끝낸다. 다음 턴은 시작하지 않는다
(스레드는 취소되지 않아서, 다음 턴이 같은 엔진을 동시에 건드리게 되기 때문, verify-web D5).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
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
from agenthan.voice.call.ports import Clock, Outbound, PortError, SttResult, SttStream, Tts
from agenthan.voice.call.sentences import TTS_LIMIT, split_sentences
from agenthan.voice.call.vad import SilenceDetector, VadSettings
from agenthan.voice.dialog.cart import Cart
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import TERMINAL
from agenthan.voice.nlu.base import Nlu
from agenthan.voice.store_config import StoreConfig

log = logging.getLogger(__name__)

NluChoice = Literal["fake", "hcx"]
Phase = Literal["idle", "listening", "thinking", "speaking", "ended"]


@dataclass(frozen=True)
class SessionSettings:
    vad: VadSettings = field(default_factory=VadSettings)
    turn_timeout_s: float = 40.0  # HCX 최악(재시도 2번 + Retry-After)보다 길게
    tts_concurrency: int = 2
    sentence_limit: int = TTS_LIMIT
    chunk_bytes: int = CHUNK_BYTES  # 내리는 오디오 조각 ≈ 200ms
    stt_backlog_ms: int = 3_000  # STT 쓰기가 이만큼 밀리면 통화를 끝낸다 (stt_backlog)
    stt_batch_ms: int = 1_000  # 밀린 프레임을 DATA 하나로 묶는 최대 길이


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
        self._pending: list[_Utterance] = []
        self._commit = False  # "말 끝" 버튼: 다음 DATA에 epFlag
        self._forced = False  # "말 끝" 버튼: 다음 말 끝 결과는 붙잡지 않는다
        self._button_at: float | None = None
        self._voice_at: float | None = None
        self._ep_at: float | None = None
        self._client_start: dict[str, Any] = {}
        self._broken = False

    # ------------------------------------------------------------ 바깥 진입점

    async def open(self) -> None:
        if self._writer is None:
            self._writer = asyncio.create_task(self._write_loop(), name=f"{self.call_id}-writer")

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
        ep = ended or self._commit
        if ep:
            if self._commit:
                self._vad.end_speech()
            self._commit = False
            self._ep_at = now
        self._stt_q.put_nowait((pcm, ep, self._turn + 1))
        self._stt_backlog += len(pcm)
        backlog_ms = self._stt_backlog // BYTES_PER_MS
        record = self._log.get(self._turn)
        if record is not None and backlog_ms > record.fields.get("stt_backlog_max", 0):
            record.fields["stt_backlog_max"] = backlog_ms
        if backlog_ms >= self._settings.stt_backlog_ms:
            self._fail(PortError("회선이 느려 통화를 이어 갈 수 없어요. 잠시 후 다시 걸어 주세요.",
                                 code="stt_backlog", retry=True), "stt")
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
            self._on_playback_done(turn, event.get("seq"), event.get("output_latency"))
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
            self._fail(PortError("엔진 응답이 늦어요", code="engine_timeout", retry=True), "engine")
        except Exception as err:
            self._fail(err, "engine")

    async def _setup(self) -> None:
        nlu = self._nlu
        if isinstance(nlu, str):
            nlu = await asyncio.to_thread(make_nlu, nlu)  # hcx는 .env를 읽는다
        # list_menu()가 동기라 엔진 생성도 스레드에서 (verify-web D5 조건 2)
        self._engine = await asyncio.to_thread(
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
        if self.done or self._terminal or self._phase == "speaking":
            return  # 반이중: 봇 말하는 중 결과는 버린다
        if r.text.strip():
            self._parts.append(r)
        if not r.ends_speech:
            return
        if not self._parts:
            if r.ep_flag:
                self._forced = False  # 말 없이 누른 버튼
            return
        forced = self._forced
        if not forced and self._vad.speech_since(r.end_ms):
            return  # 결과 끝 뒤에 말이 이어졌다. 다음 결과와 합친다
        self._forced = False
        utterance = _Utterance(
            text=" ".join(p.text.strip() for p in self._parts),
            fragments=len(self._parts),
            epd_type=r.epd_type,
            end_by="button" if forced else ("vad" if r.ep_flag else r.epd_type),
            stt_final=self._clock.monotonic(),
            speech_end=self._voice_at,
            ep_sent=self._ep_at if r.ep_flag else None,
            button=self._button_at if forced else None,
        )
        self._parts = []
        if self._phase == "listening":
            self._dispatch(utterance)
        else:
            self._pending.append(utterance)

    def _dispatch(self, u: _Utterance) -> None:
        self._turn += 1
        turn = self._turn
        self._phase = "thinking"
        self._flush_log(before=turn)
        self._log.begin(turn, text=u.text, epd_type=u.epd_type, fragments=u.fragments,
                        splits=u.fragments - 1, utterances=u.utterances, end_by=u.end_by)
        for name, at in (("speech_end", u.speech_end), ("ep_sent", u.ep_sent),
                         ("button", u.button), ("stt_final", u.stt_final)):
            if at is not None:
                self._log.mark(turn, name, at=at)
        self._emit({"type": "stt.final", "turn": turn, "text": u.text, "epd_type": u.epd_type,
                    "fragments": u.fragments, "end_by": u.end_by})
        self._emit_state()
        self._turns.put_nowait((turn, u))

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
        if sentences:
            self._last_seq[turn] = len(sentences) - 1
            self._phase = "speaking"
            self._emit_state()
            await self._speak(turn, sentences)
        self._emit({"type": "bot.done", "turn": turn, "sentences": len(sentences)})
        self._log.mark(turn, "bot_done")
        if not sentences:
            self._after_playback(turn)

    async def _call_engine(self, fn: Callable[[], TurnResult]) -> TurnResult:
        task = asyncio.ensure_future(asyncio.to_thread(fn))
        done, _ = await asyncio.wait({task}, timeout=self._settings.turn_timeout_s)
        if not done:
            raise _EngineTimeout  # 스레드는 계속 돈다. 이 엔진은 다시 부르지 않는다
        return task.result()

    async def _speak(self, turn: int, sentences: list[str]) -> None:
        """문장마다 TTS를 동시에 `tts_concurrency`개까지 부르고, 보내는 건 문장 순서대로."""
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
        try:
            for seq, (job, text) in enumerate(zip(jobs, sentences, strict=True)):
                pcm = await job
                self._emit({"type": "bot.say", "turn": turn, "seq": seq, "text": text})
                for i, (chunk, last) in enumerate(chunk_pcm(pcm, self._settings.chunk_bytes)):
                    frame = pack_audio_frame(AudioFrame(turn, seq, chunk, last))
                    on_sent = (lambda: self._log.mark(turn, "audio_first")) if i == 0 else None
                    self._emit_audio(frame, on_sent)
                self._emit({"type": "bot.audio.end", "turn": turn, "seq": seq})
        finally:
            for job in jobs:
                job.cancel()

    def _on_playback_done(self, turn: int, seq: Any, output_latency: Any) -> None:
        if self._phase != "speaking" or turn != self._turn:
            return
        last = self._last_seq.get(turn)
        if seq is not None and last is not None and seq < last:
            return
        record = self._log.get(turn)
        if record is not None and output_latency is not None:
            record.fields["output_latency"] = output_latency
        self._after_playback(turn)

    def _after_playback(self, turn: int) -> None:
        self._log.mark(turn, "play_done")
        if self._terminal:
            self._end()
            return
        self._phase = "listening"
        self._emit_state()
        if self._pending:
            merged = _Utterance.merge(self._pending)
            self._pending = []
            self._dispatch(merged)

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
        if isinstance(err, PortError):
            code, retry, message = err.code, err.retry, str(err)
        else:
            log.exception("통화 세션 오류 (%s, %s)", self.call_id, where, exc_info=err)
            code, retry, message = where, True, "잠시 후 다시 걸어 주세요."
        record = self._log.get(self._turn)
        if record is not None:
            record.fields["error"] = code
        self._emit({"type": "error", "turn": self._turn, "code": code, "message": message,
                    "retry": retry})
        self._end()

    def _end(self) -> None:
        self._phase = "ended"
        self._ready = False
        self._done.set()


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
