#!/usr/bin/env bash
# 전화 주문 배포 도우미 (티켓 09). 서버의 저장소 루트에서 부른다.
#
#   ./deploy/phone/deploy.sh up              배포·재배포 (지금 이미지를 prev로 남기고 새로 빌드)
#   ./deploy/phone/deploy.sh rollback [태그]  이전 이미지로 되돌림 (태그 생략 = prev)
#   ./deploy/phone/deploy.sh images          되돌릴 수 있는 태그 목록
#   ./deploy/phone/deploy.sh logs [app|caddy] 로그 따라 보기
#   ./deploy/phone/deploy.sh ps              상태
set -euo pipefail

cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f deploy/phone/compose.yaml)
IMAGE=agenthan-phone

if [[ ! -f deploy/phone/.env ]]; then
  echo "deploy/phone/.env가 없어요. cp deploy/phone/.env.example deploy/phone/.env 후 값을 채우세요" >&2
  exit 1
fi

case "${1:-}" in
  up)
    if docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
      docker tag "$IMAGE:latest" "$IMAGE:prev"
    fi
    "${COMPOSE[@]}" up -d --build
    docker tag "$IMAGE:latest" "$IMAGE:$(git rev-parse --short HEAD)"
    "${COMPOSE[@]}" ps
    ;;
  rollback)
    tag="${2:-prev}"
    docker tag "$IMAGE:$tag" "$IMAGE:latest"
    "${COMPOSE[@]}" up -d --no-build --force-recreate app
    "${COMPOSE[@]}" ps
    ;;
  images)
    docker image ls "$IMAGE"
    ;;
  logs)
    "${COMPOSE[@]}" logs -f --tail=200 ${2:+"$2"}
    ;;
  ps)
    "${COMPOSE[@]}" ps
    ;;
  *)
    sed -n '2,9p' "$0"
    exit 1
    ;;
esac
