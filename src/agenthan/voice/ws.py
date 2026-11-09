"""WS 라우터 `/api/phone/ws` (spec WS 계약, 티켓 04). 연결 하나 = 통화 세션 하나.

    app.include_router(phone_router(store=..., config=..., order_sink=..., settings=...))

- 올리는 binary(PCM16LE 16kHz mono 100ms)는 `feed_audio`, text(JSON)는 `feed_event`로 넘긴다
- 내보내기는 세션의 송신 작업 하나가 `WsOutbound`로만 한다 (Starlette `send`에 잠금이 없음)
- 세션이 끝나거나(확정·handoff·무응답 등 `call.ended`, 오류) 손님이 끊으면(`call.end`·소켓
  끊김) 세션을 닫고(STT 닫기·JSONL 쓰기) 소켓을 닫는다
- 동시 통화가 `max_calls`를 넘으면 세션 없이 `error{code: "busy"}`를 보내고 닫는다 (리뷰 17번).
  통화 길이(`max_call_s`)와 `call.start` 대기(10초)는 세션이 지킨다
- 턴 모델(Smart Turn, 3단계 티켓 03)은 라우터를 만들 때 한 번 연다(체크섬 확인). 없거나 틀리면
  경고를 남기고 끝말 규칙만으로 뜬다. 모델 세션 하나와 전용 2스레드 풀(`phone-turn-*`)을 모든
  통화가 같이 쓴다
- 엔진(NLU 만들기·엔진 생성·턴)은 라우터 하나가 가진 전용 스레드 풀에서 돈다 (티켓 09).
  루프 기본 풀은 2 vCPU에서 6개뿐이고 DNS 조회(`getaddrinfo`) 등과 같이 쓴다. 시간을 넘긴
  엔진 스레드는 취소되지 않고 남으므로 통화당 2개(`ENGINE_THREADS_PER_CALL`)를 잡는다

가게 지식·설정·`OrderSink`는 만드는 쪽이 넣는다. 여기에는 가짜 기본값이 없다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from agenthan.core.contracts import OrderSink, StoreKnowledge
from agenthan.voice import adapters
from agenthan.voice.adapters import SttFactory, TtsFactory
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.call.ports import (
    CUSTOMER_RETRY,
    ClientGone,
    Clock,
    PortError,
    SystemClock,
    Tts,
    TurnModel,
)
from agenthan.voice.call.session import CallSession, turn_pool
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.smart_turn import load_turn_model
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.stt.boost import menu_boost_words

WS_PATH = "/api/phone/ws"
ENGINE_THREADS_PER_CALL = 2  # 지금 도는 턴 1 + 시간을 넘겨 아직 남은 스레드 1
ENGINE_THREAD_PREFIX = "phone-engine"

log = logging.getLogger(__name__)


class WsOutbound:
    """`Outbound` 포트 → Starlette WebSocket. 세션의 송신 작업만 부른다.

    손님 쪽이 끊겨 못 보내면(Starlette `WebSocketDisconnect`, 그 밑의 uvicorn `ClientDisconnected`
    같은 `OSError`) `ClientGone`을 올린다. 세션이 traceback 없이 한 줄만 남긴다(감사 문제 B)
    """

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send_event(self, event: dict[str, Any]) -> None:
        text = json.dumps(event, ensure_ascii=False)
        try:
            await self._ws.send_text(text)
        except (WebSocketDisconnect, OSError) as err:
            raise ClientGone(_gone(err)) from err

    async def send_audio(self, frame: bytes) -> None:
        try:
            await self._ws.send_bytes(frame)
        except (WebSocketDisconnect, OSError) as err:
            raise ClientGone(_gone(err)) from err


def _gone(err: BaseException) -> str:
    code = getattr(err, "code", None)
    return f"{type(err).__name__} {code}" if code is not None else type(err).__name__


def phone_router(
    *,
    store: StoreKnowledge,
    config: StoreConfig,
    order_sink: OrderSink,
    settings: VoiceSettings,
    stt_factory: SttFactory | None = None,
    tts_factory: TtsFactory | None = None,
    turn_model: TurnModel | None = None,
) -> APIRouter:
    """`settings.stt`·`tts`가 `clova`인데 어댑터가 없으면 여기서 `AdapterMissing`.

    `stt_factory`·`tts_factory`를 주면 설정 대신 그것으로 통화마다 포트를 만든다.
    `turn_model`을 주면 설정(`PHONE_TURN_MODEL`) 대신 그 턴 모델을 쓴다.
    """
    if stt_factory is None:
        # 키워드 부스팅 단어(티켓 10). 메뉴는 라우터를 만들 때 한 번 읽는다(`list_menu`가 동기라
        # 통화마다 이벤트 루프에서 부르지 않는다). 메뉴 이름이 바뀌면 서버를 다시 띄운다
        boost = menu_boost_words(store.list_menu()) if settings.stt == "clova" else ()
        stt_factory = adapters.stt_factory(settings, boost_words=boost)
    make_stt = stt_factory
    make_tts = tts_factory or adapters.tts_factory(settings)
    config = replace(config, tz=settings.tz)
    engine_pool = ThreadPoolExecutor(
        max_workers=settings.max_calls * ENGINE_THREADS_PER_CALL,
        thread_name_prefix=ENGINE_THREAD_PREFIX,
    )
    if turn_model is None and settings.turn_model == "smart_turn":
        turn_model = load_turn_model(settings.turn_model_path)  # 실패면 None(규칙만), 경고 로그
    session_settings = settings.session_settings()
    log.info("말 끝 판정: %s", "끝말 규칙 + Smart Turn" if turn_model else "끝말 규칙만")
    router = APIRouter()
    active = 0  # 지금 열린 통화 수 (이벤트 루프 하나라 잠금이 필요 없다)

    @router.websocket(WS_PATH)
    async def phone_ws(ws: WebSocket) -> None:
        nonlocal active
        await ws.accept()
        if active >= settings.max_calls:
            log.info("동시 통화 %d개 상한이라 거절", settings.max_calls)
            await _refuse(ws, PortError("동시 통화 상한", code="busy",
                                        customer="지금 통화가 많아요. " + CUSTOMER_RETRY))
            return
        active += 1
        try:
            await _serve(ws)
        finally:
            active -= 1

    async def _serve(ws: WebSocket) -> None:
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
            clock=store_clock(settings),
            outbound=WsOutbound(ws),
            order_sink=order_sink,
            log_dir=settings.log_dir,
            settings=session_settings,
            executor=engine_pool,
            turn_model=turn_model,
            turn_executor=turn_pool() if turn_model is not None else None,
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


def store_clock(settings: VoiceSettings) -> Clock:
    """가게 시계. `PHONE_NOW`가 있으면 그 시각에 멈춘 시계 (통화 세션·사장님 허브가 같이 쓴다)."""
    if settings.now is not None:
        return FakeClock(settings.now.astimezone(settings.tz))
    return SystemClock(settings.tz)


async def _read(ws: WebSocket, session: CallSession) -> None:
    """읽기 작업 하나. 손님이 끊으면 끝난다. 세션 쪽 호출은 STT·엔진을 기다리지 않는다."""
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
    except Exception:  # 여기서 죽으면 gather가 예외를 삼켜 조용히 끊긴다 (리뷰 10번)
        log.exception("WS 읽기 작업 오류 (%s)", session.call_id)


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
        code, retry, message = err.code, err.retry, err.customer_message
        if code != "busy":
            log.warning("통화 포트를 만들지 못함 %s: %s", code, err)  # 상세는 서버 로그에만
    else:
        log.exception("통화 포트를 만들지 못함", exc_info=err)
        code, retry, message = "config", False, CUSTOMER_RETRY
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
