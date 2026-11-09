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
- 무음 감지(`call/vad.py`): RMS 임계값 1,500, 말 시작 200ms, 말 끝 무음 400ms (티켓 07에서 녹음 17개로 맞춤, 시작값은 500/200/500). 근거는 `VadSettings` 주석. 시작값 500에서는 배경 소리(RMS 400~1,900) 때문에 17개 중 12개가 녹음 안에서 말 끝을 못 잡았고, 지금 값은 17개 모두 잡는다. 3단계부터 `epFlag`는 무음 250ms(`ep_silence_ms`)에 먼저 보내고 턴 판정은 400ms에 한다(아래 3단계 지연 개선). 코드에서는 `SessionSettings(vad=VadSettings(...))`, 서버에서는 `PHONE_VAD_THRESHOLD`·`PHONE_VAD_START_MS`·`PHONE_VAD_END_SILENCE_MS`·`PHONE_VAD_EP_SILENCE_MS`로 바꾼다
- 조각 합치기: 결과가 왔을 때 결과 끝 뒤에 **새로 시작한** 말 구간(200ms 이상)이 있으면 붙잡는다. epFlag 응답이 아닌 결과(gap 등)는 무음 감지가 아직 말하는 중이거나 써 둔 epFlag의 응답을 기다리는 중이어도 붙잡고, 턴은 epFlag 응답으로 넘긴다(티켓 10: CLOVA는 90ms 쉼에서도 gap 조각을 낸다. 07 실측 "나폴|레옹도", "맥도비 두개랑|아이스…"). 배경 소리 때문에 "말하는 중"이 안 풀려도 마지막으로 글을 붙잡은 때부터 3초(`hold_cap_s`) 안에 넘긴다(리뷰 C4: 새 글이 오면 다시 센다). 버린 결과(반이중·확정 뒤·통화 끝)는 JSONL `dropped`에 남는다. 붙잡은 뒤 1.8초(`hold_max_s`, 3단계 임시 실측값) 안에 새 결과가 없으면 턴을 넘긴다(`end_by: hold_timeout`). 그때 이어진 말이 아직 열려 있거나 그 `epFlag` 응답을 기다리는 중이면 마지막 글부터 3초(`hold_cap_s`)까지 기다린다. 봇 첫 소리가 나가면 붙잡아 둔 조각은 다음 턴으로 넘기고 "말 끝" 표시를 지운다. 1.4초 쉼(D5)은 두 턴이 된다(뒤쪽 말은 엔진 중이거나 첫 소리 전이면 다음 턴에 합쳐지고, 봇 소리가 나간 뒤에 한 말이면 버려진다). 3단계 끝남/이어짐 판정이 이어짐 글("-랑" 등)이면 합친다(아래 3단계)
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

## 3단계: 말 끝 판정 (`call/turn_end.py`, 티켓 02)

- 흐름: 조각 붙잡기가 손님 말 하나를 만들면 세션이 끝말 규칙으로 판정한다. **끝남**·**기다림 요청**이면 바로 엔진으로, **이어짐**이면 판정 중 국면에서 마지막 말 끝(마지막 유성 프레임 도착) + 2.0초(`cont_wait_s`, `PHONE_CONT_WAIT_S`)까지 기다린다. 그 안에 다시 말하면 합쳐서 처음부터 다시 판정하고, 말하는 중이면 그 말 끝부터 다시 센다. 대기 전체 상한은 대기 중 마지막으로 글이 들어온 때부터 8초(`cont_max_s`, 리뷰 P3)다. 글이 계속 들어오는 긴 주문은 쪼개지 않고, 글 없는 배경 소리가 "말하는 중"을 붙잡을 때만 상한에서 넘긴다. 국면 값은 화면에 `listening` 그대로 나간다
- 이어짐 마감은 조각 붙잡기(`hold_max_s`)와 다른 타이머라 붙잡기가 마감을 덮어쓰지 않는다. 대기 중 "말 끝" 버튼은 기다린 말로 바로 끝낸다
- 끝말 규칙(`classify_turn_end`, 순수 함수): 글·대화 상태·직전 질문 종류(`question_kind`: 확인/닫힌/열린)·메뉴 이름 → 라벨·근거·`firm`(확실함)·`filler_only`·`backchannel`
  - 이어짐: 끝 토큰이 연결어미(-랑·-고·-아서/-어서·-면, 단어 예외 목록), 접속사(그리고·또·근데·그럼…), 필러(음·어·저기…, 띄어쓰기로 봄), 단위 없는 수(두·세·"2"), 메뉴 이름 앞부분("나폴")
  - 끝남(확실): 확인 질문 뒤 예/아니요(`dialog/rules.yes_or_no`와 같은 목록), 끝 마침표, 종결어미 -요·-다·-까·-죠. 애매(-고요·-이고요·-는데)는 끝남이지만 확실하지 않다(턴 모델이 가를 자리)
  - 기다림 요청: 말 끝의 잠깐만(요)·잠시만(요). 엔진에 `wait_request=True`로 넘긴다(응답 문장·15초 대기는 아래 티켓 04)
  - 반례 표는 `tests/voice/test_turn_end.py`(아무거나·뭐라고·짜장면·여기서·김민서·"2"·나폴)
