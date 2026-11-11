# 전화 주문 NCP 배포 (티켓 09)

폰 → `https://<도메인>` (Caddy, 자동 인증서) → `app:8000` (FastAPI: React 화면 + `/api/phone/ws`, 한 오리진). 가게는 더미 fixture.

| 파일 | 하는 일 |
|---|---|
| `Dockerfile` | 1단계 Node로 `web/` 빌드 → 2단계 Python 3.11 + uv로 실행. 키는 안 들어간다 |
| `compose.yaml` | `app` + `caddy`. Caddy 인증서는 이름 있는 볼륨 `caddy_data`(`/data`) |
| `Caddyfile` | `{$DOMAIN}` → `app:8000` 리버스 프록시(WS 포함). `ACME_CA`로 스테이징/운영 전환 |
| `.env.example` | 서버 `deploy/phone/.env` 틀. 이름만 |
| `deploy.sh` | `up`(빌드 → 비우기 → 교체) · `rollback [태그]` · `drain`·`undrain` · `health` · `images` · `logs [app\|caddy]` · `ps` |

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
./deploy/phone/deploy.sh health               # {"ok":true,"active":통화 수,"draining":false,"version":"커밋",…}
./deploy/phone/deploy.sh rollback             # 직전(prev)으로. 더 예전은 images로 태그 보고 rollback <태그>
./deploy/phone/deploy.sh logs app             # 앱 로그 (주문·오류). caddy는 logs caddy
docker compose -f deploy/phone/compose.yaml cp app:/app/logs ./phone-logs   # 통화 지연 JSONL 꺼내기
```

키만 바꿨으면 `docker compose -f deploy/phone/compose.yaml up -d app`(빌드 없이 새 env로. 통화 중인 손님은 끊기니 먼저 `deploy.sh drain` 후 `health`의 `active`가 0인지 본다). **`down -v`는 쓰지 않는다**: `caddy_data`가 지워져 인증서를 다시 받고, `phone_logs`의 하루 예산 기록도 사라진다.

`deploy.sh up`이 하는 순서 (6단계):

1. **먼저 빌드** (`compose build app`). 도는 컨테이너는 예전 이미지로 통화를 계속 받는다. 빌드가 2 vCPU를 쓰니 통화가 적은 시간에 한다
2. **새 통화 받기 중지**: 컨테이너 안 `/app/logs/DRAIN` 파일을 만든다. 새로 거는 손님은 "잠시 점검 중이에요. 1분쯤 뒤에 다시 걸어 주세요"를 본다
3. **진행 중 통화가 0이 되기를 기다린다** (`/healthz`의 `active`, 최대 `DRAIN_TIMEOUT_S` 기본 600초). 급하면 `DRAIN_TIMEOUT_S=0 ./deploy/phone/deploy.sh up`(바로 교체, 통화 중인 화면에는 "서버 점검으로 통화를 끝낼게요"가 뜬다)
4. **교체** (`up -d --no-build`) → 점검 표시 지움 → 커밋 태그 → 오래된 태그 정리(`KEEP_IMAGES` 기본 3개 + `latest`·`prev`) → 디스크 사용률 경고(`DISK_WARN_PCT` 기본 80)

중간에 멈추면(Ctrl+C·오류) 점검 표시를 지우고 다시 통화를 받는다. 안 지워졌으면 `./deploy/phone/deploy.sh undrain`. 발표 전날 저녁부터는 배포하지 않는다.

## 4-1. 운영 환경 변수 (6단계)

`deploy/phone/.env`에 적는다. 안 적으면 기본값. 바꾼 뒤에는 `drain` → `active` 0 확인 → `docker compose -f deploy/phone/compose.yaml up -d app`. 전체 목록과 뜻은 `src/agenthan/voice/settings.py` 머리 주석.

| 변수 | 기본 | 서버 권장 | 뜻 |
|---|---|---|---|
| `PHONE_ALLOWED_ORIGINS` | compose가 `https://$DOMAIN` | 그대로 | 이 오리진의 화면만 통화를 건다. 터널 등 다른 주소도 쓰면 쉼표로 추가, 끄려면 `*` |
| `PHONE_TRUSTED_PROXIES` | compose가 `private` | 그대로 | Caddy가 준 `X-Forwarded-For`에서 손님 주소를 읽는다 |
| `PHONE_IP_MAX_CALLS` | 4 | 4 | 주소 하나의 동시 통화. 발표장 와이파이는 여러 명이 주소 하나를 쓴다 |
| `PHONE_IP_CALLS_PER_HOUR` | 30 | 30 | 주소 하나가 한 시간에 거는 통화 수 |
| `PHONE_DAY_MAX_CALLS` | 0(끔) | 300 | 하루 통화 수. 넘으면 "오늘은 전화 주문이 마감됐어요" |
| `PHONE_DAY_MAX_MINUTES` | 0(끔) | 600 | 하루 통화 분 합계. CLOVA 비용의 진짜 상한 |
| `PHONE_MAX_CALL_S` | 600 | 300 | 통화 하나의 최대 길이(주문 한 건은 보통 1~2분) |
| `PHONE_WS_MAX_BYTES` | 65536 | 그대로 | WS 메시지 하나의 최대 크기 |
| `PHONE_NLU_MAX_TOKENS` | 0(코드 기본 512) | 200 | HCX NLU `maxTokens`. `hcx-*.jsonl`의 `completion` 최댓값 + 여유로 정한다 |
| `PHONE_HCX_RPM` | 0(끔) | 70 | HCX NLU 분당 요청 상한(테스트 키 QPM 90의 80%). 서비스 앱 키면 360 |
| `PHONE_HCX_TPM` | 0(끔) | 64000 | 분당 토큰 상한(TPM 80,000의 80%). 서비스 앱 키면 190000 |
| `PHONE_NLU_FALLBACK` | rules | rules | HCX 실패 턴을 규칙 NLU가 맡는다 |
| `PHONE_TTS_RETRIES` · `PHONE_TTS_FALLBACK` | 1 · caption | 그대로 | CLOVA Voice 재시도 1회, 그래도 실패한 문장 하나는 무음 + 자막 |
| `PHONE_LOG_KEEP_DAYS` | 14 | 14 | 손님 말 JSONL·녹음 보관 일수. 화면 안내 문구도 14일이다. 바꾸면 `PhonePage.tsx` 문구도 바꾼다 |
| `PHONE_DISK_WARN_PCT` | 80 | 80 | 디스크 사용률 경고 로그 기준 |
| `PHONE_OWNER_MAX_CALL_CARDS` · `PHONE_OWNER_CALL_CARD_HOURS` | 50 · 24 | 그대로 | 사장님 화면의 끝난 통화 카드 상한 |
| `PHONE_OWNER_MAX_ORDER_CARDS` · `PHONE_OWNER_ORDER_CARD_HOURS` | 200 · 72 | 그대로 | 주문 카드 상한 |
| `PHONE_MAX_ORDER_QTY` · `PHONE_MAX_ORDER_TOTAL` | 가게 JSON(100 · 1,000,000) | 30 · 300000 | 한 통화 총 수량·총액 상한 |

