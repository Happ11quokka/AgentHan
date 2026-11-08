# 리뷰 도구 통합 안내 (`integrate/review-tools`)

근형님께. 종현 작성, 2026-11-08.

## 1. 한 줄 요약

근형님 `feature/9-review-tools`(0fb5f03)와 종현 `main`(ebbef63)을 합친 `integrate/review-tools`
브랜치를 만들었습니다. 근형님 리뷰 로직(`src/review_tools`, `src/naver_review_clf`)은 동작을 그대로 두고,
사장님 비서가 리뷰 기능을 근형님 `run_turn()`으로 넘기도록 연결했습니다. HCX 클라이언트·옵션은
새 공용 패키지 `src/hcx`로 모았습니다. **앞으로 이 브랜치에서
같이 작업하면 됩니다.**

## 2. 최종 구조

```
사장님 발화
 → OwnerAssistant (기능 매칭)                         ← 종현
     ├ menu                     → upsert_menu
     ├ review_reply, ai_review  → review_delegate.handle(utterance, session)
     │                              → agenthan.agent.review.agent.run_turn + ReviewToolbox  ← 근형님 코드
     └ 그 밖의 15개 기능          → 이름·설명 안내 (도구는 정의만, 아직 미연결)
```

- 한 대화에 `ReviewSession` 하나: `ReviewToolbox`(미답글 목록, 초안, 보고 제안)와 리뷰 대화 기록을 유지합니다.
- 시스템 값은 비서가 넣습니다: `store_id`(더미 가게 id), `today`, `owner_lang`(발화 문자로 판별: ko/en/ru/vi/zh).
- `handle()`은 `{"text": 답변, "blocks": 시스템이 붙인 원문·초안 블록}`을 돌려줍니다. `run_turn()`이 `messages`에는
  답변만 넣는 점을 이용해 나눕니다(`run_turn` 수정 없음).
- 초안 확인이나 보고 주기 제안을 기다리는 동안(`toolbox.drafts` 또는 `pending_suggestion`)에는
  "이대로 올려줘", "응 그렇게 해줘"처럼 기능 단어가 없는 말도 리뷰 흐름에 남깁니다.

## 3. 기존 대비 바뀐 점

| 항목 | 근형님 기존 | 종현 기존 | 최종 |
| --- | --- | --- | --- |
| 대화 루프 | `run_turn()` 단독 (`scripts/chat_demo.py`) | `OwnerAssistant.ask()` (기능 1개 → 그 도구만 노출) | `OwnerAssistant`가 기능을 고르고 리뷰 기능은 `run_turn()`에 위임 |
| 리뷰 도구 | 5개 도구 + 규칙 라우터, 초안 작성이 핵심 | `save_review_reply`(사장님이 준 답글 저장만, "초안 금지"), `analyze_reviews`(목록만 반환) | 근형님 도구로 통일. 종현 쪽 두 도구, "초안 금지" 문구, 해당 평가 사례 삭제 |
| HCX 클라이언트 | openai SDK, OpenAI 호환 `/v1/openai`. 클라이언트 생성이 분류기·hcx_llm·스크립트에 따로 있음, `request_options()`는 분류기 모듈에 | urllib, v3 네이티브 `/v3/chat-completions`, camelCase | 새 공용 패키지 `src/hcx`: `make_client()`, `request_options(model, max_tokens, json_schema)`, `tool_call_options()`. 세 패키지와 스크립트 모두 여기서 가져감. `tool_choice` 안 보냄, 재시도 `HCX_MAX_RETRIES`(기본 8) |
| 데이터·메뉴 저장소 | `SnapshotRepo`, `MenuCatalog`(`data/` JSON) | `DummyStore`(메뉴 `{id,name,price}`, 리뷰 `{id,review_text,reply_text,status}`) | `DummyStore` 하나. 위에 `ReviewRepo`/`MenuCatalog`/`MockReplyStore` 어댑터. 리뷰는 스냅샷과 같은 `{review_id, visit_date, rating, text, features, has_owner_reply[, owner_reply]}`, 메뉴에 `aliases` 추가. `upsert_menu`로 등록한 메뉴가 바로 리뷰 메뉴 매칭에 반영 |
| 테스트 데이터 | 실제 명동 스냅샷(gitignore) → 데이터 없으면 테스트 4개 파일 수집 실패 | 오프라인 기본 | `tests/fixtures/`의 합성 리뷰·메뉴(실제 원문 없음). 전체 오프라인 pytest 통과 |
| 의존성 | openai, scikit-learn 1.9.x, numpy, pydantic, joblib, openpyxl | 없음(stdlib만) | 근형님 의존성 그대로. "의존성 없음" 원칙은 이 브랜치에서 폐기(README에 명시) |