- 규칙 + 턴 모델 합치기(`combine_turn_end`): 점수 ≥ 0.7이면 끝남, ≤ 0.3이면 이어짐, 그 사이와 점수 없음은 규칙. 확실한 판정은 모델보다 앞선다. 마침표·종결어미는 완결된 주문(닫힌·확인 질문의 답이거나 메뉴 이름이 통째로 든 말)일 때만 확실하다. 열린 질문 뒤 "그게 다예요"처럼 메뉴 없는 끝은 낮은 점수면 기다린다(리뷰 P2. EoT-Bench 합침 오끊김 2.4% → 1.6%, Smart Turn만과 같음. 평균 대기 1.02 → 1.13초). 점수는 Smart Turn(아래, 티켓 03)이 낸다
- 필러만 있는 말("음…")이 상한까지 안 이어지면 엔진에 넘기지 않는다(실패로 세지 않음). 지금 턴 기록의 `fillers`에 남는다
- 낱말 목록은 `dialog/rules.py` 한 곳에 둔다: 문장부호 `PUNCT`, 필러 `FILLERS`(끝말 규칙과 기다림 요청 앞 머리말이 같이 씀. "음음 잠깐만요"는 내용 없는 기다림 요청), 맞장구 `BACKCHANNELS`(짧은 예 말 `YES_SHORT` + "어·아·음·네네", 예 판정과 같은 말을 공유)
- 미리 해석(`nlu/prefetch.py`): 이어짐 대기를 시작할 때 붙잡은 글과 `engine.nlu_context()`로 NLU를 미리 부른다(규칙 단계에 걸리는 글·필러는 안 부름). 엔진이 같은 `(글, NluContext)`로 `parse`하면 그 결과를 쓰고, 다르면 새로 부른다. 요청 본문은 같아서 HCX 녹화본을 다시 만들지 않는다. 미리 부른 호출 시간 제한 5초(`prefetch_timeout_s`). 엔진(장바구니 변경)은 턴을 넘긴 뒤에만 돈다(스냅숏 없음)
- JSONL: `turn_end {label, reason, path(rule·model·cap·button), score, backchannel}`, 앞선 판정 `turn_end_steps`, 이어짐 대기 `cont_wait_ms`, `nlu_prefetch {prefetched, used, discarded, miss, wait_ms}`, 버린 결과 `dropped[].backchannel`
- 테스트: `tests/voice/test_turn_end.py`(표), `test_call_turn_end.py`(세션), `test_engine_nlu_context.py`

## 3단계: Smart Turn (`smart_turn.py`, 티켓 03)

- 무엇: 손님 오디오만 보고 "말이 끝났을 확률"을 내는 오디오 턴 모델. Pipecat Smart Turn v3.2 CPU int8 ONNX(8.7MB, 약 8M 파라미터, Whisper Tiny 인코더 + 작은 분류 머리). **서버 안에서 돌리는 자체 호스팅 오픈 모델이다. 외부 모델 API(Whisper API 등)는 부르지 않는다.** NCP에 같은 기능이 없고 직접 만들기 어려워서 쓴다(사용자 결정 2026-11-08)
- onnxruntime 공식 빌드는 리눅스에서도 1DS로 Microsoft에 사용 기록을 보내는 것이 기본이다(휠의 `Privacy.md`). `smart_turn.py`가 런타임이 뜨기 전에 `ORT_DISABLE_TELEMETRY=1`을 두고 `disable_telemetry_events()`를 부른다. 이미지에도 같은 ENV가 있다
- 라이선스: Smart Turn 코드·모델 BSD-2-Clause(Daily), 바탕 `openai/whisper-tiny` Apache-2.0, 평가 데이터 LiveKit EoT-Bench CC-BY-4.0(평가에만 쓰고 저장소에 넣지 않는다). 이미지에는 `deploy/phone/NOTICE-smart-turn.txt`가 모델 옆(`/app/models/`)에 같이 들어간다
- 언제 도나: 무음 감지가 말 끝(무음 400ms)을 볼 때마다 한 번. 통화마다 최근 8초 링 버퍼(봇이 말하는 동안 들어온 프레임은 넣지 않음)의 사본을 전용 2스레드 풀(`phone-turn-*`, 프로세스에 하나)에서 점수 낸다. ONNX 세션 하나를 모든 통화가 같이 쓰고 intra_op 1. 새 말 구간이 시작되면 앞 점수는 버리고, 이어짐 대기 중에는 버퍼를 비우지 않아 다음 말 끝에서 턴 전체를 다시 본다. 턴을 넘기면 비운다
- 판정: `combine_turn_end`에 점수를 넘긴다(0.7 이상 끝남, 0.3 이하 이어짐, 그 사이 규칙, 확실한 규칙이 먼저). 판정 때 점수가 아직 없으면(`late`)·실패면(`error`) 규칙만. 보통은 기다릴 일이 없다: 말 끝(400ms)부터 STT 확정 글(말 끝 + 0.9~1.1초)까지가 모델의 시간이다(맥 1스레드 약 50ms). 다만 조기 `epFlag` 응답이 말 끝(400ms) 전에 와서 말 끝에 넘기는 빠른 경로는 같은 순간에 낸 점수를 `turn_score_wait_s`(150ms)까지 기다린다(리뷰 C5. 안 그러면 늘 `late`). 새 말 구간·봇 턴·통화 끝에서는 줄 서 있는 점수 작업을 취소해 공유 풀을 비운다(리뷰 C7, 감사 11번)
- 입력 규칙은 원본과 같다: 16kHz mono, 8초보다 길면 앞을 자르고 짧으면 앞에 0을 채움, 8초 전체로 정규화. 특징(Whisper 로그 멜)은 `transformers` 없이 numpy로 만든다. 런타임 의존성은 `onnxruntime`·`numpy`
- 설정: `PHONE_TURN_MODEL`(기본 `smart_turn`, `off`로 끔), `PHONE_TURN_MODEL_PATH`(기본 `/app/models/smart-turn-v3.2-cpu.onnx`), `PHONE_TURN_LOW`/`PHONE_TURN_HIGH`(0.3/0.7). 라우터를 만들 때 모델을 한 번 열고 SHA-256을 고정값과 비교한다. 파일이 없거나 틀리거나 못 열면 경고 로그를 남기고 끝말 규칙만으로 뜬다(통화는 막지 않음). 로컬에서 쓰려면 저장소 루트에서 `uv run python -m agenthan.voice.fetch_turn_model`로 받는다(`fetch_turn_model.py`). 이미지와 같은 고정 리비전·SHA-256(`smart_turn.py`의 `MODEL_URL`·`MODEL_SHA256`)으로 gitignore된 `models/smart-turn-v3.2-cpu.onnx`에 두고, 체크섬이 틀리면 파일을 남기지 않는다. 서버는 `PHONE_TURN_MODEL_PATH="$PWD/models/smart-turn-v3.2-cpu.onnx"`로 띄우고 시작 로그 `말 끝 판정: 끝말 규칙 + Smart Turn`을 확인한다. 모델 파일은 커밋하지 않는다(`.gitignore`의 `models/`·`*.onnx`)
- 배포: `deploy/phone/Dockerfile`이 Hugging Face 고정 리비전에서 `ADD --checksum`으로 받는다(틀리면 빌드 실패). 이미지가 약 150MB 커진다(의존성 + 모델 8.7MB)
- JSONL: `turn_end.score`, `turn_model {src: model·late·error·none, ms}`(모델이 꺼져 있으면 없음)
- 오프라인 평가(CI 밖): `uv run --with pyarrow python -m agenthan.voice.turn_eval --model <onnx> --eot data/eot-bench/ko.parquet --recordings private/recordings/<이름>/wav --manifest private/recordings/<이름>/turn-eval.json --out private/turn-eval`. 멈춤마다 그때까지의 글·오디오로 지금(2단계)·규칙만·Smart Turn만·합친 판정의 오끊김률과 평균 대기를 직전 질문 종류별로, 대역·상한 훑기와 함께 낸다. 결과는 저장소의 `data/`·`private/` 아래에만 쓰고 받아 적은 글은 쓰지 않는다. 결과를 공개할 때는 "LiveKit EoT-Bench" 출처를 적는다. 자체 벤치의 한국어 정확도(합성 음성 위주)는 발표에 쓰지 않는다
- 테스트: `tests/voice/test_smart_turn.py`(입력 규칙, 특징 = `WhisperFeatureExtractor`에서 따로 뽑은 값, 체크섬. 실제 모델 테스트는 `PHONE_TURN_MODEL_PATH`에 파일이 있을 때만), `test_call_smart_turn.py`(세션, `FakeTurnModel`), `test_turn_eval.py`(평가 시간 모델)

