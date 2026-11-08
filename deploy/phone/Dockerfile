# syntax=docker/dockerfile:1.7
# 전화 주문 서버 이미지 (티켓 09). 화면(React 빌드)과 /api/phone/ws를 한 오리진으로 낸다.
#
#   빌드 맥락은 저장소 루트다. compose.yaml이 그렇게 부른다:
#     docker compose -f deploy/phone/compose.yaml up -d --build
#   혼자 빌드할 때(저장소 루트에서):
#     docker build -f deploy/phone/Dockerfile -t agenthan-phone .
#
# 키는 이미지에 넣지 않는다. 실행할 때 compose의 env_file(deploy/phone/.env)로만 들어간다.
# 무엇을 맥락에서 빼는지는 옆의 Dockerfile.dockerignore.
# 첫 줄의 문법 지시자는 지우지 않는다: 모델을 받는 `ADD --checksum`이 dockerfile 1.6 이상에서만 된다.

# ---- 1단계: 화면 빌드 (Node) ---------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2단계: 실행 (Python 3.11 + uv) ----------------------------------------------------------
FROM python:3.11-slim-bookworm AS app
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# 의존성만 먼저 (코드만 바뀐 재배포는 이 층을 다시 쓰지 않는다). --locked: uv.lock이 안 맞으면 실패
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# Smart Turn 모델 (3단계 티켓 03, 자체 호스팅 오픈 모델 BSD-2). HF 고정 리비전에서 받고 SHA-256이
# 다르면 빌드가 실패한다. 값은 `voice/smart_turn.py`의 MODEL_URL·MODEL_SHA256과 같다(배포 테스트가 본다).
# URL로 받은 파일은 기본 600이라 app 사용자가 읽게 644로 둔다. 앱도 시작할 때 체크섬을 한 번 더 본다
# (틀리거나 없으면 끝말 규칙만으로 뜬다). huggingface.co에 못 닿는 서버면 이 줄에서 빌드가 멈춘다.
# 폴더를 먼저 만든다: ADD가 만드는 폴더는 app 사용자가 들어가지 못한다(2026-11-08 amd64 빌드 확인)
RUN install -d -m 755 /app/models
ADD --chmod=644 \
    --checksum=sha256:2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f \
    https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx \
    /app/models/smart-turn-v3.2-cpu.onnx
COPY deploy/phone/NOTICE-smart-turn.txt /app/models/NOTICE-smart-turn.txt

# 코드. 편집 설치(기본)라 core/fakes.py가 저장소 루트 기준으로 fixtures/를 찾는다(/app/fixtures)
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY fixtures/ fixtures/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

COPY --from=web /web/dist web/dist

# 루트로 돌리지 않는다. logs/는 볼륨이 붙는 자리라 미리 만들어 둔다(이름 있는 볼륨이 소유자를 물려받음)
RUN useradd --system --uid 10001 --home-dir /app app \
    && mkdir -p /app/logs && chown app:app /app/logs
USER app

# 코드 기본값과 같지만 서버에서 헷갈리지 않게 적어 둔다. 키는 여기 적지 않는다
ENV PHONE_TZ=Asia/Seoul \
    PHONE_WEB_DIST=/app/web/dist \
    PHONE_LOG_DIR=/app/logs \
    PHONE_TURN_MODEL_PATH=/app/models/smart-turn-v3.2-cpu.onnx \
    ORT_DISABLE_TELEMETRY=1 \
    GRPC_DNS_RESOLVER=native

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)"]
CMD ["python", "-m", "agenthan.voice.server", "--host", "0.0.0.0", "--port", "8000"]
