"""테스트용 로컬 `grpc.aio` 가짜 CLOVA `NestService` (spec STT 어댑터 테스트).

CLOVA 실측(`research/s2-verify-clova.md`)에 맞춘 동작:
- 인증 메타데이터가 틀리면 첫 응답 자리에서 `UNAUTHENTICATED` / "Invalid authentication token"
- CONFIG에 `{"config": {"status": ..., "contextCode": {...}}, "responseType": ["config"]}`
  (`contextCode`는 문서에 없는 필드. 어댑터가 무시해야 한다)
- `epFlag=true` DATA마다 `replies`의 다음 글을 `endPoint` 결과로 낸다. 다 쓰면 빈 글 + `epFlag`
- 시각(`startTimestamp`·`endTimestamp`)은 이 연결에서 받은 오디오 기준 (연결마다 0부터)
- 클라이언트가 쓰기를 끝내면(`done_writing`) 스트림을 닫는다(EOF)

`Connection.push(json)`으로 아무 응답이나 끼워 넣고, `Connection.abort(code, details)`로 끊는다.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import grpc

from agenthan.voice.stt import nest_pb2, nest_pb2_grpc

KEY = "test-secret-key"
BYTES_PER_MS = 32


@dataclass
class Connection:
    metadata: dict[str, str]
    requests: list[Any] = field(default_factory=list)
    audio_bytes: int = 0
    utter_start_ms: int = 0
    writing_done: bool = False
    out: asyncio.Queue = field(default_factory=asyncio.Queue)

    @property
    def configs(self) -> list[dict]:
        return [json.loads(r.config.config) for r in self.requests if r.type == nest_pb2.CONFIG]

    @property
    def data(self) -> list[Any]:
        return [r for r in self.requests if r.type == nest_pb2.DATA]

    @property
    def extras(self) -> list[dict]:
        return [json.loads(r.data.extra_contents or "{}") for r in self.data]

    @property
    def audio_ms(self) -> int:
        return self.audio_bytes // BYTES_PER_MS

    def push(self, contents: dict) -> None:
        self.out.put_nowait(("send", contents))

    def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.out.put_nowait(("abort", (code, details)))


def transcription(text: str, start: int, end: int, *, epd: str = "gap", ep_flag: bool = False,
                  seq_id: int = 0, confidence: float = 0.9) -> dict:
    """CLOVA `transcription` 응답 하나 (실측 모양, 필드 일부)."""
    return {
        "responseType": ["transcription"],
        "uid": "fake-uid",
        "transcription": {
            "text": text, "position": 0, "periodPositions": [], "periodAlignIndices": [],
            "epFlag": ep_flag, "seqId": seq_id, "epdType": epd,
            "startTimestamp": start, "endTimestamp": end, "confidence": confidence,
            "alignInfos": [],
        },
    }


class FakeNest(nest_pb2_grpc.NestServiceServicer):
    def __init__(self, replies: Iterable[str] = (), *, key: str = KEY,
                 config_status: str = "Success", boost_status: str = "Success") -> None:
        self.key = key
        self.config_status = config_status
        self.boost_status = boost_status  # CONFIG에 keywordBoosting이 있을 때 응답 상태
        self.replies = deque(replies)
        self.connections: list[Connection] = []
        self.connected = asyncio.Condition()

    async def wait_connections(self, n: int, timeout: float = 3.0) -> Connection:
        async with self.connected:
            await asyncio.wait_for(
                self.connected.wait_for(lambda: len(self.connections) >= n), timeout
            )
        return self.connections[n - 1]

    def _on_request(self, conn: Connection, req: Any) -> None:
        conn.requests.append(req)
        if req.type == nest_pb2.CONFIG:
            config = {"status": self.config_status, "contextCode": {"status": "Success"},
                      "transcription": {"status": "Success"},
                      "semanticEpd": {"status": "Success"}}
            if "keywordBoosting" in json.loads(req.config.config):  # 실측 A/B 응답 모양
                config["keywordBoosting"] = {"status": self.boost_status}
            conn.push({"config": config, "responseType": ["config"], "uid": "fake-uid"})
            return
        conn.audio_bytes += len(req.data.chunk)
        extra = json.loads(req.data.extra_contents or "{}")
        if extra.get("epFlag"):
            text = self.replies.popleft() if self.replies else ""
            conn.push(transcription(text, conn.utter_start_ms, conn.audio_ms, epd="endPoint",
                                    ep_flag=True, seq_id=extra.get("seqId", 0)))
            conn.utter_start_ms = conn.audio_ms

    async def recognize(self, request_iterator, context):
        conn = Connection(metadata=dict(context.invocation_metadata()))
        async with self.connected:
            self.connections.append(conn)
            self.connected.notify_all()
        if conn.metadata.get("authorization") != f"Bearer {self.key}":
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid authentication token")

        async def read() -> None:
            async for req in request_iterator:
                self._on_request(conn, req)
            conn.writing_done = True
            conn.out.put_nowait(("eof", None))

        reader = asyncio.create_task(read())
        try:
            while True:
                kind, payload = await conn.out.get()
                if kind == "eof":
                    return
                if kind == "abort":
                    await context.abort(*payload)
                yield nest_pb2.NestResponse(contents=json.dumps(payload, ensure_ascii=False))
        finally:
            reader.cancel()


class NestServer:
    """`async with NestServer(FakeNest(...)) as srv:` → `srv.endpoint` (127.0.0.1:포트, 평문)."""

    def __init__(self, servicer: FakeNest) -> None:
        self.servicer = servicer
        self.server = grpc.aio.server()
        nest_pb2_grpc.add_NestServiceServicer_to_server(servicer, self.server)
        self.port = self.server.add_insecure_port("127.0.0.1:0")
        self.endpoint = f"127.0.0.1:{self.port}"

    async def __aenter__(self) -> NestServer:
        await self.server.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.server.stop(grace=None)