## 3단계: 지연 개선 묶음 (티켓 01)

- "봇 말하는 중"(speaking)은 첫 오디오를 보낼 때부터다. 엔진이 끝나고 첫 문장 TTS를 합성하는 동안은 thinking이라, 그 사이 한 말은 다음 턴으로 들어간다(2단계 버그 수정). 반이중으로 버리는 것은 봇 소리가 나간 뒤에 **시작한** 말의 결과뿐이다(결과 도착 시각이 아니라 오디오 시각 `start_ms`로 본다)
- 조기 `epFlag`: 무음이 250ms(`VadSettings.ep_silence_ms`, `PHONE_VAD_EP_SILENCE_MS`)에 닿는 프레임에 `epFlag`를 붙이고, 턴 판정은 그대로 무음 400ms(`end_silence_ms`)에서 한다. 250~400ms 쉼 뒤 말이 다시 시작되면 받은 글을 붙잡아 다음 글과 합친다. 응답이 400ms 전에 오면 무음 400ms 프레임에서 턴을 넘긴다. 100ms 프레임이라 실제로는 무음 300ms 프레임에 나간다. 녹음 17개(`vad_check … 1500/200/400/400` 대 `…/250`): 말 끝 → `epFlag` 중앙값 400 → 300ms, 말 중간 쉼의 `epFlag` 3 → 5개, 녹음 안 말 끝 17/17 그대로
- `hold_max_s` 1.8초: "epFlag 송신 → 글 있는 응답" p99로 정하는 값이다. 2단계 유효 실측이 8턴뿐이라(p50 0.56, p90 1.05, 최대 1.78초) 최대값을 덮는 임시값이다. 다시 잴 때는 아래 `turn_summary`의 `stt_wait`를 30턴 이상 모은다
- TTS 첫 문장 앞 무음 자르기(`call/trim.py`, Coval TTFA 방식): 10ms 창을 1ms씩 옮겨 RMS가 풀스케일 1%(≈328) 이상인 첫 창을 찾고, 그 안의 첫 가청 샘플 20ms 앞에서 1ms 단위로 자른다. 1초 안에 소리가 없으면 자르지 않는다. 값은 `SessionSettings(tts_trim=TrimSettings(...))`. 자른 길이는 JSONL `tts_trim_ms`. CLOVA Voice 앞 무음은 아직 재지 않았다
- 생각 중 효과음: 말 끝 + 1.5초(`thinking_after_s`)까지 봇 목소리가 없으면 `bot.thinking {turn}`을 한 번 보낸다. 첫 오디오를 보내면 타이머를 끈다. 브라우저(`web/src/screens/phone/thinkingCue.ts`)는 직접 만든 사인파 파일(`thinking-cue.wav`, `web/scripts/make-thinking-cue.mjs`로 생성, 목소리·TTS 아님)을 반복 재생하다가 봇 오디오가 오거나 턴이 넘어가면 멈춘다. 봇 재생 중 모아 둔 말로 연 턴은 재생이 끝나 넘긴 때부터 센다
- 봇이 먼저 말하는 턴: `CallSession.bot_turn(reason, act)`가 듣는 중일 때만 새 턴 번호로 `act(engine, now)`를 엔진 스레드에서 돌린다(무음 되묻기·기다림 응답, 티켓 04·05). 손님 말을 붙잡거나 판정 중이면 `None`. JSONL `bot_turn`
- JSONL 지표: `end_to_voice_ms`(말 끝 → 브라우저 `playback.started`, 둘 다 서버 도착 시각이라 올림 지연이 지워짐), `end_to_thinking_ms`와 `marks.thinking`, `queued`(봇 재생 중 모아 둔 말로 연 턴, 말 끝 기준 지연 없음), `empty`(말소리 뒤 글 없는 `epFlag` 응답, 합성 `eof` 제외), `tts_trim_ms`, `bot_turn`. 판정 경로는 `end_by`(무음 감지·버튼·gap·…)와 `turn_end.path`(rule·model·cap·button). 브라우저 `client.metrics`에 `thinking_ms`
- 요약(숫자만, 글은 안 냄): `uv run python -m agenthan.voice.call.turn_summary logs/phone-YYYYMMDD.jsonl` → 말 끝 → 첫 목소리 P50·P90, 통화당 느린 턴(2.5초 초과) 수, 효과음 시각, `end_by`·`turn_end.path`별 턴 수, 빈 턴, `stt_wait` P50·P90·P99, 미리 해석 `prefetch`(적중률·부른 호출·버린 호출·안 쓴 이유·기다린 ms)
- 테스트: `tests/voice/test_call_latency.py`(세션), `test_turn_summary.py`, `test_call_vad.py`, 웹 `phoneCall.test.ts`(효과음)

## 3단계: 말 끊기 버튼 (티켓 05)

