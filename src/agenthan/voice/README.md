# Part C · AI 전화 주문 (`voice/`)

- 담당: 임동현
- 하는 일: 휴대폰 브라우저 통화 화면 → WebSocket → 서버 → CLOVA Speech(STT) → HCX 주문 대화 → CLOVA Voice(TTS). 턴 관리(말 끝 감지, 끼어들기), 복창·확인, 주문서(`Order`)를 `OrderSink`로 넘기기.
- 한국어만 지원. 메뉴 판단은 `StoreKnowledge.search_menu()`로.
- 쓸 수 있는 것: `agenthan.core.*`. Part A·B 내부는 import하지 않는다.
- 자세한 요구사항: `docs/PRD.md` §6.3 · 리서치: `docs/research/03-phone-voice.md`, `05-channeltalk-voice-benchmark.md`

## 1단계: 텍스트 주문 대화 엔진

- 진입점: `dialog/engine.py`의 `DialogEngine.start(now)` / `handle(text, now) -> TurnResult`
  - `now`는 어느 시간대여도 된다. 엔진이 `StoreConfig.tz`(기본 KST)로 바꿔서 영업시간·당일·`pickup_time`(+09:00)을 판단한다
  - 시간대가 없는(naive) `now`는 이미 가게 시각(KST)이라고 보고 tz만 붙인다. 서버에서는 `datetime.now(timezone.utc)`처럼 시간대가 있는 값을 넘기는 편이 안전하다
- 직접 해 보기 (가짜 NLU, 키 필요 없음): `uv run python -m agenthan.voice.cli --now 2026-11-08T16:00:00+09:00`
  - 지금 시각에서 3시간 넘게 뒤인 픽업 시각은 "오후 6시 반에 오시는 거죠?"처럼 한 번 더 확인한다(티켓 04). 예시 시각이 16:00인 이유다
- HCX로 해 보기: `uv run python -m agenthan.voice.cli --nlu hcx --now 2026-11-08T16:00:00+09:00`
  - 키: 저장소 루트의 `.env`에 `CLOVASTUDIO_API_KEY=<키 원문>`을 넣는다(`Bearer ` 없이). 환경 변수 `CLOVASTUDIO_API_KEY`가 있으면 그것을 먼저 쓴다. 형식은 `.env.example`
  - `.env`는 **지금 폴더 기준**으로 읽는다. 저장소 루트에서 실행한다. 키를 못 찾으면 CLI가 종료 코드 2로 끝난다
  - 기본은 도구 없이 본문 JSON으로 답을 받는다(`HcxNlu(tools=False)`). `parse_turn` 도구를 쓰려면 코드에서 `HcxNlu.from_env(tools=True)`
- HCX 녹화본(cassette) 다시 만들기: `tests/voice/test_hcx_gold.py`가 `tests/voice/cassettes/`의 녹화본을 재생하므로 평소 테스트는 키·네트워크 없이 돈다
  - 프롬프트·도구 스키마·`nlu_gold.json`을 바꾸면 요청 본문이 달라져 재생이 실패한다. 그때 다시 녹화한다
  - 둘 다: `uv run pytest tests/voice/test_hcx_gold.py --record-mode=rewrite` (설정마다 실제 호출 30번, `.env` 키 필요)
  - 기본 설정만: `uv run pytest tests/voice/test_hcx_gold.py -k "not with_tools" --record-mode=rewrite`
  - 녹화할 때 키·요청 id·쿠키·XSRF 헤더는 지우고 응답 헤더는 content-type만 남긴다. `test_cassettes_hold_no_secret`이 커밋 전에 한 번 더 본다
