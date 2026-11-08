"""개발용 서버: `voice`만 띄우는 작은 FastAPI 앱 (티켓 04). 더미 가게로 띄운다.

    uv run python -m agenthan.voice.server            # http://127.0.0.1:8000, WS /api/phone/ws
    uv run python -m agenthan.voice.server --host 0.0.0.0 --port 8000 --reload

저장소 루트에서 실행한다(`.env`·`web/dist`·`logs/`를 지금 폴더 기준으로 찾는다).
설정은 `settings.py` 머리 주석의 `PHONE_*` 변수. 키가 없어도 기본값(전부 `fake`)으로 뜬다.

11/17 통합 때 `app/`은 이 파일을 쓰지 않고 `ws.phone_router(...)`만 붙인다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from fastapi import FastAPI

from agenthan.core.contracts import Order
from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.ws import phone_router

log = logging.getLogger(__name__)


class LoggedOrderSink(MemoryOrderSink):
    """2단계 `OrderSink`: 메모리에 쌓고 로그 한 줄 (주문 내용은 `order` 이벤트로 화면에 간다)."""

    def submit(self, order: Order) -> None:
        super().submit(order)
        log.info("주문 %s: %s, 항목 %d개, 픽업 %s",
                 order.id, order.status, len(order.lines), order.pickup_time)


def create_app(settings: VoiceSettings | None = None) -> FastAPI:
    """`clova`를 골랐는데 어댑터가 없으면 `AdapterMissing`으로 뜨기 전에 멈춘다."""
    settings = settings or VoiceSettings.from_env()
    fixture = settings.store_fixture
    store = (
        FakeStoreKnowledge.from_fixture(fixture) if fixture else FakeStoreKnowledge.from_fixture()
    )
    app = FastAPI(title="AgentHan 전화 주문 (개발용)")
    app.include_router(
        phone_router(
            store=store,
            config=StoreConfig.from_fixture(fixture),
            order_sink=LoggedOrderSink(),
            settings=settings,
        )
    )
    if (settings.web_dist / "index.html").is_file():
        # 일반 라우트(WS 포함)가 먼저고, 없는 화면 경로(`/phone`)는 index.html로 (Accept: text/html)
        app.frontend("/", directory=settings.web_dist)
    else:
        log.info("%s가 없어 화면 없이 WS만 띄움 (npm run build 하면 같이 냄)", settings.web_dist)
    log.info("통화 설정: NLU=%s STT=%s TTS=%s 시각=%s",
             settings.nlu, settings.stt, settings.tts, settings.now or "지금")
    return app


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="voice 개발용 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="코드가 바뀌면 다시 띄운다")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run("agenthan.voice.server:create_app", factory=True, host=args.host,
                port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