- 기본은 반이중 그대로다. 봇이 말하는 동안 브라우저는 0 프레임을 보낸다
- 아래로: `state`에 `interruptible`(봇 소리가 나가는 중이고 막는 턴이 아니면 참). `bot.done`에 끊긴 턴이면 `interrupted: true`(그때 `sentences`는 보낸 문장 수)
- 위로: `user.interrupt {turn}`(버튼), `playback.interrupted {turn, seq, played_ms}`(브라우저가 멈춘 자리. `played_ms`는 그 문장 첫 소리부터, 스피커 지연을 뺀 값)
- 막는 턴: 최종 복창 턴(엔진이 `TurnResult.readback`으로 알린다, 리뷰 C6. CONFIRM 상태여도 "네, 천천히 말씀하세요"·"잘 못 들었어요" 턴은 끊을 수 있다), 마무리·사장님 연결 안내·취소(TERMINAL). 인사는 허용. 서버도 막는 턴의 끊기를 받지 않아서, 복창 중 버튼 + "네"로 총액을 못 듣고 확정되는 길이 없다
- 서버: 끊으면 문장 보내기 작업(`_speak`)을 취소해 남은 TTS 합성과 `bot.say`를 멈추고, 송신 큐에 남은 그 턴 오디오·자막을 버리고, 바로 듣기로 간다. 문장 보내기는 턴 작업과 따로 돌아서 끼어든 말의 엔진 호출이 남은 TTS를 기다리지 않는다. 엔진은 되돌리지 않는다(소리가 나간 턴은 들었다고 본다)
- 브라우저(`phoneCall.interrupt`, `Player.interrupt`): 재생을 멈추고, 그 턴 이하 오디오를 헤더 `turn`으로 버리고, 마이크를 서버 답 없이 바로 연다. 끊은 턴의 `playback.done`은 보내지 않는다
- JSONL: `interrupt {by, seq, played_ms, heard: [{seq, heard(heard·partial·unheard), ms, played_ms, chars}]}`, `marks.interrupted`. `ms`는 보낸 문장 소리 길이(보내기 전에 끊긴 문장은 `null`), `chars`는 일부 들은 문장의 들은 글자 수 어림(글자 비율)
- 끊긴 턴 기록은 브라우저 `playback.interrupted`(들은 만큼)가 올 때까지 열어 둔다. 끊는 순간 대기 중이던 말이 다음 턴을 먼저 열어도 보고가 오면 바로 닫아 쓴다. 끝내 안 오면 그다음 턴을 열 때 닫는다(리뷰 C2)
- 복창 앞 "네" 거르기(리뷰 P1): 최종 복창 첫 문장을 합성하는 동안(첫 소리 전) 한 "네"·맞장구는 대기했다가 재생이 끝난 뒤 버린다. 말한 시각(`start_ms`)으로 본다. 반이중은 첫 소리 전에 시작한 말, 음성 끼어들기는 금액 문장 끝 전에 시작한 말이다. 정정("아니 두 개요")은 받는다. 버린 말은 JSONL `dropped[] {reason: early_yes}`
- 무응답 타이머는 티켓 04가 `_after_playback`(재생 끝·끊김 둘 다)에 건다
- 테스트: `tests/voice/test_call_interrupt.py`, 웹 `callState.test.ts`·`phoneCall.test.ts`

## 3단계: 무응답·되묻기·"잠깐만요" (티켓 04)

- 엔진이 할 말을 정하고(`dialog/engine.py`, `templates.py`) 세션은 타이머만 맡는다
  - `engine.no_input(now)`: 1·2번째는 "여보세요?" + 직전 질문(`_question_again(short=True)`, 복창 중이면 지금 장바구니 복창). 직전 질문 앞의 안내·사과 문장은 빼고 질문 문장만 한다(`templates.question_only`, 감사 문제 A). 영업 종료 뒤 거절은 "9시 전에 몇 시쯤 오세요?"처럼 짧은 질문을 따로 둔다(`_ask(again=…)`). 못 알아들음("죄송해요, 잘 못 알아들었어요.") 뒤도 같고, 손님이 "뭐라고요?"라고 하면 앞 문장까지 통째로 다시 한다. 3번째(`MAX_NO_INPUT`)는 "연결이 원활하지 않아 전화를 끊을게요. 다시 걸어 주세요." 뒤 DONE(Order 없음). 횟수는 받아들인 말(unknown이 아닌 act가 하나라도 있는 `handle`)에 0. 못 알아들음 실패 횟수와 따로 센다
  - `engine.handle(text, now, wait_request=True)`: 앞부분이 없거나 예/아니요·필러뿐이면 NLU 없이 "네, 천천히 말씀하세요."(실패로 안 셈, 무응답 횟수 0). 앞부분이 있으면 그것만 해석하고, 주문 중이면 다음 질문 문장 대신 "천천히 말씀하세요."(직전 질문은 남아 다음 되묻기가 한다). 복창 중에는 "네, 잠시만요"가 확정이 되지 않는다
  - `engine.not_heard(now)`: "죄송해요, 잘 못 들었어요. 다시 말씀해 주세요." 실패·무응답 횟수 둘 다 그대로