- 시각 파싱 라이브러리 `korean-datetime`은 `timeparse.py` 한 파일에서만 쓴다. 바꿔 끼울 때는 이 파일만 고친다
- 처리기 추가: `@on("act 종류", State.X)`를 붙인 메서드 하나. 대사는 `dialog/templates.py`에만 쓴다
- 갈아 끼우는 자리: 메뉴 판정 `matching.py`, 픽업 시간 `timeparse.py`, NLU `nlu/` (`Nlu` Protocol)
- NLU 앞 규칙 단계: `dialog/rules.py` (예/아니요·주문 취소·사장님 연결·다시 말해 주세요). 메뉴 질문 답은 `dialog/menu_info.py`가 `MenuItem` 필드로만 만든다
- 2단계 통화 세션은 아래 "2단계" 참고
- L2 대본 시나리오: `tests/voice/scenarios/*.json` (형식은 `tests/voice/scenario_runner.py` 머리 주석). 턴마다 상태·장바구니·금지 규칙(확인 전 확정, 복창 없는 CONFIRM, 한 턴 두 질문, 복창 총액, 150자, 괄호·이모지)을 보고 최종 `Order`를 비교한다. 새 대본은 JSON 파일 하나를 더하면 `test_scenarios.py`가 집어 간다

## 2단계: 통화 세션 (`call/`)

- 진입점: `call/session.py`의 `CallSession`. 통화 1건. FastAPI·gRPC·httpx를 모르고 `call/ports.py`의 포트로만 바깥과 만난다
  - `SttStream`(오디오·`epFlag` 쓰기, 결과 읽기), `Tts`(문장 → PCM16 16kHz), `Clock`(KST `now`, 단조 시각), `Outbound`(이벤트·오디오 송신), `OrderSink`(계약)
  - 실패는 `PortError(code, retry)`로 올린다. 세션이 `error` 이벤트로 옮기고 통화를 끝낸다. 손님에게 가는 `message`는 고정 문구(`PortError.customer_message`: 다시 걸 수 있으면 "잠시 후 다시 걸어 주세요.")이고, 어댑터 메시지는 서버 로그와 JSONL `error_detail`에만 남는다
  - 세션이 스스로 쓰는 코드: `engine_timeout`(20초), `stt_backlog`(STT 쓰기가 3초 밀림), `start_timeout`(소켓을 열고 10초 안에 `call.start` 없음), `call_too_long`(`max_call_s`, 기본 10분), 라우터의 `busy`(동시 통화 상한)
- 쓰는 법: `open()` → 읽기 작업에서 `feed_audio(pcm)` / `feed_event(dict)` → `wait_done()` → `close()`
  - `feed_audio`는 무음 감지만 하고 프레임을 큐에 넣는다. STT 쓰기는 세션의 쓰기 작업 하나가 한다(밀리면 `epFlag` 프레임까지 묶어 DATA 하나로, 바이트는 버리지 않음). 그래서 STT가 느려도(4분 30초 재연결 포함) 읽기 작업이 `playback.done`·"말 끝"을 늦게 받지 않는다
  - `playback.done`이 안 와도 보낸 소리 길이 + 3초 뒤에는 서버가 듣기로 돌아간다(JSONL `play_done_timeout`)
