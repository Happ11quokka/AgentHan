"""배포 파일 `deploy/phone/` 안전장치 (티켓 09). Docker 없이 글자만 본다.

- 서버 `.env` 틀에 값이 없다(키가 git에 안 들어간다)
- 빌드 맥락에서 `.env`가 빠지고, Dockerfile이 키를 `ENV`·`ARG`로 받지 않는다
- Caddy 인증서 폴더(`/data`)가 이름 있는 볼륨이다(재배포마다 재발급 → LE 한도)
- 처음엔 Let's Encrypt 스테이징, 앱 하나가 화면과 WS를 같이 낸다
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

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
    # 전화 앱에 사장님 비서(/assistant)를 붙인 발표 서버로 뜬다 (agenthan.serve)
    assert '"agenthan.serve", "--host", "0.0.0.0"' in dockerfile
    assert "AGENTHAN_SESSIONS_DIR=/app/sessions" in dockerfile


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


def test_caddy_hides_the_owner_token_in_the_access_log() -> None:
    # 6단계: 사장님 토큰은 쿼리 `?key=`로 온다. 접속 로그에 그대로 남기지 않는다
    caddyfile = _text("Caddyfile")
    assert re.search(r"log \{\s+format filter \{\s+request>uri query \{\s+replace key REDACTED",
                     caddyfile)
    assert "Referrer-Policy no-referrer" in caddyfile
    assert not re.search(r"^\tlog$", caddyfile, re.M), "가리지 않는 기본 log를 남기지 않는다"


# ---------------------------------------------------------------- 6단계: 배포 안전


def test_the_container_health_check_asks_healthz_and_the_image_knows_its_commit() -> None:
    dockerfile = _text("Dockerfile")
    check = dockerfile[dockerfile.index("HEALTHCHECK"):]
    assert "http://127.0.0.1:8000/healthz" in check
    assert "ARG GIT_SHA=dev" in dockerfile and "PHONE_VERSION=$GIT_SHA" in dockerfile
    assert "GIT_SHA: ${GIT_SHA:-dev}" in _text("compose.yaml")


def test_compose_tells_the_app_to_trust_caddy_and_only_its_own_origin() -> None:
    compose = _text("compose.yaml")
    assert "PHONE_TRUSTED_PROXIES: ${PHONE_TRUSTED_PROXIES:-private}" in compose
    assert "PHONE_ALLOWED_ORIGINS: ${PHONE_ALLOWED_ORIGINS:-https://${DOMAIN}}" in compose
    assert not re.search(r"^\s+ports:\s*\n\s+- \"?8000", compose, re.M), "8000은 밖에 열지 않는다"


FAKE_DOCKER = """#!/usr/bin/env bash
# 가짜 docker: 받은 인자를 적고, /healthz 질문에는 ACTIVE 파일의 다음 줄을 답한다
echo "$*" >> "$FAKE_LOG"
case "$*" in
  *"image ls"*) printf '%s\\n' latest prev new1 new2 new3 old1 old2 ;;
  *healthz*)
    if [[ ! -s "$FAKE_ACTIVE" ]]; then exit 1; fi
    head -n 1 "$FAKE_ACTIVE"
    if [[ $(wc -l < "$FAKE_ACTIVE") -gt 1 ]]; then sed -i.bak 1d "$FAKE_ACTIVE"; fi
    ;;
esac
exit 0
"""


def _deploy(tmp_path: Path, *args: str, active: str = "", **env: str) -> tuple[list[str], str]:
    """`deploy.sh`를 가짜 docker로 돌린다. (docker 호출 목록, 출력)."""
    if shutil.which("bash") is None:
        pytest.skip("bash 없음")
    root = tmp_path / "repo"
    (root / "deploy" / "phone").mkdir(parents=True, exist_ok=True)
    script = root / "deploy" / "phone" / "deploy.sh"
    script.write_text(_text("deploy.sh"), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (root / "deploy" / "phone" / ".env").write_text("DOMAIN=x\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log, active_file = tmp_path / "docker.log", tmp_path / "active"
    log.write_text("")
    active_file.write_text(active)
    run = subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "FAKE_LOG": str(log), "FAKE_ACTIVE": str(active_file), "DRAIN_POLL_S": "0",
             "GIT_DIR": str(tmp_path / "no-git"), **env},
    )
    assert run.returncode == 0, run.stderr
    return log.read_text().splitlines(), run.stdout + run.stderr


def _index(calls: list[str], needle: str) -> int:
    found = [i for i, call in enumerate(calls) if needle in call]
    assert found, f"{needle!r} 호출이 없다: {calls}"
    return found[0]


def test_deploy_builds_first_then_drains_waits_for_calls_and_only_then_swaps(tmp_path) -> None:
    calls, out = _deploy(tmp_path, "up", active="2\n2\n1\n0\n")
    build = _index(calls, "compose.yaml build app")
    drain = _index(calls, "touch /app/logs/DRAIN")
    swap = _index(calls, "up -d --no-build")
    undrain = _index(calls, "rm -f /app/logs/DRAIN")
    assert build < drain < swap < undrain
    waits = [i for i, call in enumerate(calls) if "healthz" in call]
    assert len(waits) == 4 and drain < waits[1] and waits[-1] < swap  # 0이 될 때까지 물어봤다
    assert not any("--build" in call.split() for call in calls), "교체할 때 다시 빌드하지 않는다"
    assert "새 통화 받기를 멈췄어요" in out


def test_deploy_stops_waiting_at_the_time_limit(tmp_path) -> None:
    calls, out = _deploy(tmp_path, "up", active="3\n", DRAIN_TIMEOUT_S="3", DRAIN_POLL_S="1")
    assert _index(calls, "touch /app/logs/DRAIN") < _index(calls, "up -d --no-build")
    assert "남은 채로 교체" in out


def test_the_first_deploy_has_nothing_to_drain(tmp_path) -> None:
    calls, out = _deploy(tmp_path, "up", active="")  # /healthz가 답하지 않는다
    assert not any("touch /app/logs/DRAIN" in call for call in calls)
    assert _index(calls, "build app") < _index(calls, "up -d --no-build")
    assert "건너뛰어요" in out


def test_deploy_keeps_only_the_newest_image_tags(tmp_path) -> None:
    calls, _ = _deploy(tmp_path, "up", active="0\n", KEEP_IMAGES="3")
    removed = [call.split()[-1] for call in calls if call.startswith("rmi ")]
    assert removed == ["agenthan-phone:old1", "agenthan-phone:old2"]  # latest·prev·최근 3개는 남김
    assert any(call.startswith("image prune -f") for call in calls)


def test_rollback_also_drains_before_swapping_and_drain_can_be_switched_by_hand(tmp_path) -> None:
    calls, _ = _deploy(tmp_path, "rollback", active="1\n0\n")
    assert (_index(calls, "touch /app/logs/DRAIN") < _index(calls, "tag agenthan-phone:prev")
            < _index(calls, "--force-recreate app") < _index(calls, "rm -f /app/logs/DRAIN"))
    calls, out = _deploy(tmp_path, "drain", active="2\n")
    assert any("touch /app/logs/DRAIN" in call for call in calls) and "2개" in out
    calls, _ = _deploy(tmp_path, "undrain")
    assert any("rm -f /app/logs/DRAIN" in call for call in calls)