## 4-2. 비상 대응 (6단계)

| 상황 | 볼 것 | 할 일 |
|---|---|---|
| 손님이 "오늘은 마감됐어요"를 본다 | `/healthz?key=…`의 `day`(오늘 통화 수·분), 컨테이너 안 `/app/logs/phone-budget.json` | 하루 예산을 올린다(`.env` `PHONE_DAY_MAX_*`) → `up -d app`. 급하면 `docker compose -f deploy/phone/compose.yaml exec app rm /app/logs/phone-budget.json` 후 `up -d app` |
| "잠시 점검 중이에요"가 계속 뜬다 | `health`의 `draining: true` | `./deploy/phone/deploy.sh undrain` |
| "지금 통화가 많아요"·"같은 곳에서 걸려 온 통화가 많아요" | `health`의 `active`, 로그의 `통화 거절 busy_ip` | 발표장 와이파이면 `PHONE_IP_MAX_CALLS`·`PHONE_IP_CALLS_PER_HOUR`를 올리거나 `0`으로 끈다 |
| 화면이 "서버에 연결하지 못했어요"만 뜬다 | 주소가 `.env`의 `DOMAIN`과 같은지(Origin 검사) | 다른 주소로 열었으면 `PHONE_ALLOWED_ORIGINS=*` 또는 그 주소 추가 → `up -d app` |
| 봇이 자꾸 엉뚱하게 알아듣는다(HCX 한도) | 로그 `HCX NLU 호출 실패 … http-429`, `/app/logs/hcx-*.jsonl`의 `status`·`fallback` | `PHONE_NLU_MAX_TOKENS`를 줄이고 `PHONE_HCX_RPM`·`_TPM`을 건다. 계속이면 `PHONE_MAX_CALLS`를 낮춘다. 서비스 앱 키 승인 뒤 키 교체 |
| 봇 목소리가 한 문장씩 빈다 | 로그 `TTS 실패 … 소리 없이 자막만 보냄` | CLOVA Voice 한도·장애. 콘솔 사용량 확인. 연달아 실패하면 통화가 안내 후 끝난다 |
| 디스크 경고 | 로그 `디스크 사용률 N%`, `df -h`, `docker system df` | `docker builder prune --keep-storage 5GB`, `./deploy/phone/deploy.sh images`로 태그 확인, `PHONE_LOG_KEEP_DAYS` 낮추기, 녹음 끄기 |
| 장난 통화가 몰린다 | 사장님 화면 미완료 카드, 로그 `통화 거절` | `PHONE_DAY_MAX_*`·`PHONE_IP_CALLS_PER_HOUR`를 낮춘다. 멈추려면 `./deploy/phone/deploy.sh drain` |
| 새 버전이 이상하다 | `health`의 `version` | `./deploy/phone/deploy.sh rollback` |
| 사장님 토큰이 샜다 | — | `.env`의 `PHONE_OWNER_KEY`를 새로 만들어 넣고 `up -d app`. Caddy 접속 로그는 `key`를 `REDACTED`로 남긴다(6단계 전 로그에는 남아 있다: `docker compose … logs caddy`가 도는 컨테이너를 다시 만들면 사라진다) |

