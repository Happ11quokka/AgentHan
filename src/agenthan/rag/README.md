# Part B · 가게 지식 (`rag/`)

- 담당: 정민서
- 하는 일: 외국인 가게 1곳 데이터(메뉴·설명·리뷰) 정제·마스킹 → 인덱싱 → `StoreKnowledge` 구현. 플레이스 기능 목록 검색(사장님 요청 → 기능 매칭)용 데이터도 같이 관리한다. 사장님 질문 시나리오와 골드셋을 만든다.
- **비서와 전화 둘 다의 기반이라 가장 먼저 끝나야 한다.**
- 쓸 수 있는 것: `agenthan.core.*`. Part A·C 내부는 import하지 않는다.
- **실제 데이터와 관련 코드는 `data/`·`private/`에만. 절대 커밋하지 않는다.** 레포·발표 자료는 `fixtures/` 더미만. 내부 방법은 `private/README.md`(카톡으로 받기).
- 완료 기준: `tests/test_contracts.py`가 진짜 구현으로도 통과.
- 자세한 요구사항: `docs/PRD.md` §6.2 · 리서치: `docs/research/02-store-rag.md` · 임베딩·RAG 기본값: `docs/sdk.md` 4장 ADR-03, 부록 B-5
