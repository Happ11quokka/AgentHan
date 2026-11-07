# Part A · 사장님 비서 (`agent/` + `tools/`)

- 담당: 노종현, 노근형
- 하는 일: 사장님 한마디 → 의도 파악 → 플레이스 기능 매칭 → tool 호출 → 결과 정리. 대표 기능 3개(메뉴 사진 등록, 리뷰 요약, 통계 리포트)는 실제 입력까지 한다.
- `agent/`: LangGraph 그래프, 프롬프트, 서브에이전트. `tools/`: tool 함수와 더미 플레이스 API.
- 쓸 수 있는 것: `agenthan.core.*`. Part B·C 내부(`agenthan.rag.*`, `agenthan.voice.*`)는 import하지 않는다.
- 가게 지식은 `StoreKnowledge`로 받는다. 진짜 구현 전에는 `core.fakes.FakeStoreKnowledge.from_fixture()`.
- 자세한 요구사항: `docs/PRD.md` §6.1 · 리서치: `docs/research/01-agent-hcx-langgraph.md` · CLOVA 호출·LangGraph 예제: `docs/sdk.md` 3장, 부록 B-3·B-6
