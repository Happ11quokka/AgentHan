# 04. 웹·인프라·통합 조사

> 조사일 2026-11-07 · 공통

> 범위: 3파트를 따로 만들고 합치는 방법, FastAPI·React 구조, MCP를 쓸지 말지, NCP Server 배포, 팀 Git 협업.
> 중복 피함: NCP 서비스 목록·크레딧·pgvector·Cloud Functions 한계·LB의 WebSocket 미확인은 [16c-infra-mcp.md](../../../.scratch/ai-biseo-ideation/research/16c-infra-mcp.md)와 [16-ncp-console-capabilities.md](../../../.scratch/ai-biseo-ideation/research/16-ncp-console-capabilities.md)를 본다. 여기서는 그 결론 위에 "어떻게 만들고 올리나"만 다룬다.
> 표기: [공식] 공식 문서·레지스트리에서 직접 확인 · [2차] 검색 요약·커뮤니티 글 · [추론] 근거를 바탕으로 한 판단 · **미확인** 확인 못 함.
> 버전은 2026-11-07에 PyPI·npm·GitHub API에서 직접 조회한 값이다.

## 요약

1. **합치기**: 레포에 이미 있는 `core/contracts.py`(Protocol) + `core/fakes.py` + `tests/test_contracts.py`(같은 테스트를 가짜·진짜 구현에 돌림)가 4명·2주에 맞는 최소 수준이다. 여기에 "브라우저로 나가는 이벤트"(`core/events.py`, Pydantic)와 "더미 플레이스 API"(`PlacePort`)만 더하면 된다.
2. **백엔드**: FastAPI 0.162.1. 파트별 `APIRouter`를 `app/main.py` 하나에 붙이고, 채팅은 내장 SSE(`fastapi.sse`, 0.135.0~), 전화는 `@router.websocket`, 빌드된 React는 `app.frontend("/", directory=...)`(0.138.0~)로 같은 서버에서 낸다.
3. **프론트**: React 19.3 + Vite 8.3. 채팅 UI 라이브러리는 HCX와 직접 붙지 않는다(우리 백엔드가 프로토콜을 맞춰야 함). 그래서 **직접 만든 작은 채팅 + `form.patch` 이벤트를 받아 오른쪽 폼에 반영하는 훅 하나**를 추천한다.
4. **MCP**: 메인 경로는 그냥 Python 함수 tool. FastMCP 래핑은 11/17 이후 여유가 있을 때 하는 반나절짜리 덤(발표에서 MCP Inspector로 "같은 함수가 MCP 서버로도 열린다"를 보여 주는 용도).
5. **배포**: Server(KVM g3) 1대 + Docker Compose(app + Caddy). HTTPS는 **Caddy + DuckDNS 무료 서브도메인**이 가장 쉽다(인증서 자동, WebSocket·SSE 자동 처리). ACG는 22(팀 IP만)·80·443. 키는 `.env`(git 제외) + `pydantic-settings`.

---

## 1. 3파트를 따로 만들고 합치는 방법

### 1-1. 패턴: 포트와 어댑터, 가짜 구현, 계약 테스트