상태 자세히: `curl -s "https://$DOMAIN/healthz?key=<PHONE_OWNER_KEY>"` → 하루 예산(`day`), 디스크(`disk_pct`), 스레드 수, HCX 분당 사용량. 토큰 없이 부르면 통화 수·점검 여부·버전만 준다.

## 5. 비상: 도메인·인증서가 막힌 날 (cloudflared Quick Tunnel)

```bash
docker run --rm --network agenthan-phone_default cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://app:8000
```

출력의 `https://<임의>.trycloudflare.com/phone`을 연다. 주소는 실행마다 바뀌고 SSE는 안 된다(WS는 됨). 서버가 아예 없으면 노트북에서 `uv run python -m agenthan.voice.server` 뒤 `cloudflared tunnel --url http://localhost:8000`.

## 6. Smart Turn (말 끝 판정 모델, 3단계)

- 무엇: 손님 오디오로 "말이 끝났을 확률"을 내는 오픈 모델(Pipecat Smart Turn v3.2 CPU int8 ONNX, 8.7MB, BSD-2). 서버 안 `phone-turn-*` 스레드 2개에서 돌고 외부 API는 부르지 않는다. 끝말 규칙과 합쳐 말이 끝났는지 정한다. 자세한 것은 `src/agenthan/voice/README.md` "3단계: Smart Turn"
- 빌드 때 받는다: `Dockerfile`이 Hugging Face 고정 리비전(`f766f81d…`)에서 `ADD --checksum=sha256:2bb02631…`으로 `/app/models/smart-turn-v3.2-cpu.onnx`에 받는다. SHA-256이 다르면 빌드가 실패한다. 그래서 첫 빌드와 캐시가 없는 빌드는 서버에서 huggingface.co로 나가는 HTTPS가 돼야 한다(ACG 아웃바운드 전체). 라이선스 고지 `NOTICE-smart-turn.txt`가 모델 옆에 같이 들어간다
- 이미지 크기: 모델 8.7MB와 `onnxruntime`·`numpy` 때문에 약 150MB 커진다
- 앱이 뜰 때 체크섬을 다시 확인한다. `deploy.sh logs app`에 `말 끝 판정: 끝말 규칙 + Smart Turn`이 나오면 켜진 것이다. 파일이 없거나 틀리면 경고를 남기고 `끝말 규칙만`으로 뜬다(통화는 막지 않는다)
- 끄기: 문제가 있으면 `deploy/phone/.env`에 `PHONE_TURN_MODEL=off`를 적고 `docker compose -f deploy/phone/compose.yaml up -d app`(빌드 없이). 끝말 규칙만으로 돈다. 모델 경로 `PHONE_TURN_MODEL_PATH`는 이미지에 들어 있으니 적지 않는다