- 세션 무음 타이머: `_after_playback`(`playback.done`·안전망·말 끊기)에서 `no_input_s` 6초(`PHONE_NO_INPUT_S`), 지난 손님 말이 기다림 요청이면 `wait_request_s` 15초(`PHONE_WAIT_REQUEST_S`). VAD 말 구간 중에는 멈추고 그 말 끝(무음 400ms)부터 다시 센다. 손님 말을 붙잡거나 판정하는 중이면 기다린다. 글 없는 소리가 계속 와도 처음 마감 + 6초(`no_input_slack_s`)에는 되묻는다. 다만 그때 손님이 말하는 중(VAD 말 구간)이면 끼어들지 않고 그 말이 끝나자마자 되묻는다(리뷰 P4). 쉼(무음 400ms) 없는 말 구간이 8초(`no_input_speech_max_s`)를 넘으면 말이 아니라 끊기지 않는 배경 소리로 보고 상한에서 되묻는다(감사 2번. 안 그러면 소음이 `max_call_s` 600초까지 되묻기를 막는다). 둘 다 `SessionSettings` 값이다. 되묻기는 `bot_turn("no_input", …)`로 새 턴 번호를 받는다
- 말했는데 글 없음: 그 `epFlag`까지 말소리 프레임이 500ms(`not_heard_min_ms`) 이상인데 빈 `epFlag` 응답이 오면, 1초(`not_heard_wait_s`) 안에 글 있는 결과(늦은 gap 조각)가 없을 때 `bot_turn("not_heard", …)`. 어댑터 재연결 `eof`, 말 없이 누른 버튼, 말하는 중, 판정 중이면 하지 않는다. JSONL `empty[].voiced_ms`
- 대화가 끝나 서버가 통화를 끝낼 때 `call.ended {turn, reason, sms}`를 보낸다(reason: confirmed · handoff · cancelled · no_input. `sms`는 그때까지 손님에게 보낸 문자 미리보기, 4단계 티켓 07). 그 턴 JSONL에 `end_reason`. 위로 오는 `call.end`는 손님이 끊은 것이다(리뷰 S13에서 이름을 나눔). 브라우저는 이유를 남겼다가 소켓이 닫히면 통화 종료 화면에 적는다("통화가 끝났어요 · 말씀이 없어 끊었어요"). 가짜 휴대폰은 `end_reason`에 남긴다
- 사장님 쪽 알림(4단계 티켓 02): 세션 포트 `OwnerChannel`(`call/ports.py`, 구현은 사장님 허브 `owner/hub.py`). `call_started`(`call.ready` 때), 턴마다 `cart_changed`(계약 `OrderLine` 목록)·`bot_said`, 손님 턴마다 `customer_said`, 그리고 `call_ended(reason)`. 통화를 끝내는 길은 모두 `CallSession._end(reason)` 한 곳을 지나고 "통화 끝"은 통화당 한 번 나간다. 이유: confirmed · handoff · cancelled · no_input · hangup(손님 `call.end`·소켓 끊김·`close()`) · error(어댑터·엔진 오류, 엔진 시간 초과, `call.start` 대기·통화 길이 초과). 포트가 예외를 올려도 통화는 잇는다. 테스트 `test_call_owner.py`(가짜 `RecordingOwnerChannel`)
- 사장님께 묻기(4단계 티켓 05, 화면·로그 이름 ASK_OWNER): 사장님 쪽이 붙은 통화(`CallSession(owner=...)`)만 엔진을 `ask_owner=True`로 만든다. 메뉴 질문이 근거 없음으로 끝나고 질문 종류가 알레르기·재료·그 밖(가능 여부)이면(`menu_info.menu_answer`의 `MenuAnswer.for_owner`. 맛·설명·가격·옵션은 열지 않는다, 티켓 06) 엔진은 상태를 그대로 두고 열린 질문 하나를 들며 "잠시만요, 사장님께 여쭤볼게요."로 턴을 끝내고 `TurnResult.owner_question`(`dialog/owner_question.py` `OwnerQuestion`: 번호·메뉴 id·이름·손님 말 원문)을 싣는다. 세션은 `OwnerChannel.open_question(call_id, question, reply)`로 열고(바로 돌아옴), 답(`OwnerReply`: 번호·yes/no/unsure)은 `reply`로 세션 받는 곳에 쌓였다가 듣는 중이 되면(재생 끝) `bot_turn("owner_answer", …)` → 엔진 `owner_answered(number, answer, now)`가 고정 존댓말 + 하던 질문. 번호가 다른 답은 버린다. 허브는 `owner.question`·`owner.question.update` 카드와 `POST /api/owner/questions/{id}/answer`(한 번만, 두 번째 409). 티켓 06: 엔진 막는 조건(복창이 한 번 나간 뒤·이미 열린 질문·통화당 2번·시간 초과 한 번 뒤)이면 "그건 제가 정확히 모르겠어요". 세션은 카드를 보낸 때부터 `owner_wait_s`(20초, `PHONE_OWNER_WAIT_S`, 허브 카드 마감과 같은 값)가 지나면 `OwnerChannel.question_timed_out`을 알리고 시간 초과 답을 큐에 넣는다 → "확인해서 문자로 알려 드릴게요." + 하던 질문. 답·열람(`OwnerSeen`, `POST …/questions/{id}/seen` → "사장님이 보고 계세요, 조금만요." 질문마다 한 번)은 무응답 타이머와 같은 조건(듣는 중, 붙잡거나 판정하는 손님 말 없음, VAD 말 구간 아님, epFlag 응답 대기 아님(`hold_cap_s`까지))일 때만 말하고, 아니면 재생 끝·0.1초 재시도에서 다시 본다. 기다리는 동안 무응답 타이머는 멈춘다(`_start_quiet`). 마무리(CLOSING)에 열린 질문이 있으면 마무리 대사에 문자 약속을 붙이고 닫는다. 통화가 끝나면(`_end`) 질문·큐·타이머를 비우고, 허브는 그 통화의 열린 질문 카드를 `call_ended`로 바꾼다. 카드 상태 open·answered·timeout·call_ended, timeout·call_ended도 답을 받는다(세션은 버림, 손님 문자로 감). 답을 못 받은 질문(open·timeout)이 있는 통화의 확정 주문 카드는 `owner_check: true`. 턴 기록: 질문 턴 `owner_question{number, menu_id, opened_at}`, 답 턴 `owner_answer{question, answer, received_at, wait_ms, deferred_ms, seen_ms}`, 열람 턴 `owner_seen{…}`. 테스트 `test_engine_ask_owner.py`·`test_call_ask_owner.py`·`test_owner_questions.py`·`test_owner_http.py`
- 늦은 답·자유 입력·손님 문자(4단계 티켓 07): 실제 문자는 보내지 않고 허브가 통화 id마다 "손님에게 보낸 문자" 미리보기를 쌓는다(`owner/sms.py` 고정 문장). 종류: 확정 주문(`order`, 메뉴 요약·합계·픽업 시각), 늦은 버튼 답(`late_answer`, timeout·call_ended 카드에 온 답, 통화에서 말했을 고정 존댓말), 자유 입력 답(`free_answer`). 자유 입력은 `POST …/answer` 본문 `{"answer": "free", "text": "…"}`(빈 글·500자 넘음 422, 버튼과 합쳐 한 번만, 두 번째 409). 통화에는 종류만 가고(엔진 "사장님이 답을 남기셨어요. 문자로 보내 드릴게요." + 하던 질문, 사장님 글은 읽지 않는다), 허브가 같은 번역기 포트로 사장님 언어 → ko 번역해(메뉴명 보호 없음, 다듬지 않음) "사장님 답변(번역): «…»" 틀에 넣는다. 번역기가 없거나 실패면 원문 + `translate_failed`. 질문 카드에 `answer_text`(원문)·`answer_text_ko`(번역). 문자는 주문 카드·통화 카드의 `sms`에 실리고(생기면 카드를 다시 보냄), 세션은 `OwnerChannel.customer_texts`로 `call.ended`에 담는다. 통화 화면은 통화가 끝나면 `GET /api/phone/calls/{id}/sms`(토큰 없음, 모르는 통화 404) SSE를 연다: `sms.snapshot` → 새 문자마다 `sms` → 2분 뒤 `sms.end`(화면이 닫아 다시 붙지 않음). 테스트 `test_owner_sms.py`·`test_owner_http.py`·`test_call_ask_owner.py`, 웹 `callState`·`textStream`·`phoneCall`·`ownerText` vitest
- 테스트: 시나리오 `s22`~`s26`(`scenario_runner`에 `{"event": "no_input"|"not_heard"}`·`"wait_request": true` 턴, 대사 전체를 보는 `expect_say_exact`), `test_engine_no_input.py`, `test_call_no_input.py`(세션)

