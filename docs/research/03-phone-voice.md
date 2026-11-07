# Part C AI 전화 주문: 음성 파이프라인 구현 조사

> 조사일 2026-11-07 · 담당 파트 C

선행 조사 [17-ai-phone-order-on-ncp.md](../../../.scratch/ai-biseo-ideation/research/17-ai-phone-order-on-ncp.md)(PSTN 불가 판단, 웹 데모 구성, EPD 기본값, 참고 서비스)와 [16b-ai-services.md](../../../.scratch/ai-biseo-ideation/research/16b-ai-services.md)(CLOVA Speech·Voice·Papago 사양과 요금)는 다시 쓰지 않았다. 이 문서는 구현할 때 필요한 세부 사항만 다룬다.

등급: **[공식]** NCP·벤더 공식 문서, 패키지 레지스트리 · **[2차]** 기사, 블로그, 버그 트래커 · **[추론]** 문서를 근거로 한 우리 판단 · 확인 못 한 것은 **미확인**.

## 요약

1. **CLOVA Speech 스트리밍은 Python 예제와 proto가 공식 문서에 다 있다.** `nest.proto`를 문서에서 복사해 `grpcio-tools`로 컴파일한다. 설정 JSON을 먼저 보내고 응답을 받은 뒤에 오디오를 보낸다. 중간(partial) 결과는 없고, EPD 단위 확정 결과만 온다. 브라우저 VAD가 말 끝을 잡으면 `epFlag=true` + `seqId`를 보내 확정을 앞당기는 방식이 핵심이다.
2. **TTS 화자는 `nara_call`(아라, 상담원)을 1순위로 들어 본다.** CLOVA Voice는 문장당 200자 제한(VS18)이 있고 스트리밍 출력이 없다. 그래서 문장 단위로 병렬 호출하고 순서대로 재생한다. 지연·호출 한도 수치는 공개되지 않았다.
3. **브라우저는 `@ricky0123/vad-web` 0.0.31(Silero VAD)로 말 시작·끝을 잡는다.** "전화 받기" 버튼 탭이 마이크 권한과 오디오 재생 잠금 해제를 한 번에 처리한다. **iOS 26.2 이후 Safari에서 에코 제거를 켜고 마이크를 열면 재생음이 지직거리는 미해결 버그가 있다.** 데모 폰은 안드로이드 크롬을 기본으로 한다.
4. **프레임워크 없이 FastAPI + grpc.aio + httpx로 직접 짜는 쪽을 권장한다.** Pipecat(1.15.0)은 CLOVA용 서비스가 없어서 STT·TTS 클래스를 직접 만들어야 한다. 브라우저용으로는 WebRTC를 권장해 인프라가 늘어난다. LiveKit Agents는 LiveKit 서버가 따로 필요하다. 대화 상태 설계는 Pipecat Flows `food_ordering`과 LiveKit `drive_thru` 예제를 참고한다.
5. **사장님 쪽은 "메뉴명은 DB에서, 자유 문장만 Papago로" 옮긴다.** Papago는 한국어에서 13개 언어로 번역하지만 ko→vi·th·id 용어집은 지원하지 않는다. CLOVA Speech 스트리밍 자체 번역(`translation.targets`)을 쓰면 통화 중 사장님 언어 자막도 띄울 수 있다.

---

## Q1. CLOVA Speech 실시간 스트리밍(gRPC) Python 클라이언트

### 1-1. 준비물과 인증

