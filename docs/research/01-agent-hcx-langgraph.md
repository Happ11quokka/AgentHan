# 01. 사장님 비서 에이전트: HCX × LangGraph 리서치

> PRD v0.2(2026-11-08) 메모: CLOVA 호출은 이 문서의 추천(`langchain-naver`)과 달리 sdk.md 네이티브 REST 공통 클라이언트로 바꿨다. 아래 1장의 이슈 표가 그 근거다. 정본은 `docs/PRD.md` §2.

> 조사일 2026-11-07 · 담당 파트 A

- 기존 조사([16a](../../../.scratch/ai-biseo-ideation/research/16a-clova-studio.md), [18](../../../.scratch/ai-biseo-ideation/research/18-hcx-agent-practice.md), [16c](../../../.scratch/ai-biseo-ideation/research/16c-infra-mcp.md), `docs/sdk.md`)에 있는 모델 사양, 요금, 골드셋 100문항 구성, RAG 설계는 다시 쓰지 않았다. 필요한 곳은 "16a §표", "18 §5"처럼 가리킨다.
- 등급: **[공식]** NCP 공식 문서·LangChain 공식 문서·공식 GitHub 소스와 릴리스 / **[2차]** 서드파티 글이나 코드 / **[추론]** 출처를 조합한 우리 판단. 확인하지 못한 것은 **미확인**으로 적었다.
- 버전은 2026-11-07에 PyPI JSON API로 직접 확인했다.
- "로컬 확인"은 API 키 없이 최신 패키지를 설치해 요청 본문만 만들어 본 것이다. 실제 HCX 호출은 하지 않았다.

## 요약

1. **langchain-naver는 아직 0.1.1(2025-08-07)이 최신이다.** 최신 LangChain 1.x·langchain-openai 1.7.4와 같이 설치해도 import와 요청 생성은 된다(로컬 확인). 모델마다 되는 기능이 다르다. 도구 호출은 005·007·DASH-002 모두 된다. 이미지는 005만, 구조화 출력(SO)은 007만 된다. 이미지·도구·SO·thinking은 한 요청에 하나만 쓸 수 있다.
2. **LangChain/LangGraph 공식 문서는 "서브에이전트를 도구로 감싸는 방식"을 권한다.** `langgraph-supervisor` README도 이 라이브러리 대신 도구 방식을 쓰라고 한다. 서브에이전트에는 질문만 주고 마지막 답만 돌려받는다. 이렇게 하면 오케스트레이터 맥락이 깨끗하게 유지된다.
3. **도구가 많을 때는 "먼저 고르고 나중에 붙인다".** DASH-002로 기능 후보를 고른 뒤 그 기능의 도구 1~3개만 붙인다. LangChain 1.x의 `LLMToolSelectorMiddleware`와 `wrap_model_call`로 도구 목록을 바꿀 수 있다. 다만 도구 선택기는 SO를 쓰므로 HCX-007이 필요하다.
4. **메뉴 사진은 HCX-005 비전 단독(2단계)이 현실적이다.** CLOVA OCR은 문서상 ko·ja·zh-TW만 지원해서 키릴 문자·베트남어 메뉴판에는 맞지 않을 수 있다. 1단계로 HCX-005가 사진을 텍스트로 읽고, 2단계로 텍스트 모델이 `MenuItem` JSON을 만든다. 저장은 사장님 승인 뒤에만 한다.
5. **평가는 LangSmith 없이 pytest + `agentevals`로 할 수 있다(로컬 확인).** 한도는 테스트 키 기준 HCX-005·007이 60 QPM / 60k TPM이다. TPM은 "입력 + max_tokens 예약분"으로 세고, 메인 계정 하나를 팀 4명이 같이 쓴다. 그래서 응답 캐시와 `max_tokens=1024` 고정이 꼭 필요하다.

---

## 1. langchain-naver · ChatClovaX 지원 범위

### 1-1. 버전 (PyPI, 2026-11-07 확인)

| 패키지 | 최신 버전 | 올린 날 | 메모 |
|---|---|---|---|
| langchain-naver | **0.1.1** | 2025-08-07 | 1년 넘게 새 릴리스가 없다. 의존성은 `langchain-core>=0.3.51`, `langchain-openai>=0.3.12`이고 상한이 없다. [공식] |
| langchain | 1.4.9 | 2026-11-04 | [공식] |
| langchain-core | 1.7.6 | 2026-11-05 | [공식] |
| langchain-openai | 1.7.4 | 2026-11-04 | ChatClovaX가 이 패키지를 상속한다. [공식] |
| langgraph | 1.2.20 | 2026-11-05 | [공식] |

- ChatClovaX는 `langchain_openai`의 **비공개 함수**(`_convert_message_to_dict` 등)를 가져다 쓴다. 그래서 langchain-openai가 올라가면 깨질 수 있다. 2026-11-07 최신 조합에서는 import, `bind_tools`, 이미지 메시지 변환, `with_structured_output`이 모두 만들어졌다(로컬 확인). → **uv.lock으로 버전을 고정한다.** [공식 소스 + 추론]
- 공식 연동 문서에는 버전 번호가 없다. "최신 버전 유지", Python 3.9 이상이라고만 적혀 있다. 문서 메타에 `stale: true`가 붙어 있다. [공식]

