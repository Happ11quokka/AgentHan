"""발표 서버: 전화 주문(Part C)과 사장님 비서(Part A)를 한 앱·한 주소로 낸다.

    uv run --env-file .env python -m agenthan.serve --port 8000

- 전화 쪽(`agenthan.voice.server`)이 `/api/*`·WS·화면(`web/dist`)을 내고, 여기서 비서 API
  (`/assistant/*`, `agenthan.app.server`)를 같은 앱에 붙인다.
  화면은 주소로 갈린다(`web/src/main.tsx`).
- voice는 core 말고 다른 파트를 import하지 않는 규칙이라, 둘을 묶는 일은 이 파일이 한다.
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
    app = voice_server.create_app(settings)
    include_assistant(app, settings.env)  # from_env면 os.environ·.env를 합친 값
    return app


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
