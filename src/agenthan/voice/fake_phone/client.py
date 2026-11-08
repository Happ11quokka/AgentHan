"""가짜 휴대폰: 녹음을 `/api/phone/ws`에 마이크처럼 흘려 넣는 손님 흉내 (티켓 07, spec 끝-끝).

하는 일 (06 통화 화면과 같은 규칙):
- `call.start`를 보내고 `call.ready` 뒤부터 100ms 프레임(3,200바이트)을 `pace` 속도로 계속 보낸다
  (`pace=1`이 실제 속도). 녹음을 보내는 중이 아니면 0 프레임(디지털 무음)
- 봇 오디오: 문장의 첫 조각이 오면 앞 문장이 끝난 뒤에 '재생'을 시작해 `playback.started`,
  그 문장 길이만큼 지나면 `playback.done{output_latency: 0}`. 재생 중과 끝난 뒤 150ms는
  녹음 대신 0 프레임을 보낸다(반이중). 그 동안의 녹음은 버려진다(`muted_ms`)
- 녹음 하나 = 손님 말 하나. 인사가 끝나 듣는 중이 되면 첫 녹음, 그 녹음의 `stt.final`이 오고
  봇이 대답을 마쳐 다시 듣는 중이 되면 다음 녹음. 녹음을 다 보낸 뒤 `no_turn_timeout_s` 안에
  `stt.final`이 없으면 그 녹음은 턴을 못 만든 것으로 보고 넘어간다
- 서버가 소켓을 닫으면(확정·handoff 재생 끝, 오류) 끝. 녹음을 다 썼는데 안 닫히면
  `no_turn_timeout_s` 뒤에 `call.end`를 보낸다

시각은 모두 연결 시작부터의 ms(이 프로세스 단조 시계). 받은 이벤트(`events`)와 보낸 제어
이벤트(`sent`)에 `t`를 붙여 남긴다. 서버의 턴별 지연 기록은 `metrics` 이벤트로 받는다.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from agenthan.voice.call.frames import BYTES_PER_MS, unpack_audio_frame

FRAME_MS = 100
FRAME_BYTES = FRAME_MS * BYTES_PER_MS
SILENT = bytes(FRAME_BYTES)
UNMUTE_AFTER_S = 0.15  # spec 반이중: 재생이 끝나고 약 150ms 뒤 마이크를 다시 보낸다


@dataclass(frozen=True)
class Clip:
    id: str
    pcm: bytes  # PCM16LE 16kHz mono
    speech_end_s: float | None = None  # 녹음 안에서 말이 끝난 시각 (기준값, `audio.speech_end_s`)


@dataclass
class ClipRun:
    id: str
    sent_at_ms: float = 0.0  # 첫 프레임을 보낸 시각
    done_at_ms: float = 0.0  # 마지막 프레임을 보낸 시각
    speech_end_ms: float | None = None  # 말 끝 (보낸 시각 + 녹음 안 말 끝)
    muted_ms: int = 0  # 봇 재생 때문에 0으로 바꿔 보낸 녹음 길이
    turns: list[int] = field(default_factory=list)  # 이 녹음 동안 생긴 손님 턴
    texts: list[str] = field(default_factory=list)


@dataclass
class CallRun:
    clips: list[ClipRun]
    events: list[dict[str, Any]] = field(default_factory=list)  # 받은 이벤트 + `t`
    sent: list[dict[str, Any]] = field(default_factory=list)  # 보낸 제어 이벤트 + `t`
    metrics: list[dict[str, Any]] = field(default_factory=list)
    order: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    closed_by_server: bool = False


class _Phone:
    def __init__(self, ws: Any, clips: list[Clip], pace: float, gap_s: float,
                 no_turn_timeout_s: float) -> None:
        self.ws = ws
        self.clips = clips
        self.pace = pace
        self.gap_s = gap_s
        self.no_turn_timeout_s = no_turn_timeout_s
        self.run = CallRun([ClipRun(c.id) for c in clips])
        self.t0 = time.monotonic()
        self.out: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.changed = asyncio.Event()  # 받은 이벤트가 생기면 깨운다
        self.ready = False
        self.listening = False
        self.listening_since = 0.0
        self.closed = False
        self.current: int | None = None  # 지금 보내는 녹음 번호
        self.feed: list[bytes] = []  # 지금 보낼 녹음 프레임들
        # 재생 흉내
        self.cursor = 0.0  # 앞 문장 재생이 끝나는 시각(초, t0 기준)
        self.playing: dict[tuple[int, int], dict[str, Any]] = {}
        self.muted_until = 0.0
        self.timers: set[asyncio.Task[None]] = set()

    def now(self) -> float:
        return time.monotonic() - self.t0

    def ms(self) -> float:
        return round(self.now() * 1000, 1)

    # ---------------------------------------------------------------- 보내기

    def send_event(self, **event: Any) -> None:
        self.run.sent.append({**event, "t": self.ms()})
        self.out.put_nowait(json.dumps(event, ensure_ascii=False))

    async def writer(self) -> None:
        while (item := await self.out.get()) is not None:
            try:
                await self.ws.send(item)
            except Exception:
                self.closed = True
                self.changed.set()
                return

    async def mic(self) -> None:
        """100ms마다 프레임 하나. 녹음 중이면 녹음, 아니면 0. 재생 중이면 0."""
        start = self.now()
        i = 0
        while not self.closed:
            i += 1
            await asyncio.sleep(max(0.0, start + i * FRAME_MS / 1000 * self.pace - self.now()))
            frame = SILENT
            if self.feed:
                frame = self.feed.pop(0)
                if self.muted():
                    self.run.clips[self.current or 0].muted_ms += FRAME_MS
                    frame = SILENT
                if not self.feed:
                    self.run.clips[self.current or 0].done_at_ms = self.ms()
                    self.changed.set()
            self.out.put_nowait(frame)

    def muted(self) -> bool:
        return bool(self.playing) or self.now() < self.muted_until

    # ---------------------------------------------------------------- 받기

    async def reader(self) -> None:
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    self.on_audio(message)
                else:
                    self.on_event(json.loads(message))
                self.changed.set()
        except Exception:
            pass
        self.closed = True
        self.run.closed_by_server = True
        self.changed.set()

    def on_event(self, event: dict[str, Any]) -> None:
        self.run.events.append({**event, "t": self.ms()})
        kind = event.get("type")
        if kind == "call.ready":
            self.ready = True
        elif kind == "state":
            listening = event.get("phase") == "listening"
            if listening and not self.listening:
                self.listening_since = self.now()
            self.listening = listening
        elif kind == "stt.final":
            self.listening = False
            if self.current is not None:
                clip = self.run.clips[self.current]
                clip.turns.append(event["turn"])
                clip.texts.append(event["text"])
        elif kind == "bot.audio.end":
            self.on_sentence_end(event["turn"], event["seq"])
        elif kind == "metrics":
            self.run.metrics.append({k: v for k, v in event.items() if k != "type"})
        elif kind == "order":
            self.run.order = event
        elif kind == "error":
            self.run.error = event

    def on_audio(self, raw: bytes) -> None:
        frame = unpack_audio_frame(raw)
        key = (frame.turn, frame.seq)
        sentence = self.playing.get(key)
        if sentence is None:  # 문장의 첫 조각: 앞 문장이 끝난 뒤 재생
            start = max(self.now(), self.cursor)
            sentence = self.playing[key] = {"start": start, "bytes": 0}
            self.later(start, lambda: self.send_event(type="playback.started",
                                                      turn=frame.turn, seq=frame.seq))
        sentence["bytes"] += len(frame.pcm)

    def on_sentence_end(self, turn: int, seq: int) -> None:
        sentence = self.playing.get((turn, seq))
        if sentence is None:
            return
        end = sentence["start"] + sentence["bytes"] / BYTES_PER_MS / 1000 * self.pace
        self.cursor = max(self.cursor, end)

        def done() -> None:
            self.playing.pop((turn, seq), None)
            self.muted_until = max(self.muted_until, self.now() + UNMUTE_AFTER_S * self.pace)
            self.send_event(type="playback.done", turn=turn, seq=seq, output_latency=0)

        self.later(end, done)

    def later(self, at: float, fn: Any) -> None:
        async def wait() -> None:
            await asyncio.sleep(max(0.0, at - self.now()))
            fn()

        task = asyncio.create_task(wait())
        self.timers.add(task)
        task.add_done_callback(self.timers.discard)

    # ---------------------------------------------------------------- 대본

    async def until(self, cond: Any, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else self.now() + timeout
        while not cond():
            if self.closed:
                return False
            left = None if deadline is None else deadline - self.now()
            if left is not None and left <= 0:
                return False
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=min(left or 0.05, 0.05))
            except TimeoutError:
                pass
        return True

    def settled(self) -> bool:
        """듣는 중이고, 그 상태가 `gap_s` 넘게 이어졌다 (합친 턴이 바로 뒤따르지 않음)."""
        return (self.listening and not self.playing
                and self.now() - self.listening_since >= self.gap_s * self.pace)

    async def script(self) -> None:
        self.send_event(type="call.start", turn=0, mic={"fake_phone": True}, ua="fake_phone")
        long_wait = 60.0  # 인사·대답 기다리기 (HCX 최악 + TTS)
        if not await self.until(lambda: self.ready, long_wait):
            return
        for index, clip in enumerate(self.clips):
            if not await self.until(self.settled, long_wait):
                return
            run = self.run.clips[index]
            self.current = index
            frames = [clip.pcm[i:i + FRAME_BYTES] for i in range(0, len(clip.pcm), FRAME_BYTES)]
            frames[-1] = frames[-1] + bytes(FRAME_BYTES - len(frames[-1]))
            run.sent_at_ms = self.ms()
            if clip.speech_end_s is not None:
                run.speech_end_ms = round(run.sent_at_ms + clip.speech_end_s * 1000 * self.pace, 1)
            self.feed = frames
            await self.until(lambda: not self.feed)
            # 이 녹음이 턴을 만들 때까지(또는 포기)
            if not await self.until(lambda r=run: bool(r.turns),
                                    self.no_turn_timeout_s * self.pace):
                if self.closed:
                    return
                continue
        if await self.until(lambda: self.closed, self.no_turn_timeout_s * self.pace):
            return
        self.send_event(type="call.end", turn=0)
        await self.until(lambda: self.closed, 3.0)


async def run_call(url: str, clips: list[Clip], *, pace: float = 1.0, gap_s: float = 0.3,
                   no_turn_timeout_s: float = 6.0) -> CallRun:
    """통화 하나를 건다. 녹음을 차례로 손님 말로 보내고, 끝날 때까지의 기록을 돌려준다."""
    from websockets.asyncio.client import connect

    async with connect(url, max_size=None) as ws:
        phone = _Phone(ws, clips, pace, gap_s, no_turn_timeout_s)
        tasks = [asyncio.create_task(t) for t in (phone.reader(), phone.writer(), phone.mic())]
        try:
            await phone.script()
        finally:
            phone.closed = True
            phone.out.put_nowait(None)
            await asyncio.wait({tasks[1]}, timeout=2.0)
            await ws.close()
            for task in [*tasks, *phone.timers]:
                task.cancel()
            await asyncio.gather(*tasks, *phone.timers, return_exceptions=True)
    return phone.run