| 항목 | 내용 | 등급·출처 |
|---|---|---|
| 접속 주소 | `clovaspeech-gw.ncloud.com:50051`, TLS(`grpc.ssl_channel_credentials()`) | [공식] [gRPC API](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-grpc) |
| proto 파일 | 다운로드 링크는 없다. 문서 본문 "1. Protoc Compiler 설치 및 준비"에 `nest.proto` 전문이 있다. 패키지 `com.nbp.cdncp.nest.grpc.proto.v1`, 서비스 `NestService.recognize(stream NestRequest) returns (stream NestResponse)` 하나뿐이다. | [공식] 같은 문서 |
| 메시지 구조 | `NestRequest{type: CONFIG(0)/DATA(1), oneof part{config: NestConfig{config: string}, data: NestData{chunk: bytes, extra_contents: string}}}`, `NestResponse{contents: string}`. 설정과 결과는 모두 **JSON 문자열**로 오간다. | [공식] 같은 문서 |
| 코드 생성 | `pip3 install grpcio-tools` → `python3 -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. nest.proto` → `nest_pb2.py`, `nest_pb2_grpc.py` 생성. 문서 일부에 `nest_grpc_pb2`라는 오타가 있다. | [공식] 같은 문서 |
| 패키지 버전 | `grpcio` 1.85.1, `grpcio-tools` 1.85.1(둘 다 2026-10-30, Python ≥3.10) | [공식] [PyPI grpcio](https://pypi.org/project/grpcio/), [PyPI grpcio-tools](https://pypi.org/project/grpcio-tools/) |
| 인증 | 메타데이터 `("authorization", "Bearer {secretKey}")`. 문서 주석: "**소문자 authorization 필수**". Secret Key는 스트리밍 도메인 빌더 → 설정 → [연동 정보]에서 만든다. | [공식] 같은 문서, [스트리밍 빌더](https://guide.ncloud-docs.com/docs/clovaspeech-builder-streaming) |
| 도메인 생성 전제 | "도메인을 생성하기 위해서는 먼저 **Object Storage의 버킷**을 생성해야 합니다." 스트리밍 결과는 Object Storage에 자동 저장된다. Free 플랜은 스트리밍 불가, Basic만 된다. | [공식] [도메인 가이드](https://guide.ncloud-docs.com/docs/clovaspeech-domain), [스트리밍 빌더](https://guide.ncloud-docs.com/docs/clovaspeech-builder-streaming) |
| 공식 예제 위치 | API 가이드 → CLOVA Speech → 예제 → [실시간 스트리밍 인식](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-example02)(Java, Python). Python 예제는 **파일을 32,000바이트(1초)씩** 동기 generator로 보낸다. 마이크·asyncio 예제는 없다. NCP 공식 GitHub 저장소 예제는 찾지 못했다(미확인). | [공식] 예제 페이지 |

### 1-2. 설정(Config) JSON — 주문 통화용 추천값

| 필드 | 기본값 | 추천 시작값(추론) | 메모 |
|---|---|---|---|
| `transcription.language` | (선택) | `"ko"` | 문서는 선택이라지만 넣으라고 권장한다 [공식] |
| `keywordBoosting.boostings[]` | 없음 | `[{"words":"아메리카노,카페라떼,...","weight":3}]` | 쉼표 앞뒤 공백 없이. weight 0~5.0. Part B 메뉴 DB에서 통화 시작 때 만든다. 빌더 기준 500단어 이하 권장 [공식] |
| `useDomainBoostings` + `boostingIDs` | false | 쓰지 않음 | 빌더에 등록한 ID 방식. `boostings`와 함께 못 쓴다 [공식] |
| `semanticEpd.gapThreshold` | 0(미사용) | 700 | VAD+epFlag가 주 경로이고, 이 값은 안전망이다 |
| `semanticEpd.durationThreshold` | 0 | 10000 | 긴 나열 주문 대비. 문서가 "직접 설정"을 권장한다 [공식] |
| `semanticEpd.usePeriodEpd` / `useWordEpd` | false | true / true | period를 켜면 word도 켜라고 권장 [공식] |
| `semanticEpd.skipEmptyText` | false | true | 빈 결과(`text:""`, confidence 1)를 막는다 [공식] |
| `semanticEpd.syllableThreshold` | 0 | 0 | 작게 주면 유사 중간 결과가 될 수 있지만, 끊긴 경계에서 문맥이 유지되는지는 미확인 |
| `translation.targets` | 없음 | `["vi"]` 등(선택) | 사장님 자막용(Q7). Papago Application 등록이 필요하다 [공식] |

### 1-3. 요청·응답 흐름과 주의점

| 항목 | 내용 | 등급·출처 |
|---|---|---|
| 순서 | ① CONFIG 1회 전송 → ② `responseType: config`, `config.status: "Success"` 응답 확인 → ③ DATA 반복. 설정 응답 전에 DATA를 보내면 `ConfigRequest did not complete` 오류. | [공식] [gRPC API](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-grpc) |
| 오디오 형식 | 16kHz·mono·16bit PCM, 헤더 없음. 다르면 `Invalid format`. | [공식] 같은 문서 |
| 청크 크기·속도 | **0.1~0.4초 청크를 실시간 속도로 균일하게** 보낸다(0.4초 = 12,800바이트). 무음 구간에도 끊지 않는다. 10초 분량을 한 번에 보내면 `unvoice`로 강제 종료되고 경계 단어가 빠진다. | [공식] [문제 해결](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-troubleshoot-grpc) |
| `extra_contents` | `{"seqId": N, "epFlag": true/false}`. `epFlag=true`면 버퍼를 즉시 처리해 결과를 낸다. 응답의 `transcription.epFlag`·`seqId`를 비교해 "이 요청까지 다 받았다"를 확인한다. seqId는 0이 아닌 값 권장. | [공식] 같은 문서 |
| 일시 정지 | 기능은 없다. `epFlag=true`를 보내고 잠시 요청을 멈추는 방식으로 대신하라고 안내한다. | [공식] [문제 해결](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-troubleshoot-grpc) |
| 응답 필드 | `transcription.text`, `position`(전체 문장에서 위치), `periodPositions`, `epdType`, `startTimestamp`/`endTimestamp`(ms), `confidence`(음절 신뢰도 기하평균), `alignInfos[]{word,start,end,confidence}`. 전체 문장은 `position` 기준으로 이어 붙인다. | [공식] [gRPC API](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-grpc) |
| 중간(partial) 결과 | 문서에 "partial/interim" 개념이 없다. **EPD 경계마다 확정 조각만 온다**(미확인이 아니라 "제공한다는 기술이 없음"). 화면 자막은 조각이 올 때마다 갱신한다. | [공식] 같은 문서 · [추론] |
| 연결 수명 | "Connection Lifetime 제한을 **5분**"으로 둔다. 재연결 로직을 넣으라고 권장한다. 만료 시 `Lifespan expired`. | [공식] [문제 해결](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-troubleshoot-grpc) |
| 동시 연결 | 도메인당 15개 초과 시 `RESOURCE_EXHAUSTED`. 서버 자원 부족 시 `No more slot`. | [공식] [gRPC API](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-grpc) |
| 연결 관리 | 인식이 끝나면 채널을 닫는다. 송수신 없이 오래 붙잡고 있으면 채널을 못 얻을 수 있다. | [공식] [문제 해결](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-troubleshoot-grpc) |

### 1-4. 우리 서버용 구현 요점 (추론)

| 요점 | 방법 | 근거 |
|---|---|---|
| asyncio로 돌리기 | 공식 예제는 동기 generator다. FastAPI 안에서는 `grpc.aio`를 쓴다. `request_iterator` 없이 호출하고 `call.write()`로 보내고 `call.read()`로 받을 수 있다. 단, iterator 방식과 read/write 방식을 한 RPC에서 섞으면 안 된다. | [공식] [grpc.aio 문서](https://grpc.github.io/grpc/python/grpc_asyncio.html) |
| 통화 1건 = gRPC 스트림 1개 | 손님이 "받기"를 누를 때 열고, 끊을 때 마지막에 `epFlag=true` 후 `done_writing()` → 채널 close. | [공식] 문제 해결 문서 권장 |
| 말 끝 확정 | 브라우저 VAD가 `speech_end`를 보내면 서버가 다음 DATA에 `{"seqId": 턴번호, "epFlag": true}`를 붙인다. 응답에서 같은 `seqId`+`epFlag:true`가 오면 그 턴의 STT를 **확정**으로 본다. 앞서 온 조각을 이어 붙여 한 턴 문장을 만든다. | [공식] epFlag/seqId 설명 · [추론] |
| 5분 대응 | ① 주문 통화는 보통 2~3분이라 대부분 걸리지 않는다. ② 4분 30초가 지나면 **다음 턴 확정 직후**에 새 스트림을 열어(설정 재전송) 바꿔 끼운다. ③ `grpc.RpcError`가 나면 즉시 재연결하고, 그동안 들어온 오디오는 2초 링버퍼에 담았다가 보낸다. | [공식] 5분 제한 · [추론] |
| 메뉴 부스팅 | 통화 시작 때 가게 메뉴명·옵션명(사이즈, 샷 추가 등)을 `boostings`로 넣는다. 가게마다 다르므로 도메인 ID 방식보다 인라인이 편하다. | [공식] keywordBoosting · [추론] |
| 낮은 신뢰도 | `confidence`나 메뉴명 단어의 `alignInfos[].confidence`가 낮으면(시작값 0.7, 실측 필요) 그 항목만 다시 확인 질문을 한다. | [공식] 필드 · [추론] 임계값 |

---

## Q2. CLOVA Voice(Premium) TTS

### 2-1. API 핵심

| 항목 | 내용 | 등급·출처 |
|---|---|---|
| 호출 | `POST https://naveropenapi.apigw.ntruss.com/tts-premium/v1/tts`, 헤더 `X-NCP-APIGW-API-KEY-ID`/`X-NCP-APIGW-API-KEY`(Application의 Client ID/Secret), `application/x-www-form-urlencoded` | [공식] [CLOVA Voice 개요](https://api.ncloud-docs.com/docs/ai-naver-clovavoice), [TTS Premium](https://api.ncloud-docs.com/docs/ai-naver-clovavoice-ttspremium) |
| 출력 | `format=wav`일 때 `sampling-rate` 8000/16000/**24000(기본)**/48000. 응답은 파일 한 덩어리(스트리밍 없음). | [공식] TTS Premium |
| 길이 제한 | 호출당 2,000자(한국어). **문장당 최대 200자 초과 시 VS18 오류.** 문장 분할기에서 150자 안팎으로 자른다(추론). | [공식] [CLOVA Voice 개요 상태 코드](https://api.ncloud-docs.com/docs/ai-naver-clovavoice) |
| 쉼 조절 | 별도 지연 기능은 없다. 쉼표·마침표로 0.5~1초 안에서만 조절된다. | [공식] [CLOVA Voice 문제 해결](https://guide.ncloud-docs.com/docs/clovavoice-troubleshoot) |
| 읽지 않는 글자 | "기호나 괄호 안의 텍스트는 미변환". 응답 문장에 괄호·이모지·마크다운이 섞이지 않게 LLM 출력을 정리한다. | [공식] TTS Premium |
| 429 오류 | Application에서 해당 API를 선택하지 않으면 429(Quota Exceed). 초당 호출 한도는 공개되지 않았다(미확인). | [공식] CLOVA Voice 개요 |
| 지연 | 공식 수치 없음, 공개 측정 글도 찾지 못했다(미확인). 첫날 짧은 문장(10자)·중간(40자)·긴 문장(120자)으로 p50/p95를 잰다. | — |
| 저장 금지 | 실시간 호출만 허용, 응답 재사용 불가(16b에 정리). 필러 음성도 매번 호출한다. | [공식] [문제 해결](https://guide.ncloud-docs.com/docs/clovavoice-troubleshoot) |
| 요금 | 월 기본료 90,000원(Application당, 만드는 순간 부과), 100만 자 포함, 초과 1,000자당 100원(16b 기준). 통화 1건 응답이 400자면 기본료로 약 2,500통화. | [공식] [16b](../../../.scratch/ai-biseo-ideation/research/16b-ai-services.md), [사용 준비](https://guide.ncloud-docs.com/docs/clovavoice-spec) |

### 2-2. 전화 응대용 화자 후보

목록 전체는 [TTS Premium 문서](https://api.ncloud-docs.com/docs/ai-naver-clovavoice-ttspremium)에 있다. 아래는 그중 가게 전화에 맞을 만한 것만 고른 것이다. "어울림"은 이름·설명만 보고 판단한 것이라 [추론]이고, 팀이 직접 들어 보고 정한다.

| speaker | 이름(문서) | 성별 | 감정 옵션 | 고른 이유 |
|---|---|---|---|---|
| `nara_call` | 아라(상담원) | 여 | 없음(문서상 emotion 지원 목록에 없음) | **이름 자체가 상담원용**이다. 1순위 |
| `vara` | 아라(Pro) | 여 | emotion 0~3, strength 0~2 | `emotion=2`(기쁨)로 밝은 가게 톤 |
| `vyuna` / `vgoeun` | 유나(Pro) / 고은(Pro) | 여 | emotion, strength | Pro 품질 대안 |
| `vdaeseong` / `vian` | 대성(Pro) / 이안(Pro) | 남 | vdaeseong만 emotion | 남성 목소리 대안 |
| `nes_c_hyeri`, `nes_c_kihyo`, `nes_c_mikyung`, `nes_c_sohyun` | 혜리, 기효, 미경, 소현 | 여·남 | 없음 | `nes_c_` 접두어 뜻은 문서에 없다(미확인). 같이 들어 본다 |

- `speed`는 −5(2배 빠름)~10(0.5배 느림)이다. 전화는 0 또는 −1부터 시작한다(추론). [공식]
- `mijin`은 16kHz만 지원한다. [공식]

### 2-3. 문장 단위 병렬 합성 전략 (추론)

| 단계 | 방법 |
|---|---|
| ① 문장 분할 | HCX 스트림 토큰을 모으다가 `. ? ! ~ 요.` 같은 끝에서 자른다. 첫 문장은 짧게(예: "네, 아메리카노 두 잔이요.") 나오도록 프롬프트에서 지시한다. 150자를 넘으면 쉼표에서 강제로 자른다(VS18 회피). |
| ② 정규화 | TTS 전에 숫자를 한국어로 바꾼다: "2잔"→"두 잔", "12,000원"→"만 이천 원", "3:30"→"세 시 삼십 분". CLOVA Voice의 숫자 읽기 규칙은 문서에 없어서(미확인) 코드로 고정한다. 채널톡도 날짜·시간·URL 발음을 따로 다뤘다고 밝혔다([2차] [디지털인사이트](https://ditoday.com/?p=155410)). |
| ③ 병렬 호출 | 문장이 나오는 즉시 `httpx.AsyncClient`로 TTS 요청을 띄운다. 동시 요청은 세마포어로 3개까지 둔다(한도 미확인이라 보수적으로). |
| ④ 순서 보장 | 요청은 병렬, 전송은 순서대로 한다. 문장 i의 결과를 기다린 뒤 i+1을 보낸다. 각 오디오에 `turn_id`, `seq`를 붙인다. |
| ⑤ 형식 | `wav`, `sampling-rate=24000`(기본). 브라우저 `decodeAudioData`가 WAV를 바로 푼다. mp3보다 디코딩이 단순하다. 크기는 24kHz×2바이트 = 48KB/초라 WebSocket으로 충분하다. |
| ⑥ 첫 소리 앞당기기 | 손님 말이 끝나면 NLU를 기다리지 않고 "네," 한 마디 TTS를 바로 호출해 먼저 튼다. 캐시 금지라 매번 호출한다. |

---

## Q3. 브라우저: 마이크, VAD, barge-in, 권한

### 3-1. 버전과 지원 범위

| 항목 | 값 | 등급·출처 |
|---|---|---|
| `@ricky0123/vad-web` | **0.0.31**(2026-09-12). 의존성 `onnxruntime-web ^1.17.0`. README는 `onnxruntime-web@1.22.0` + `vad-web@0.0.31` CDN 조합을 예시로 든다. | [공식] [npm](https://www.npmjs.com/package/@ricky0123/vad-web), [GitHub README](https://github.com/ricky0123/vad) |
| `@ricky0123/vad-react` | 0.0.37(2026-09-12), vad-web 0.0.31 사용 | [공식] [npm](https://www.npmjs.com/package/@ricky0123/vad-react) |
| `onnxruntime-web` 최신 | 1.31.1(2026-10-26). vad-web README 예시는 1.22.0이다. **README 조합(1.22.0)으로 고정**하는 편이 안전하다(추론). | [공식] [npm](https://www.npmjs.com/package/onnxruntime-web) |
| 내부 모델 | Silero VAD를 ONNX Runtime Web으로 돌린다. `model`은 `"legacy"`(기본)·`"v5"`·`"v6"`. | [공식] [vad 문서 API](https://docs.vad.ricky0123.com/user-guide/api/) |
| AudioWorklet 지원 | Chrome·Android Chrome 66+, Safari 14.1+, **iOS Safari 14.5+** | [공식] [MDN BCD 8.1.8](https://github.com/mdn/browser-compat-data) |
| getUserMedia 지원 | Chrome·Android 53+, iOS Safari 11+ | [공식] MDN BCD |
| HTTPS | 보안 컨텍스트(HTTPS, localhost)에서만 `navigator.mediaDevices`가 있다. HTTPS 페이지에서는 WebSocket도 `wss://`여야 한다(혼합 콘텐츠). | [공식] [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) |
| 오디오 재생 잠금 | 사용자 동작 전에 만든 AudioContext는 `suspended`로 시작하고, 동작 후 `resume()`을 불러야 한다. | [공식] [Chrome 자동재생 정책](https://developer.chrome.com/blog/autoplay) |

### 3-2. vad-web 설정 (주문 통화용)

| 옵션 | 기본값 [공식] | 추천 시작값 [추론] | 이유 |
|---|---|---|---|
| `model` | `legacy` | `v5` | 최신 Silero 계열. 첫날 legacy와 비교 |
| `positiveSpeechThreshold` | 0.3 | 0.5 | 가게·행사장 소음에서 오검출 줄이기 |
| `negativeSpeechThreshold` | 0.25 | 0.35 | positive보다 약간 낮게 |
| `redemptionMs` | 1400 | **600** | 이 시간만큼 조용하면 말 끝. 기본 1.4초는 1.5초 목표에 너무 길다 |
| `minSpeechMs` | 400 | 250 | "네", "포장이요" 같은 짧은 대답을 살린다 |
| `preSpeechPadMs` | 800 | 300 | 서버로는 오디오를 계속 보내므로 패딩은 짧아도 된다 |
| `getStream` | 기본 getUserMedia(echoCancellation·noiseSuppression·autoGainControl 켬, 1채널) | 우리가 연 스트림을 넘김 | 마이크 하나를 VAD와 서버 전송이 같이 쓰게 한다 |

콜백은 `onSpeechStart`, `onSpeechRealStart`(minSpeechMs를 넘긴 진짜 발화), `onSpeechEnd(audio)`(16kHz Float32), `onVADMisfire`, `onFrameProcessed(probabilities, frame)`가 있다. [공식] [vad 문서 API](https://docs.vad.ricky0123.com/user-guide/api/)

### 3-3. 마이크 → 16kHz PCM 만들기

| 방법 | 내용 | 판단 |
|---|---|---|
| A. vad-web 프레임 재사용 | `onFrameProcessed`가 프레임마다 불리고 `frame`(원시 오디오)을 준다. VAD는 16kHz로 모델을 돌리므로 이 프레임을 Int16으로 바꿔 3~4개(약 100ms)씩 묶어 보내면 **워클릿을 따로 안 짜도 된다**. 프레임 길이·샘플레이트가 16kHz인지는 문서에 없다(미확인). 첫날 `frame.length`를 찍어 확인한다. | **1순위**(비전공자 팀에 쉬움) [추론] |
| B. 직접 AudioWorklet | AudioContext는 기본 샘플레이트(보통 48kHz, iOS는 44.1kHz일 수도 있음)로 만든다. 워클릿에서 `sampleRate` 전역값을 읽어 16kHz로 줄이고(평균 또는 간단한 저역 필터 후 솎기) Int16으로 바꿔 100ms(3,200바이트)마다 `port.postMessage`로 넘긴다. `new AudioContext({sampleRate:16000})`도 Chrome 74+/iOS 14.5+에서 되지만, 마이크 스트림과 샘플레이트가 다를 때의 동작은 브라우저마다 달라 피한다. | A가 안 될 때 [추론] · [공식] MDN BCD |
| 공통 | Float32(−1~1) → Int16(×32767, 자르기) little-endian. WebSocket `binaryType='arraybuffer'`로 그대로 보낸다. 서버는 받은 그대로 gRPC chunk에 넣는다. | [공식] 오디오 형식 요건 |

### 3-4. barge-in(말 끊기)

| 단계 | 방법 [추론] |
|---|---|
| 감지 | AI 음성이 나오는 중에 `onSpeechRealStart`가 오면 끊기로 본다(`onSpeechStart`만으로는 기침·잡음에 끊긴다). 재생 중에는 `positiveSpeechThreshold`를 0.6~0.7로 올리는 방법도 시험한다. |
| 브라우저 동작 | ① 재생 중인 모든 `AudioBufferSourceNode.stop()` ② 재생 대기열 비우기 ③ 서버에 `{"type":"barge_in","turn_id":N}` 전송 ④ 이후 도착하는 `turn_id ≤ N` 오디오는 버린다. |
| 서버 동작 | 진행 중인 HCX 스트림과 TTS 작업을 취소하고(`task.cancel()`), 턴 번호를 올린다. 대화 기록에는 **실제로 재생된 문장까지만** 남긴다(브라우저가 문장별 재생 완료를 알려 준다). |
| 에코 | 마이크를 계속 열어 두므로 AI 목소리가 STT에 들어갈 수 있다. `echoCancellation`을 켜고, AI 재생 시간대와 겹치는 STT 조각은 barge-in이 없었다면 버린다. |
| 실패 대비 | barge-in이 불안정하면 "AI가 말하는 동안은 VAD만 듣고, 끊기 감지 시에만 STT 반영"하는 반이중(half-duplex) 모드로 내린다. |

### 3-5. iOS Safari / 안드로이드 크롬 주의점

| 항목 | 내용 | 등급·출처 |
|---|---|---|
| "전화 받기" 버튼 = 사용자 동작 | 이 탭 한 번에서 `getUserMedia` 요청 + `AudioContext.resume()` + VAD 시작을 같이 한다. 통화 UI와 브라우저 제약이 자연스럽게 맞는다. | [공식] 자동재생 정책 · [추론] |
| **iOS 26.2~27 재생음 지직거림** | 에코 제거를 켠 마이크가 열려 있는 동안 Web Audio·`<audio>` 재생이 지직거린다(WebKit 326286, 2026-10 보고, 미해결). 에코 제거를 끄면 깨끗하지만 **수화부(귀 스피커)로 작게** 나온다. 실행 사이에 상태가 남아 Safari를 완전히 꺼야 재현이 초기화된다. | [2차] [WebKit 326286](https://bugs.webkit.org/show_bug.cgi?id=326286) |
| iOS 마이크 켜면 음질 저하 | 마이크를 열면 재생 샘플레이트가 낮아지고 모노가 된다(WebKit 311451, iOS 26.4·27 베타, 미해결). | [2차] [WebKit 311451](https://bugs.webkit.org/show_bug.cgi?id=311451) |
| iOS AudioWorklet 녹음 깨짐(옛 버그) | 2023년 iOS 15.7에서 워클릿 녹음이 깨진다는 보고가 있다(미해결 상태로 방치). 최신 iOS 재현 여부는 미확인. | [2차] [WebKit 244203](https://bugs.webkit.org/show_bug.cgi?id=244203) |
| 결론 | **데모 기본 기기는 안드로이드 크롬.** 아이폰은 첫 주에 실기 테스트하고, 문제가 있으면 "에코 제거 끄고 폰을 귀에 대고 통화"하는 진짜 전화 자세로 쓴다(수화부 출력이 오히려 전화답다). 심사위원 폰이 아이폰일 수 있으니 우리 안드로이드 폰을 건네는 시나리오를 준비한다. | [추론] |
| 권한 프롬프트 | 브라우저는 최소 첫 번째에는 반드시 묻는다. 사용자가 무시하면 Promise가 끝나지 않을 수 있다. 화면에 "마이크 허용을 눌러 주세요" 안내와 타임아웃을 둔다. | [공식] [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) |
| 화면 꺼짐 | 통화 중 화면이 꺼지면 오디오가 멈출 수 있다. Screen Wake Lock API로 막는다(지원 범위 미확인). | [추론] |

---

## Q4. FastAPI WebSocket 양방향 구조와 1.5초 파이프라인

### 4-1. 연결 하나에 도는 작업들

```
[폰 브라우저]                                [NCP 서버 · FastAPI · uvicorn 1 worker]
 mic → (vad-web 프레임 or 워클릿) ─PCM16 100ms(binary)─▶ ws_reader ──▶ audio_q ──▶ stt_task(grpc.aio CLOVA Speech)
 VAD 이벤트 ─JSON {speech_start/speech_end/barge_in}─▶ ws_reader ──▶ stt_task(epFlag+seqId), dialog_task(취소)
                                                                         │ 확정 문장(turn N)
                                                                         ▼
                                                     dialog_task: NLU(HCX FC) → 주문 상태기계 → 응답 문장
                                                                         │ 문장 1, 2, 3 …
                                                                         ▼
 재생 큐 ◀─[header turn_id,seq]+WAV(binary)── tts_sender ◀── tts 작업들(httpx, 세마포어 3, 순서 보장)
 자막·주문카드 ◀─JSON {stt_final/bot_text/order/latency}── ws_writer
                                                     owner_hub ─▶ 사장님 화면(WebSocket /owner/{store_id})
```

| 구성 요소 | 내용 | 등급·출처 |
|---|---|---|
| WebSocket API | `receive()`(원시 메시지, bytes/text 구분), `receive_bytes()`, `receive_text()`, `send_bytes()`, `send_json()`, 끊김 시 `WebSocketDisconnect`. 오디오는 binary, 이벤트는 text JSON으로 한 소켓에 섞는다. | [공식] [FastAPI WebSocket 레퍼런스](https://fastapi.tiangolo.com/reference/websockets/) |
| 패키지 | `fastapi` 0.162.1, `uvicorn` 0.54.0, `websockets` 17.4, `httpx` 0.28.1, `grpcio` 1.85.1 | [공식] PyPI 각 페이지 |
| 동시성 | 연결마다 `asyncio.TaskGroup`으로 reader·stt·dialog·tts·writer를 묶는다. 하나가 죽으면 같이 정리된다. 상태는 메모리에 두므로 uvicorn 워커는 1개로 한다. | [추론] |
| 턴 번호 | `turn_id`를 모든 메시지에 붙인다. barge-in이나 새 발화가 오면 올린다. 늦게 도착한 옛 턴 결과는 버린다. 이게 없으면 끊긴 뒤에 옛 대답이 다시 나온다. | [추론] |
| 인증서 | 폰 브라우저 접속이라 공인 도메인 + TLS가 필요하다. NCP 서버에 nginx + Let's Encrypt, 또는 NCP Load Balancer + Certificate Manager. | [공식] HTTPS 요건 · [추론] 방법 |

### 4-2. 1.5초 지연 예산

"손님 말이 실제로 끝난 시점 → 첫 소리"를 잰다. 단계별 공식 지연 수치는 모두 미확인이라 목표값이다.

| 단계 | 목표 | 줄이는 방법 [추론] |
|---|---|---|
| ① 말 끝 감지(VAD `redemptionMs`) | 600ms | 기본 1,400ms에서 내린다. 이어질 말이면 기다리는 규칙은 ③에서 처리 |
| ② STT 확정(epFlag 후 결과) | ≤300ms(미확인) | 서버 gap 대기를 쓰지 않고 epFlag로 즉시 확정 |
| ③ "끝났나" 판단 + NLU | ≤500ms(미확인) | 끝말이 "~하고/~랑/그리고/어/음"이면 1.0~1.5초 더 기다림(채널톡 87% 데이터, 17번 조사). NLU는 HCX-DASH-002 FC 비스트리밍 1회 |
| ④ 응답 문장 | 0ms(템플릿) / 스트림 첫 문장 | 복창·빠진 슬롯 질문·확인은 **템플릿**. 메뉴 설명 같은 자유 질문만 HCX 스트리밍 |
| ⑤ 첫 문장 TTS | ≤400ms(미확인) | 첫 문장은 짧게. "네," 즉시 응답을 ①직후 병렬로 호출 |
| ⑥ 전송 + 디코딩 + 재생 시작 | ~100ms | WAV, 재생 큐 미리 준비 |
| **합계** | ①~⑥ ≈ 1.5~1.9초 | "네," 선응답으로 체감은 ①+⑤ ≈ 1.0초 |

- **선제 실행:** VAD가 말 끝을 잡는 즉시 STT 확정과 NLU를 시작한다. 손님이 1.5초 안에 다시 말하면 결과를 버리고 다시 듣는다. LiveKit·Pipecat 류가 쓰는 방식과 같은 생각이다(추론).
- **측정:** 각 단계 시각을 서버 로그와 사장님 화면에 ms로 띄운다. 첫 주 안에 실측해 위 목표를 고친다.

---

## Q5. 턴 관리와 주문 대화 상태

### 5-1. 참고 설계(공개 예제)

| 사례 | 공개된 설계 포인트 | 등급·출처 |
|---|---|---|
| Pipecat Flows `food_ordering.py` | 노드 그래프: `initial`(메뉴 고르기) → `choose_pizza`/`choose_sushi`(세부 주문) → `confirm`(확인) → `end`(`post_actions: end_conversation`). 각 노드는 `task_messages`(그 단계 지시)와 함수 목록을 갖고, 함수 결과로 다음 노드를 정한다. `revise_order`로 확인 단계에서 수정으로 돌아간다. pipecat-ai-flows 1.4.0 | [공식] [예제 코드](https://github.com/pipecat-ai/pipecat-flows/blob/main/examples/food_ordering.py), [PyPI](https://pypi.org/project/pipecat-ai-flows/) |
| LiveKit Agents `drive_thru` | 주문은 코드 객체 `OrderState.items{order_id: OrderedItem}`. 항목 타입(세트·해피밀·단품)을 pydantic으로 정의하고 사이즈 같은 옵션을 `Literal`로 제한한다. LLM은 `order_combo_meal`, `order_regular_item`, `remove_order_item`, `list_order_items` 같은 **도구만 호출**하고 실제 장바구니 변경은 코드가 한다. 변경할 때마다 `on_change`로 화면에 장바구니를 푸시한다. 손님이 조용하면 "아직 계신지" 확인하는 루프가 있다. | [공식] [order.py](https://github.com/livekit/agents/blob/main/examples/drive_thru/order.py), [agent.py](https://github.com/livekit/agents/blob/main/examples/drive_thru/agent.py) |
| 채널톡 보이스 알프 | 자체 턴 감지 모델, 사내 카페 전화 주문 1만 건에서 "기다린 경우 87%가 1.5초 안에 이어 말함", 날짜·시간·URL 발음 처리. 확인 방식·지연 수치는 비공개. | [2차] [디지털인사이트](https://ditoday.com/?p=155410), [벤처스퀘어](https://www.venturesquare.net/1121742/) |
| KT AI 통화비서 | 2021~2022 자료 기준: 예약·주문 접수, AI 메모를 전용 앱으로 전송, Lite는 앱 문자 발신. 2026년 LLM 전환·현재 기능은 미확인. | [2차] [뉴스토마토](https://newstomato.com/ReadNews.aspx?no=1145223), [App Store](https://apps.apple.com/kr/app/ai-%ED%86%B5%ED%99%94%EB%B9%84%EC%84%9C-lite/id1640406058) |

### 5-2. 우리 주문 슬롯 (추론)

| 슬롯 | 필수 | 채우는 법 | 확인 방식 |
|---|---|---|---|
| `items[]` = {`menu_id`, `qty`, `options{}`} | 예 | NLU 도구 `add_item(menu_name, qty, options)` → 코드가 메뉴 DB와 맞춰 `menu_id`로 바꾼다. 없는 메뉴면 도구가 오류를 돌려주고 AI가 "그 메뉴는 없어요, ○○는 있어요"라고 답한다. | 항목마다 짧게 되받기("아메리카노 두 잔이요.") |
| 필수 옵션(사이즈, 온도, 맵기 등) | 메뉴별 | 메뉴 DB에 `required_options`를 둔다. 비어 있으면 상태기계가 질문을 고른다. | 질문 → 답 |
| `pickup_time` | 예 | "20분 뒤", "6시 반" → 코드가 시각으로 바꾸고 가게 준비 시간보다 이르면 다시 제안 | 최종 복창에 포함 |
| `customer_name` | 선택 | 성만 받는다("김"씨) | 최종 복창 |
| `phone` | 예(실서비스) | 실제 전화는 발신번호(CID)가 있으니 **묻지 않는다.** 데모는 가짜 발신번호를 미리 채우고 "이 번호로 문자 드릴까요?"만 묻는다. 숫자 받아쓰기는 STT 오류가 잦다. | 예/아니오 |
| `requests` | 선택 | 자유 문장("덜 맵게", "수저 빼 주세요")을 그대로 저장 | 최종 복창 |
| `total` | 자동 | **코드가 계산**한다. LLM에 계산을 맡기지 않는다. | 최종 복창 |

### 5-3. 상태기계 (추론)

| 상태 | 들어오는 조건 | AI가 하는 일 | 다음 |
|---|---|---|---|
| `GREETING` | 통화 연결 | "네, ○○분식입니다. 주문 도와드릴게요." | `ORDERING` |
| `ORDERING` | 기본 | 항목 추가·변경·삭제. 메뉴 질문이면 RAG로 답하고 돌아온다. | 필수 옵션 빔 → `CLARIFY` / "그게 다예요" → `CONFIRM` |
| `CLARIFY` | 필수 슬롯 빔 | 한 번에 **하나만** 묻는다("사이즈는 톨, 그란데 중에 어떤 걸로 드릴까요?") | `ORDERING` |
| `CONFIRM` | 주문 끝 신호 | 전체 복창: 메뉴·수량·옵션·픽업 시간·총액 → "맞으세요?" | 예 → `CLOSING` / 수정 → `ORDERING` |
| `CLOSING` | 확인됨 | "○시 ○분에 준비해 둘게요. 문자로도 보내 드릴게요." | `DONE`(사장님 화면에 주문서 전송) |
| `ASK_OWNER` | 답을 모름(RAG 근거 없음), 예외 요청 | "잠시만요, 사장님께 여쭤볼게요." → 사장님 화면에 번역된 질문 + 빠른 답 버튼. 20초 안에 답이 오면 번역해서 말한다. | 답 옴 → 원래 상태 / 시간 초과 → "확인해서 문자로 알려 드릴게요" |
| `FALLBACK` | 같은 질문 3번 실패, 손님이 "사람이랑 통화" 요청 | "사장님께 메모 남겨 드릴게요." 통화 요약을 사장님 언어로 남긴다. | `DONE` |
| 전역 | 8초 무음 | "여보세요? 듣고 계세요?" 2번이면 종료 | — |
| 전역 | "다시 말해 주세요" | 마지막 AI 문장을 다시 TTS(새로 호출) | 이전 상태 |

- **역할 나누기:** LLM은 ① 손님 말 → 도구 호출(슬롯 추출) ② 자유 질문 답변 문장만 만든다. 상태 전이, 메뉴 매칭, 총액, 확인 문구는 코드와 템플릿이 한다. 이러면 지연이 줄고 복창이 틀리지 않는다.
- **모델:** HCX-DASH-002(32K, Function calling 지원, 가벼운 작업용)를 NLU에 쓴다. HCX-005도 FC를 지원한다. Structured Outputs는 HCX-007만 된다. [공식] [CLOVA Studio 모델](https://guide.ncloud-docs.com/docs/clovastudio-model)
- **FC 스트리밍:** v3 스트림에서 도구 호출은 `toolCalls[].function.partialJson` 조각으로 오고 마지막 `result` 이벤트에 완성된 `arguments`가 온다. NLU는 비스트리밍으로 받는 편이 단순하다. [공식] [Function calling](https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-fc)

---

## Q6. 오픈소스 음성 에이전트 프레임워크를 쓸까

| 항목 | Pipecat | LiveKit Agents | 직접 구현 |
|---|---|---|---|
| 최신 버전 | `pipecat-ai` 1.15.0(2026-10-29, Python ≥3.11) [공식] [PyPI](https://pypi.org/project/pipecat-ai/) | `livekit-agents` 1.8.11(2026-11-05, Python 3.10~3.14) [공식] [PyPI](https://pypi.org/project/livekit-agents/) | FastAPI 0.162.1 등 |
| 필요한 인프라 | 브라우저용은 `SmallWebRTCTransport`(aiortc) 권장. 시그널링은 직접, 다른 네트워크 간에는 STUN, 엄격한 NAT은 TURN이 필요하다. `FastAPIWebsocketTransport`는 "전화(telephony)와 서버 간 연동에 가장 적합"하고 일반 클라이언트-서버는 WebRTC를 권장한다. [공식] [SmallWebRTC](https://docs.pipecat.ai/server/services/transport/small-webrtc), [FastAPI WS](https://docs.pipecat.ai/server/services/transport/fastapi-websocket) | **LiveKit 서버(클라우드 또는 자체 호스팅)에 등록**해야 하고 에이전트가 방(room)에 들어가는 구조다. [공식] [LiveKit Agents](https://docs.livekit.io/agents/) LiveKit Cloud는 NCP 밖이다(추론). | NCP 서버 1대 + HTTPS 도메인 |
| CLOVA STT 연결 | 공식 서비스 없음. `STTService`를 상속해 `run_stt`를 구현하고 gRPC 스트림을 붙여야 한다. [공식] [STT 서비스 레퍼런스](https://reference-server.pipecat.ai/en/stable/api/pipecat.services.stt_service.html) | 공식 플러그인 없음. 플러그인 직접 작성(방법 문서는 이번에 확인 못 함) | grpc.aio 직접 |
| CLOVA Voice 연결 | 공식 서비스 없음. HTTP형 TTS로 직접 구현. 문장 묶기(기본 문장 단위, 언어 설정 따라감)와 끊기 처리가 내장. 한국어 문장 경계 처리 품질은 미확인. [공식] [TTS 가이드](https://docs.pipecat.ai/guides/learn/text-to-speech) | 직접 작성 | httpx 직접 |
| HCX 연결 | CLOVA Studio가 OpenAI 호환 API(`https://clovastudio.stream.ntruss.com/v1/openai/`)를 준다. OpenAI용 LLM 서비스에 `base_url`만 바꿔 쓸 수 있을 것이다. 단 **`stream_options`는 미지원 필드**라, 라이브러리가 이 값을 보내면 오류가 날 수 있다(미확인). [공식] [오픈AI 호환성](https://api.ncloud-docs.com/docs/clovastudio-openaicompatibility) | 같은 위험 | 공식 v3 API 직접 |
| 장점 | VAD·끊기·문장 분할·지연 측정이 이미 있다. Flows로 상태기계를 선언형으로 짠다. | 대규모·상용급 | 구조가 단순해 팀 전원이 읽을 수 있다. epFlag/seqId 같은 CLOVA 고유 동작을 그대로 쓴다. 심사 때 "전부 NCP"를 설명하기 쉽다. |
| 단점(2주·비전공자 팀) | 프레임·프로세서 개념 학습, 커스텀 STT/TTS 2개 작성, WebRTC 디버깅. 행사장 네트워크에서 UDP가 막히면 TURN이 필요하다. | LiveKit 서버 운영까지 추가. 2주에 과함 | 끊기·순서 보장·취소를 직접 짜야 한다 |
| **판단** | 팀원 중 Pipecat 경험자가 있을 때만 | **쓰지 않는다** | **권장** |

- 직접 구현 모듈은 5개면 된다: `ws_gateway`(소켓·이벤트), `stt_clova`(gRPC·epFlag·재연결), `dialog`(상태기계·NLU·템플릿), `tts_clova`(문장 분할·정규화·병렬·순서), `owner_hub`(사장님 화면 푸시·번역). 각 모듈이 작아서 비전공자 팀원에게 `dialog` 템플릿 문구와 메뉴 데이터처럼 코드 밖 작업을 나눌 수 있다(추론).
- 설계는 Pipecat Flows의 노드 구조와 LiveKit `drive_thru`의 "도구는 LLM, 상태 변경은 코드" 원칙을 가져온다.

---

## Q7. 주문 완료 후 사장님에게 전달

| 항목 | 내용 | 등급·출처 |
|---|---|---|
| Papago 텍스트 번역 | `POST https://papago.apigw.ntruss.com/nmt/v1/translation`, 헤더 `X-NCP-APIGW-API-KEY-ID/KEY`, 필드 `source`(auto 가능)·`target`·`text`(5,000자)·`glossaryKey`·`honorific`. 응답 `message.result.translatedText`. | [공식] [텍스트 번역](https://api.ncloud-docs.com/docs/ai-naver-papagonmt-translation) |
| 한국어에서 갈 수 있는 언어 | en, ja, zh-CN, zh-TW, **vi, th, id**, fr, es, ru, de, it (12개). 네팔어·우즈베크어·몽골어 등은 없다. | [공식] 같은 문서 |
| 용어집 제약 | 용어집·치환은 ko⇔en·ja·zh·fr만 된다. **ko→vi·th·id 용어집은 안 된다**(en⇔vi·th·id만 됨). 그래서 메뉴명을 번역기에 맡기면 매번 다르게 나올 수 있다. | [공식] 같은 문서 |
| 메뉴명 처리 | 메뉴명·옵션명은 Part B 메뉴 DB에 **사장님 언어 이름을 미리 저장**하고 `menu_id`로 찾아 보여 준다. 번역 API는 손님 요청사항 같은 자유 문장에만 쓴다. | [추론] |
| 미지원 언어 | Papago가 없는 언어는 HCX로 번역하거나 영어로 보여 준다. HCX의 해당 언어 품질은 미확인이라 데모는 Papago 지원 언어(예: 베트남어, 중국어) 사장님으로 잡는다. | [추론] |
| 통화 중 실시간 자막 | CLOVA Speech 스트리밍 설정에 `translation.targets:["vi"]`를 넣으면 인식 결과를 Papago로 번역해 준다(Streaming 2.0, 2025-11-20). `mergedResult=false`(기본)면 번역 결과가 따로 오고 번역용 EPD(gap 2,000ms 기본)를 쓴다. 도메인 추가 기능으로 Papago 번역을 켜고 Papago Application을 등록해야 한다. 사장님 화면에 "손님이 지금 하는 말"을 사장님 언어로 띄울 수 있다. | [공식] [gRPC API](https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-grpc), [릴리스 노트](https://api.ncloud-docs.com/docs/ai-clovaspeech-grpc-releasenote), [도메인 가이드](https://guide.ncloud-docs.com/docs/clovaspeech-domain) |
| 요금 | Papago 텍스트 100만 자당 20,000원(16b). 주문서 1건 200자면 0.4원 수준. | [공식] [16b](../../../.scratch/ai-biseo-ideation/research/16b-ai-services.md) |
| 사장님 화면 구성 | ① 새 주문 알림(소리 + 큰 카드) ② 메뉴·수량·옵션(사장님 언어, 아래 작게 한국어 원문) ③ 픽업 시각·총액 ④ 요청사항(번역 + 원문) ⑤ "접수"·"시간 늦춤(+10분)" 버튼 → 손님 문자 미리보기 ⑥ `ASK_OWNER` 질문 카드(빠른 답 버튼: "가능/불가/+1,000원") | [추론] |
| 손님 문자 | NCP SENS는 발신번호 서류 인증에 영업일 3~4일(17번 조사). 데모는 화면 속 문자 미리보기로 대체한다. | [공식] [SENS 발신번호](https://guide.ncloud-docs.com/docs/sens-callingno) |

---

## PRD에 넣을 방향(확정 아님)

### 추천 구조

- **클라이언트:** 손님용 폰 웹(HTTPS, 전화 수신 화면 → "받기" → 통화 중 화면)과 사장님용 웹(주문 카드, 실시간 번역 자막, 질문 카드, 단계별 지연 ms). 데모 폰은 안드로이드 크롬.
- **서버:** NCP 서버 1대에 FastAPI(uvicorn 1 worker). WebSocket 두 개: `/call/{store_id}`(손님 오디오·이벤트), `/owner/{store_id}`(사장님 화면 푸시).
- **AI:** CLOVA Speech 스트리밍(gRPC, ko, 메뉴 부스팅, 사장님 언어 번역 타깃), HCX-DASH-002(NLU 도구 호출) + HCX-005 또는 DASH-002(자유 질문 RAG 답변 스트리밍), CLOVA Voice Premium(`nara_call` 1순위, wav 24k), Papago(요청사항 번역).
- **프레임워크:** 쓰지 않는다. 모듈 5개(`ws_gateway`, `stt_clova`, `dialog`, `tts_clova`, `owner_hub`)로 직접 만든다. 설계는 Pipecat Flows 노드 구조와 LiveKit drive_thru의 "도구는 LLM, 상태는 코드" 원칙을 따른다.

### 파이프라인

1. "받기" 탭 → 마이크 권한 + AudioContext resume + vad-web 시작(v5, redemption 600ms, minSpeech 250ms) + WebSocket 연결 + gRPC 스트림 열기(설정 응답 확인 후 오디오 시작).
2. 브라우저는 16kHz PCM16을 100ms마다 계속 보낸다(무음 포함).
3. VAD `speech_end` → 서버가 `epFlag=true, seqId=turn_id` 전송 → 같은 seqId 결과가 오면 턴 확정. 동시에 "네," TTS를 띄운다.
4. 끝말이 이어질 말이면 최대 1.5초 기다린다. 아니면 HCX-DASH-002 FC로 슬롯을 뽑고, 상태기계가 다음 말을 정한다(템플릿 우선).
5. 응답 문장 → 숫자 한글화 → 150자 이하로 자르기 → TTS 병렬(최대 3) → `turn_id, seq`를 붙여 순서대로 전송 → 브라우저 재생 큐.
6. AI가 말하는 중 `onSpeechRealStart` → 재생 중지·큐 비우기 → 서버가 HCX·TTS 작업 취소, 턴 번호 증가.
7. `CONFIRM`에서 전체 복창(총액은 코드 계산) → "맞으세요?" → `CLOSING` → 사장님 화면에 사장님 언어 주문서 + 손님 문자 미리보기.
8. 모르는 질문은 `ASK_OWNER`: 사장님 화면에 번역된 질문과 빠른 답 버튼, 20초 대기, 답을 한국어로 바꿔 손님에게 말한다.
9. 4분 30초가 넘으면 턴 사이에 gRPC 스트림을 새로 연다. 오류가 나면 즉시 재연결하고 2초 버퍼를 보낸다.

### 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| CLOVA Speech·HCX·Voice 지연 수치가 공식적으로 없음 | 1.5초 목표 미달 | 11/10까지 단계별 실측. "네," 선응답, 템플릿 응답, 선제 실행. 안 되면 push-to-talk 모드(17번 조사 fallback) |
| iOS 26.2+ Safari 재생음 지직거림·음질 저하(WebKit 326286, 311451 미해결) | 아이폰 심사위원 체험 품질 | 안드로이드 크롬 기본. 아이폰은 에코 제거 끄고 귀에 대는 모드 시험 |
| 중간 STT 결과 없음 | 자막이 조각 단위로만 뜸, 말 끊기 판단을 STT로 못 함 | 끊기는 VAD로만 판단. 자막은 조각 단위 갱신으로 충분 |
| 에코가 STT에 섞임 | AI 말을 손님 말로 오인 | echoCancellation 켬, 재생 구간 STT 조각 무시, 필요 시 반이중 모드 |
| CLOVA Voice 호출 한도 미확인, 문장당 200자 | 429·VS18 오류 | 세마포어 3, 150자 분할, 실패 시 1회 재시도 후 다음 문장 |
| 메뉴명 오인식 | 잘못된 주문 | 메뉴 부스팅, 신뢰도 낮은 항목만 재확인, 최종 복창 필수 |
| Object Storage 버킷·Basic 도메인·CLOVA Voice 기본료(월 9만 원, 만들면 바로 부과) | 준비 지연·비용 | 11/8 안에 버킷·스트리밍 도메인·Voice·Papago Application 생성. 대회 크레딧 확인 |
| HTTPS·wss 미비 | 폰에서 마이크 자체가 안 열림 | 첫날 도메인 + TLS부터 세팅 |
| 행사장 네트워크 | 데모 실패 | 폰 테더링 백업, 녹화 영상 백업(17번 조사) |

### 일정 제안(11/8~11/20)

| 날짜 | 할 일 |
|---|---|
| 11/8~9 | NCP 리소스 생성, HTTPS 도메인, gRPC 파일 스트리밍 성공(공식 예제), TTS 화자 청취 비교 |
| 11/10~12 | 브라우저 마이크 → WS → gRPC 실시간 자막, VAD+epFlag 턴 확정, 단계별 지연 실측 |
| 11/13~15 | 상태기계·NLU 도구·템플릿, TTS 병렬·순서 재생, barge-in |
| 11/16~18 | 사장님 화면(번역 주문서, 실시간 자막, ASK_OWNER), 숫자 정규화, 아이폰·안드로이드 실기 테스트 |
| 11/19~20 | 시나리오 리허설 10회, 지연 튜닝, 녹화 백업 |

---

## 출처

**[공식] NCP**
- CLOVA Speech 실시간 스트리밍 API(gRPC): https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-grpc
- CLOVA Speech 실시간 스트리밍 예제(Java·Python): https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-example02
- CLOVA Speech 실시간 스트리밍 문제 해결(5분 수명, 청크, epFlag): https://api.ncloud-docs.com/docs/ai-application-service-clovaspeech-troubleshoot-grpc
- CLOVA Speech 스트리밍 릴리스 노트: https://api.ncloud-docs.com/docs/ai-clovaspeech-grpc-releasenote
- CLOVA Speech 도메인 가이드(Object Storage 필요): https://guide.ncloud-docs.com/docs/clovaspeech-domain
- CLOVA Speech 스트리밍 인식 빌더(Secret Key, 부스팅 500단어 권장): https://guide.ncloud-docs.com/docs/clovaspeech-builder-streaming
- CLOVA Voice 개요(헤더, 상태 코드 VS18): https://api.ncloud-docs.com/docs/ai-naver-clovavoice
- CLOVA Voice TTS Premium(화자 목록, 파라미터): https://api.ncloud-docs.com/docs/ai-naver-clovavoice-ttspremium
- CLOVA Voice 사용 준비: https://guide.ncloud-docs.com/docs/clovavoice-spec
- CLOVA Voice 문제 해결: https://guide.ncloud-docs.com/docs/clovavoice-troubleshoot
- CLOVA Studio 오픈AI 호환성: https://api.ncloud-docs.com/docs/clovastudio-openaicompatibility
- CLOVA Studio Function calling(v3): https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-fc
- CLOVA Studio Chat Completions v3: https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3
- CLOVA Studio 모델 비교: https://guide.ncloud-docs.com/docs/clovastudio-model
- CLOVA Studio 릴리스 노트: https://api.ncloud-docs.com/docs/clovastudio-releasenote
- Papago 텍스트 번역: https://api.ncloud-docs.com/docs/ai-naver-papagonmt-translation
- SENS 발신번호: https://guide.ncloud-docs.com/docs/sens-callingno

**[공식] 라이브러리·표준**
- grpc.aio 문서: https://grpc.github.io/grpc/python/grpc_asyncio.html
- PyPI: https://pypi.org/project/grpcio/ · https://pypi.org/project/grpcio-tools/ · https://pypi.org/project/fastapi/ · https://pypi.org/project/uvicorn/ · https://pypi.org/project/websockets/ · https://pypi.org/project/httpx/ · https://pypi.org/project/pipecat-ai/ · https://pypi.org/project/pipecat-ai-flows/ · https://pypi.org/project/livekit-agents/
- npm: https://www.npmjs.com/package/@ricky0123/vad-web · https://www.npmjs.com/package/@ricky0123/vad-react · https://www.npmjs.com/package/onnxruntime-web
- ricky0123/vad GitHub README: https://github.com/ricky0123/vad
- vad-web API 문서: https://docs.vad.ricky0123.com/user-guide/api/
- MDN getUserMedia: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- MDN browser-compat-data 8.1.8(AudioWorklet, getUserMedia 지원 버전): https://github.com/mdn/browser-compat-data
- Chrome 자동재생 정책(AudioContext suspended): https://developer.chrome.com/blog/autoplay
- FastAPI WebSocket 레퍼런스: https://fastapi.tiangolo.com/reference/websockets/
- Pipecat SmallWebRTCTransport: https://docs.pipecat.ai/server/services/transport/small-webrtc
- Pipecat FastAPIWebsocketTransport: https://docs.pipecat.ai/server/services/transport/fastapi-websocket
- Pipecat TTS 가이드: https://docs.pipecat.ai/guides/learn/text-to-speech
- Pipecat STT 가이드: https://docs.pipecat.ai/guides/learn/speech-to-text
- Pipecat STTService 레퍼런스: https://reference-server.pipecat.ai/en/stable/api/pipecat.services.stt_service.html
- Pipecat Flows food_ordering 예제: https://github.com/pipecat-ai/pipecat-flows/blob/main/examples/food_ordering.py
- LiveKit Agents 개요: https://docs.livekit.io/agents/
- LiveKit drive_thru 예제: https://github.com/livekit/agents/tree/main/examples/drive_thru

**[2차]**
- WebKit 326286(iOS 26.2~27 에코 제거 시 재생 지직거림): https://bugs.webkit.org/show_bug.cgi?id=326286
- WebKit 311451(마이크 사용 시 재생 음질 저하): https://bugs.webkit.org/show_bug.cgi?id=311451
- WebKit 244203(AudioWorklet 녹음 깨짐, 2023): https://bugs.webkit.org/show_bug.cgi?id=244203
- 디지털인사이트, 채널톡 보이스 알프 출시: https://ditoday.com/?p=155410
- 벤처스퀘어, 보이스 알프 출시: https://www.venturesquare.net/1121742/
- 뉴스토마토, KT AI 통화비서 Lite(2022): https://newstomato.com/ReadNews.aspx?no=1145223
- App Store, KT AI 통화비서 Lite: https://apps.apple.com/kr/app/ai-%ED%86%B5%ED%99%94%EB%B9%84%EC%84%9C-lite/id1640406058

**선행 조사**
- 17-ai-phone-order-on-ncp.md, 16b-ai-services.md(같은 저장소 `.scratch/ai-biseo-ideation/research/`)