## 7. 배포 직후 스모크 (6단계 품질)

배포(`deploy.sh up`)가 끝나고 `app`이 Up이면 **컨테이너 안에서** 한 번 돌린다. 서버와 같은 환경 변수·가게 데이터를 읽는다.

```bash
docker compose -f deploy/phone/compose.yaml exec app python -m agenthan.voice.smoke
echo $?    # 0 통과 · 1 실패 · 2 설정 오류(키 없음 등)
```

- 마지막 줄이 한 줄 요약이다: `SMOKE PASS s1 9/9 (flaky 0, nlu=hcx, calls 20) s2 1/1 (flaky 0, voice p50 1500ms p90 2100ms n=4) 85.0s`. 그 위는 결과 JSON(실패한 대본·통화는 `problems`와 대화가 들어 있다). 파일로도 남기려면 `--json /app/logs/smoke.json`(`phone_logs` 볼륨)
- **S1**: 가게 데이터로 만든 대본 9개(한 품목·두 품목·수량 정정·빼기·꼭 고를 것·큰 수량 확인·"지금 바로"·취소·사장님 연결)를 실제 NLU(`PHONE_NLU`)로 엔진에 넣고 최종 주문서를 본다. HCX 약 20번, 호출 사이 0.7초 이상(분당 86번 이하, 테스트 키 한도 분당 90). 약 30~40초
- **S2**: 가짜 휴대폰이 `ws://127.0.0.1:8000/api/phone/ws`에 통화 1건을 건다. 손님 말 4마디를 CLOVA Voice로 그때그때 합성해(봇과 다른 화자 `PHONE_SMOKE_SPEAKER`, 기본 `nara`. 파일로 남기지 않는다) 실제 속도로 올린다 → CLOVA Speech → HCX 2번 → CLOVA Voice. 주문 확정과 말 끝 → 첫 소리(P50·P90)를 낸다. 약 40~60초. 픽업은 "지금 바로 갈게요"라서, **영업시간 밖이면 주문 대신 사장님 연결 통화 1건**으로 음성 경로만 본다(결과 `note`)
- 실패한 대본·통화는 한 번 다시 돌린다. 그때 통과하면 `flaky`로 표시하고 통과로 센다(같은 것이 배포 두 번 연속 flaky면 결함으로 본다)
- 선택: `--skip-s2`(엔진·NLU만) · `--skip-s1` · `--calls 2`(+ 사장님 연결 통화) · `--calls 3`(+ 두 품목 주문) · `--max-voice-p50-ms 2500`(중앙값이 넘으면 실패. 기본은 숫자만 낸다) · `--nlu fake`(키 없이 엔진만, 로컬 확인용)
- **사장님 화면에 안 섞이게**: `deploy/phone/.env`에 `PHONE_SMOKE_KEY=<아무 긴 값>`을 넣고 `app`을 다시 띄운다. 스모크가 같은 값을 `call.start`에 실어 보내고, 서버는 그 통화를 사장님 화면(통화 카드·자막)·주문 받는 곳에 알리지 않고 턴 기록에 `smoke: true`를 남긴다(`turn_summary`가 뺀다). 결과 JSON `s2.marked`가 `true`인지 본다. 값이 없으면 스모크 통화가 보통 통화처럼 사장님 화면에 뜬다
- 서버가 가짜 모드(`PHONE_STT=fake`)면 S2는 사인파를 보내고 서버 대본(`PHONE_FAKE_STT_REPLIES`)대로 간다

## 키 규칙

- 키는 `deploy/phone/.env`(서버, `chmod 600`)에만. compose가 실행할 때 `app`에 환경 변수로 넣는다. Caddy에는 안 준다
- 이미지에 안 들어간다: Dockerfile은 `src/`·`fixtures/`·`web/`와 의존성 목록만 복사하고 `Dockerfile.dockerignore`가 `.env*`를 뺀다. Dockerfile에 `ENV`·`ARG`로 키를 적지 않는다
- 화면에 키 금지(Vite `VITE_*`는 번들에 그대로 보인다)
- `docker compose config`·`docker inspect`는 키 값을 찍는다. 그 출력을 캡처·공유하지 않는다
- 값에 `$`가 있으면 `.env`에서 작은따옴표로 감싼다. 대회 뒤 키 재발급·서버 반납