- 내리는 오디오: `call/frames.py` (8바이트 헤더 kind·flags·seq·turn + PCM16LE 16kHz, 약 200ms 조각). TTS PCM을 200ms로 자르는 것은 세션이 한다
- 대사 → TTS 문장(150자 이하) 나누기, TTS 동시 2개·문장 순서 보내기도 세션이 한다(`call/sentences.py`)
- 무음 감지(`call/vad.py`): RMS 임계값 1,500, 말 시작 200ms, 말 끝 무음 400ms (티켓 07에서 녹음 17개로 맞춤, 시작값은 500/200/500). 근거는 `VadSettings` 주석. 시작값 500에서는 배경 소리(RMS 400~1,900) 때문에 17개 중 12개가 녹음 안에서 말 끝을 못 잡았고, 지금 값은 17개 모두 잡는다. 코드에서는 `SessionSettings(vad=VadSettings(...))`, 서버에서는 `PHONE_VAD_THRESHOLD`·`PHONE_VAD_START_MS`·`PHONE_VAD_END_SILENCE_MS`로 바꾼다
- 조각 합치기: 결과가 왔을 때 결과 끝 뒤에 **새로 시작한** 말 구간(200ms 이상)이 있으면 붙잡는다. epFlag 응답이 아닌 결과(gap 등)는 무음 감지가 아직 말하는 중이거나 써 둔 epFlag의 응답을 기다리는 중이어도 붙잡고, 턴은 epFlag 응답으로 넘긴다(티켓 10: CLOVA는 90ms 쉼에서도 gap 조각을 낸다. 07 실측 "나폴|레옹도", "맥도비 두개랑|아이스…"). 배경 소리 때문에 "말하는 중"이 안 풀려도 3초(`hold_cap_s`) 안에 넘긴다. 버린 결과(반이중·확정 뒤·통화 끝)는 JSONL `dropped`에 남는다. 붙잡은 뒤 1.2초(`hold_max_s`) 안에 새 결과가 없으면 턴을 넘긴다(`end_by: hold_timeout`). 그때 이어진 말이 아직 열려 있거나 그 `epFlag` 응답을 기다리는 중이면 3초(`hold_cap_s`)까지 기다린다. 봇이 말하기 시작하면 붙잡아 둔 조각과 "말 끝" 표시를 지운다. 1.4초 쉼(D5)은 두 턴이 된다(뒤쪽 말은 엔진 중이면 다음 턴에 합쳐지고, 봇이 말하는 중이면 버려진다). 3단계 끝남/이어짐 판정에서 다룬다
- STT 오인식 대응(티켓 10): 메뉴 매처는 자모 혼동 비용 표(`hangul.confusable_distance`)로 잰다(맥도빅 0.94, 맥도비 0.89, 나폴내용 0.92). 거절 경계는 0.6 → 0.7(맥도날드·블루베리 거절). 옵션은 대상어 퍼지 창 + 빼는 말(`dialog/options.py`, "샤워크림은 빼 주세요" → 사워크림 빼기, "빼지 마세요"는 거름). CLOVA CONFIG에 가게 메뉴 이름·별칭·옵션 대상어를 `keywordBoosting` weight 3으로 넣는다(`stt/boost.py`). 4분 30초 재연결 때 앞 연결은 epFlag 응답을 받은 뒤 쓰기를 닫는다
- 지연 기록(`call/latency.py`): `<log_dir>/phone-YYYYMMDD.jsonl`, 턴마다 한 줄. 기본 `logs/`
- 가짜 STT·TTS·시계: `call/fakes.py` (테스트와 키 없는 개발 서버용)
- 테스트: `tests/voice/test_call_session*.py`, 도구는 `tests/voice/call_harness.py`
- TTS 어댑터(`tts_clova.py`, 티켓 03): `ClovaVoiceTts.from_env()` → `Tts` 포트. 문장 하나를 CLOVA Voice Premium(`nara_call`, wav 16kHz)에 POST하고 WAV `data` 청크만 돌려준다. 키는 환경 변수 → `.env`(지금 폴더)의 `CLOVA_VOICE_CLIENT_ID`·`CLOVA_VOICE_CLIENT_SECRET`
  - 오류 `code`: `tts_auth`(401·403, 다시 걸어도 안 됨) · `tts_429` · `tts_vsNN`(VS26·VS99만 재시도 가능) · `tts_http_NNN` · `tts_timeout` · `tts_network` · `tts_bad_audio`(16kHz mono PCM16 아님) · `tts_config`(키 없음)
  - 실측(opt-in, 호출 9번): `AGENTHAN_ENV_FILE=<저장소 루트>/.env uv run pytest tests/voice/test_tts_clova_live.py -m live -s`. `live` 마커 테스트는 `-m live`일 때만 돈다(`tests/voice/conftest.py`)

### STT 어댑터 (`stt/`)

