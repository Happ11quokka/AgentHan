"""WS 라우터 `/api/phone/ws` (spec WS 계약, 티켓 04). 연결 하나 = 통화 세션 하나.

    app.include_router(phone_router(store=..., config=..., order_sink=..., settings=...))

- 올리는 binary(PCM16LE 16kHz mono 100ms)는 `feed_audio`, text(JSON)는 `feed_event`로 넘긴다
- 내보내기는 세션의 송신 작업 하나가 `WsOutbound`로만 한다 (Starlette `send`에 잠금이 없음)
- 세션이 끝나거나(확정·handoff·`call.end`·오류) 손님이 끊으면 세션을 닫고(STT 닫기·JSONL 쓰기)
  소켓을 닫는다

가게 지식·설정·`OrderSink`는 만드는 쪽이 넣는다. 여기에는 가짜 기본값이 없다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from typing import Any

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from agenthan.core.contracts import OrderSink, StoreKnowledge
from agenthan.voice import adapters
from agenthan.voice.adapters import SttFactory, TtsFactory
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.call.ports import Clock, PortError, SystemClock, Tts
from agenthan.voice.call.session import CallSession
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_config import StoreConfig

WS_PATH = "/api/phone/ws"

log = logging.getLogger(__name__)


class WsOutbound:
    """`Outbound` 포트 → Starlette WebSocket. 세션의 송신 작업만 부른다."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send_event(self, event: dict[str, Any]) -> None:
        await self._ws.send_text(json.dumps(event, ensure_ascii=False))

    async def send_audio(self, frame: bytes) -> None:
        await self._ws.send_bytes(frame)


def phone_router(
    *,
    store: StoreKnowledge,
    config: StoreConfig,
    order_sink: OrderSink,
    settings: VoiceSettings,
    stt_factory: SttFactory | None = None,
    tts_factory: TtsFactory | None = None,
) -> APIRouter:
    """`settings.stt`·`tts`가 `clova`인데 어댑터가 없으면 여기서 `AdapterMissing`.

    `stt_factory`·`tts_factory`를 주면 설정 대신 그것으로 통화마다 포트를 만든다.
    """
    make_stt = stt_factory or adapters.stt_factory(settings)
    make_tts = tts_factory or adapters.tts_factory(settings)
    config = replace(config, tz=settings.tz)
    router = APIRouter()

    @router.websocket(WS_PATH)
    async def phone_ws(ws: WebSocket) -> None:
        await ws.accept()
        tts: Tts | None = None
        try:
            tts = make_tts()
            stt = make_stt()  # 만들기만 한다. 연결(`start`)은 세션이 한다
        except Exception as err:  # 키 없음 등. 세션 전이라 여기서 직접 한 번 보낸다
            await _aclose(tts)
            await _refuse(ws, err)
            return
        session = CallSession(
            store=store,
            config=config,
            nlu=settings.nlu,
            stt=stt,
            tts=tts,
            clock=_clock(settings),
            outbound=WsOutbound(ws),
            order_sink=order_sink,
            log_dir=settings.log_dir,
        )
        await session.open()
        reader = asyncio.create_task(_read(ws, session))
        done = asyncio.create_task(session.wait_done())
        try:
            await asyncio.wait({reader, done}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # 서버가 이 처리기를 취소해도(종료·테스트 클라이언트) STT 닫기·JSONL 쓰기는 끝낸다.
            # `session.close()`는 송신 비우기를 2초로 끊으므로 오래 막히지 않는다
            with anyio.CancelScope(shield=True):
                for task in (reader, done):
                    task.cancel()
                await asyncio.gather(reader, done, return_exceptions=True)
                await session.close()  # 남은 송신을 비운 뒤라 이제 이 코루틴이 유일한 송신자
                await _aclose(tts)
                await _close(ws)

    return router


def _clock(settings: VoiceSettings) -> Clock:
    if settings.now is not None:
        return FakeClock(settings.now.astimezone(settings.tz))
    return SystemClock(settings.tz)


async def _read(ws: WebSocket, session: CallSession) -> None:
    """읽기 작업 하나. 손님이 끊으면 끝난다."""
    try:
        while not session.done:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (data := message.get("bytes")) is not None:
                await session.feed_audio(data)
            elif (text := message.get("text")) is not None:
                event = _parse(text)
                if event is not None:
                    await session.feed_event(event)
    except WebSocketDisconnect:
        return


def _parse(text: str) -> dict[str, Any] | None:
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        log.info("JSON이 아닌 text 메시지를 버림 (%d자)", len(text))
        return None
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        log.info("type 없는 이벤트를 버림")
        return None
    return event


async def _refuse(ws: WebSocket, err: Exception) -> None:
    if isinstance(err, PortError):
        code, retry, message = err.code, err.retry, str(err)
    else:
        log.exception("통화 포트를 만들지 못함", exc_info=err)
        code, retry, message = "config", False, "잠시 후 다시 걸어 주세요."
    try:
        await ws.send_text(json.dumps(
            {"type": "error", "turn": 0, "code": code, "message": message, "retry": retry},
            ensure_ascii=False,
        ))
    except Exception:
        log.info("error 이벤트를 보내지 못함", exc_info=True)
    await _close(ws)


async def _aclose(tts: Tts | None) -> None:
    """HTTP 연결을 들고 있는 TTS(`ClovaVoiceTts`)는 통화가 끝나면 닫는다. 가짜는 `aclose`가 없다."""
    aclose = getattr(tts, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:
            log.info("TTS 닫기 실패", exc_info=True)


async def _close(ws: WebSocket) -> None:
    if ws.application_state == WebSocketState.CONNECTED:
        try:
            await ws.close()
        except Exception:
            log.debug("소켓 닫기 실패 (이미 끊김)", exc_info=True)
