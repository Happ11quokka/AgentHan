"""배포 파일 `deploy/phone/` 안전장치 (티켓 09). Docker 없이 글자만 본다.

- 서버 `.env` 틀에 값이 없다(키가 git에 안 들어간다)
- 빌드 맥락에서 `.env`가 빠지고, Dockerfile이 키를 `ENV`·`ARG`로 받지 않는다
- Caddy 인증서 폴더(`/data`)가 이름 있는 볼륨이다(재배포마다 재발급 → LE 한도)
- 처음엔 Let's Encrypt 스테이징, 앱 하나가 화면과 WS를 같이 낸다
"""

from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "phone"
KEY_NAMES = (
    "CLOVASTUDIO_API_KEY",
    "CLOVA_SPEECH_SECRET_KEY",
    "CLOVA_SPEECH_GRPC_ENDPOINT",
    "CLOVA_VOICE_CLIENT_ID",
    "CLOVA_VOICE_CLIENT_SECRET",
)


def _text(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_server_env_template_has_names_only() -> None:
    lines = [ln for ln in _text(".env.example").splitlines() if ln and not ln.startswith("#")]
    assigned = dict(ln.split("=", 1) for ln in lines)
    assert all(value == "" for value in assigned.values()), assigned
    assert set(KEY_NAMES) | {"DOMAIN", "ACME_CA"} <= set(assigned)


def test_keys_never_reach_the_image() -> None:
    ignore = _text("Dockerfile.dockerignore").splitlines()
    assert "**/.env" in ignore and "**/.env.*" in ignore
    dockerfile = _text("Dockerfile")
    for key in KEY_NAMES:
        assert key not in dockerfile
    assert not re.search(r"^\s*COPY\s+\.\s", dockerfile, re.M), "저장소 통째 COPY 금지"


def test_caddy_keeps_certificates_on_a_named_volume_and_starts_on_staging() -> None:
    compose = _text("compose.yaml")
    assert "caddy_data:/data" in compose
    assert re.search(r"^  caddy_data:", compose, re.M), "이름 있는 볼륨으로 선언"
    assert "acme-staging-v02.api.letsencrypt.org" in compose
    caddyfile = _text("Caddyfile")
    assert "{$DOMAIN}" in caddyfile and "acme_ca {$ACME_CA}" in caddyfile
    assert "reverse_proxy app:8000" in caddyfile


def test_the_app_serves_the_react_build_and_the_socket_on_one_origin() -> None:
    dockerfile = _text("Dockerfile")
    assert "COPY --from=web /web/dist web/dist" in dockerfile
    assert "PHONE_WEB_DIST=/app/web/dist" in dockerfile
    assert "PHONE_TZ=Asia/Seoul" in dockerfile and "GRPC_DNS_RESOLVER=native" in dockerfile
    assert '"agenthan.voice.server", "--host", "0.0.0.0"' in dockerfile


def test_the_image_fetches_the_pinned_smart_turn_model_and_checks_it() -> None:
    # 3단계 티켓 03: 고정 리비전 URL + SHA-256. 앱 코드의 고정값과 같아야 앱 시작 확인도 통과한다
    from agenthan.voice.settings import DEFAULT_TURN_MODEL_PATH
    from agenthan.voice.smart_turn import MODEL_SHA256, MODEL_URL

    dockerfile = _text("Dockerfile")
    assert dockerfile.splitlines()[0].startswith("# syntax=docker/dockerfile:1."), "ADD --checksum"
    add = re.search(r"^ADD (.+?)(?<!\\)$", dockerfile, re.M | re.S)
    assert add, "모델은 ADD로 받는다"
    line = add.group(0).replace("\\\n", " ")
    assert f"--checksum=sha256:{MODEL_SHA256}" in line
    assert MODEL_URL in line and "/resolve/main/" not in line  # 브랜치가 아니라 고정 리비전
    assert "--chmod=644" in line  # URL로 받은 파일은 600이라 app 사용자가 못 읽는다
    # ADD가 만든 폴더는 app 사용자가 못 들어간다. 먼저 755로 만든다
    assert dockerfile.index("install -d -m 755 /app/models") < dockerfile.index(add.group(0))
    assert line.split()[-1] == str(DEFAULT_TURN_MODEL_PATH)
    assert f"PHONE_TURN_MODEL_PATH={DEFAULT_TURN_MODEL_PATH}" in dockerfile
    assert "ORT_DISABLE_TELEMETRY=1" in dockerfile  # onnxruntime 1DS 사용 기록 전송 끔


def test_the_model_licence_notice_ships_with_the_image() -> None:
    notice = _text("NOTICE-smart-turn.txt")
    assert "BSD 2-Clause License" in notice and "Daily" in notice
    assert "COPY deploy/phone/NOTICE-smart-turn.txt" in _text("Dockerfile")