- `stt/clova.py`의 `ClovaSttStream(ClovaSttSettings(...))`: `SttStream` 포트를 CLOVA Speech 스트리밍 gRPC(`grpc.aio`)로 구현. 키는 설정으로 받는다(`ClovaSttSettings.from_env(사전)`이 `CLOVA_SPEECH_SECRET_KEY`·`CLOVA_SPEECH_GRPC_ENDPOINT`를 읽음). 모듈이 `.env`를 직접 읽지는 않는다
  - CONFIG: 한국어 + `semanticEpd`(`gapThreshold=700`, `durationThreshold=10000`). `durationThreshold`를 빼면 400ms 조각으로 쪼개진다
  - 오류: `stt_auth`(재시도 X), `stt_config`(재시도 X), `stt_disconnected`, `stt_busy`(15개 초과), `stt_timeout`
  - 연결 4분 30초가 지난 뒤 `epFlag` 프레임을 쓰면 그 뒤 새 연결로 바꾼다(4분 45초까지 `epFlag`가 없으면 그냥 바꿈). 결과 시각은 통화 하나에 이어진다
- `nest.proto`는 공식 문서 본문 그대로. 생성 코드(`nest_pb2*.py`)는 커밋돼 있다. 바꿨으면 `uv run python src/agenthan/voice/stt/gen_proto.py`
- 테스트: `tests/voice/test_stt_clova.py`(로컬 가짜 `NestService` `tests/voice/fake_nest.py`, 오프라인)
- 실측(키 필요, `live` 마커): `uv run pytest tests/voice/test_stt_clova_live.py -m live -s`. 이 맥에서 gRPC DNS가 실패하면 `GRPC_DNS_RESOLVER=native`를 앞에 붙인다

## 2단계: 통화 서버 (`ws.py`, `server.py`)

- 개발 서버 띄우기 (키 없이 전부 가짜, 저장소 루트에서): `uv run python -m agenthan.voice.server`
  - `http://127.0.0.1:8000`, WS는 `ws://127.0.0.1:8000/api/phone/ws`. 폰(USB `adb reverse tcp:8000 tcp:8000`)에서 열려면 그대로, 같은 망에서 열려면 `--host 0.0.0.0`. 코드 고치며 쓰려면 `--reload`
  - `web/dist`(`cd web && npm run build`)가 있으면 화면도 같이 낸다. `/phone` 새로고침도 된다. 개발 중에는 Vite(5173)를 띄우고 `/api` 프록시로 이 서버(8000)에 붙인다
  - 가짜 STT는 `epFlag`(무음 감지나 "말 끝" 버튼)마다 `PHONE_FAKE_STT_REPLIES`의 다음 말을 낸다(기본: 메도빅 하나요 → 그게 다예요 → 6시 반이요 → 네). 가짜 TTS는 무음
- 설정: 환경 변수 → `.env`(지금 폴더). 변수 표는 `settings.py` 머리 주석
  - `PHONE_NLU` fake|hcx, `PHONE_STT` fake|clova, `PHONE_TTS` fake|clova (기본 전부 fake)
  - `PHONE_TZ`(기본 Asia/Seoul), `PHONE_LOG_DIR`(기본 `logs/`, gitignore), `PHONE_NOW`(가게 시각 고정, 시연용), `PHONE_STORE_FIXTURE`, `PHONE_WEB_DIST`
  - 통화 한도: `PHONE_MAX_CALLS`(동시 통화, 기본 10. 넘으면 `error{code: "busy"}`), `PHONE_MAX_CALL_S`(통화 하나, 기본 600초). `PHONE_VAD_*`는 양수만 받고 말 끝 무음은 100ms 이상
  - 엔진 스레드: 라우터 하나가 엔진 전용 스레드 풀(`phone-engine-*`, `PHONE_MAX_CALLS` × 2개)을 갖고 NLU 만들기·엔진 생성·턴을 거기서 돌린다. 루프 기본 풀(2 vCPU에서 6개)은 DNS 조회 등과 같이 쓰고, 시간을 넘긴 엔진 스레드는 취소되지 않고 남기 때문이다(티켓 09)
  - 배포(NCP + Caddy + DuckDNS): 저장소 루트 `deploy/phone/README.md`
  - 키: `CLOVASTUDIO_API_KEY`(hcx), `CLOVA_SPEECH_SECRET_KEY`·`CLOVA_SPEECH_GRPC_ENDPOINT`(STT), `CLOVA_VOICE_CLIENT_ID`·`CLOVA_VOICE_CLIENT_SECRET`(TTS)
  - `clova`를 골랐는데 어댑터 모듈이 없으면 서버가 뜨기 전에 `AdapterMissing`으로 멈춘다. 어댑터 연결은 `adapters.py` 한 곳(TTS는 `tts_clova.ClovaVoiceTts`, STT는 `stt.clova.ClovaSttStream`). 키가 비어 있으면 통화를 걸 때 `error{code: "tts_config"}`·`error{code: "stt_config"}`
  - gRPC DNS: `PHONE_STT=clova`면 `adapters.py`가 `grpc`를 import하기 전에 `GRPC_DNS_RESOLVER`가 비어 있을 때 `native`로 둔다. 맥에서 gRPC 기본 DNS(c-ares)가 "Could not contact DNS servers"(`stt_disconnected`)로 실패해서다. 다른 값을 쓰려면 환경 변수나 `.env`에 `GRPC_DNS_RESOLVER=ares`처럼 적는다