## 4. 근형님 코드에서 바뀐 부분

동작(도구·라우터·답글 정책·분석 로직)은 바꾸지 않았고, HCX 설정을 한 곳으로 모으고 테스트 데이터를
합성 fixture로 바꿨습니다. `models/`는 그대로입니다.

| 파일 | 바뀐 점 | 이유 |
| --- | --- | --- |
| `src/naver_review_clf/backends/hcx.py` | `DEFAULT_BASE_URL`·클라이언트 생성을 `hcx`에서 가져옴. `request_options()`는 `hcx.request_options(model, max_tokens, JSON_SCHEMA)`를 감싼 함수로 남김 | 세 패키지가 같은 클라이언트·옵션을 쓰도록. 기존 import 경로는 그대로 동작 |
| `src/review_tools/hcx_llm.py` | 클라이언트를 `hcx.make_client()`로, `request_options(self.model, max_tokens, schema)`로 이 호출의 스키마를 직접 넘김 | 분류기 스키마를 받아 바꿔 끼우던 코드가 필요 없어짐 (HCX-007 동작 동일) |
| `src/review_tools/agent.py` (`run_turn`) | 옵션을 `hcx.tool_call_options(model)`로 | 비서 쪽 도구 호출과 같은 규칙 (max_tokens 1024, response_format 없음) |
| `scripts/chat_demo.py` | `hcx.make_client()`, `--snapshot`·`--menus` 인자 추가 (기본값은 기존 `data/` 경로) | 실제 데이터 없이 fixture로도 데모 가능 |
| `scripts/hcx_check.py`, `scripts/hcx_spec_check.py` | `hcx.make_client(max_retries=2)` | 클라이언트 생성 통일. 실측 스크립트라 SDK 기본 재시도 2회 유지 |
| `tests/test_agent.py`, `test_analyze.py`, `test_replies.py`, `test_report.py` | 데이터 경로 `data/snapshots/test_baseline.jsonl` → `tests/fixtures/review_snapshot.jsonl`, `data/menus_sample.json` → `tests/fixtures/menus.json` | 저장소가 public이라 실제 리뷰는 커밋할 수 없고, 데이터가 없으면 수집 단계에서 실패했음 |
| 같은 4개 파일 | 매장명 `홍만당`→`연습떡집`, `보배반점`→`연습반점`, `원조남산왕돈까스`→`연습돈까스` | 합성 리뷰를 실제 매장 이름에 붙이지 않기 위해 |

검증 내용(assert)은 그대로입니다. 합성 데이터는 기존 assert의 의미가 유지되게 만들었습니다:
연습떡집은 최근 30일 4건(격주 보고), 14일 규칙상 미답글 3건이고 부정 리뷰 `r00732`(딸기모찌 맛이 없어요)가
맨 앞, 연습반점은 하루 3건(매일 보고), 연습돈까스는 바퀴벌레 고위험 리뷰. 생성 스크립트는
`tests/fixtures/make_fixtures.py`입니다. 실제 스냅샷으로 다시 확인하려면 경로만 되돌려 돌리면 됩니다.

`pyproject.toml`은 자동 병합됐습니다(근형님 의존성·패키지 목록 + 종현 쪽 `-m "not network"` 기본값).

### 종현 코드에서 바뀐 부분 (참고)

| 파일 | 바뀐 점 |
| --- | --- |
| `src/agenthan/agent/review_delegate.py` (신규) | `new_session()`, `handle()`, `detect_lang()`, `REVIEW_FEATURES` |
| `src/agenthan/tools/review_adapters.py` (신규) | `DummyStoreReviewRepo`, `DummyStoreMenuCatalog`, `DummyStoreReplyStore`(등록 = 저장소에 `owner_reply`, `saved_not_sent`) |
| `src/hcx/__init__.py` (신규) | 공용 HCX 설정과 CLOVA 실측 제약 메모. `tests/test_hcx.py` |
| `src/agenthan/agent/clova_chat.py` | urllib v3 → `hcx` 클라이언트. 키 없음·오류 메시지 정제 |
| `src/agenthan/agent/owner_assistant.py` | 리뷰 기능 위임 분기, OpenAI 메시지 형식, 프롬프트에서 답글 관련 문구 삭제 |
| `src/agenthan/tools/owner_tools.py`, `feature_tools.py` | `save_review_reply`, `analyze_reviews`(ai_review) 삭제, `DELEGATED_FEATURES` 추가. 도구 18 → 16개 |
| `src/agenthan/tools/dummy_store.py`, `dummy_store_seed.json` | 리뷰 스키마 변경(이전 형식은 읽을 때 변환), 메뉴 `aliases`, 가게 `category`, `save_review_reply` 삭제 |
| `src/agenthan/eval/tool_selection.py`, `tests/data/tool_selection_cases.jsonl` | 리뷰 사례 7개 삭제(68 → 61건) |
| `tests/test_review_delegate.py` (신규) 외 기존 테스트 | 위임 흐름(목록 → 초안 → 수정 → 등록), 어댑터, 언어 판별, 새 클라이언트 |