## 3단계: 음성 끼어들기 (`call/barge.py`, 티켓 06)

- 스위치 `PHONE_VOICE_BARGE_IN`: `off`(기본) · `shadow` · `on`. 꺼져 있으면 2단계·티켓 05와 똑같다(반이중 + 말 끊기 버튼, `call.ready`에 새 필드도 없다). 실기기 시험 전에는 켜지 않는다
- 켜도 브라우저가 `call.start.voice_barge_in: true`를 보낸 통화만 쓴다. 웹 `bargeIn.ts`가 안드로이드 크롬이고 마이크에 에코 제거가 걸렸을 때만 참으로 보낸다(삼성 인터넷·웨일·엣지·인앱 WebView·아이폰·데스크톱은 거짓). 서버가 켜면 `call.ready.voice_barge_in`(`on`·`shadow`)을 보내고, 그 통화의 브라우저는 봇이 말하는 동안에도 진짜 마이크 소리를 보낸다
- `shadow`: 판정과 기록만 하고 줄이기·멈춤을 보내지 않는다. 봇 말 중에 한 말은 반이중처럼 버린다(손님 경험은 그대로, 시험 T1~T3용)
- 판정(서버, 시각은 모두 오디오 시각): 봇 말 중 `PHONE_BARGE_THRESHOLD`(RMS 1,500) 이상 소리가 0.25초(`PHONE_BARGE_DUCK_MS`) 이어지면 `bot.duck`, 1.0초(`PHONE_BARGE_STOP_MS`, 1,000 아래로는 못 둔다. 한국어 맞장구가 최대 2.4초) 이어지거나 맞장구가 아닌 STT 글이 오면 `bot.stop`. 맞장구·빈 글·에코 글이거나 소리가 끝나고 2초 안에 글이 없으면 `bot.unduck`. 끼어들기를 정리한 뒤 1초는 다시 줄이지 않는다. 헛 끼어들기마다 그 통화의 멈춤 문턱을 0.25초씩 올린다(2.5초까지)
- 위치 규칙: 인사·마무리·사장님 연결·취소 안내는 음성 끼어들기를 막는다(인사는 버튼만). 남은 봇 말이 2초보다 짧으면 소리만으로는 멈추지 않는다. 최종 복창은 소리로 멈추지 않고, 금액 문장이 끝나기 전(마지막 "맞으세요?"가 시작하기 전, 말한 시각 기준)의 "네"·맞장구는 무시한다. 정정 글이면 멈추고 1단계처럼 마지막에 담은 줄을 고친다. "맞으세요?" 도중의 "네"는 답이다
- 거르기: STT 글은 도착 시각이 아니라 `start_ms`(말한 시각)로 봇 말 구간(첫 소리 ~ 재생 끝 보고·끊긴 때)과 비교한다. 구간 안에서 시작한 말만 판정하고, 맞장구는 구간 끝 + 150ms까지 구간 안으로 본다. 봇 문장과 두 글자 조각이 60% 이상 겹치면 에코 글이다. 재생이 끝날 때 무음 감지가 말하는 중(대개 에코)이면 그 프레임에 `epFlag`를 붙여 STT 구간을 끊고 무음 감지를 비운다. 문장 위치는 `playback.started`를 받은 순간의 오디오 시각이다
- 멈추면 버튼과 같은 자리에서 끊는다(남은 TTS·`bot.say` 취소, 듣기로, 무음 타이머 시작). 끼어든 말은 진행 중인 말이라 무음 타이머는 그 말 끝까지 기다린다. 들은 만큼은 서버가 오디오 시각으로 어림해 두고(`interrupt.est`), 브라우저 `playback.interrupted`가 오면 그 값으로 바꾼다
- 아래로: `bot.duck {turn}`, `bot.unduck {turn}`, `bot.stop {turn}`. 브라우저는 봇 목소리를 GainNode로 약 -18 dB 페이드(약 150ms, 되돌리기 약 80ms)하고, 다음 턴 소리가 오면 원래 크기로 돌린다. `bot.stop`이면 재생을 멈추고 그 턴 이하 오디오를 버리고 `playback.interrupted`를 보낸다(복창처럼 버튼이 막힌 턴에서도)
- JSONL: 봇 말 턴마다 `barge {kind(blocked·open·readback), stop_ms, rms{frames,p50,p95,max}, events[{act, by(sound·text), at_ms, shadow?}], results[{start_ms, end_ms, text, verdict}]}`. 버린 결과 `dropped[].barge`에 판정. 음성으로 끊은 턴은 `interrupt {by: voice, reason, seq, played_ms, heard, report_ms}`(`report_ms`는 `bot.stop` → 브라우저 보고). 손님 턴마다 `listen_rms {voiced, quiet}`(그 말을 듣는 동안 말소리 프레임·말 구간 밖 프레임의 RMS, 문턱을 정하는 근거)
- 측정 요약: `uv run python -m agenthan.voice.call.barge_summary logs/phone-YYYYMMDD.jsonl`(서버를 다른 `PHONE_TURN_LOW/HIGH`로 띄웠으면 `--low`·`--high`로 같은 값. 숫자만. 통화별 마이크 설정·에코 바닥·줄이기/멈춤 수, 판정별 수, 멈춤 지연, 듣는 동안 RMS, Smart Turn 점수 대역). 실기기 시험 순서표는 `.scratch/phone-s3-turn-management/device-check.md`
- 실기기 시험용 웹 선택: 주소 뒤 `?mic_first=1`(재생 컨텍스트를 마이크 허용 뒤에 만듦, T4), `?agc=0`(자동 음량 끔, T5). `call.start.ctx_order`에 남는다
- 테스트: `tests/voice/test_call_barge_in.py`(세션), `test_barge_summary.py`, 웹 `bargeIn.test.ts`·`callState.test.ts`·`phoneCall.test.ts`

