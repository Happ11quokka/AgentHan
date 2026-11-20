"""발표 서버: 전화 주문(Part C)과 사장님 비서(Part A)를 한 앱·한 주소로 낸다.

    uv run --env-file .env python -m agenthan.serve --port 8000

- 전화 쪽(`agenthan.voice.server`)이 `/api/*`·WS·화면(`web/dist`)을 내고, 여기서 비서 API
  (`/assistant/*`, `agenthan.app.server`)를 같은 앱에 붙인다.
  화면은 주소로 갈린다(`web/src/main.tsx`).
- voice는 core 말고 다른 파트를 import하지 않는 규칙이라, 둘을 묶는 일은 이 파일이 한다.
- AGENTHAN_DB_URL이 있으면 전화 가게 메뉴를 DB에서 읽고 확정·넘김 주문을 phone_orders 표에 남긴다
  (`agenthan.db`). 비서 가게 파일도 쓸 때마다 DB에 한 벌 남는다(tools/dummy_store.py).
- AGENTHAN_ASSISTANT=off면 비서를 붙이지 않는다(전화만). 대화마다 가게 파일을
  AGENTHAN_SESSIONS_DIR(기본 .agenthan/sessions)에 쓴다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from fastapi import FastAPI

from agenthan.voice import server as voice_server
from agenthan.voice.settings import VoiceSettings

log = logging.getLogger(__name__)

ASSISTANT_ENV = "AGENTHAN_ASSISTANT"
SESSIONS_ENV = "AGENTHAN_SESSIONS_DIR"


def create_app(settings: VoiceSettings | None = None) -> FastAPI:
    settings = settings or VoiceSettings.from_env()
    store_raw, sinks = phone_db(settings)
    knowledge = phone_knowledge(store_raw, settings)
    app = voice_server.create_app(
        settings, store_raw=store_raw, order_sinks=sinks, knowledge=knowledge
    )
    include_assistant(app, settings.env)  # from_env면 os.environ·.env를 합친 값
    return app


def phone_db(settings: VoiceSettings) -> tuple[dict | None, list]:
    """AGENTHAN_DB_URL이 있으면 전화 가게를 DB(stores.raw)에서 읽고, 주문을 phone_orders에 남긴다.
    DB가 없거나 그 가게가 DB에 없으면 지금처럼 PHONE_STORE_FIXTURE 파일을 읽는다."""
    from agenthan import db

    url = settings.env.get(db.DB_URL_ENV, "").strip()
    if not url:
        return None, []
    from agenthan.core.fakes import SAMPLE_STORE
    from agenthan.db.orders import PgOrderSink
    from agenthan.db.stores import phone_store_raw

    fixture = settings.store_fixture or SAMPLE_STORE
    raw = phone_store_raw(fixture, url)
    store_id = str(((raw or {}).get("store") or {}).get("id") or "")
    log.info("DB 켬: 전화 가게 %s, 주문은 phone_orders에도 남김",
             f"DB({store_id})" if raw else f"파일({fixture.name}, DB에 없음)")
    return raw, [PgOrderSink(store_id=store_id, url=url)]


def phone_knowledge(store_raw: dict | None, settings: VoiceSettings):
    """전화 가게를 Part B 검색(RagStoreKnowledge)으로 연다.

    DB에 그 가게가 있으면 DB(벡터는 pgvector, 리뷰 의미 검색 포함), 없으면 None(전화 기본 가게
    지식). 메뉴 목록은 가게 JSON과 같다."""
    store_id = str(((store_raw or {}).get("store") or {}).get("id") or "")
    if not store_id:
        return None
    from agenthan.rag import RagStoreKnowledge

    url = settings.env.get("AGENTHAN_DB_URL", "").strip() or None
    try:
        knowledge = RagStoreKnowledge.from_db(store_id, url=url)
    except Exception as error:
        log.warning("전화 가게 지식을 Part B로 못 열어 기본으로: %s", type(error).__name__)
        return None
    log.info("전화 가게 지식: Part B 검색 (DB %s, 벡터 %s)", store_id,
             "켬" if knowledge.vector.enabled else "끔(CLOVASTUDIO_API_KEY 없음)")
    return knowledge


def include_assistant(app: FastAPI, env: Mapping[str, str]) -> None:
    if env.get(ASSISTANT_ENV, "on").strip().lower() == "off":
        log.info("%s=off라 사장님 비서(/assistant)는 붙이지 않음", ASSISTANT_ENV)
        return
    from agenthan.app import pilot
    from agenthan.app.server import DEFAULT_ROOT
    from agenthan.app.server import create_app as create_assistant_app

    root = env.get(SESSIONS_ENV, "").strip() or DEFAULT_ROOT
    app.include_router(create_assistant_app(make_assistant=pilot.assistant, root=root).router)
    log.info("사장님 비서(/assistant) 붙임, 대화 파일은 %s", root)


def main(argv: Sequence[str] | None = None) -> None:
    voice_server.main(argv, factory="agenthan.serve:create_app")


if __name__ == "__main__":
    main()
