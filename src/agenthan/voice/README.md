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
  - 실패는 `PortError(code, retry)`로 올린다. 세션이 `error` 이벤트로 옮기고 통화를 끝낸다
- 쓰는 법: `open()` → 읽기 작업에서 `feed_audio(pcm)` / `feed_event(dict)` → `wait_done()` → `close()`
- 내리는 오디오: `call/frames.py` (8바이트 헤더 kind·flags·seq·turn + PCM16LE 16kHz, 약 200ms 조각). TTS PCM을 200ms로 자르는 것은 세션이 한다
- 대사 → TTS 문장(150자 이하) 나누기, TTS 동시 2개·문장 순서 보내기도 세션이 한다(`call/sentences.py`)
- 무음 감지(`call/vad.py`): RMS 임계값 500, 말 시작 200ms, 말 끝 무음 500ms가 시작값. `SessionSettings(vad=VadSettings(...))`로 바꾼다
- 지연 기록(`call/latency.py`): `<log_dir>/phone-YYYYMMDD.jsonl`, 턴마다 한 줄. 기본 `logs/`
- 가짜 STT·TTS·시계: `call/fakes.py` (테스트와 키 없는 개발 서버용)
- 테스트: `tests/voice/test_call_session*.py`, 도구는 `tests/voice/call_harness.py`

## 2단계: 통화 서버 (`ws.py`, `server.py`)

- 개발 서버 띄우기 (키 없이 전부 가짜, 저장소 루트에서): `uv run python -m agenthan.voice.server`
  - `http://127.0.0.1:8000`, WS는 `ws://127.0.0.1:8000/api/phone/ws`. 폰(USB `adb reverse tcp:8000 tcp:8000`)에서 열려면 그대로, 같은 망에서 열려면 `--host 0.0.0.0`. 코드 고치며 쓰려면 `--reload`
  - `web/dist`(`cd web && npm run build`)가 있으면 화면도 같이 낸다. `/phone` 새로고침도 된다. 개발 중에는 Vite(5173)를 띄우고 `/api` 프록시로 이 서버(8000)에 붙인다
  - 가짜 STT는 `epFlag`(무음 감지나 "말 끝" 버튼)마다 `PHONE_FAKE_STT_REPLIES`의 다음 말을 낸다(기본: 메도빅 하나요 → 그게 다예요 → 6시 반이요 → 네). 가짜 TTS는 무음
- 설정: 환경 변수 → `.env`(지금 폴더). 변수 표는 `settings.py` 머리 주석
  - `PHONE_NLU` fake|hcx, `PHONE_STT` fake|clova, `PHONE_TTS` fake|clova (기본 전부 fake)
  - `PHONE_TZ`(기본 Asia/Seoul), `PHONE_LOG_DIR`(기본 `logs/`, gitignore), `PHONE_NOW`(가게 시각 고정, 시연용), `PHONE_STORE_FIXTURE`, `PHONE_WEB_DIST`
  - 키: `CLOVASTUDIO_API_KEY`(hcx), `CLOVA_SPEECH_SECRET_KEY`·`CLOVA_SPEECH_GRPC_ENDPOINT`(STT), `CLOVA_VOICE_CLIENT_ID`·`CLOVA_VOICE_CLIENT_SECRET`(TTS)
  - `clova`를 골랐는데 어댑터 모듈이 없으면 서버가 뜨기 전에 `AdapterMissing`으로 멈춘다. 어댑터 연결은 `adapters.py` 한 곳
- 통합(`app/`, 11/17): `app.include_router(phone_router(store=..., config=StoreConfig(store_name=...), order_sink=..., settings=VoiceSettings.from_env()))`. 라우터에는 가짜 기본값이 없다(가게 지식·설정·`OrderSink`를 넣는다). `server.py`는 더미 가게로 띄우는 개발용이라 `app/`은 쓰지 않는다
- 연결 하나 = `CallSession` 하나. 읽기 작업 하나가 받고, 보내기는 세션의 송신 작업 하나만 한다. 끊기거나 세션이 끝나면 STT를 닫고 JSONL을 쓴 뒤 소켓을 닫는다
- 테스트: `tests/voice/test_phone_ws.py`(TestClient WebSocket), `test_phone_settings.py`, `test_phone_server.py`