| 개념 | 뜻(우리 말로) | 우리 레포에 대응 | 등급·출처 |
|---|---|---|---|
| 포트(port) | 파트 사이의 약속. "이 함수가 이런 입력에 이런 출력을 준다" | `core/contracts.py`의 `StoreKnowledge`, `OrderSink` | [공식] [Cockburn, Hexagonal Architecture](https://alistair.cockburn.us/hexagonal-architecture) — 핵심 로직을 DB·장치 없이 만들고 테스트, 바깥 연결은 바꿔 끼우는 어댑터 |
| 어댑터(adapter) | 약속을 실제로 지키는 구현. 가짜와 진짜 두 벌 | `core/fakes.py`(가짜), Part B의 pgvector 구현(진짜) | 위와 같음 |
| `typing.Protocol` | 상속 없이 "모양만 맞으면 통과"하는 타입 | 이미 사용 중 | [공식] [Python typing](https://docs.python.org/3.11/library/typing.html#typing.Protocol). `@runtime_checkable`은 메서드 **존재만** 보고 시그니처는 안 본다 |
| 계약 테스트 | 가짜와 진짜가 같은 결과를 내는지 같은 테스트로 확인 | `tests/test_contracts.py`의 `IMPLEMENTATIONS` 리스트 + `pytest` fixture params | [공식] [Fowler, ContractTest](https://martinfowler.com/bliki/ContractTest.html) — 진짜 서비스 대상 계약 테스트는 하루 1회 정도로 따로 돌린다 |
| Pydantic 판별 유니온 | `type` 글자 하나로 어떤 이벤트인지 고르는 모델 | 새로 만들 `core/events.py` | [공식] [Pydantic Unions](https://pydantic.dev/docs/validation/latest/concepts/unions/) (`Literal` 태그 + `Field(discriminator=...)`) |

### 1-2. 4명·2주에 맞는 최소 수준

| 할 것 | 할 필요 없는 것 | 이유 |
|---|---|---|
| Protocol 3~4개(지식, 주문, 플레이스, LLM) | 인터페이스마다 ABC·DI 컨테이너 | Protocol은 상속이 필요 없어 비전공자가 덜 헷갈린다 [추론] |
| 파트마다 가짜 구현 1개(`fakes.py`) | 가짜를 여러 시나리오로 확장 | 가짜는 "다른 파트가 막히지 않게" 하는 용도 [추론] |
| 계약 테스트: 포트당 3~6개 | Pact 같은 소비자 주도 계약 도구 | 같은 레포·같은 언어라 pytest 하나면 된다 [추론] |
| 진짜 구현 테스트는 키가 있을 때만(`skipif`) | CI | CI 없음. 각자 `push` 전에 한 줄 명령으로 돌린다(6장) [추론] |
| 브라우저로 나가는 이벤트는 Pydantic | 도메인 dataclass를 전부 Pydantic으로 갈아엎기 | FastAPI는 dataclass도 응답 모델로 받는다 [공식] [FastAPI Dataclasses](https://fastapi.tiangolo.com/advanced/dataclasses/). JSON으로 나가는 경계만 Pydantic이면 충분 [추론] |

### 1-3. 지금 레포 상태와 더할 것

| 이미 있음 | 더할 것(이름은 예시) |
|---|---|
| `StoreKnowledge`, `OrderSink`, `MenuItem`, `Order` (dataclass + Protocol) | `PlacePort`: 더미 스마트플레이스 API(메뉴 조회·초안 저장·등록) |
| `FakeStoreKnowledge`, `MemoryOrderSink` | `FakePlace`(메모리 dict) — 이게 사실상 "더미 플레이스 서버" |
| 계약 테스트(`IMPLEMENTATIONS` 패턴) | `core/events.py`: `TextDelta`, `ToolStatus`, `FormPatch`, `OrderUpdate`, `Done`, `Error` |
| — | `core/config.py`: `Settings`(pydantic-settings, `.env` 읽기) |

주의 [추론]: 지금 Protocol 메서드는 동기(`def`)다. SSE·WebSocket 핸들러는 `async`라서, 진짜 구현이 네트워크(CLOVA·DB)를 동기로 부르면 이벤트 루프가 멈춘다. 둘 중 하나로 정한다: (a) 계약을 `async def`로 바꾼다, (b) 호출하는 쪽에서 `starlette.concurrency.run_in_threadpool`로 감싼다. 첫 주 계약 확정 때 같이 정한다.

### 1-4. 합치는 일정(제안) [추론]

| 날짜 | 이벤트 | 완료 기준 |
|---|---|---|
| 11/10 | 계약 v1 고정 | `contracts.py`·`events.py` 머지, 이후 변경은 카톡 공지 |
| 11/12 | 가짜로 첫 통합 | 브라우저에서 채팅 → 가짜 에이전트 → 오른쪽 폼 값 1개 채워짐 |
| 11/14 | 첫 배포 | HTTPS URL에서 휴대폰 마이크 → WebSocket 연결 확인 |
| 11/17 | 진짜 구현 교체 | `IMPLEMENTATIONS`에 진짜 추가, 계약 테스트 통과 |
| 11/19 | 리허설 | 데모 시나리오 3회 연속 성공 |
| 11/20 | 코드 동결 | 이후 버그 수정만 |

---

## 2. FastAPI 구조 (fastapi 0.162.1, 2026-11-07 릴리스)

### 2-1. 기능별 정리

| 기능 | 쓰는 API | 들어온 버전 | 핵심 메모 | 등급·출처 |
|---|---|---|---|---|
| 파트별 라우터 | `APIRouter(prefix=..., tags=[...])` + `app.include_router(...)` | 오래됨 | prefix 끝에 `/` 금지. 모듈을 import해서 `assistant.router`처럼 쓰면 이름 충돌이 없다 | [공식] [Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/) |
| WebSocket | `@router.websocket("/ws")`, `accept()`, `receive_bytes/text`, `send_json`, `WebSocketDisconnect` | 오래됨 | APIRouter에도 `websocket()` 있음. 거절은 `WebSocketException(code=1008)` | [공식] [WebSockets](https://fastapi.tiangolo.com/advanced/websockets/), [APIRouter 레퍼런스](https://fastapi.tiangolo.com/reference/apirouter/) |
| WebSocket 테스트 | `TestClient(app).websocket_connect("/ws")` | 오래됨 | pytest에서 서버 없이 테스트 | [공식] [Testing WebSockets](https://fastapi.tiangolo.com/advanced/testing-websockets/) |
| SSE 스트리밍 | `from fastapi.sse import EventSourceResponse, ServerSentEvent`, `response_class=EventSourceResponse` + `yield` | **0.135.0** (2026-03-01) | POST도 된다. 15초마다 keep-alive ping, `Cache-Control: no-cache`, `X-Accel-Buffering: no` 자동. `event=`로 이벤트 이름, `data=`는 JSON 인코딩 | [공식] [Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/), [릴리스 노트](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/release-notes.md) |
| 프론트 서빙 | `app.frontend("/", directory="web/dist")` | **0.138.0** (2026-06-20), `check_dir="auto"` 0.141.0 | API 라우트가 항상 우선. 없는 경로는 `index.html`로 돌려줌(SPA). `fastapi dev`에서는 `dist`가 없어도 경고만 | [공식] [Frontend](https://fastapi.tiangolo.com/tutorial/frontend/), 릴리스 노트 |
| (예전 방식) 정적 파일 | `app.mount("/", StaticFiles(directory=..., html=True))` | 오래됨 | 마운트 앱은 OpenAPI에 안 잡힘. 새 코드는 `app.frontend()` 권장 | [공식] [Static Files](https://fastapi.tiangolo.com/tutorial/static-files/), [Starlette StaticFiles](https://starlette.dev/staticfiles/) |
| 설치 묶음 | `fastapi[standard]` | — | `uvicorn[standard]`(WebSocket용 `websockets` 포함), `pydantic-settings`, `httpx` 포함 | [공식] PyPI `requires_dist`, [Uvicorn 설치](https://uvicorn.dev/installation/) |

### 2-2. 라우트 배치(제안) [추론]

| 경로 | 주인 | 방식 | 내용 |
|---|---|---|---|
| `POST /api/assistant/chat` | Part A | SSE | 사장님 메시지 + 현재 폼 상태 → `text.delta`·`tool.status`·`form.patch`·`done` 이벤트 |
| `GET/PUT /api/place/menu…` | Part A(tools) | JSON | 더미 스마트플레이스 API. 오른쪽 화면이 이걸 읽고 쓴다 |
| `GET /api/knowledge/search` | Part B | JSON | 디버그·발표용(에이전트는 Python으로 직접 부름) |
| `WS /api/phone/ws` | Part C | WebSocket | 휴대폰 마이크 PCM ↔ STT·TTS, 주문 상태 |
| `GET /api/orders/stream` | Part C | SSE | 사장님 화면에 새 주문 알림(`order.update`) |
| `/` 그 외 | 프론트 | `app.frontend` | `web/dist` |

API를 모두 `/api` 아래에 두면 SPA 경로와 겹치지 않는다 [2차] [커뮤니티 예시 PR](https://github.com/iFallenDawn/sxcndhxnd/pull/19).

### 2-3. 왜 채팅은 SSE, 전화는 WebSocket인가 [추론]

| | SSE | WebSocket |
|---|---|---|
| 방향 | 서버 → 브라우저 한 방향 | 양방향 |
| 맞는 곳 | 채팅 토큰·폼 패치(요청 1번에 응답 여러 조각) | 전화(마이크 소리를 계속 올리고, 음성을 계속 받음) |
| 장점 | 일반 HTTP라 프록시·디버깅 쉬움, FastAPI가 ping 자동 | 오디오 바이너리 전송 |
| 주의 | 브라우저 `EventSource`는 GET만, 헤더·본문 못 보냄 → POST는 `fetch` + 스트림 읽기로 받는다 [공식] [MDN EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource/EventSource) | 끊김 대비 ping, 재접속 처리 |

---

## 3. 프론트엔드

### 3-1. 버전 [공식, npm 조회]

| 패키지 | 최신 | 메모 |
|---|---|---|
| `react` | 19.3.1 | |
| `vite` | 8.3.6 | Node `^20.19.0 \|\| >=22.12.0` 필요 |
| `@vitejs/plugin-react` | 6.2.1 | |
| `create-vite` | 9.3.4 | `npm create vite@latest web -- --template react-ts` [공식] [Vite Guide](https://vite.dev/guide/) |
| `zustand` | 5.1.0 | 폼 상태 저장소(선택) |
| `tailwindcss` | 4.3.5 | 선택 |
| `typescript` | 7.0.2 | |

### 3-2. 채팅 UI 라이브러리가 HCX 백엔드와 붙나

| 라이브러리 | 버전 | 비OpenAI 백엔드 연결 방법 | 폼 패치 같은 커스텀 이벤트 | 판단 |
|---|---|---|---|---|
| Vercel AI SDK UI (`ai`, `@ai-sdk/react` `useChat`) | 7.0.164 / 4.0.168 | 백엔드가 **UI Message Stream 프로토콜**(SSE, 헤더 `x-vercel-ai-ui-message-stream: v1`, `text-start/delta/end`, `tool-*`, `finish`, `data: [DONE]`)을 내면 된다. Python 백엔드도 가능하다고 공식 문서가 명시 | `data-*` 파트(`data-form-patch`)를 보내고 `useChat`의 `onData`에서 받는다. `transient: true`면 대화 기록에 안 남음 | 가능. 단 프로토콜 규칙(블록 id, start/finish)을 정확히 맞춰야 하고 메이저 버전이 자주 바뀐다 |
| assistant-ui (`@assistant-ui/react`) | 0.15.32 | (a) `useLocalRuntime` + `ChatModelAdapter`의 `async *run`에서 우리 API를 `fetch`, (b) `@assistant-ui/react-data-stream`(0.12.40)의 `useDataStreamRuntime`은 위 AI SDK 프로토콜을 요구 | tool UI·Human-in-the-loop 지원. 일부 API가 `unstable_` 접두어 | 가능. 예쁜 기본 UI가 장점, 개념(runtime·adapter·toolkit)이 많다 |
| CopilotKit (`@copilotkit/react-core`) | 1.85.0 | AG-UI 프로토콜. 문서상 구성은 React ↔ **CopilotKit Runtime(Node)** ↔ Python 에이전트 | 공유 상태(`useCoAgent`/`useAgent`)가 바로 우리가 원하는 그림 | 구조가 무겁다(Node 런타임 추가). 2주엔 비추천 |
| 직접 구현 | — | `fetch` + `ReadableStream`으로 SSE 줄 파싱(30줄 안팎) | 우리 이벤트 그대로 `switch(type)` | **추천** |

출처: [AI SDK Stream Protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) [공식], [AI SDK Streaming Data](https://ai-sdk.dev/docs/ai-sdk-ui/streaming-data) [공식], [assistant-ui LocalRuntime](https://www.assistant-ui.com/docs/runtimes/custom/local-runtime) [공식], [assistant-ui Data Stream](https://www.assistant-ui.com/docs/runtimes/custom/data-stream) [공식], [AG-UI State](https://docs.ag-ui.com/concepts/state) [공식], CopilotKit 런타임 구성은 [2차] 검색 요약([useCoAgent](https://docs.copilotkit.ai/reference/v1/hooks/useCoAgent)). `@microsoft/fetch-event-source`는 마지막 릴리스가 2.0.1(2021-04)이라 새로 의존하지 않는다 [공식, npm 조회].

판단 근거 [추론]: 우리 화면의 핵심은 "말풍선"보다 "오른쪽 폼이 채워지는 모습"이다. 라이브러리를 쓰면 말풍선은 쉬워지지만 폼 패치는 어차피 직접 만들어야 한다. 이벤트 종류가 5~6개뿐이라 직접 만든 훅이 비전공자에게 더 읽기 쉽다. 프론트 경험자가 있고 시간이 남으면 그때 AI SDK `useChat` + `data-form-patch`로 바꿔도 이벤트 의미는 그대로 옮겨진다.

### 3-3. "에이전트가 오른쪽 폼을 채우는" 공유 상태 패턴

참고 모델: AG-UI는 상태를 `STATE_SNAPSHOT`(전체 교체)과 `STATE_DELTA`(JSON Patch RFC 6902 배열)로 보낸다 [공식] [AG-UI State](https://docs.ag-ui.com/concepts/state). 우리는 이걸 더 단순하게 줄인다 [추론].

| 단계 | 누가 | 무엇을 |
|---|---|---|
| 1 | 사장님 | "우즈벡 쌀국수 9,000원으로 메뉴 추가해 줘" 입력. 프론트가 메시지와 **현재 폼 값**을 함께 `POST /api/assistant/chat` |
| 2 | 에이전트(Part A) | HCX가 `fill_menu_form(name, price, description, ...)` tool 호출 |
| 3 | tool 함수 | 값 검증(Pydantic) → `FakePlace`에 초안 저장 → SSE로 `form.patch` 이벤트 발행 |
| 4 | 프론트 | `form.patch`를 받으면 폼 상태에 값을 덮어쓰고, 바뀐 칸을 잠깐 강조(채워지는 연출) |
| 5 | 사장님 | 오른쪽 폼에서 직접 고칠 수 있다. 다음 메시지 때 고친 값이 다시 1번처럼 올라간다 |
| 6 | 사장님 또는 에이전트 | "등록" → `PUT /api/place/menu/{id}` 또는 `submit_menu_form` tool. 등록 전 확인 버튼은 사람 몫 |

이벤트 예시 이름(제안):

| `type` | 필드 | 쓰임 |
|---|---|---|
| `text.delta` | `text` | 말풍선 글자 스트리밍 |
| `tool.status` | `name`, `state`(`running`/`done`/`error`), `label` | "메뉴 화면 여는 중…" 같은 상태 표시 |
| `form.patch` | `form`(`"menu"` 등), `values`(바뀐 칸만), `draft_id` | 오른쪽 폼 채우기 |
| `panel.open` | `screen`(`"place.menu"` 등) | 오른쪽에 어떤 더미 화면을 열지 |
| `order.update` | `order` | 전화 주문이 사장님 화면에 뜰 때 |
| `done` / `error` | `message` | 끝·실패 |

`values`를 JSON Patch 대신 "바뀐 칸만 담은 dict"로 두면, 프론트는 `setForm({...form, ...values})` 한 줄로 끝난다 [추론].

### 3-4. 비전공자도 손댈 수 있는 프론트 구조(제안) [추론]

| 파일 | 하는 일 | 손대는 사람 |
|---|---|---|
| `web/src/api/stream.ts` | SSE 줄 파싱, 이벤트를 콜백으로 넘김 | 프론트 담당만 |
| `web/src/state/useAgent.ts` | 메시지 목록·폼 상태·이벤트 `switch` | 프론트 담당 |
| `web/src/components/Chat.tsx` | 왼쪽 대화창 | 누구나(문구·스타일) |
| `web/src/screens/place/MenuForm.tsx` | 오른쪽 더미 스마트플레이스 메뉴 화면 | 누구나 |
| `web/src/screens/phone/PhonePage.tsx` | 휴대폰 전화 데모(마이크 → WebSocket) | Part C |

개발 중에는 Vite `server.proxy`로 `/api`를 FastAPI(8000)에 넘긴다. WebSocket은 `ws: true` [공식] [Vite server options](https://vite.dev/config/server-options). 프론트를 안 만지는 사람은 `web` 빌드 한 번 후 FastAPI만 켜고 `localhost:8000`을 열면 된다(`app.frontend`가 `dist`를 낸다).

---

## 4. "MCP처럼 권한 있는 접근"을 어떻게 보여 줄까

### 4-1. 확인한 사실

| 항목 | 내용 | 등급·출처 |
|---|---|---|
| 버전 | `fastmcp` 4.1.6(2026-11-03), 공식 `mcp` SDK 2.3.0, `langchain-mcp-adapters` 0.3.2, `langchain-naver` 0.1.1 | [공식] PyPI 조회 |
| FastMCP 최소 코드 | `mcp = FastMCP("Demo")`, `@mcp.tool` 함수, `mcp.run()` | [공식] [FastMCP Welcome](https://gofastmcp.com/getting-started/welcome) |
| FastAPI에 붙이기 | `mcp_app = mcp.http_app(path="/")` → `FastAPI(lifespan=mcp_app.lifespan)` → `app.mount("/mcp", mcp_app)`. 기존 lifespan이 있으면 `combine_lifespans` | [공식] [FastMCP FastAPI](https://gofastmcp.com/integrations/fastapi) |
| FastAPI 앱 자동 변환 | `FastMCP.from_fastapi(app)` 가능. 단 "잘 다듬은 MCP 서버가 자동 변환보다 LLM 성능이 훨씬 좋다"는 공식 팁 | [공식] [FastMCP OpenAPI](https://gofastmcp.com/integrations/openapi) |
| 테스트 | `async with Client(mcp) as c: await c.list_tools()` — 서버 없이 메모리에서 | [공식] [FastMCP FastAPI](https://gofastmcp.com/integrations/fastapi) |
| 전송 방식 | 표준은 stdio와 Streamable HTTP. HTTP는 `Origin` 검증·인증 권장 | [공식] [MCP Transports 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) |
| HCX와 MCP | CLOVA Studio는 MCP 클라이언트가 아니다. 우리 코드가 MCP 도구를 HCX `tools`로 바꿔 넘겨야 함 | 기존 조사 16c·16 |
| NCP 포럼 쿡북 | 4부작(1부 LangChain+네이버 검색 MCP+HCX-005, 2부 세션·컨텍스트, 3부 네이버 로그인 OAuth, 4부 MCP Inspector 등 운영 팁). 예제는 FastMCP 서버, 기본 주소 `http://127.0.0.1:8000/mcp/` | [2차] 검색 요약([1부](https://www.ncloud-forums.com/topic/574/), [2부](https://www.ncloud-forums.com/topic/580/), [3부](https://www.ncloud-forums.com/topic/581/), [4부](https://www.ncloud-forums.com/topic/582/)). 본문은 이번에도 포럼 도메인 DNS 실패로 **미확인** |

### 4-2. 선택지 비교 [추론]

| | A. Python 함수 tool만 | B. 함수 tool + FastMCP로도 노출(덤) | C. 에이전트가 MCP로만 도구 호출 |
|---|---|---|---|
| 구조 | 에이전트 → `PlacePort` → `FakePlace` | A 그대로 + 같은 함수를 `/mcp`에 등록 | 에이전트 → MCP 클라이언트 → `/mcp` → 함수 |
| 추가 작업 | 없음 | 반나절(등록 코드 + lifespan 합치기 + Inspector 시연 준비) | 1~2일(`langchain-mcp-adapters` 연결, 세션, 디버깅) |
| 실패 위험 | 낮음 | 낮음(메인 경로와 분리) | 중간(한 홉 더, 버전 변화 큼: fastmcp 4.x) |
| 발표 설득력 | 말로만 "실서비스는 MCP/API" | MCP Inspector나 Claude 데스크톱에서 `update_menu` 도구 목록이 보이는 화면 한 장 | 가장 "진짜 같음"이지만 관객 눈에는 B와 같아 보임 |
| 판단 | 기본 | **11/17 이후 여유 있으면** | 비추천 |

권장: **A로 개발**하고 tool 함수를 `PlacePort` 뒤에 둔다. 발표 문구는 "지금은 더미 API, 실서비스는 사업자 인증 후 공식 API 또는 MCP 서버로 같은 함수 계약을 바꿔 끼운다". 시간이 남으면 B를 붙여 "같은 함수가 MCP 표준으로도 열린다"를 30초 보여 준다. 권한 연출은 tool마다 `scope`(`menu:read`, `menu:write`)와 "등록 전 사장님 확인" 단계를 화면에 보이게 하는 편이 MCP 자체보다 효과가 크다.

---

## 5. NCP Server 배포 최소 경로

### 5-1. 서버 사양

| 스펙 코드 | vCPU / 메모리 / 기본 디스크 | 비고 | 등급·출처 |
|---|---|---|---|
| `mi1-g3` (Micro, KVM) | 1 / 1GB / 10GB | 한국 거주자 1인 1대, 결제정보 등록 시점부터 1년 서버비 무료. 10GB 초과 디스크·공인 IP·네트워크는 유료. 리소스 상황에 따라 생성 제한 가능, 성능 보장 없음 | [공식] [ncloud SSD Server 상품 페이지](https://www.ncloud.com/product/compute/ssdServer) |
| `micro-g1` (XEN) | 1 / 1GB / 50GB | 구세대 | [공식] [ncloud Server 상품 페이지](https://www.ncloud.com/product/compute/server) |
| `c2-g3` (High CPU) | 2 / 4GB | g3 최소 유료 사양 | [공식] SSD Server 페이지 |
| `s2-g3` (Standard) | 2 / 8GB | | [공식] SSD Server 페이지, [서버 사양(VPC)](https://guide.ncloud-docs.com/docs/server-spec-vpc) |
| Compact | g3 표에 없음(g1에만 Compact-g1) | | [공식] 상품 페이지 |
| 시간·월 요금 | 페이지가 스크립트로 그려 값 추출 실패 | **미확인** → 콘솔 요금 계산기 | — |
| 대회 전용 크레딧 | 공개 자료에 없음 | **미확인**(16c 결론 유지) | — |

판단 [추론]: 앱(FastAPI+에이전트) + Caddy + Docker를 1GB에 올리면 이미지 빌드 중 메모리가 모자랄 수 있다. **`c2-g3`(2vCPU/4GB) 추천**, 크레딧이 빠듯하면 Micro로 시작하고 프론트 빌드는 노트북에서 한 뒤 `dist`만 올린다. 벡터 DB를 Cloud DB for PostgreSQL로 따로 쓰면 앱 서버는 작아도 된다.

### 5-2. Docker vs uv 직접 실행

| | Docker Compose (app + caddy) | uv 직접 + systemd + Caddy(apt) |
|---|---|---|
| 처음 설정 | Docker 설치 1회, `docker compose up -d --build` | uv·Caddy 설치, systemd 유닛 작성 |
| 재현성 | 높음(노트북에서 똑같이 띄워 볼 수 있음) | 서버 상태에 의존 |
| 비전공자 | 명령 하나 외우면 됨 | 리눅스 서비스 개념 필요 |
| 메모리 | 조금 더 씀 | 가벼움(Micro에 유리) |
| 판단 | **추천**(기존 16c 결론과 같음) | Micro에서 버틸 때 대안 |

uv 공식 Docker 패턴: `COPY --from=ghcr.io/astral-sh/uv:<버전> /uv /uvx /bin/`, `UV_COMPILE_BYTECODE=1`, 캐시 마운트 + `uv sync --locked`(락 파일이 안 맞으면 실패), `.venv`는 `.dockerignore` [공식] [uv Docker 가이드](https://docs.astral.sh/uv/guides/integration/docker/). 우리 프로젝트는 Python 3.11 고정(`requires-python >=3.11,<3.12`)이라 베이스 이미지를 `python:3.11-slim`으로 맞춘다 [추론]. 프론트는 멀티스테이지(Node 단계에서 `npm ci && npm run build` → `dist`를 Python 이미지로 복사) [추론].

### 5-3. HTTPS 얻기 (휴대폰 마이크에 필수)

`getUserMedia()`는 보안 컨텍스트(HTTPS 또는 localhost)에서만 된다. HTTP에서는 `navigator.mediaDevices`가 `undefined` [공식] [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia). 그래서 휴대폰으로 노트북 IP(HTTP)에 붙으면 마이크가 안 된다. **배포 URL을 일찍 만들어야 하는 이유**다.

| 방법 | 준비물 | 장점 | 단점·위험 | 판단 |
|---|---|---|---|---|
| **Caddy + DuckDNS 서브도메인** (`agenthan.duckdns.org`) | DuckDNS 가입, A 레코드를 공인 IP로 | Caddy가 공개 도메인이면 Let's Encrypt 인증서를 자동 발급·갱신. `reverse_proxy`가 WebSocket을 자동 처리, `text/event-stream`은 즉시 flush. `duckdns.org`는 Public Suffix List에 있어 인증서 발급 한도를 남과 덜 나눈다 | DuckDNS 서비스 가용성에 의존 [2차] | **1순위** |
| Caddy + `<공인IP>.nip.io` / `sslip.io` | 없음(가입도 불필요) | 바로 된다 | nip.io·sslip.io는 Public Suffix List에 없어 Let's Encrypt 한도를 전 세계 사용자와 나눈다. nip.io 안내문도 한도에 걸릴 수 있다고 적음 | 2순위(예비) |
| Caddy + Let's Encrypt **IP 주소 인증서** | 없음 | 도메인 자체가 필요 없음. 2026-01-15 정식 제공, 수명 약 6일(shortlived 프로필 필수), http-01/tls-alpn-01만 | Caddy는 IP 주소를 기본으로 내부 CA에 맡긴다 → `tls { issuer acme { profile shortlived } }` 같은 명시 설정 필요. Caddy에서 IP 인증서 발급이 되는지 **미확인**(사전 테스트 필요) | 3순위 |
| 도메인 구매 + Caddy | 도메인 비용 | 가장 안정적, 발표 URL이 깔끔 | 결제·DNS 반영 시간 | 여유 있으면 |
| NCP ALB + Certificate Manager | 자기 도메인(Cloud Basic 무료 DV는 **DNS 검증**), LB 비용 | NCP 상품만으로 구성 | LB 비용 추가, ALB의 WebSocket·idle timeout은 문서 **미확인**(16c), 2026-02-12부터 인증서 최대 199일 | 비추천 |

출처: [Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https) [공식], [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) [공식], Caddy 최신 v2.11.9(2026-11-05) [공식, GitHub API], [Let's Encrypt 6일·IP 인증서 GA](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability) [공식], [Caddy shortlived 설정](https://caddy.community/t/shortlived-certificates-from-lets-encrypt/33399) [2차], [nip.io](https://nip.io/) [공식], [Public Suffix List](https://publicsuffix.org/list/public_suffix_list.dat) [공식, 직접 grep: `duckdns.org` 있음, `nip.io`·`sslip.io` 없음], [NCP Certificate Manager 인증서](https://guide.ncloud-docs.com/docs/en/certificatemanager-cmissuedcertificate-feature) [2차 검색 요약].

### 5-4. ACG(방화벽) 포트

사용자가 만든 ACG에는 기본 규칙이 없어 직접 넣어야 하고, Outbound 규칙이 없으면 서버가 밖으로 보내는 요청이 막힐 수 있다 [공식] [ACG(VPC) 가이드](https://guide.ncloud-docs.com/docs/server-acg-vpc).

| 방향 | 프로토콜/포트 | 소스·대상 | 이유 |
|---|---|---|---|
| Inbound | TCP 22 | 팀원 IP만(/32) | SSH. 0.0.0.0/0 금지 [추론] |
| Inbound | TCP 80 | 0.0.0.0/0 | Let's Encrypt http-01 검증, HTTPS로 리다이렉트 |
| Inbound | TCP 443 | 0.0.0.0/0 | 서비스(HTTPS·WSS) |
| Inbound | TCP 8000 | **열지 않음** | 앱은 Caddy 뒤에서만 [추론] |
| Outbound | 전체 | 0.0.0.0/0 | CLOVA Studio·Speech·Papago API 호출, 패키지 설치 |

Micro 서버도 공인 IP는 유료다 [공식] 상품 페이지.

### 5-5. 비밀키 관리

| 할 것 | 방법 | 등급·출처 |
|---|---|---|
| 키는 `.env`에만 | `.gitignore`에 `.env`. 키 이름만 적은 `.env.example`을 커밋 | [추론] |
| 코드에서 읽기 | `core/config.py`의 `Settings(BaseSettings)` 하나로 모아 읽기(`pydantic-settings` 2.16.2, `fastapi[standard]`에 포함) | [공식] PyPI |
| 로컬 실행 | `uv run --env-file .env ...`(`UV_ENV_FILE`로도 지정) | [공식] [uv CLI 레퍼런스](https://docs.astral.sh/uv/reference/cli/) |
| 서버 | `scp`로 `.env` 복사, `chmod 600`, compose의 `env_file:` | [추론] |
| 프론트에 키 금지 | Vite의 `VITE_` 변수는 번들에 그대로 노출된다. API 키를 넣지 말 것 | [공식] [Vite Env](https://vite.dev/guide/env-and-mode) |
| 계정 | 팀원별 Sub Account, 대회 후 키 재발급·서버 반납 | 기존 16c·16 |
| 더 단단히 | NCP Secret Manager(선택) | 기존 16c |

---

## 6. 비전공자 포함 팀의 Git 협업

### 6-1. main 직접 푸시에서 충돌 줄이기

| 팁 | 방법 | 등급·출처 |
|---|---|---|
| 폴더 소유 | 파트별 폴더는 주인만 고친다(아래 표). 남의 폴더는 카톡으로 요청 | [추론] |
| 공유 파일은 한 사람 | `core/`, `app/main.py`, `pyproject.toml`, `uv.lock`, `web/package.json`·`package-lock.json`은 통합 담당 1명이 고치거나, 고치기 전 공지 | [추론] |
| pull은 rebase로 | `git config --global pull.rebase true` — pull 때 merge 커밋 대신 내 커밋을 위로 다시 쌓는다 | [공식] [git-config](https://git-scm.com/docs/git-config) |
| 고친 채로 pull | `git config --global rebase.autoStash true` — rebase 전 자동 stash, 끝나고 다시 적용(충돌 가능성은 남음) | [공식] git-config |
| 작게 자주 | 30분~1시간 단위 커밋, 시작 전·푸시 전 `git pull` | [추론] |
| 포맷 충돌 없애기 | 커밋 전 `ruff format` (이미 dev 의존성에 ruff 있음) | [추론] |
| 락 파일 충돌 | `uv.lock` 충돌은 손으로 고치지 말고 한쪽을 받은 뒤 `uv lock` 다시 실행. `package-lock.json`도 `npm install`로 다시 생성 | [추론] |
| CODEOWNERS | PR에서 리뷰어 자동 지정 기능이라 **직접 푸시엔 효과 없음** | [공식] [GitHub CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) |

### 6-2. 폴더 소유(제안) [추론]

| 폴더 | 주인 |
|---|---|
| `src/agenthan/agent/`, `src/agenthan/tools/` | Part A |
| `src/agenthan/rag/` | Part B |
| `src/agenthan/voice/` | Part C |
| `src/agenthan/core/`, `src/agenthan/app/`, `deploy/` | 통합 담당 |
| `web/src/screens/place/` | Part A + 디자인 |
| `web/src/screens/phone/` | Part C |
| `web/src/api/`, `web/src/state/` | 프론트 담당 |

### 6-3. 설치·실행을 한 줄로

| 도구 | 버전 | 설치 | 장점 | 단점 | 등급·출처 |
|---|---|---|---|---|---|
| uv 기본 | 0.13.7 | 설치 스크립트 1회 | `uv run`이 실행 전에 환경을 자동으로 맞춘다 | **uv에는 아직 작업 실행기(task runner) 기능이 없다** — 관련 이슈 #5903 열림 | [공식] [uv run](https://docs.astral.sh/uv/concepts/projects/run/), [이슈 #5903](https://github.com/astral-sh/uv/issues/5903) |
| poethepoet | 0.49.0 | `uv add --dev poethepoet` → `uv sync`에 포함 | 작업을 `pyproject.toml`의 `[tool.poe.tasks]`에 둠. 추가 설치 없음 | 셸 문법 작업은 posix 전용 표시 | [공식] [poethepoet](https://poethepoet.natn.io/index.html) |
| just | `rust-just` 1.59.0 | `uv tool install rust-just` | 문법 단순, 여러 OS | 별도 설치·파일 하나 더 | [공식] PyPI 조회 |
| Makefile | — | macOS·리눅스 기본 | 익숙함 | Windows는 make 설치 필요, 탭 문법 | [추론] |

추천 [추론]: **poethepoet**. 팀원은 `uv sync` 한 번이면 작업 명령까지 깔린다. 예시 작업 이름: `uv run poe api`(백엔드 개발 서버), `uv run poe web`(Vite), `uv run poe check`(ruff format + ruff check + pytest), `uv run poe build`(프론트 빌드). 팀에 Windows 사용자가 없고 모두 make에 익숙하면 Makefile도 된다.

---

## PRD에 넣을 방향(확정 아님)

### 추천 구조

```
휴대폰 브라우저 ──WSS──┐
사장님 노트북 ──HTTPS──┤
                       ▼
        [NCP Server c2-g3 · Docker Compose]
        Caddy(443, 인증서 자동) ──▶ FastAPI app(8000, 내부)
                                     ├─ /api/assistant/chat   (SSE)  Part A
                                     ├─ /api/place/*          (JSON) 더미 스마트플레이스
                                     ├─ /api/knowledge/*      (JSON) Part B
                                     ├─ /api/phone/ws         (WS)   Part C
                                     ├─ /api/orders/stream    (SSE)  사장님 화면 알림
                                     ├─ (선택) /mcp           FastMCP 래핑
                                     └─ /                     app.frontend(web/dist)
                                     ▼
                     CLOVA Studio · Speech · Voice · Papago / (선택) Cloud DB for PostgreSQL
```

### 폴더

```
AgentHan/
  src/agenthan/
    core/
      contracts.py   # 지금 있음: StoreKnowledge, OrderSink, MenuItem, Order … + PlacePort 추가
      events.py      # 새로: UIEvent 판별 유니온 (Pydantic)
      config.py      # 새로: Settings (pydantic-settings)
      fakes.py       # 지금 있음 + FakePlace 추가
    agent/  router.py, graph.py          # Part A
    tools/  place.py                     # Part A: PlacePort 쓰는 tool 함수
    rag/    router.py, pgvector_store.py # Part B
    voice/  router.py, session.py        # Part C
    app/    main.py                      # 라우터 조립 + 가짜/진짜 선택 + app.frontend
  web/                                   # React + Vite
    src/api/stream.ts  src/state/useAgent.ts
    src/components/Chat.tsx
    src/screens/place/MenuForm.tsx  src/screens/phone/PhonePage.tsx
  tests/  test_contracts.py (지금 있음), test_events.py, test_ws_phone.py
  deploy/ Dockerfile, compose.yaml, Caddyfile
  .env.example
```

### 계약 예시 이름

| 이름 | 종류 | 주인 → 사용자 |
|---|---|---|
| `StoreKnowledge.search_menu / get_menu / list_menu / recent_reviews` | Protocol(있음) | B → A, C |
| `OrderSink.submit(order)` | Protocol(있음) | 사장님 화면 → C |
| `PlacePort.get_menu() / save_draft(item) / publish(draft_id)` | Protocol(새로) | A(tools) → A(agent), web |
| `UIEvent = TextDelta \| ToolStatus \| FormPatch \| PanelOpen \| OrderUpdate \| Done \| Error` | Pydantic, `type` 판별 | 백엔드 → web |
| `FormPatch(form="menu", values={...}, draft_id=...)` | Pydantic | A → web |
| `PhoneClientMsg / PhoneServerMsg` | Pydantic(WS 제어 메시지), 오디오는 바이너리 프레임 | C ↔ web |
| `Settings(clova_api_key, clova_speech_secret, ...)` | pydantic-settings | 전체 |

### 결정할 것

1. Protocol을 `async def`로 바꿀지(추천: 네트워크를 타는 것만 async), 아니면 호출부에서 `run_in_threadpool`.
2. 채팅 UI: 직접 구현(추천) vs AI SDK `useChat`.
3. 서버 사양: `c2-g3`(추천) vs Micro.
4. HTTPS: DuckDNS(추천) vs 도메인 구매.
5. MCP 래핑: 11/17에 Part A 진행 상황 보고 결정.

### 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| 휴대폰 마이크가 HTTP에서 안 됨 | 전화 데모 불가 | 11/14까지 HTTPS 배포, 휴대폰 2종(iOS·Android)으로 확인 |
| 동기 계약 + async 핸들러 | 스트리밍 중 서버가 멈춘 듯 보임 | 1-3의 결정을 11/10에 |
| 계약 변경이 늦게 퍼짐 | 통합 날 깨짐 | 계약 파일은 통합 담당만, 변경 시 카톡 공지 + `uv run poe check` |
| 최신 버전 변화(FastAPI 0.16x는 주 단위로 릴리스, fastmcp 4.x, ai 7.x) | 예제가 안 맞음 | `uv.lock`·`package-lock.json` 커밋, 11/20 이후 업그레이드 금지 |
| Micro 1GB 메모리 부족 | 빌드·실행 중 죽음 | c2-g3 또는 노트북 빌드 후 `dist`만 업로드 |
| nip.io 인증서 한도 | 당일 HTTPS 실패 | DuckDNS 또는 구매 도메인을 기본으로, nip.io는 예비 |
| HCX 테스트 키 분당 60회(16 조사) | 리허설 중 429 | 리허설은 한 명만 진짜 키, 나머지는 가짜 LLM |
| WebSocket idle 끊김 | 전화 중 끊김 | 15~30초 ping(16c), Caddy 직결(LB 안 씀) |
| 서버·공인 IP 반납 누락 | 대회 후 과금 | 11/26 반납 체크리스트 |

---

## 출처

공식 문서
- FastAPI: [Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/) · [WebSockets](https://fastapi.tiangolo.com/advanced/websockets/) · [Testing WebSockets](https://fastapi.tiangolo.com/advanced/testing-websockets/) · [Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/) · [Frontend](https://fastapi.tiangolo.com/tutorial/frontend/) · [Static Files](https://fastapi.tiangolo.com/tutorial/static-files/) · [Dataclasses](https://fastapi.tiangolo.com/advanced/dataclasses/) · [APIRouter 레퍼런스](https://fastapi.tiangolo.com/reference/apirouter/) · [릴리스 노트(GitHub)](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/release-notes.md)
- [Starlette StaticFiles](https://starlette.dev/staticfiles/) · [Uvicorn 설치](https://uvicorn.dev/installation/)
- [Python typing.Protocol](https://docs.python.org/3.11/library/typing.html#typing.Protocol) · [Pydantic Unions](https://pydantic.dev/docs/validation/latest/concepts/unions/)
- [Cockburn, Hexagonal Architecture](https://alistair.cockburn.us/hexagonal-architecture) · [Fowler, ContractTest](https://martinfowler.com/bliki/ContractTest.html)
- Vite: [Guide](https://vite.dev/guide/) · [Server Options](https://vite.dev/config/server-options) · [Env and Mode](https://vite.dev/guide/env-and-mode)
- Vercel AI SDK: [Stream Protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) · [Streaming Data](https://ai-sdk.dev/docs/ai-sdk-ui/streaming-data)
- assistant-ui: [LocalRuntime](https://www.assistant-ui.com/docs/runtimes/custom/local-runtime) · [Data Stream](https://www.assistant-ui.com/docs/runtimes/custom/data-stream)
- [AG-UI State Management](https://docs.ag-ui.com/concepts/state) · [CopilotKit useCoAgent](https://docs.copilotkit.ai/reference/v1/hooks/useCoAgent)
- MDN: [getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) · [EventSource()](https://developer.mozilla.org/en-US/docs/Web/API/EventSource/EventSource)
- FastMCP: [Welcome](https://gofastmcp.com/getting-started/welcome) · [FastAPI 연동](https://gofastmcp.com/integrations/fastapi) · [OpenAPI 연동](https://gofastmcp.com/integrations/openapi) · [MCP Transports 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- NCP: [SSD Server(KVM g3) 상품](https://www.ncloud.com/product/compute/ssdServer) · [Server 상품](https://www.ncloud.com/product/compute/server) · [서버 사양(VPC)](https://guide.ncloud-docs.com/docs/server-spec-vpc) · [ACG(VPC)](https://guide.ncloud-docs.com/docs/server-acg-vpc) · [Certificate Manager 인증서](https://guide.ncloud-docs.com/docs/en/certificatemanager-cmissuedcertificate-feature)
- Caddy: [Automatic HTTPS](https://caddyserver.com/docs/automatic-https) · [reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- Let's Encrypt: [6일·IP 인증서 GA (2026-01-15)](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability) · [Public Suffix List](https://publicsuffix.org/list/public_suffix_list.dat) · [nip.io](https://nip.io/)
- uv: [Running commands](https://docs.astral.sh/uv/concepts/projects/run/) · [CLI 레퍼런스](https://docs.astral.sh/uv/reference/cli/) · [Docker 가이드](https://docs.astral.sh/uv/guides/integration/docker/) · [이슈 #5903 task runner](https://github.com/astral-sh/uv/issues/5903) · [poethepoet](https://poethepoet.natn.io/index.html)
- Git·GitHub: [git-config](https://git-scm.com/docs/git-config) · [About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)

버전 조회(2026-11-07)
- PyPI: fastapi 0.162.1, uvicorn 0.54.0, pydantic 2.14.3, pydantic-settings 2.16.2, sse-starlette 3.7.0, fastmcp 4.1.6, mcp 2.3.0, langchain-mcp-adapters 0.3.2, langchain-naver 0.1.1, ag-ui-protocol 1.0.0, pytest 9.2.0, uv 0.13.7, poethepoet 0.49.0, rust-just 1.59.0
- npm: react 19.3.1, vite 8.3.6, @vitejs/plugin-react 6.2.1, create-vite 9.3.4, ai 7.0.164, @ai-sdk/react 4.0.168, @assistant-ui/react 0.15.32, @assistant-ui/react-data-stream 0.12.40, @assistant-ui/react-ai-sdk 1.4.21, @copilotkit/react-core 1.85.0, @ag-ui/client 1.0.4, zustand 5.1.0, tailwindcss 4.3.5, typescript 7.0.2, openapi-typescript 7.13.0, @microsoft/fetch-event-source 2.0.1(2021)
- GitHub: caddyserver/caddy v2.11.9 (2026-11-05)

2차 자료
- [NCP 포럼 MCP 실전 쿡북 1부](https://www.ncloud-forums.com/topic/574/) · [2부](https://www.ncloud-forums.com/topic/580/) · [3부](https://www.ncloud-forums.com/topic/581/) · [4부](https://www.ncloud-forums.com/topic/582/) — 검색 결과 요약만 확인(본문 **미확인**)
- [Caddy 커뮤니티: shortlived 인증서](https://caddy.community/t/shortlived-certificates-from-lets-encrypt/33399)
- [FastAPI + Vite SPA 예시 PR](https://github.com/iFallenDawn/sxcndhxnd/pull/19)

기존 조사(중복 제외 근거)
- [16c-infra-mcp.md](../../../.scratch/ai-biseo-ideation/research/16c-infra-mcp.md) · [16-ncp-console-capabilities.md](../../../.scratch/ai-biseo-ideation/research/16-ncp-console-capabilities.md)
