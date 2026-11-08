"""CLOVA Speech 실시간 스트리밍 STT 어댑터 (`grpc.aio`). `call.ports.SttStream` 구현 (티켓 02).

흐름 (공식 gRPC 문서 + `research/s2-verify-clova.md` 실측):
- 연결: TLS 채널 + 소문자 `authorization: Bearer <secret>` 메타데이터
  (Python gRPC는 대문자 메타데이터 키를 받지 않는다)
- CONFIG를 먼저 쓰고 `config.status == "Success"` 응답을 받은 뒤에야 DATA를 쓴다
  (문서의 `ConfigRequest did not complete` 오류 대비).
  응답의 다른 항목(`contextCode` 등)은 보지 않는다
- DATA마다 `extra_contents = {"epFlag": ..., "seqId": ...}`.
  `epFlag` 응답(빈 글일 수 있음)도 넘긴다
- 5분 연결 제한: 연결이 `reconnect_after_s`(4분 30초)를 넘긴 뒤 `epFlag` 프레임을 쓰면
  (= 턴 사이) 다음 프레임부터 새 연결로 보낸다. 앞 연결은 그 epFlag 응답을 받은 뒤 쓰기를
  닫고(티켓 10), 남은 결과를 다 받을 때까지(EOF) 읽는다(그사이 끊기면 로그만 남긴다).
  응답 없이 끝나면 빈 epFlag 응답(`epd_type="eof"`)을 대신 낸다
  `epFlag`가 오지 않고 `hard_reconnect_after_s`를 넘기면 그냥 바꾼다
- 결과 시각은 통화 오디오 시각으로 이어 준다: 연결마다 CLOVA 시각이 0부터라서,
  그 연결 전까지 쓴 오디오 길이를 더한다

키는 `ClovaSttSettings`로 받는다(이 모듈은 `.env`를 읽지 않는다).
키는 `repr`·로그·오류 메시지에 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import grpc

from agenthan.voice.call.frames import BYTES_PER_MS
from agenthan.voice.call.ports import PortError, SttResult
from agenthan.voice.stt import nest_pb2, nest_pb2_grpc
from agenthan.voice.stt.boost import BOOST_WEIGHT

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "clovaspeech-gw.ncloud.com:50051"


@dataclass(frozen=True)
class ClovaSttSettings:
    secret_key: str = field(repr=False)
    endpoint: str = DEFAULT_ENDPOINT
    language: str = "ko"
    # 안전망 말 끝 판정. 주 경로는 세션 무음 감지 → epFlag (spec 말 끝 판정)
    gap_threshold_ms: int = 700
    # 꼭 넣는다. 빼면(0 이하) CLOVA 기본값 약 400ms가 걸려
    # 한 문장이 400ms 조각 대여섯 개로 온다 (실측 R2)
    duration_threshold_ms: int = 10_000
    secure: bool = True  # 테스트의 로컬 가짜 서버만 False
    config_timeout_s: float = 10.0
    reconnect_after_s: float = 270.0  # 4분 30초. CLOVA 연결 수명은 5분
    hard_reconnect_after_s: float = 285.0
    # 재연결 때 앞 연결이 epFlag 응답을 기다리는 최대 시간. 5분 제한(300초)보다 먼저 닫히게
    retire_wait_s: float = 10.0
    # 키워드 부스팅(티켓 10). 비어 있으면 CONFIG에 넣지 않는다. 단어는 `stt.boost.menu_boost_words`
    boost_words: tuple[str, ...] = ()
    boost_weight: float = BOOST_WEIGHT

    def __post_init__(self) -> None:
        if not self.secret_key:
            raise PortError("CLOVA Speech 키가 없어요", code="stt_config", retry=False)
        object.__setattr__(self, "endpoint", normalize_endpoint(self.endpoint))

    @classmethod
    def from_env(cls, env: Mapping[str, str], **overrides: Any) -> ClovaSttSettings:
        """`CLOVA_SPEECH_SECRET_KEY`·`CLOVA_SPEECH_GRPC_ENDPOINT`를 넘겨 받은 사전에서 읽는다."""
        endpoint = env.get("CLOVA_SPEECH_GRPC_ENDPOINT") or DEFAULT_ENDPOINT
        return cls(secret_key=env.get("CLOVA_SPEECH_SECRET_KEY", ""), endpoint=endpoint,
                   **overrides)


def normalize_endpoint(endpoint: str) -> str:
    """`https://host` · `host` · `host:port` → `host:port` (포트가 없으면 50051)."""
    host = endpoint.strip()
    for scheme in ("https://", "http://", "grpcs://", "grpc://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    host = host.rstrip("/")
    if ":" not in host:
        host += ":50051"
    return host


def build_config(settings: ClovaSttSettings) -> dict[str, Any]:
    """CONFIG JSON. 최소 옵션만 (spec 말 끝 판정과 STT 설정).

    부스팅 단어가 있으면 요청별 `keywordBoosting.boostings` 하나로 넣는다(`stt/boost.py`).
    `useDomainBoostings`는 넣지 않는다(같이 쓰면 요청이 실패한다).
    """
    config: dict[str, Any] = {
        "transcription": {"language": settings.language},
        "semanticEpd": {
            "skipEmptyText": True,
            "useWordEpd": True,
            "usePeriodEpd": True,
            "gapThreshold": settings.gap_threshold_ms,
            "durationThreshold": settings.duration_threshold_ms,
        },
    }
    if settings.boost_words and settings.boost_weight > 0:
        config["keywordBoosting"] = {"boostings": [
            {"words": ",".join(settings.boost_words), "weight": settings.boost_weight}
        ]}
    return config


def parse_result(contents: dict[str, Any], offset_ms: int) -> SttResult | None:
    """`transcription` 응답 → `SttResult`. 다른 응답이면 None. 모르는 필드는 무시한다."""
    t = contents.get("transcription")
    if "transcription" not in contents.get("responseType", []) or not isinstance(t, dict):
        return None
    confidence = t.get("confidence")
    return SttResult(
        text=str(t.get("text") or ""),
        start_ms=offset_ms + int(t.get("startTimestamp") or 0),
        end_ms=offset_ms + int(t.get("endTimestamp") or 0),
        epd_type=str(t.get("epdType") or ""),
        ep_flag=bool(t.get("epFlag")),
        seq_id=int(t.get("seqId") or 0),
        confidence=float(confidence) if confidence is not None else None,
    )


class _Connection:
    """CLOVA 연결 하나 (최대 5분). `offset_ms` = 이 연결 전까지 쓴 통화 오디오 길이."""

    def __init__(self, settings: ClovaSttSettings, offset_ms: int, opened_at: float) -> None:
        self.offset_ms = offset_ms
        self.opened_at = opened_at
        if settings.secure:
            self.channel = grpc.aio.secure_channel(settings.endpoint,
                                                   grpc.ssl_channel_credentials())
        else:
            self.channel = grpc.aio.insecure_channel(settings.endpoint)
        stub = nest_pb2_grpc.NestServiceStub(self.channel)
        self.call = stub.recognize(
            metadata=(("authorization", f"Bearer {settings.secret_key}"),)
        )
        self.writing = True
        self.retired = False  # 새 연결로 바꿨다. 남은 결과만 받는다
        self.end_ms = 0  # 바꾼 때의 통화 오디오 시각
        # 써 둔 epFlag 중 응답을 아직 못 받은 수. 0이면 `ep_settled`가 켜진다
        self.ep_pending = 0
        self.ep_last_seq = 0
        self.ep_settled = asyncio.Event()
        self.ep_settled.set()

    async def configure(self, config: dict[str, Any], timeout: float) -> str | None:
        """CONFIG를 쓰고 성공 응답을 기다린다. 부스팅 상태(`config.keywordBoosting.status`,
        부스팅을 안 넣었으면 None)를 돌려준다. 부스팅만 실패하면 통화는 그대로 간다."""
        await self.call.write(nest_pb2.NestRequest(
            type=nest_pb2.CONFIG,
            config=nest_pb2.NestConfig(config=json.dumps(config)),
        ))
        while True:
            response = await asyncio.wait_for(self.call.read(), timeout)
            if response == grpc.aio.EOF:
                raise SttDisconnected("CLOVA Speech가 설정 중에 연결을 닫았어요")
            contents = json.loads(response.contents)
            if "config" in contents.get("responseType", []):
                answer = contents.get("config") or {}
                status = answer.get("status")
                if status != "Success":
                    raise PortError(f"CLOVA Speech 설정 실패: {status}", code="stt_config",
                                    retry=False)
                if "keywordBoosting" not in config:
                    return None
                boost = answer.get("keywordBoosting")
                return str(boost.get("status")) if isinstance(boost, dict) else "missing"

    async def write(self, pcm: bytes, ep_flag: bool, seq_id: int) -> None:
        if ep_flag:  # 쓰기 전에 센다. 응답이 쓰기 `await` 사이에 먼저 읽힐 수 있다
            self.ep_pending += 1
            self.ep_last_seq = seq_id
            self.ep_settled.clear()
        await self.call.write(nest_pb2.NestRequest(
            type=nest_pb2.DATA,
            data=nest_pb2.NestData(
                chunk=pcm, extra_contents=json.dumps({"epFlag": ep_flag, "seqId": seq_id})
            ),
        ))

    def note_ep_reply(self) -> None:
        self.ep_pending = max(0, self.ep_pending - 1)
        if self.ep_pending == 0:
            self.ep_settled.set()

    async def done_writing(self) -> None:
        if self.writing:
            self.writing = False
            await self.call.done_writing()

    async def close(self) -> None:
        self.call.cancel()
        await self.channel.close()


class SttAuthError(PortError):
    def __init__(self, message: str = "CLOVA Speech 인증에 실패했어요") -> None:
        super().__init__(message, code="stt_auth", retry=False)


class SttDisconnected(PortError):
    def __init__(self, message: str = "CLOVA Speech 연결이 끊겼어요") -> None:
        super().__init__(message, code="stt_disconnected", retry=True)


def _port_error(err: grpc.aio.AioRpcError) -> PortError:
    """gRPC 상태 → `PortError`. 메시지에는 상태 이름과 서버 설명만 (키 없음)."""
    code = err.code()
    details = err.details() or ""
    if code in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED):
        return SttAuthError()
    if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
        return PortError("CLOVA Speech 연결이 꽉 찼어요", code="stt_busy", retry=True)
    if code == grpc.StatusCode.INVALID_ARGUMENT:
        return PortError(f"CLOVA Speech 요청 오류: {details}", code="stt_config", retry=False)
    return SttDisconnected(f"CLOVA Speech 연결이 끊겼어요 ({code.name}: {details})")


class ClovaSttStream:
    """통화 하나 = 이 객체 하나. 안에서 5분 제한 때문에 연결을 여러 번 바꿀 수 있다."""

    def __init__(self, settings: ClovaSttSettings, *,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._monotonic = monotonic
        self._queue: asyncio.Queue[SttResult | PortError | None] = asyncio.Queue()
        self._conn: _Connection | None = None
        self._readers: set[asyncio.Task[None]] = set()
        self._retired: set[_Connection] = set()  # 바꾼 뒤 남은 결과를 받는 중인 앞 연결
        self._retirers: set[asyncio.Task[None]] = set()  # 앞 연결 쓰기를 닫을 때를 기다리는 작업
        self._written_bytes = 0
        self._closed = False
        # 마지막 연결 CONFIG 응답의 `keywordBoosting.status`. 부스팅을 안 넣었으면 None
        self.boost_status: str | None = None

    @property
    def audio_ms(self) -> int:
        """지금까지 쓴 통화 오디오 길이 (ms)."""
        return self._written_bytes // BYTES_PER_MS

    async def start(self) -> None:
        self._conn = await self._open()

    async def _open(self) -> _Connection:
        conn = _Connection(self._settings, self.audio_ms, self._monotonic())
        try:
            boost = await conn.configure(build_config(self._settings),
                                         self._settings.config_timeout_s)
        except BaseException as err:
            await conn.close()
            if isinstance(err, grpc.aio.AioRpcError):
                raise _port_error(err) from None
            if isinstance(err, TimeoutError):
                raise PortError("CLOVA Speech 설정 응답이 없어요", code="stt_timeout",
                                retry=True) from None
            raise
        if boost is not None:
            self.boost_status = boost
            words = len(self._settings.boost_words)
            if boost == "Success":
                log.info("CLOVA Speech 키워드 부스팅 %d단어 적용", words)
            else:  # 부스팅 없이 계속한다. 인식은 되므로 통화를 끊을 일은 아니다
                log.warning("CLOVA Speech 키워드 부스팅 실패 (%s, %d단어)", boost, words)
        reader = asyncio.create_task(self._read(conn), name="clova-stt-read")
        self._readers.add(reader)
        reader.add_done_callback(self._readers.discard)
        return conn

    async def _read(self, conn: _Connection) -> None:
        try:
            while True:
                response = await conn.call.read()
                if response == grpc.aio.EOF:
                    break
                result = parse_result(json.loads(response.contents), conn.offset_ms)
                if result is not None:
                    if result.ep_flag:
                        conn.note_ep_reply()
                    self._queue.put_nowait(result)
        except asyncio.CancelledError:
            raise
        except grpc.aio.AioRpcError as err:
            if conn.retired:  # 바꿔 둔 앞 연결. 통화는 새 연결로 이어진다 (리뷰 12번)
                log.info("CLOVA Speech 앞 연결이 오류로 끝남 (%s)", err.code().name)
            elif not self._closed and err.code() != grpc.StatusCode.CANCELLED:
                self._queue.put_nowait(_port_error(err))
            return
        except Exception:
            log.exception("CLOVA Speech 응답 읽기 실패")
            if not self._closed and not conn.retired:
                self._queue.put_nowait(SttDisconnected())
            return
        finally:
            if conn.retired:  # 바꿔 둔 앞 연결: 남은 결과를 다 받았으니 닫는다
                self._retired.discard(conn)
                self._settle_lost_replies(conn)
                await conn.close()
        if not conn.retired and not self._closed:  # 우리가 바꾸지 않았는데 서버가 닫음
            self._queue.put_nowait(SttDisconnected("CLOVA Speech가 연결을 닫았어요"))

    def _settle_lost_replies(self, conn: _Connection) -> None:
        """앞 연결이 epFlag 응답 없이 끝났다(티켓 10 #8). 그 말의 글은 잃었다.

        빈 epFlag 응답(`epd_type="eof"`)을 대신 내서, 세션이 그 응답을 기다리며 붙잡아 둔 조각을
        `hold_cap_s`까지 기다리지 않고 바로 턴으로 넘기게 한다.
        """
        if conn.ep_pending == 0 or self._closed:
            return
        log.warning("CLOVA Speech 앞 연결이 epFlag 응답 %d개 없이 끝남", conn.ep_pending)
        conn.ep_pending = 0
        conn.ep_settled.set()
        self._queue.put_nowait(SttResult("", conn.end_ms, conn.end_ms, "eof", True,
                                         conn.ep_last_seq))

    async def write(self, pcm: bytes, *, ep_flag: bool = False, seq_id: int = 0) -> None:
        if self._closed:
            return
        if self._conn is None:
            raise PortError("STT가 시작되지 않았어요", code="stt", retry=True)
        if self._age() >= self._settings.hard_reconnect_after_s:
            await self._reconnect()  # 턴 사이가 오지 않았다. 5분에 끊기기 전에 바꾼다
        try:
            await self._conn.write(pcm, ep_flag, seq_id)
        except grpc.aio.AioRpcError as err:
            raise _port_error(err) from None
        except (asyncio.InvalidStateError, grpc.aio.UsageError):  # 이미 끝난 RPC에 쓰기
            raise SttDisconnected() from None
        self._written_bytes += len(pcm)
        if ep_flag and self._age() >= self._settings.reconnect_after_s:
            # epFlag 프레임으로 이 연결의 버퍼가 비워졌다(턴 사이). 다음 프레임부터 새 연결
            await self._reconnect()

    def _age(self) -> float:
        assert self._conn is not None
        return self._monotonic() - self._conn.opened_at

    async def _reconnect(self) -> None:
        """앞 연결은 남은 결과를 EOF까지 계속 읽고, 새 프레임은 새 연결로 보낸다.

        앞 연결의 쓰기는 써 둔 epFlag의 응답을 다 받은 뒤에 닫는다(`retire_wait_s`까지 기다림,
        `_retire`). epFlag 프레임 바로 뒤에 닫으면 CLOVA가 뒷조각·epFlag 응답보다 EOF를 먼저 낼
        수 있다(실측 A/B 1/13회, 티켓 10 #8). 그러면 그 턴의 말을 잃는다.

        새 연결의 CONFIG 응답(실측 0.1~0.2초)까지 이 `write`가 기다린다. 기다리는 쪽은 세션의
        STT 쓰기 작업이라 WS 읽기는 멈추지 않는다(밀린 프레임은 세션 큐에 쌓인다).
        """
        old = self._conn
        assert old is not None
        old.retired = True
        old.end_ms = self.audio_ms
        self._retired.add(old)
        retire = asyncio.create_task(self._retire(old), name="clova-stt-retire")
        self._retirers.add(retire)
        retire.add_done_callback(self._retirers.discard)
        log.info("CLOVA Speech 다시 연결 (통화 오디오 %dms)", self.audio_ms)
        self._conn = None
        self._conn = await self._open()

    async def _retire(self, old: _Connection) -> None:
        try:
            await asyncio.wait_for(old.ep_settled.wait(), self._settings.retire_wait_s)
        except TimeoutError:
            log.info("CLOVA Speech 앞 연결 epFlag 응답이 %.0f초 안에 없어 쓰기를 닫음",
                     self._settings.retire_wait_s)
        try:
            await old.done_writing()
        except Exception:
            log.info("CLOVA Speech 앞 연결 쓰기 닫기 실패", exc_info=True)

    def results(self) -> AsyncIterator[SttResult]:
        return self._results()

    async def _results(self) -> AsyncIterator[SttResult]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, PortError):
                raise item
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        conns = [c for c in (self._conn, *self._retired) if c is not None]
        self._conn = None
        self._retired.clear()
        for conn in conns:
            with contextlib.suppress(Exception):
                await conn.close()
        tasks = [*self._readers, *self._retirers]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._queue.put_nowait(None)
