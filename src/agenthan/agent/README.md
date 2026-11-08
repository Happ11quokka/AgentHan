# Part A · 사장님 비서 (`agent/` + `tools/`)

- 담당: 노종현, 노근형
- 하는 일: 사장님 한마디 → 의도 파악 → 플레이스 기능 매칭 → tool 호출 → 결과 정리. 대표 기능 3개(메뉴 사진 등록, 리뷰 요약, 통계 리포트)는 실제 입력까지 한다.
- `agent/`: LangGraph 그래프, 프롬프트, 서브에이전트. `tools/`: tool 함수와 더미 플레이스 API.
- 쓸 수 있는 것: `agenthan.core.*`. Part B·C 내부(`agenthan.rag.*`, `agenthan.voice.*`)는 import하지 않는다.
- 가게 지식은 `StoreKnowledge`로 받는다. 진짜 구현 전에는 `core.fakes.FakeStoreKnowledge.from_fixture()`.
- 자세한 요구사항: `docs/PRD.md` §6.1 · 리서치: `docs/research/01-agent-hcx-langgraph.md` · CLOVA 호출·LangGraph 예제: `docs/sdk.md` 3장, 부록 B-3·B-6

## 지금 구현된 것 (PRD v0.2 기준 위치)

```
사장님 발화
 → OwnerAssistant (agent/owner_assistant.py)
     ① 기능 후보: agent/place_matcher.py (키워드 30% + CLOVA 임베딩 70%, 18개 기능)
     ② 기능 하나 고르기: 지금은 매칭 1위 (PRD는 HCX-DASH-002 select_feature — 아직 안 함)
     ③ 그 기능 도구만 붙여 HCX-005 실행
        ├ menu                    → upsert_menu (tools/owner_tools.py, 더미 플레이스 API에 저장)
        ├ review_reply, ai_review → 리뷰 서브에이전트 (agent/review_delegate.py → agent/review/)
        └ 그 밖의 기능              → 이름·설명 안내 (도구는 tools/feature_tools.py에 정의만)
```

| 위치 | 하는 일 |
|---|---|
| `agent/owner_assistant.py` | 오케스트레이터 대화 루프 (CLI: `python -m agenthan.agent.owner_assistant`) |
| `agent/place_matcher.py`, `place_features.json` | 플레이스 기능 카탈로그(=PRD의 `features.json`)와 요청→기능 매칭 |
| `agent/review/` | 리뷰 서브에이전트: 분석·요약, 미답글, 답글 초안·등록(더미), 정기 보고 루틴(A-2·A-4) |
| `agent/review_delegate.py` | 오케스트레이터 → 리뷰 서브에이전트 위임 |
| `tools/dummy_store.py` | 더미 플레이스 API (로컬 `.agenthan/store.json`, 실제 네이버 연동 없음) |
| `tools/owner_tools.py`, `feature_tools.py` | 메뉴 도구와 15개 기능 도구(미연결) |
| `tools/review_clf/` | 리뷰 [특징] 분류기 (`models/baseline.joblib`은 실제 리뷰로 학습해 레포에 없음 · 카톡으로 받기) |
| `tools/knowledge_adapters.py` | `StoreKnowledge`(Part B 계약) → 리뷰 서브에이전트의 리뷰·메뉴 입력 |
| `tools/review_adapters.py` | 더미 플레이스 API → 리뷰 서브에이전트 입력 |
| `eval/tool_selection.py` | 16개 도구를 다 붙였을 때 HCX-005 도구 선택 평가 |

공통 HCX 클라이언트는 `core/llm.py`, 임베딩은 `core/embedding.py`를 쓴다.

### PRD 방향과 아직 다른 점

- HCX 호출이 openai SDK(OpenAI 호환 API)다. PRD는 sdk.md 네이티브 REST v3. `core/llm.py`만 바꾸면 되게 모아 뒀다.
- 오케스트레이터가 LangGraph `StateGraph`가 아니라 직접 짠 루프다. 승인(HITL)도 `interrupt()`가 아니라 대화 턴 확인이다.
- 기능 고르기(HCX-DASH-002 `select_feature`)가 없다. 매칭 1위를 그대로 쓴다.
- 메뉴 사진 → 등록(A-1)과 통계 리포트(A-3)는 아직 없다. 메뉴 도구는 이름·가격만.
- 더미 플레이스 API 시드(`tools/dummy_store_seed.json`)가 `fixtures/sample_store.json`과 다른 가게다. 메뉴·리뷰 읽기를 `StoreKnowledge`로 옮기면 하나로 맞춘다.

## 실행

```bash
uv run --env-file .env python -m agenthan.agent.owner_assistant
uv run --env-file .env python -m agenthan.agent.owner_assistant --store /tmp/agenthan-demo/store.json
uv run python -m agenthan.agent.place_matcher build-index   # 카탈로그를 고친 뒤 기능 벡터 다시 만들기
uv run --env-file .env python -m agenthan.eval.tool_selection --out tool_eval.jsonl
uv run pytest -q -m network                                 # 실제 CLOVA 호출 테스트 (키 필요)
```

예: `신메뉴 연습 차를 등록하고 싶어요` → 가격 질문 → `5000원`.
리뷰 예: `답글 안 단 리뷰 있어?` → `안 좋은 리뷰에 답글 써줘` → `더 정중하게 고쳐줘` → `이대로 올려줘`.
답글은 `owner_reply.status = "saved_not_sent"`로 저장만 하고 보내지 않는다.

리뷰 서브에이전트만 따로 돌리기(합성 fixture):

```bash
uv run --env-file .env python scripts/chat_demo.py --store 연습떡집 \
  --snapshot tests/fixtures/review_snapshot.jsonl --menus tests/fixtures/menus.json \
  --ask "딸기모찌 반응 어때?"
```

분류기·스냅샷·평가 스크립트는 `docs/review-clf.md`, 리뷰 통합 설계는 `docs/integrate-review-tools.md`.
