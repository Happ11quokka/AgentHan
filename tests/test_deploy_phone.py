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