## 2단계: 통화 서버 (`ws.py`, `server.py`)

- 개발 서버 띄우기 (키 없이 전부 가짜, 저장소 루트에서): `uv run python -m agenthan.voice.server`
  - `http://127.0.0.1:8000`, WS는 `ws://127.0.0.1:8000/api/phone/ws`. 폰(USB `adb reverse tcp:8000 tcp:8000`)에서 열려면 그대로, 같은 망에서 열려면 `--host 0.0.0.0`. 코드 고치며 쓰려면 `--reload`
  - `web/dist`(`cd web && npm run build`)가 있으면 화면도 같이 낸다. `/phone` 새로고침도 된다. 개발 중에는 Vite(5173)를 띄우고 `/api` 프록시로 이 서버(8000)에 붙인다
  - 가짜 STT는 `epFlag`(무음 감지나 "말 끝" 버튼)마다 `PHONE_FAKE_STT_REPLIES`의 다음 말을 낸다(기본: 메도빅 하나요 → 그게 다예요 → 6시 반이요 → 네). 가짜 TTS는 무음
- 설정: 환경 변수 → `.env`(지금 폴더). 변수 표는 `settings.py` 머리 주석
  - `PHONE_NLU` fake|hcx, `PHONE_STT` fake|clova, `PHONE_TTS` fake|clova (기본 전부 fake)
  - `PHONE_TZ`(기본 Asia/Seoul), `PHONE_LOG_DIR`(기본 `logs/`, gitignore), `PHONE_NOW`(가게 시각 고정, 시연용), `PHONE_STORE_FIXTURE`, `PHONE_WEB_DIST`
  - 통화 한도: `PHONE_MAX_CALLS`(동시 통화, 기본 10. 넘으면 `error{code: "busy"}`), `PHONE_MAX_CALL_S`(통화 하나, 기본 600초). 이어짐 대기 `PHONE_CONT_WAIT_S`(기본 2.0초, 3단계), 무응답 대기 `PHONE_NO_INPUT_S`(6초)·`PHONE_WAIT_REQUEST_S`(15초), 사장님 질문 답 대기 `PHONE_OWNER_WAIT_S`(20초, 4단계). `PHONE_VAD_*`는 양수만 받고 말 끝 무음은 100ms 이상
  - 엔진 스레드: 라우터 하나가 엔진 전용 스레드 풀(`phone-engine-*`, `PHONE_MAX_CALLS` × 2개)을 갖고 NLU 만들기·엔진 생성·턴을 거기서 돌린다. 루프 기본 풀(2 vCPU에서 6개)은 DNS 조회 등과 같이 쓰고, 시간을 넘긴 엔진 스레드는 취소되지 않고 남기 때문이다(티켓 09)
  - 배포(NCP + Caddy + DuckDNS): 저장소 루트 `deploy/phone/README.md`
  - 키: `CLOVASTUDIO_API_KEY`(hcx), `CLOVA_SPEECH_SECRET_KEY`·`CLOVA_SPEECH_GRPC_ENDPOINT`(STT), `CLOVA_VOICE_CLIENT_ID`·`CLOVA_VOICE_CLIENT_SECRET`(TTS)
  - `clova`를 골랐는데 어댑터 모듈이 없으면 서버가 뜨기 전에 `AdapterMissing`으로 멈춘다. 어댑터 연결은 `adapters.py` 한 곳(TTS는 `tts_clova.ClovaVoiceTts`, STT는 `stt.clova.ClovaSttStream`). 키가 비어 있으면 통화를 걸 때 `error{code: "tts_config"}`·`error{code: "stt_config"}`
  - gRPC DNS: `PHONE_STT=clova`면 `adapters.py`가 `grpc`를 import하기 전에 `GRPC_DNS_RESOLVER`가 비어 있을 때 `native`로 둔다. 맥에서 gRPC 기본 DNS(c-ares)가 "Could not contact DNS servers"(`stt_disconnected`)로 실패해서다. 다른 값을 쓰려면 환경 변수나 `.env`에 `GRPC_DNS_RESOLVER=ares`처럼 적는다
- 통합(`app/`, 11/17): `app.include_router(phone_router(store=..., config=StoreConfig(store_name=...), order_sink=..., settings=VoiceSettings.from_env()))`. 라우터에는 가짜 기본값이 없다(가게 지식·설정·`OrderSink`를 넣는다). `server.py`는 더미 가게로 띄우는 개발용이라 `app/`은 쓰지 않는다
- 연결 하나 = `CallSession` 하나. 읽기 작업 하나가 받고, 보내기는 세션의 송신 작업 하나만 한다. 끊기거나 세션이 끝나면 STT를 닫고 JSONL을 쓴 뒤 소켓을 닫는다
- 손님 쪽이 갑자기 끊겨 송신이 실패하면(Starlette `WebSocketDisconnect`, uvicorn `ClientDisconnected`) `WsOutbound`가 포트 예외 `ClientGone`으로 바꾸고, 세션은 traceback 없이 한 줄만 남기고 통화를 끝낸다. 그 밖의 송신 실패는 traceback을 남긴다(감사 문제 B)
- 테스트: `tests/voice/test_phone_ws.py`(TestClient WebSocket), `test_phone_settings.py`, `test_phone_server.py`

## 2단계: 끝-끝 측정 (`fake_phone/`, 티켓 07)