- 통합(`app/`, 11/17): `app.include_router(phone_router(store=..., config=StoreConfig(store_name=...), order_sink=..., settings=VoiceSettings.from_env()))`. 라우터에는 가짜 기본값이 없다(가게 지식·설정·`OrderSink`를 넣는다). `server.py`는 더미 가게로 띄우는 개발용이라 `app/`은 쓰지 않는다
- 연결 하나 = `CallSession` 하나. 읽기 작업 하나가 받고, 보내기는 세션의 송신 작업 하나만 한다. 끊기거나 세션이 끝나면 STT를 닫고 JSONL을 쓴 뒤 소켓을 닫는다
- 테스트: `tests/voice/test_phone_ws.py`(TestClient WebSocket), `test_phone_settings.py`, `test_phone_server.py`

## 2단계: 끝-끝 측정 (`fake_phone/`, 티켓 07)

- 녹음(`private/recordings/<이름>/*.m4a`)을 16kHz WAV로 바꿔(`wav/`, 맥 `afconvert`) `/api/phone/ws`에 100ms씩 실제 속도로 흘려 넣는 가짜 휴대폰. 봇 오디오는 길이만큼 '재생'하고 `playback.started/done`을 보내며, 재생 중에는 0 프레임을 보낸다(반이중). A·B·C 묶음은 한 통화, D는 녹음마다 한 통화
- 측정 서버(키 필요, 저장소 루트의 `.env`): `PHONE_STT=clova PHONE_TTS=clova PHONE_NLU=fake PHONE_NOW=2026-11-08T16:30:00+09:00 PHONE_LOG_DIR=private/fake-phone/logs uv run python -m agenthan.voice.server --port 8010` (HCX는 `PHONE_NLU=hcx`)
- 흘려 넣기: `uv run python -m agenthan.voice.fake_phone --url ws://127.0.0.1:8010/api/phone/ws --recordings private/recordings/donghyeon --convert --label clova-fake`. 결과는 `private/fake-phone/<시각>-<label>/`(`calls.jsonl` 원기록, `rows.jsonl` 녹음별 줄, `summary.md` 표). 받아 적은 글이 들어 있으니 `private/` 밖으로 옮기지 않는다
- 말 끝 → 결과·첫 소리는 녹음에서 따로 잰 기준 말 끝(`audio.speech_end_s`)부터 잰다. VAD 값을 바꿔도 같은 기준으로 비교된다
- 키 없이 VAD 후보 비교: `uv run python -m agenthan.voice.fake_phone.vad_check private/recordings/donghyeon/wav 500/200/500 1500/200/400`
- 네트워크가 느리면(업로드 수십 KB/s) STT 결과와 TTS가 수 초씩 늦어 측정값이 의미 없다. 측정 전에 업로드 150KB/s 이상, CLOVA까지 RTT 100ms 미만인지 본다
- 테스트: `tests/voice/test_fake_phone_*.py`(같은 프로세스 uvicorn + 가짜 STT·TTS, 오프라인), `test_call_vad.py`
