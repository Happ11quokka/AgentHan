"""개발용 서버: `voice`만 띄우는 작은 FastAPI 앱 (티켓 04). 더미 가게로 띄운다.

    uv run python -m agenthan.voice.server            # http://127.0.0.1:8000, WS /api/phone/ws
    uv run python -m agenthan.voice.server --host 0.0.0.0 --port 8000 --reload

저장소 루트에서 실행한다(`.env`·`web/dist`·`logs/`를 지금 폴더 기준으로 찾는다).
설정은 `settings.py` 머리 주석의 `PHONE_*` 변수. 키가 없어도 기본값(전부 `fake`)으로 뜬다.
확정 주문은 사장님 허브(4단계)로 간다. 사장님 화면은 `/owner?key=<PHONE_OWNER_KEY>`,
SSE는 `/api/owner/stream?key=…` (`owner/http.py`).
Papago 키가 있으면 시작 때 연결을 한 번 데우고(최대 `PHONE_TRANSLATE_TIMEOUT_S`초, 실패해도 뜬다)
끝날 때 닫는다(티켓 12).

11/17 통합 때 `app/`은 이 파일을 쓰지 않고 허브·`ws.phone_router(...)`·`owner_router(...)`를
조립한다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.housekeeping import INTERVAL_S, Housekeeper
from agenthan.voice.owner.http import customer_texts_router, owner_router
from agenthan.voice.owner.hub import OwnerHub
from agenthan.voice.owner.papago import papago_from_env
from agenthan.voice.runtime import HcxRuntime
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_check import ensure_usable
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.ws import make_gate, phone_router, store_clock

log = logging.getLogger(__name__)


def create_app(settings: VoiceSettings | None = None) -> FastAPI:
    """`clova`를 골랐는데 어댑터가 없으면 `AdapterMissing`으로 뜨기 전에 멈춘다."""
    settings = settings or VoiceSettings.from_env()
    fixture = settings.store_fixture
    store = (
        FakeStoreKnowledge.from_fixture(fixture) if fixture else FakeStoreKnowledge.from_fixture()
    )
    config = StoreConfig.from_fixture(fixture)
    # 가게 데이터 검사 (5단계 티켓 06): 오류면 여기서 멈추고, 경고는 로그에 남긴다
    ensure_usable(store.list_menu(), config, log)
    # 확정 주문과 통화 상황(통화 중·넘김·미완료)을 받아 사장님 화면으로 보낸다(주문은 로그 한 줄도
    # 남긴다). 손님 화면의 `order` 이벤트는 그대로
    # 손님 요청·넘김 카드 손님 말은 Papago로 사장님 언어(ru)로 옮긴다. 키가 없으면 원문만 (티켓 03)
    translator = papago_from_env(settings.env, timeout=settings.translate_timeout_s,
                                 keepalive=settings.translate_keepalive_s)
    if translator is None:
        log.info("PAPAGO_CLIENT_ID·PAPAGO_CLIENT_SECRET이 없어 사장님 카드를 번역하지 않음")
    hub = OwnerHub(store=store, clock=store_clock(settings), translator=translator,
                   question_wait_s=settings.owner_wait_s)
    # 6단계: 문지기와 HCX 연결은 프로세스에 하나. 서버가 끝날 때 닫는다
    gate = make_gate(settings)
    hcx = HcxRuntime(settings) if settings.nlu == "hcx" else None
    keeper = Housekeeper(log_dir=settings.log_dir, record_dir=settings.record_dir,
                         keep_days=settings.log_keep_days, disk_warn_pct=settings.disk_warn_pct)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 통화 첫 카드가 새 연결 비용(실측 P50 1.06초)을 내지 않게 시작 때 한 번 보낸다 (티켓 12)
        if translator is not None and await translator.warm():
            log.info("Papago 연결을 데움")
        # 보관 기한이 지난 손님 말 기록·녹음을 지우고 디스크를 본다. 시작 때 한 번은 기다린다
        await asyncio.to_thread(keeper.run_once)
        chores = asyncio.create_task(_later(keeper))
        try:
            yield
        finally:
            chores.cancel()
            await asyncio.gather(chores, return_exceptions=True)
            if translator is not None:
                await translator.aclose()
            if hcx is not None:
                hcx.close()

    app = FastAPI(title="AgentHan 전화 주문 (개발용)", lifespan=lifespan)
    app.include_router(
        phone_router(
            store=store,
            config=config,
            order_sink=hub,
            settings=settings,
            owner=hub,
            knowledge=hub.knowledge,  # 사장님 보충 지식: 허브가 쓰고 통화 엔진이 읽는다 (티켓 13)
            gate=gate,
            hcx=hcx,
        )
    )
    app.include_router(owner_router(hub, key=settings.owner_key))
    app.include_router(customer_texts_router(hub))  # 손님 "받은 문자" 스트림 (티켓 07)
    if not settings.owner_key:
        log.warning("PHONE_OWNER_KEY가 없어 사장님 화면(/api/owner/*)이 잠겨 있음 (모두 401)")
    if (settings.web_dist / "index.html").is_file():
        # 일반 라우트(WS 포함)가 먼저고, 없는 화면 경로(`/phone`)는 index.html로 (Accept: text/html)
        app.frontend("/", directory=settings.web_dist)
    else:
        log.info("%s가 없어 화면 없이 WS만 띄움 (npm run build 하면 같이 냄)", settings.web_dist)
    log.info("통화 설정: NLU=%s STT=%s TTS=%s 시각=%s",
             settings.nlu, settings.stt, settings.tts, settings.now or "지금")
    return app


async def _later(keeper: Housekeeper) -> None:
    await asyncio.sleep(INTERVAL_S)
    await keeper.run_forever()


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="voice 개발용 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="코드가 바뀌면 다시 띄운다")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = VoiceSettings.from_env()
    # 접속 로그는 끈다: 사장님 토큰이 쿼리(`?key=`)로 와서 줄마다 남는다. 접속 기록은 Caddy가
    # 토큰을 가리고 남긴다(6단계). WS 메시지는 64KB까지(uvicorn 기본은 16MB)
    uvicorn.run("agenthan.voice.server:create_app", factory=True, host=args.host,
                port=args.port, reload=args.reload, ws_max_size=settings.ws_max_bytes,
                access_log=False)


if __name__ == "__main__":
    main()