- 녹음(`private/recordings/<이름>/*.m4a`)을 16kHz WAV로 바꿔(`wav/`, 맥 `afconvert`) `/api/phone/ws`에 100ms씩 실제 속도로 흘려 넣는 가짜 휴대폰. 봇 오디오는 길이만큼 '재생'하고 `playback.started/done`을 보내며, 재생 중에는 0 프레임을 보낸다(반이중). A·B·C 묶음은 한 통화, D는 녹음마다 한 통화
- 측정 서버(키 필요, 저장소 루트의 `.env`): `PHONE_STT=clova PHONE_TTS=clova PHONE_NLU=fake PHONE_NOW=2026-11-08T16:30:00+09:00 PHONE_LOG_DIR=private/fake-phone/logs uv run python -m agenthan.voice.server --port 8010` (HCX는 `PHONE_NLU=hcx`)
- 흘려 넣기: `uv run python -m agenthan.voice.fake_phone --url ws://127.0.0.1:8010/api/phone/ws --recordings private/recordings/donghyeon --convert --label clova-fake`. 결과는 `private/fake-phone/<시각>-<label>/`(`calls.jsonl` 원기록, `rows.jsonl` 녹음별 줄, `summary.md` 표). 받아 적은 글이 들어 있으니 `private/` 밖으로 옮기지 않는다
- 말 끝 → 결과·첫 소리는 녹음에서 따로 잰 기준 말 끝(`audio.speech_end_s`)부터 잰다. VAD 값을 바꿔도 같은 기준으로 비교된다
- 키 없이 VAD 후보 비교: `uv run python -m agenthan.voice.fake_phone.vad_check private/recordings/donghyeon/wav 500/200/500 1500/200/400 1500/200/400/250` (네 번째 값은 조기 `epFlag` 무음 ms, 없으면 250)
- 네트워크가 느리면(업로드 수십 KB/s) STT 결과와 TTS가 수 초씩 늦어 측정값이 의미 없다. 측정 전에 업로드 150KB/s 이상, CLOVA까지 RTT 100ms 미만인지 본다
- 테스트: `tests/voice/test_fake_phone_*.py`(같은 프로세스 uvicorn + 가짜 STT·TTS, 오프라인), `test_call_vad.py`

## 4단계: 사장님 카드 번역 (`owner/`, 티켓 03)

- 번역기 포트 `owner/translate.py` `Translator.translate(texts, source, target) -> list[str]`(실패 `TranslateError`). 구현은 Papago 하나(`owner/papago.py`, 키 `PAPAGO_CLIENT_ID`·`PAPAGO_CLIENT_SECRET`, 요청 시간 제한 `PHONE_TRANSLATE_TIMEOUT_S` 3초, 유휴 연결 유지 `PHONE_TRANSLATE_KEEPALIVE_S` 60초, 개발 서버는 시작 때 한 번 데우고 끝날 때 닫는다). 키가 없으면 개발 서버는 번역 없이 원문만 보인다. 가짜 `owner/fakes.py` `FakeTranslator`
- 카드 줄: `name_owner`(원어 이름, 비면 한국어), `options_owner`(정적 사전 `owner/lexicon.py`), `request` + `request_owner`(번역) + `badges`(`remove`·`allergy`, 한국어 원문 규칙 `owner/badges.py`). 넘김 카드는 `heard` 옆에 `heard_owner`. 질문 카드는 `menu_name_owner`·`text_owner`(번역이 오면 `owner.question.update`)
- 번역은 비동기: 카드는 원문으로 먼저, 번역이 오면 같은 카드를 다시 보낸다. 같은 문장은 통화 동안 한 번(`owner/translation.py`)
- 보호(`owner/protect.py`): 메뉴명·별칭과 Papago가 뜻을 틀리는 재료 낱말(고수·파·초·밤)을 `[1]`처럼 가리고 번역 뒤 원어 이름·사전 값으로 채운다. 자리표시자가 사라지거나 늘거나, 숫자가 원문과 다르면 번역을 버린다
- 화면 `/owner?key=…&lang=ru`(기본 ru, `lang=ko`는 확인용 한국어). 고정 문구 `web/src/screens/owner/ownerText.ts`, 아래에 Papago 표기
- 테스트: `test_owner_ru_card.py`(허브, 가짜 번역기), `test_owner_papago.py`(어댑터, 녹화본 `cassettes/test_owner_papago/`. 다시 녹화: `AGENTHAN_ENV_FILE=<.env 경로> uv run pytest tests/voice/test_owner_papago.py -k recorded --record-mode=rewrite`)
- Papago 실측(CI 밖, 과금, 호출 약 240번, 티켓 04): `uv run python -m agenthan.voice.papago_measure --env <저장소 루트>/.env --out <저장소 루트>/private/papago-measure`. 지연(첫 호출·이어 쓰기·동시 3개), 메뉴명 자리표시자 보존, 부정·알레르기 세트(+ 역번역), 사장님 답 ru→ko 말투, 손님 말 흔한 모양을 03 어댑터·`protect`·`lexicon.RU` 그대로 잰다. 원기록 `<시각>.jsonl`과 요약 표 `<시각>.md`는 `private/`에만 둔다(받은 번역문 포함). 2026-11-10 결과는 연구 노트 `s4-papago-measure.md`. 순수 계산 테스트 `test_papago_measure.py`

## 4단계: 통화 자막 (`owner/hub.py`, 티켓 10)

- 04 실측(이어 쓰기 P50 601·P90 673ms)으로 넣기로 했다. 자막은 손님 말 끝 + 턴 확정 0.9~1.1초 + 번역 0.6초 = 1.5~1.8초 뒤에 뜬다(손님 다음 말보다 먼저)
- 허브 이벤트 `call.caption`: 통화 중(live) 카드 하나의 자막 전체 `{"call_id", "lines": [{"n", "who": "customer"|"bot", "text", "text_owner"}]}`, 최근 4줄(`CAPTION_LINES`), 번호 `n`은 통화 안 1부터. 세션의 `customer_said`·`bot_said`가 줄을 더한다
- 손님 말은 원문으로 먼저 보내고 카드 번역과 같은 캐시·보호(`owner/translation.py`)로 번역을 맡긴다. 번역이 오면 같은 번호 줄의 `text_owner`를 채워 다시 보낸다. 실패·버림·키 없음이면 원문만. AI 말은 번역하지 않는다(화면은 한국어 원문 회색 한 줄)
- CLOVA Speech `translation.targets`는 쓰지 않는다(응답 형식이 문서에 없다)
- snapshot의 `captions`에는 통화 중 카드 자막만. `call_ended`가 오면 자막을 버린다
- 화면: 통화 중 카드 아래 자막 4줄(`web/src/screens/owner/ownerState.ts` `captionLines`, `ownerText.ts` `captionView`). 테스트 `test_owner_captions.py`, vitest `ownerCaptions.test.ts`