### 1-2. 모델 × 기능 표

O = 공식 문서나 공식 테스트로 확인 · △ = 우회나 조건이 필요함 · X = 안 됨

| 기능 | HCX-005 | HCX-007 | HCX-DASH-002 | 근거 |
|---|---|---|---|---|
| `bind_tools`(도구 호출) | O | O (`thinking={"effort":"none"}` 필수) | O | FC 문서, 공식 통합 테스트 `test_invoke_bind_tools`(HCX-005) [공식] |
| `with_structured_output` 기본(json_schema) | X(SO 미지원) | **O** | X | 모델 문서(16a 표). 공식 테스트도 HCX-007만 쓴다. [공식] |
| `with_structured_output(method="function_calling")` | △ | △ | △ | 도구 하나를 강제 호출해서 JSON을 받는 우회다. 요청은 만들어진다(로컬 확인). 실제 품질은 미확인이다. [추론] |
| `json_mode` | X | X | X | 호환 API의 `response_format`은 `json_schema`만 유효하다. `json_object`는 쓸 수 없다. [공식] |
| 이미지 입력 | **O** | X | X | "이미지 입력은 HCX-005에서만" [공식] |
| 스트리밍(일반 답변) | O | O(thinking 스트림 포함) | O | 공식 테스트 `test_stream`, `test_stream_reasoning` [공식] |
| 스트리밍 + 도구 호출 | △ | △ | △ | 2025-04에 400 오류가 보고됐다. 우회는 `disable_streaming="tool_calling"`이다. 공식 LangGraph 테스트도 `disable_streaming=True`를 쓴다. [공식 이슈 #20·테스트] |
| 스트리밍 + 구조화 출력 | X | X(공식 테스트가 "known issue"로 skip) | X | `test_astream_structured_output` skip [공식] |
| thinking(추론) | X | O | X | [공식] |
| 튜닝 모델에서 도구 호출 | X | — | X | "튜닝 모델의 경우에는 지원하지 않습니다" [공식] |

### 1-3. 알려진 제약 (개발에 바로 걸리는 것)

| 제약 | 내용 | 대응 | 근거 |
|---|---|---|---|
| 도구 호출 ↔ 이미지 | "Function calling은 추론 또는 이미지 해석과 동시에 요청할 수 없습니다." | 이미지 호출과 도구 호출을 다른 노드로 나눈다 | FC 문서 [공식] |
| 도구 호출 ↔ thinking | HCX-007의 thinking 기본값이 `low`라서 도구를 넣으면 `400 Invalid parameter: tools, reasoning`이 난다 | `thinking={"effort":"none"}`을 꼭 넣는다. 이슈 #23(2025-10)은 아직 열려 있다 | 이슈 #23 [공식] |
| SO ↔ 도구 | SO와 도구 호출은 한 요청에 같이 못 쓴다 | 도구 선택과 JSON 만들기를 다른 호출로 나눈다 | 16a [공식] |
| 출력 토큰 기본값 | 호환 API의 `max_tokens` 기본값은 512다. 도구 호출은 1024 이상이어야 한다. ChatClovaX는 값을 넣지 않으면 아예 보내지 않는다(로컬 확인) | `max_tokens=1024`를 명시한다 | 호환 API·FC 문서 [공식] |
| `parallel_tool_calls` | `true`만 유효하다. 끌 수 없다. ChatClovaX는 이 필드를 보내지 않는다(로컬 확인) | 한 번에 여러 도구가 오면 우리 코드에서 순서를 정한다 | 호환 API 문서 [공식] |
| system 메시지 | 요청당 1개만 된다 | 요약·규칙을 system 하나에 합친다 | 18 §2-1 [공식] |
| 미지원 필드 | `stream_options`, `logprobs`, `store`, `user`, `metadata` 등 | 스트리밍 요청에 `stream_options`가 섞이지 않는 것을 확인했다(로컬 확인) | 호환 API 문서 [공식] |
| 이미지+도구 모델 출시 계획 | NAVER 담당자는 "차기 모델 출시 계획은 말씀드리기 어렵다"고 답했다(2025) | 대회 기간 안에는 기대하지 않는다 | 이슈 #21 댓글 [공식] |

---

## 2. LangGraph 오케스트레이터-서브에이전트 패턴

### 2-1. 관련 패키지 상태

| 패키지 | 버전(올린 날) | 공식 입장 | 우리 판단 |
|---|---|---|---|
| langgraph-supervisor | 0.0.31 (2025-11-19) | README: "이 라이브러리보다 **도구로 직접 만드는 supervisor 패턴**을 권한다" [공식] | 쓰지 않는다. ChatClovaX와의 호환 문제 보고도 있다(18, [2차]) |
| langgraph-swarm | 0.1.0 (2025-12-04) | 에이전트끼리 대화 주도권을 넘기는 handoff 방식 [공식] | 쓰지 않는다. 사장님과 대화하는 창구는 하나면 된다 [추론] |
| langgraph-bigtool | 0.0.3 (2025-06-03) | 임베딩으로 도구를 검색한다 [공식] | 1년 넘게 갱신이 없다. 우리 도구 수(20개 안팎)에는 과하다 [추론] |
| `create_react_agent` | langgraph 1.x | `@deprecated` 상태다. "`from langchain.agents import create_agent`로 바꾸라"고 안내한다(소스 확인) [공식] | 새 코드는 `create_agent`를 쓴다 |

### 2-2. 패턴 비교 (LangChain 공식 multi-agent 문서)

| 패턴 | 동작 | 단일 요청 모델 호출 수 | 우리에게 맞나 |
|---|---|---|---|
| **Subagents(도구로 감싸기)** | 메인 에이전트가 서브에이전트를 도구처럼 부른다. 모든 결과가 메인을 거친다 | 4 | **맞다.** 리뷰 요약·통계 보고서 워커가 이 모양이다 |
| Handoffs | 도구 호출로 상태를 바꿔서 활성 에이전트를 바꾼다 | 3 | 사장님과 대화하는 창구가 하나여서 필요 없다 |
| Router | 분류 단계가 입력을 전문 에이전트로 보낸다 | 3 | **맞다.** 기능 매칭(§4) 단계가 이 모양이다 |
| Skills | 에이전트 하나가 필요한 프롬프트를 그때그때 불러온다 | 3 | 불러온 내용이 대화에 쌓여 토큰이 커진다. HCX 한도에서는 불리하다 |
| Custom workflow | LangGraph로 정해진 흐름과 에이전트를 섞는다 | — | **전체 뼈대로 쓴다.** 위 패턴들을 노드로 넣는다 |

- 문서는 "도구가 너무 많아 잘못된 도구를 고를 때"를 멀티 에이전트를 쓸 첫 번째 이유로 든다. [공식]
- 서브에이전트는 결과가 메인을 한 번 더 거쳐서 호출이 1회 더 든다. 대신 컨텍스트 격리가 가장 강하다. [공식]

### 2-3. 컨텍스트 격리 방법

| 방법 | 어떻게 | 근거 |
|---|---|---|
| 질문만 넘기기 | 서브에이전트 도구가 `query: str`만 받는다. 부모 대화 전체는 넘기지 않는다(Deep Agents 용어로 `isolated`, 기본값) | Subagents 문서 [공식] |
| 마지막 답만 받기 | `res["messages"][-1].content`만 돌려준다. 중간 도구 결과는 메인 대화에 쌓이지 않는다 | Subagents 문서 [공식] |
| 필요한 값만 상태로 올리기 | `Command(update={...})`로 정한 키(예: `review_summary`)만 부모 상태에 쓴다. `ToolMessage`의 `tool_call_id`는 `InjectedToolCallId`로 받는다 | Subagents 문서 [공식] |
| 상태 스키마 분리 | 서브그래프 상태 스키마를 부모와 다르게 두면 내부 키가 부모에게 보이지 않는다 | Subgraphs 문서 [공식] |
| 서브그래프 기억 끄기 | 서브그래프 `checkpointer=None`(기본)이면 호출마다 새로 시작한다. `True`면 같은 스레드에서 기억이 쌓인다 | Subgraphs 문서 [공식] |
| 서브에이전트 출력 형식 지시 | "감독자는 너의 마지막 메시지만 본다"고 프롬프트에 적는다 | Subagents 문서 [공식] |

- 주의: 도구 함수 **안에서** 호출한 서브에이전트는 `get_state(subgraphs=True)`로 보이지 않는다. 디버깅용 로그는 우리가 따로 남긴다. [공식]

### 2-4. 도구가 많을 때 (HCX 도구 호출 정확도 대비)

HCX의 도구 호출 정확도는 공개 수치가 없다(18 §1). NAVER 블로그는 도구를 10~20개 이하로 두라고 권한다(18). 그래서 **한 번 호출할 때 붙는 도구 수**를 줄이는 것이 핵심이다.

| 방법 | 설명 | HCX에서 주의할 점 | 근거 |
|---|---|---|---|
| **2단계 라우팅(추천)** | ① DASH-002가 `select_feature(feature_id: enum)` 하나를 강제 호출(`tool_choice`)해 기능을 고른다 ② 그 기능의 도구 1~3개만 붙여 HCX-005가 실행한다 | DASH-002는 SO가 없어서 강제 도구 호출로 분류를 받는다 | FC 문서 `toolChoice` [공식] + [추론] |
| `wrap_model_call` + `request.override(tools=...)` | 상태(예: `state["feature"]`)를 보고 이번 호출에만 도구 목록을 바꾼다. 상태에는 저장되지 않는다 | 모델과 무관하게 동작한다. 2단계 라우팅을 이 방식으로 구현할 수 있다 | Context engineering 문서 [공식] |
| `LLMToolSelectorMiddleware` | 메인 모델 전에 LLM이 관련 도구를 고른다. `max_tools`, `always_include` 옵션이 있다 | 내부에서 `with_structured_output(schema)`를 기본(json_schema)으로 부른다(소스 확인). **선택 모델은 HCX-007(effort none)이어야** 한다. 005·DASH-002로는 미확인 | 미들웨어 문서·소스 [공식] + [추론] |
| 임베딩 도구 검색 | 도구 설명을 bge-m3로 임베딩해 상위 k개만 붙인다 | 도구가 수십 개를 넘을 때만 의미가 있다 | langgraph-bigtool [공식], NCP 사내 MCP 게이트웨이 사례(16a, [2차]) |
| 호출 수 제한 | `ToolCallLimitMiddleware`, `ModelCallLimitMiddleware`로 무한 루프를 막는다 | 60 QPM 한도를 보호한다 | 미들웨어 문서 [공식] |
| 승인 받기(HITL) | `HumanInTheLoopMiddleware` 또는 LangGraph `interrupt()`로 "저장" 같은 쓰기 도구 앞에서 멈춘다 | PRD A-1의 "사장님 승인 후 저장"과 맞는다 | 미들웨어 목록 [공식] |

---

## 3. 메뉴 사진 → 메뉴명·설명 추출

### 3-1. HCX-005 이미지 입력 제약 [공식]

| 항목 | 값 |
|---|---|
| 모델 | HCX-005만 된다 |
| 형식 | BMP, PNG, JPG, JPEG, WEBP |
| 크기 | 0바이트 초과 20MB 이하, 전체 요청 50MB 이하 |
| 해상도 | 긴 변 2240px 이하, 짧은 변 4px 이상, 가로세로 비율 1:5 ~ 5:1 |
| 장수 | 턴당 1장, 요청당 최대 5장 |
| 전달 | 공개 URL(`imageUrl.url`) 또는 Base64(`dataUri.data`). 여러 장이면 URL을 권한다 |
| 같이 못 쓰는 것 | 도구 호출, SO, thinking |
| 이미지 토큰 수 | 문서에 계산 방법이 없다(미확인). 토큰 계산 v3 API로 직접 잰다(sdk.md B-4) |
| 권장 | 이미지와 텍스트를 함께 보낸다 |

- ChatClovaX는 이미지를 OpenAI 형식(`{"type":"image_url","image_url":{"url": ...}}`)으로 보낸다. LangChain v1 표준 블록(`{"type":"image","base64":...}`)도 이 형식으로 바뀐다(로컬 확인).
- NAVER 공식 데모 `clova-tutor`는 이 OpenAI 형식에 **공개 URL**을 넣어 ChatClovaX(HCX-005)로 보낸다. [공식 GitHub]
- **Base64 data URI를 호환 API 경로로 보낼 수 있는지는 공식 문서에 없다(미확인).** 첫날 실제 호출로 확인한다. 안 되면 sdk.md의 네이티브 v3 REST(`dataUri`)로 이미지 노드만 바꾼다. [추론]
- HCX Vision은 "한국어·필기 OCR 기술을 바탕으로 문서 처리를 강화했다"고 소개됐다(2024-08). 메뉴판이나 OCR 정량 수치는 공개되지 않았다. [공식 블로그]

### 3-2. CLOVA OCR (General) 제약

| 항목 | 값 | 근거 |
|---|---|---|
| 언어 | `lang`은 **ko, ja, zh-TW**만 있다. 콘솔 지원 언어는 한·영·일이다 | General OCR API, 사용 준비 문서 [공식] |
| 형식·크기 | jpg, jpeg, png, pdf(10쪽), tif, tiff. 50MB 이하 | [공식] |
| 해상도 | 2026-04-23부터 한국 리전 API는 긴 변 8,000px 미만이다. 1960px 이하를 권한다 | 도메인 설정 문서 [공식] |
| 출력 | 글자(`inferText`), 신뢰도, 좌표, 줄바꿈. 표 추출을 켜면 표 셀 구조도 준다 | [공식] |
| 속도 | 서비스 계정당 **권장 최대 1 TPS** | 사용 준비 문서 [공식] |
| 요금 | General은 월 100건 무료다. 표 추출은 건당 25원이다. API Gateway 요금이 따로 붙는다 | 16b [공식 가격 데이터] |
| 준비 | 콘솔에서 도메인을 만들고 API Gateway를 연동해 Secret을 받아야 한다 | [공식] |

### 3-3. 비교와 추천

| 방식 | 장점 | 단점 | 판단 |
|---|---|---|---|
| **A. HCX-005 비전 단독(2단계)** | 호출 2번으로 끝난다. 사진 속 음식 모양까지 보고 설명을 쓸 수 있다. 준비할 서비스가 없다 | 도구·SO를 같이 못 써서 JSON을 바로 받을 수 없다. 가격을 잘못 읽을 위험이 있다(수치 미확인) | **추천** [추론] |
| B. CLOVA OCR → HCX 텍스트 | 글자·가격을 좌표와 함께 정확히 뽑는다(한국어 기준) | 키릴 문자(러시아·몽골)·베트남어 메뉴판은 지원 언어 밖이다(미확인). 도메인과 Gateway를 설정해야 한다. 1 TPS다 | 한국어 메뉴판만 있을 때의 대안 |
| C. OCR + HCX-005 같이 | 가격은 OCR, 설명은 비전이 맡아 서로 검증한다 | 호출이 3번이고 2주 안에 붙일 것이 늘어난다 | 시간이 남으면 |

**추천 흐름(A)** [추론]

```
사진 업로드 → (서버) 긴 변 2240px 이하 JPEG로 줄이기
→ [노드1] HCX-005 + 이미지, 도구 없음: "한 줄에 메뉴 하나: 원어명 | 한국어명 | 가격" 텍스트로 받기
→ [노드2] 텍스트만: HCX-007 SO(effort none) 또는 HCX-005 with_structured_output(method="function_calling")
          → MenuItem[] JSON (+ 가게 맥락으로 한국어 설명 생성)
→ [노드3] interrupt(): 오른쪽 더미 플레이스 화면에 미리 채우고 사장님 승인
→ 승인 시 save_menu 도구 실행
```

- 사진이 길거나 메뉴가 많으면 위아래로 잘라 1장씩 보낸다(턴당 1장, 비율 1:5 제한). [공식 제약 + 추론]
- NAVER 공식 데모도 이미지 작업에서는 도구 호출 대신 **JSON 출력 + 파싱 실패 시 재질문** 방식(Instructor)을 쓴다. [공식 GitHub, 18-clova-tutor-reference]

---

## 4. "사장님 요청 → 플레이스 기능 매칭" 방법

PRD A-0은 "전체 플레이스 기능 목록에서 맞는 기능을 찾아 설명하고 위치를 알려 준다"이다. 기능 목록은 수십 개 규모라 작다.

| 방법 | 어떻게 | 장점 | 단점 | 근거 |
|---|---|---|---|---|
| 규칙(키워드·버튼) | "리뷰", "메뉴", "손님 수" 같은 단어나 UI 버튼으로 바로 보낸다 | 공짜이고 빠르며 틀리지 않는다 | 모국어·오타·돌려 말하기에 약하다 | [추론] |
| 임베딩(bge-m3) | 기능마다 설명과 예시 발화를 임베딩해 두고 코사인 상위 k개를 찾는다 | bge-m3는 100개 넘는 언어를 지원해서 모국어 질문도 한국어 기능 설명과 맞출 수 있다. 1천 토큰당 0.2원이다 | 1등만 쓰면 비슷한 기능끼리 헷갈린다 | bge-m3 모델 카드 [공식], 16a 단가 |
| LLM 분류 | 후보 기능 ID를 enum으로 둔 `select_feature` 하나를 강제 호출한다 | 뜻을 이해한다. "없음"이나 "되묻기"도 고를 수 있다 | 호출 1번(0.3~1원)과 지연이 든다. HCX가 몽골어·베트남어를 잘 이해하는지는 미확인이다(공식 지원 언어는 한·영·일) | FC 문서 [공식], 16a 지원 언어 |
| CLOVA Studio 라우터 | 콘솔에서 도메인을 만들고 `/route` API로 분류한다. 응답은 `domain`, `blockedContent`, `safety`다 | 안전 필터가 붙어 있다 | 도메인 정의 방법·모델·요금이 문서에 없다. 문서가 stale이고 스킬셋 연동 중심이다 | 라우터 가이드·API [공식] |
| **혼합(추천)** | 규칙 → 임베딩 상위 5개 → DASH-002가 5개 + "없음" 중에서 고르기 | 대부분의 요청을 싸고 빠르게 처리하고, 애매한 것만 LLM이 본다 | 부품이 3개다 | 아래 사례 [2차] + [추론] |

- 사례(2차, 수치는 참고만): 임베딩 라우터는 "의도 목록이 고정되고 잘 정의됐을 때" 가장 잘 맞는다. LLM 분류는 정확도가 가장 높지만 비용도 가장 크다. "규칙 → 임베딩 → LLM 대체" 계층으로 비싼 호출을 크게 줄였다는 보고가 있다. 같은 데이터로 세 방식을 비교한 통제 실험은 찾지 못했다. [2차]
- LangChain 공식 Router 패턴도 "분류 단계 → 전문 에이전트"이다. 다만 매번 라우팅 호출이 1번 더 든다. [공식]

**추천 구성** [추론]

1. `features.json` 하나를 만든다. 칸은 `id`, 이름, 한 줄 설명, 스마트플레이스 메뉴 위치, 예시 발화 3~5개(한국어 + 영어), 연결 도구(`tools: []`), 대표 기능 여부.
2. 이 파일을 세 곳에 같이 쓴다. ① 임베딩 인덱스 ② `select_feature`의 enum ③ 평가 골드셋의 정답 라벨.
3. 대표 기능 3개(메뉴·리뷰·통계)로 가면 그 기능의 도구만 붙인다(§2-4). 나머지 기능은 "설명 + 위치 안내" 답변만 한다.
4. 모국어 입력은 bge-m3 교차 언어 검색에 먼저 맡긴다. 정확도가 낮으면 Papago 번역(NCP 서비스, 규칙 위반 아님)을 앞에 붙인다.

---

## 5. 에이전트 평가 (LangSmith 없이 pytest로)

지표와 골드셋 100문항 구성은 18 §5에 있다. 여기서는 **어떻게 돌리는지**만 정리한다.

| 단계 | 도구 | 하는 일 | 근거 |
|---|---|---|---|
| 단위 테스트(공짜) | `GenericFakeChatModel`을 상속하고 `bind_tools`가 자기 자신을 돌려주게 한다 | 미리 정한 `AIMessage(tool_calls=[...])`로 그래프 분기, 승인 멈춤, 도구 실행을 검증한다. 기본 클래스에는 `bind_tools`가 없다 | Unit testing 문서 [공식] |
| 궤적 비교 | `agentevals.create_trajectory_match_evaluator` | 모드는 `strict`, `unordered`, `subset`, `superset`이다. 인자 비교 모드는 `exact`, `ignore`, `subset`, `superset`이고 도구별 override와 함수 비교도 된다 | Evals 문서·소스 [공식] |
| LangSmith 필요? | **필요 없다** | `LANGSMITH_*` 환경변수 없이 `evaluator(outputs=..., reference_outputs=...)`가 `{"score": True/False}`를 돌려줬다(로컬 확인). `@pytest.mark.langsmith`, `--langsmith-output`만 LangSmith 기능이다 | Evals 문서 [공식] + 로컬 확인 |
| LLM 심사 | `create_trajectory_llm_as_judge(judge=ChatClovaX(...))` | `judge`에 LangChain 채팅 모델을 넣을 수 있다(시그니처 확인). 내부에서 구조화 출력을 쓰면 HCX-007이 필요하다(미확인) | agentevals 소스 [공식] + [추론] |
| 실제 호출 비용 줄이기 | `vcrpy` + `pytest-recording`, `--record-mode=once` | 첫 실행만 HCX를 부르고 이후에는 녹화(카세트)를 재생한다. `authorization` 헤더는 `XXXX`로 가린다. 프롬프트나 도구를 바꾸면 카세트를 지우고 다시 녹화한다 | Integration testing 문서 [공식] |
| 실행 분리 | `@pytest.mark.integration` + `addopts`로 기본 제외 | 평소 `pytest`는 공짜 테스트만 돌린다. `-m integration`일 때만 HCX를 부른다. 키가 없으면 `conftest.py`에서 skip한다 | Integration testing 문서 [공식] |

**pytest 골드셋 러너 구성** [추론]

- `tests/goldset/feature_match.jsonl`: `{utterance, lang, expected_feature, expected_tool|null}`. 기능 매칭 단계만 따로 잰다. 호출이 1번이라 싸다. 지표는 top-1과 top-3 정확도다.
- `tests/goldset/agent.jsonl`: 18 §5-2 형식이다. `pytest.mark.parametrize`로 한 줄을 테스트 하나로 만든다. 도구 결과는 고정 mock을 돌려준다.
- 판정: ① 첫 도구 이름 일치 ② 인자 허용값 일치 ③ 호출하지 않아야 할 때 안 했는지 ④ 궤적 `superset`.
- 결과는 `reports/eval-YYYYMMDD.csv`에 남긴다. 모델·프롬프트·도구 설명을 바꿀 때마다 같은 세트로 다시 돌려 비교한다(HCX-005 vs HCX-007 A/B 포함).
- PRD §10 기준("정확도보다 설계대로 동작")에 맞춰 발표에는 "기능 매칭 top-1 X%, 도구 선택 Y%"를 같이 보여 준다.

---

## 6. 비용·속도 제한 중 개발에 영향 주는 것

| 항목 | 값 | 개발 영향 | 근거 |
|---|---|---|---|
| 테스트 키 한도 | HCX-005·007 **60 QPM / 60k TPM**, DASH-002 90 / 80k, 임베딩 v2 60 QPM / 40k TPM(입력만), 리랭커 60 / 45k | 에이전트 한 턴이 2~4번 호출이므로 팀 전체로 분당 약 15~20턴이다 [추론] | 레이트리밋 문서(2025-07-17 기준) [공식] |
| TPM 세는 법 | **입력 토큰 + `maxTokens` 예약분** | `max_tokens=4096`이면 짧은 질문도 4K 이상을 먹는다. 도구 호출은 1024로 고정한다 | [공식] |
| 한도 단위 | 메인 계정 단위다. QPM이나 TPM 중 하나라도 넘으면 429가 난다 | 팀 4명이 한 계정을 쓰면 한도를 나눠 쓴다. 평가 배치를 돌릴 시간을 정한다 | [공식] |
| 남은 한도 확인 | `x-ratelimit-*` 헤더 6종(한도, 남은 양, 재설정까지 시간) | 429가 나면 재설정 시간만큼 기다린다 | [공식] |
| 서비스 앱 키 | HCX-005 300 QPM / 180k TPM 등 | 심사가 필요하고 기간은 미확인이다(16a). 발표 전에 받을 수 있을지 운영 측에 묻는다 | [공식] |
| 단가 | HCX-005·007 1천 토큰당 입력 1.25원 / 출력 5원, DASH-002 0.25 / 1원 | 대화 1회 약 5~6원. 골드셋 100문항 × 3회 반복은 약 2,000원, 약 30분이다(18 계산) | 16a [공식] |
| CLOVA OCR | 권장 1 TPS | OCR을 쓰면 평가를 순차로 돌린다 | [공식] |
| 응답 속도(지연) | **공식 수치가 없다(미확인)** | 첫 주에 모델별 p50/p95를 직접 잰다. 스트리밍은 일반 답변에만 켠다 | — |
| 대회 전용 크레딧·한도 상향 | **미확인**. 공개 공고에서 찾지 못했다 | PRD §12 "팀 계정 확인"과 같이 운영 측에 묻는다 | — |

**한도 안에서 개발하는 규칙** [추론, 18 §5-3과 같은 방향]

1. 모든 HCX 호출은 공통 래퍼 하나를 거친다. 래퍼가 맡는 일: `max_tokens` 명시, 429 백오프, 요청 해시 캐시, 토큰 사용량 로그.
2. 평소 개발과 단위 테스트는 가짜 모델로 한다. 실제 호출은 통합 테스트와 데모 리허설에만 쓴다.
3. 이미지 호출은 토큰 계산 API로 1장당 토큰을 먼저 잰다. 이미지 토큰 계산 방식이 문서에 없기 때문이다.

---

## PRD에 넣을 방향(확정 아님)

**추천 스택**
- LLM: HCX-005 메인(도구 호출·이미지). 기능 고르기는 HCX-DASH-002. JSON이 꼭 필요한 단계와 도구 선택기는 HCX-007(`effort none`). 오케스트레이터 모델은 골드셋으로 005와 007을 A/B 테스트해서 정한다.
- 호출: `langchain-naver==0.1.1` + `langchain`/`langgraph` 1.x. uv.lock으로 고정한다(비공개 API 의존 때문). 이미지 노드에서 Base64가 안 되면 그 노드만 sdk.md 네이티브 REST로 바꾼다.
- 에이전트: LangGraph 커스텀 워크플로가 뼈대다. 그 안에 `langchain.agents.create_agent`와 미들웨어(`wrap_model_call`, 호출 수 제한, HITL)를 쓴다. `langgraph-supervisor`, `create_react_agent`는 쓰지 않는다.
- 메뉴 사진: HCX-005 비전 2단계(읽기 → JSON). CLOVA OCR은 한국어 메뉴판일 때만 쓰는 선택지로 둔다.
- 평가: pytest + `agentevals`(궤적 매칭) + `pytest-recording`(녹화 재생). LangSmith는 쓰지 않는다.

**구조**
- `features.json`(기능 목록) 하나를 기능 매칭, 도구 연결, 골드셋 정답의 공통 원본으로 쓴다.
- 흐름: 입력 → 규칙/버튼 → bge-m3 상위 5개 → DASH-002 `select_feature`(enum + 없음) → 대표 기능이면 그 기능의 도구 1~3개만 붙여 HCX-005 실행 → 아니면 설명과 위치 안내.
- 리뷰 요약·통계 보고서는 서브에이전트 도구로 만든다. 입력은 질문 하나, 출력은 마지막 답과 필요한 상태 키만이다. 오케스트레이터 상태에는 요약과 슬롯만 둔다.
- 이미지·도구·SO·thinking은 한 요청에 하나만 쓴다. 그래서 노드를 기능 단위로 쪼갠다(이미지 읽기 → JSON 만들기 → 승인 → 저장).
- "저장" 같은 쓰기 도구 앞에는 `interrupt()` 승인 단계를 둔다(PRD A-1).

**리스크**
- langchain-naver가 1년 넘게 갱신되지 않았다. langchain-openai가 올라가면 깨질 수 있다. → 버전 고정, 첫날 실제 호출 스모크 테스트.
- HCX 도구 호출과 비전 OCR 정확도의 공개 수치가 없다. → 11/10~11에 기능 매칭 골드셋(30개라도)부터 돌려 모델과 구조를 정한다.
- 외국인 사장님 모국어(몽골어·베트남어·러시아어 등) 이해도가 미확인이다(HCX 공식 지원은 한·영·일). → bge-m3 교차 언어 검색, 필요하면 Papago 번역을 앞에 붙인다.
- 테스트 키 60 QPM / 60k TPM을 팀이 같이 쓴다. → 공통 래퍼(캐시·백오프·max_tokens 1024), 평가는 녹화 재생, 서비스 앱 키·대회 크레딧은 운영 측에 확인한다.
- Base64 이미지가 호환 API 경로에서 되는지 미확인이다. → 첫날 확인하고, 안 되면 네이티브 REST로 바꾼다.

---

## 출처

**NCP 공식 문서**
- CLOVA Studio Chat Completions v3(이미지 제약): https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3
- Function calling: https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-fc
- OpenAI 호환 API: https://api.ncloud-docs.com/docs/clovastudio-openaicompatibility
- LangChain 연동 가이드(2026-09-17 갱신, stale): https://guide.ncloud-docs.com/docs/clovastudio-dev-langchain
- 이용량 제어(QPM/TPM): https://guide.ncloud-docs.com/docs/clovastudio-ratelimiting
- 라우터 가이드·API: https://guide.ncloud-docs.com/docs/clovastudio-router , https://api.ncloud-docs.com/docs/clovastudio-router
- CLOVA OCR API 목록: https://api.ncloud-docs.com/docs/ai-application-service-ocr
- CLOVA OCR General API: https://api.ncloud-docs.com/docs/ai-application-service-ocr-ocr
- CLOVA OCR 사용 준비(1 TPS, 언어): https://guide.ncloud-docs.com/docs/clovaocr-spec
- CLOVA OCR General 도메인 설정(해상도·형식): https://guide.ncloud-docs.com/docs/clovaocr-general
- CLOVA OCR 상품·요금: https://www.ncloud.com/product/aiService/ocr

**NAVER 공식 GitHub·블로그**
- langchain-naver 저장소(소스 `libs/naver/langchain_naver/chat_models.py`, 통합 테스트): https://github.com/NaverCloudPlatform/langchain-naver
- 이슈 #20(스트리밍+도구 400): https://github.com/NaverCloudPlatform/langchain-naver/issues/20
- 이슈 #21(structured output 지원, 이미지+도구 모델 계획 답변): https://github.com/NaverCloudPlatform/langchain-naver/issues/21
- 이슈 #23(HCX-007 thinking 기본값): https://github.com/NaverCloudPlatform/langchain-naver/issues/23
- clova-tutor(공식 LangGraph 데모, 이미지 메시지 형식): https://github.com/NaverCloudPlatform/clova-tutor
- HyperCLOVA X Vision 소개(2024-08-19): https://clova.ai/en/tech-blog/en-introducing-hyperclova-x-vision

**PyPI(버전 확인, 2026-11-07)**
- https://pypi.org/project/langchain-naver/ · https://pypi.org/project/langgraph/ · https://pypi.org/project/langchain/ · https://pypi.org/project/langchain-core/ · https://pypi.org/project/langchain-openai/
- https://pypi.org/project/langgraph-supervisor/ · https://pypi.org/project/langgraph-swarm/ · https://pypi.org/project/langgraph-bigtool/ · https://pypi.org/project/agentevals/

**LangChain / LangGraph 공식**
- Multi-agent 개요(패턴 비교·호출 수): https://docs.langchain.com/oss/python/langchain/multi-agent
- Subagents: https://docs.langchain.com/oss/python/langchain/multi-agent/subagents
- Context engineering(동적 도구 선택): https://docs.langchain.com/oss/python/langchain/context-engineering
- 내장 미들웨어(LLMToolSelector 등): https://docs.langchain.com/oss/python/langchain/middleware/built-in
- LangGraph 서브그래프: https://docs.langchain.com/oss/python/langgraph/use-subgraphs
- 테스트 개요·단위·통합·평가: https://docs.langchain.com/oss/python/langchain/test , https://docs.langchain.com/oss/python/langchain/test/unit-testing , https://docs.langchain.com/oss/python/langchain/test/integration-testing , https://docs.langchain.com/oss/python/langchain/test/evals
- langgraph-supervisor README(도구 방식 권장): https://github.com/langchain-ai/langgraph-supervisor-py
- agentevals: https://github.com/langchain-ai/agentevals
- LangGraph 릴리스: https://github.com/langchain-ai/langgraph/releases

**모델 카드**
- BAAI bge-m3(다국어 100개 이상): https://huggingface.co/BAAI/bge-m3

**2차(참고만)**
- 의도 분류 라우터 비교 글: https://tianpan.co/blog/2026/04/16/intent-classification-agent-routers
- LLM 분류와 시맨틱 라우팅: https://alivedise.github.io/backend-engineering-essentials/ai-backend-patterns/llm-based-classification-and-semantic-routing
- 채팅 메시지 라우팅 비교 논문(SBSI): https://sol.sbc.org.br/index.php/sbsi/article/view/41348

**팀 내부 선행 조사**
- 16a CLOVA Studio 전수 조사, 16b AI 서비스(OCR 요금), 18 HCX 에이전트 실전, 18 clova-tutor 정리: `.scratch/ai-biseo-ideation/research/`
- `docs/sdk.md` 3장·4장·부록 B-2~B-6
