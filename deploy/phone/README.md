# 전화 주문 NCP 배포 (티켓 09)

폰 → `https://<도메인>` (Caddy, 자동 인증서) → `app:8000` (FastAPI: React 화면 + `/api/phone/ws`, 한 오리진). 가게는 더미 fixture.

| 파일 | 하는 일 |
|---|---|
| `Dockerfile` | 1단계 Node로 `web/` 빌드 → 2단계 Python 3.11 + uv로 실행. 키는 안 들어간다 |
| `compose.yaml` | `app` + `caddy`. Caddy 인증서는 이름 있는 볼륨 `caddy_data`(`/data`) |
| `Caddyfile` | `{$DOMAIN}` → `app:8000` 리버스 프록시(WS 포함). `ACME_CA`로 스테이징/운영 전환 |
| `.env.example` | 서버 `deploy/phone/.env` 틀. 이름만 |
| `deploy.sh` | `up`(배포·재배포) · `rollback [태그]` · `images` · `logs [app\|caddy]` · `ps` |

## 1. 사람이 처음 한 번 (동현님 개인 실습 계정)

1. **NCP Server 만들기**: High CPU `c2-g3`(2vCPU/4GB), Ubuntu 24.04, 공인 서브넷. 서버만 월 약 7.3만 원(VAT 포함), 디스크·공인 IP를 더해 **월 8만 원 안팎**, 11/13~11/26 2주면 약 4만 원(일할). 크레딧 먼저 확인
2. **공인 IP**를 만들어 서버에 붙인다(유료)
3. **ACG**: 인바운드 TCP 80·443 `0.0.0.0/0`, UDP 443 `0.0.0.0/0`(HTTP/3, 선택), TCP 22 **내 IP/32만**. 아웃바운드 전체 `0.0.0.0/0`(없으면 인증서·CLOVA 호출이 막힌다). 8000은 열지 않는다
4. **DuckDNS**: duckdns.org 로그인 → 서브도메인(예: `agenthan`) → current ip에 공인 IP → update. 노트북에서 `dig +short agenthan.duckdns.org`가 그 IP면 된다
5. **CLOVA Voice** Application 수정 → Web 서비스 URL에 `https://agenthan.duckdns.org` 추가
6. **서버 준비** (`ssh root@<공인IP>`, 비밀번호는 콘솔 "관리자 비밀번호 확인"에서 인증키로):
   ```bash
   curl -fsSL https://get.docker.com | sh          # Docker + compose 플러그인
   ufw status                                       # active면: ufw allow 80,443/tcp && ufw allow 443/udp
   git clone -b <배포 브랜치> https://github.com/Happ11quokka/AgentHan.git && cd AgentHan
   ```
   배포 브랜치는 먼저 push해 둔다(main에 합치기 전이면 `phone/s2-voice-roundtrip`)
7. **키 넣기** (노트북에서. 키가 git·이미지·채팅에 안 남는 유일한 길):
   ```bash
   cp deploy/phone/.env.example deploy/phone/.env   # 노트북에서 값 채우기 (.env는 gitignore)
   scp deploy/phone/.env root@<공인IP>:AgentHan/deploy/phone/.env
   ssh root@<공인IP> chmod 600 AgentHan/deploy/phone/.env
   ```
   `DOMAIN`은 꼭, `ACME_CA`는 비워 둔다(= 스테이징). 실제 CLOVA는 `PHONE_NLU=hcx`, `PHONE_STT=clova`, `PHONE_TTS=clova`와 키 5개

## 2. 배포 · 확인 (서버, `~/AgentHan`에서)

```bash
./deploy/phone/deploy.sh up            # 첫 빌드 수 분. 끝나면 app·caddy가 Up
./deploy/phone/deploy.sh logs caddy    # "certificate obtained successfully" 를 기다린다
echo | openssl s_client -connect agenthan.duckdns.org:443 -servername agenthan.duckdns.org 2>/dev/null \
  | openssl x509 -noout -issuer        # 스테이징이면 issuer에 (STAGING)
curl -k https://agenthan.duckdns.org/phone   # index.html이 오면 화면·프록시 OK
```

## 3. 스테이징 → 운영 인증서

스테이징 발급을 본 뒤에만 바꾼다(운영은 같은 이름 7일 5장 한도).

```bash
sed -i 's|^ACME_CA=.*|ACME_CA=https://acme-v02.api.letsencrypt.org/directory|' deploy/phone/.env
docker compose -f deploy/phone/compose.yaml up -d caddy   # caddy만 새 값으로 다시 만든다
```

issuer에 `(STAGING)`이 없어지면 끝. 폰 크롬에서 `https://agenthan.duckdns.org/phone` → 전화 받기 → 주문 1건.

## 4. 재배포 · 롤백 · 로그

```bash
git pull && ./deploy/phone/deploy.sh up       # 재배포. 직전 이미지는 prev, 새 이미지는 커밋 태그로 남는다
./deploy/phone/deploy.sh rollback             # 직전(prev)으로. 더 예전은 images로 태그 보고 rollback <태그>
./deploy/phone/deploy.sh logs app             # 앱 로그 (주문·오류). caddy는 logs caddy
docker compose -f deploy/phone/compose.yaml cp app:/app/logs ./phone-logs   # 통화 지연 JSONL 꺼내기
```

키만 바꿨으면 `docker compose -f deploy/phone/compose.yaml up -d app`(빌드 없이 새 env로). **`down -v`는 쓰지 않는다**: `caddy_data`가 지워져 인증서를 다시 받는다.

## 5. 비상: 도메인·인증서가 막힌 날 (cloudflared Quick Tunnel)

```bash
docker run --rm --network agenthan-phone_default cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://app:8000
```

출력의 `https://<임의>.trycloudflare.com/phone`을 연다. 주소는 실행마다 바뀌고 SSE는 안 된다(WS는 됨). 서버가 아예 없으면 노트북에서 `uv run python -m agenthan.voice.server` 뒤 `cloudflared tunnel --url http://localhost:8000`.

## 키 규칙

- 키는 `deploy/phone/.env`(서버, `chmod 600`)에만. compose가 실행할 때 `app`에 환경 변수로 넣는다. Caddy에는 안 준다
- 이미지에 안 들어간다: Dockerfile은 `src/`·`fixtures/`·`web/`와 의존성 목록만 복사하고 `Dockerfile.dockerignore`가 `.env*`를 뺀다. Dockerfile에 `ENV`·`ARG`로 키를 적지 않는다
- 화면에 키 금지(Vite `VITE_*`는 번들에 그대로 보인다)
- `docker compose config`·`docker inspect`는 키 값을 찍는다. 그 출력을 캡처·공유하지 않는다
- 값에 `$`가 있으면 `.env`에서 작은따옴표로 감싼다. 대회 뒤 키 재발급·서버 반납