## 5. 실행·테스트

```bash
uv sync --frozen
uv run --frozen --offline pytest -q          # 오프라인 224개 (network 2개 제외)
uv run --frozen --offline ruff check .
uv run --frozen --offline ruff format --check .
uv run --env-file .env python -m agenthan.agent.owner_assistant     # 비서 대화 (메뉴 + 리뷰)
uv run --env-file .env python scripts/chat_demo.py --store <매장명>   # 리뷰 도구만 (실제 스냅샷)
uv run --env-file .env python scripts/chat_demo.py --store 연습떡집 \
  --snapshot tests/fixtures/review_snapshot.jsonl --menus tests/fixtures/menus.json   # fixture로
uv run --env-file .env pytest -q -m network   # 실제 CLOVA 호출
```

키는 `.env`에만 두고(커밋 안 됨) 코드는 `.env`를 직접 읽지 않습니다.

## 6. 앞으로 역할

- **종현**: 라우터(`OwnerAssistant`), 15개 기능 도구를 비서에 연결, 메뉴 입력 대표 기능 + 더미 화면
- **근형**: 리뷰 도메인(`review_tools`), `create_review_request`, 영어 답변 길이
- **같이**: E2E 시나리오 — 메뉴 등록 → 그 메뉴의 리뷰 반응 → 답글

## 7. 확인 부탁드릴 결정

1. **C-1 지원 언어**: 지금은 발화 문자로 ko/en/ru/vi/zh를 판별해 `owner_lang`으로 넘깁니다(`agenthan.agent.review.agent.LANG_NAMES`와 같은 범위). 줄이거나 늘릴지.
2. **C-2 메뉴 원어명 입력 주체**: 메뉴에 `aliases` 필드는 생겼지만 입력 경로는 아직 없습니다(새 메뉴는 `aliases: []`, 정식명으로만 매칭). 사장님이 직접 넣을지, 번역으로 채울지.
3. **C-3 답글 약속 허용 범위**: `OWNER_PROMISES_ALLOWED = True`(사장님이 지시한 약속만 ⚠ 표시 후 허용) 그대로 두었습니다.
4. **`upstream/feature/assistant-core`**(032517f, `assistant.py` 804줄): 이번 통합에는 넣지 않았습니다. 쓸지, 정리할지.
5. **upstream PR 순서**: `upstream/main`은 아직 스켈레톤입니다. 이 브랜치를 PR 하나로 올릴지, 나눌지.
6. **카탈로그의 리뷰 입력 항목**: `place_features.json`의 `review_reply.input_fields`(review_text, reply_text, tone)는 위임 후 쓰이지 않지만 매칭 인덱스 해시 때문에 그대로 두었습니다. 지울지.
7. **도구 선택 평가 재측정**: 이전 수치(18개, selection_accuracy 1.0)는 16개 구성으로 다시 재야 합니다. 리뷰 쪽 라우팅 평가는 근형님 시나리오 10개로 갈음할지.

## 미검증

- 실제 HCX 호출은 이번 통합 뒤에 돌려 보지 않았습니다(오프라인 테스트만). 특히 v3 → OpenAI 호환 전환 후 메뉴 도구 호출과 위임 흐름 E2E.
- 통합 기준은 로컬에 있던 `upstream/feature/9-review-tools` 0fb5f03입니다. 그 뒤 커밋이 있으면 다시 합쳐야 합니다.
- `models/baseline.joblib`은 실제 리뷰로 학습해 저장소에 올리지 않습니다(gitignore). 카톡으로 받아 `models/`에 두거나 `scripts/train_baseline.py`로 학습합니다.
