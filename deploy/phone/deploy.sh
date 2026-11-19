#!/usr/bin/env bash
# 전화 주문 배포 도우미 (티켓 09, 6단계 운영 보강). 서버의 저장소 루트에서 부른다.
#
#   ./deploy/phone/deploy.sh up              배포·재배포: 먼저 빌드 → 새 통화 받기 중지 → 통화 0개(또는
#                                            제한 시간) 대기 → 교체 → 오래된 이미지 정리
#   ./deploy/phone/deploy.sh rollback [태그]  이전 이미지로 되돌림 (태그 생략 = prev). 같은 순서로 비운 뒤 교체
#   ./deploy/phone/deploy.sh drain           새 통화만 막는다(진행 중 통화는 그대로). 다시 받기는 undrain
#   ./deploy/phone/deploy.sh undrain         새 통화를 다시 받는다
#   ./deploy/phone/deploy.sh health          /healthz (통화 수·점검 중·버전)
#   ./deploy/phone/deploy.sh images          되돌릴 수 있는 태그 목록
#   ./deploy/phone/deploy.sh logs [app|caddy] 로그 따라 보기
#   ./deploy/phone/deploy.sh ps              상태
#
# 조정 (환경 변수):
#   DRAIN_TIMEOUT_S  통화가 끝나기를 기다리는 최대 초 (기본 600 = 통화 최대 길이). 0이면 안 기다리고 바로 교체
#   KEEP_IMAGES      남겨 둘 커밋 태그 이미지 수 (기본 3. latest·prev는 늘 남긴다)
#   DISK_WARN_PCT    디스크 사용률 경고 기준 % (기본 80)
set -euo pipefail

cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f deploy/phone/compose.yaml)
IMAGE=agenthan-phone
DRAIN_TIMEOUT_S="${DRAIN_TIMEOUT_S:-600}"
DRAIN_POLL_S="${DRAIN_POLL_S:-5}"
KEEP_IMAGES="${KEEP_IMAGES:-3}"
DISK_WARN_PCT="${DISK_WARN_PCT:-80}"
DRAIN_FILE=/app/logs/DRAIN # 앱이 보는 점검 표시 (voice/gate.py). 로그 볼륨에 있다

if [[ ! -f deploy/phone/.env ]]; then
  echo "deploy/phone/.env가 없어요. cp deploy/phone/.env.example deploy/phone/.env 후 값을 채우세요" >&2
  exit 1
fi

# 지금 도는 앱의 /healthz 한 필드. 앱이 없거나(첫 배포) 예전 이미지라 /healthz가 없으면 실패한다
health_field() {
  "${COMPOSE[@]}" exec -T app python -c \
    "import json,sys,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3))[sys.argv[1]])" \
    "$1" 2>/dev/null
}

undrain() {
  "${COMPOSE[@]}" exec -T app rm -f "$DRAIN_FILE" >/dev/null 2>&1 || true
}

# 새 통화 받기를 멈추고 진행 중 통화가 0이 되기를 기다린다 (최대 DRAIN_TIMEOUT_S초)
drain_and_wait() {
  local active waited=0
  if [[ "$DRAIN_TIMEOUT_S" == 0 ]]; then
    echo "DRAIN_TIMEOUT_S=0: 기다리지 않고 바로 교체해요 (통화 중인 손님은 끊겨요)"
    return 0
  fi
  if ! active="$(health_field active)"; then
    echo "지금 도는 앱의 /healthz를 읽지 못했어요 (첫 배포이거나 예전 이미지). 비우기를 건너뛰어요"
    return 0
  fi
  "${COMPOSE[@]}" exec -T app touch "$DRAIN_FILE"
  # 끊기면(SSH 끊김 HUP 포함) 점검 표시를 지우고 그만둔다. 안 그러면 새 통화가 계속 거절된다
  trap 'echo "중단: 새 통화를 다시 받아요" >&2; undrain; exit 130' ERR INT TERM HUP
  echo "새 통화 받기를 멈췄어요. 진행 중 통화 ${active}개"
  while [[ "$active" != 0 ]] && ((waited < DRAIN_TIMEOUT_S)); do
    sleep "$DRAIN_POLL_S"
    waited=$((waited + (DRAIN_POLL_S > 0 ? DRAIN_POLL_S : 1)))
    active="$(health_field active)" || break
    echo "  통화 ${active}개 진행 중 (${waited}/${DRAIN_TIMEOUT_S}초)"
  done
  if [[ "$active" != 0 ]]; then
    echo "제한 시간이 지나 통화 ${active}개가 남은 채로 교체해요 (화면에 점검 안내가 떠요)"
  fi
}

# 교체 뒤: 점검 표시를 지운다(컨테이너가 안 바뀌었어도 통화를 다시 받게). 새 앱도 시작할 때 지운다
after_swap() {
  trap - ERR INT TERM HUP
  undrain
}

# 커밋 태그 이미지를 최근 KEEP_IMAGES개만 남긴다 (latest·prev는 늘 남김). 목록은 새것부터 나온다
prune_images() {
  local kept=0 tag
  while read -r tag; do
    case "$tag" in latest | prev | "<none>" | "") continue ;; esac
    kept=$((kept + 1))
    if ((kept > KEEP_IMAGES)); then
      docker rmi "$IMAGE:$tag" >/dev/null 2>&1 && echo "오래된 이미지 태그 삭제: $tag" || true
    fi
  done < <(docker image ls "$IMAGE" --format '{{.Tag}}')
  docker image prune -f >/dev/null 2>&1 || true
}

disk_check() {
  local pct
  pct="$(df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}')" || return 0
  if [[ "$pct" =~ ^[0-9]+$ ]] && ((pct >= DISK_WARN_PCT)); then
    echo "경고: 디스크 사용률 ${pct}% (기준 ${DISK_WARN_PCT}%). docker builder prune --keep-storage 5GB, 오래된 로그 정리" >&2
  fi
}

case "${1:-}" in
  up)
    disk_check
    sha="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
    if docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
      docker tag "$IMAGE:latest" "$IMAGE:prev"
    fi
    # 1) 먼저 빌드: 도는 컨테이너는 예전 이미지로 통화를 계속 받는다
    GIT_SHA="$sha" "${COMPOSE[@]}" build app
    # 2) 새 통화 받기 중지 → 3) 진행 중 통화 0 또는 제한 시간
    drain_and_wait
    # 4) 교체
    GIT_SHA="$sha" "${COMPOSE[@]}" up -d --no-build
    after_swap
    docker tag "$IMAGE:latest" "$IMAGE:$sha"
    prune_images
    disk_check
    "${COMPOSE[@]}" ps
    ;;
  rollback)
    tag="${2:-prev}"
    docker image inspect "$IMAGE:$tag" >/dev/null
    drain_and_wait
    docker tag "$IMAGE:$tag" "$IMAGE:latest"
    "${COMPOSE[@]}" up -d --no-build --force-recreate app
    after_swap
    "${COMPOSE[@]}" ps
    ;;
  drain)
    "${COMPOSE[@]}" exec -T app touch "$DRAIN_FILE"
    echo "새 통화 받기를 멈췄어요. 진행 중 통화: $(health_field active || echo '?')개. 다시 받기: $0 undrain"
    ;;
  undrain)
    undrain
    echo "새 통화를 다시 받아요"
    ;;
  health)
    "${COMPOSE[@]}" exec -T app python -c \
      "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read().decode())"
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
    sed -n '2,18p' "$0"
    exit 1
    ;;
esac
