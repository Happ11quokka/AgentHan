# 02. 가게 지식 RAG (Part B) 기술 조사

> 조사일 2026-11-07 · 담당 파트 B

등급 표기: [공식] = NAVER·NCP·라이브러리 공식 문서나 원문, [2차] = 보도·법률 해설·제3자 정리, [추론] = 위 근거로 우리가 판단한 것. 확인하지 못한 것은 "미확인"으로 적었다.
중복 방지: CLOVA Studio 모델·요금·API 목록은 [16a](../../../.scratch/ai-biseo-ideation/research/16a-clova-studio.md), NCP 서비스 카탈로그는 [16c](../../../.scratch/ai-biseo-ideation/research/16c-infra-mcp.md), 플레이스 API 유무는 [02-naver-ecosystem](../../../.scratch/ai-biseo-ideation/research/02-naver-ecosystem-data.md), SDK 기본값은 `sdk.md` ADR-03·부록 B-5에 있다. 여기서는 새로 확인한 것과 Part B 판단만 적는다.

## 요약

1. **우리 규모(문서 5천 개 미만)에서는 벡터 DB 성능 차이가 의미 없다.** 정확도는 저장소가 아니라 "메뉴 1개 = 문서 1개" 구조, 별칭 사전, 한국어 키워드 검색(BM25)과 벡터 검색을 섞는 하이브리드가 정한다. 메뉴 수백 개는 프롬프트에 통째로 넣어도 약 1만 토큰이다.
2. **"네이버 클라우드 위" 어필은 Cloud DB for PostgreSQL + pgvector가 가장 쉽다.** pgvector와 pg_trgm(유사 문자열 검색)을 공식 지원한다. 단 pgvector 버전은 문서에 없고, VPC 전용이다. Search Engine Service(OpenSearch)는 k-NN·nori(한국어 분석기)가 문서에 없다. SSH로 플러그인을 직접 설치하는 방식이다.
3. **CLOVA 리랭커는 점수를 주지 않는다.** 답변·인용 문서·재검색어를 생성하는 모델이다. 근거가 없으면 `citedDocuments`가 빈 배열로 온다. 그래서 "모른다" 판정에 쓸 수 있다. 임베딩 v2는 요청당 텍스트 1개이고, 테스트 키 한도는 60 QPM이다.
4. **"네이버 연구실 RAG 기술"이라고 말할 공식 근거는 약하다.** HyperCLOVA X 기술 보고서 2편에는 RAG 장이 없고, AI 브리핑은 "HCX 기반 요약 + 출처 + 후속 질문"까지만 공개됐다. 말할 수 있는 것은 "NAVER Cloud 공식 쿡북 구조(문단 나누기 → 임베딩 v2 → 벡터DB → 리랭커 → HCX)와 CLOVA 인용형 RAG API를 따랐다"이다.
5. **실제 가게 데이터는 레포 밖에서 다룬다.** 수집 방법과 원본은 `private/`(gitignore)에 두고, 레포·발표 자료는 더미 데이터로 통일한다.

---

## 질문 1. 벡터 저장소 비교

전제: 메뉴 수십~수백 개, 리뷰 수백~수천 개. 1024차원 벡터 5천 개는 약 20MB(float32)다 [추론]. 이 규모는 전수 비교(brute force)로도 1초 안에 끝난다 [추론].

| 후보 | 하이브리드(키워드+벡터) | 메타데이터 필터 | 로컬 개발 | "NCP 위에서 동작" | 주의점 | 출처 |
|---|---|---|---|---|---|---|
| **Chroma** (현재 방향) | 로컬 OSS는 `$contains`·`$regex` 문서 필터만 확인. BM25 순위 검색은 문서에 없음. sparse+dense RRF 하이브리드 문서는 Cloud 경로에만 있음 | O (`where`) | 매우 쉬움(`pip`, 파일 1개 폴더) | 서버에 같이 올리면 됨. NCP 관리형 아님 | **기본 거리가 L2다.** 생성할 때 `{"hnsw": {"space": "cosine"}}`를 넣어야 하고, 나중에 못 바꾼다. 전문 검색은 대소문자 구분 | [공식] [configure](https://docs.trychroma.com/docs/collections/configure), [full-text](https://docs.trychroma.com/docs/querying-collections/full-text-search), [hybrid(Cloud)](https://docs.trychroma.com/cloud/search-api/hybrid-search) |
| **FAISS** | X. 벡터 인덱스 라이브러리일 뿐 | X (직접 구현) | 쉬움 | 같음 | 메타데이터·영속화를 직접 짜야 한다. 교안 `Langgraph_RAG.ipynb`가 FAISS를 썼다 | [공식] sdk.md 부록 B-5 |
| **SQLite (팀 SDK)** + FTS5 | FTS5 키워드 + 벡터를 RRF로 합치는 예시가 있다 | O (SQL) | 가장 쉬움(SDK 그대로) | 같음 | SDK는 numpy 코사인 전수 비교. 문서량이 늘면 Chroma/FAISS로 바꾸라고 ADR-03에 적혀 있다 | [공식] sdk.md ADR-03, [2차] [Alex Garcia 블로그](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html) |
| **sqlite-vec** | FTS5와 섞어 쓰는 방식(위 블로그) | O (metadata 컬럼) | 쉬움(`pip install sqlite-vec`) | 같음 | 저자가 "pre-v1, 호환성 깨짐 예상"이라고 경고 | [공식] [GitHub](https://github.com/asg017/sqlite-vec) |
| **LanceDB** | O. 로컬(Embedded)에서 FTS+벡터 하이브리드, 기본 RRF | O (기본 prefilter) | 쉬움 | 같음 | FTS 토크나이저의 한국어 지원은 문서에서 확인 못 함(미확인) | [공식] [hybrid search](https://docs.lancedb.com/search/hybrid-search) |
| **NCP Cloud DB for PostgreSQL + pgvector** | O. pgvector README가 "Postgres 전문 검색과 함께 하이브리드, RRF로 결합"을 안내. 단 Postgres 기본 전문 검색엔 한국어 파서가 없다 → Kiwi로 미리 쪼갠 토큰을 저장하거나 pg_trgm 사용 [추론] | O (SQL) | 로컬은 Docker Postgres+pgvector로 같은 SQL [추론] | **O. NCP 관리형 DB** | pgvector는 콘솔 설치·재시작 필요, Database 단위. **pg_trgm은 사용자 설치 가능.** pg_bigm·한국어 FTS 확장은 목록에 없음. **pgvector 버전은 문서에 없음(미확인).** 최신 업스트림은 0.8.7. VPC 전용이라 노트북에서 직접 붙는 방법은 미확인. 월 요금 미확인 | [공식] [NCP Extension](https://guide.ncloud-docs.com/docs/clouddbforpostgresql-postgresqlextension), [pgvector](https://github.com/pgvector/pgvector) |
| **NCP Search Engine Service (OpenSearch 2.14/2.15/2.18)** | 업스트림 OpenSearch는 k-NN·nori 플러그인이 있다. **NCP 문서엔 k-NN·nori 언급이 없다.** FAQ: "sshuser로 접속해 plugin 파일을 설치할 수 있다. 사용자 조작으로 생긴 문제는 책임지지 않는다" | O | 무거움(클러스터) | O | OSS 라이선스만. 클러스터 비용이 크다(16c). 2주 일정엔 과함 [추론] | [공식] [SES FAQ](https://guide.ncloud-docs.com/docs/ses-faq), [OpenSearch 추가 플러그인](https://docs.opensearch.org/3.5/install-and-configure/additional-plugins/index/) |
| (참고) **NCP RAG 서비스** | 문서에 검색 방식 설명 없음 | 미확인 | 콘솔 | O (관리형) | **2025-07-17 CBT 출시, CBT 동안 무료, SLA 없음.** 임베딩·벡터DB는 "기본 설정만". Passage 256~4096(기본 512), Overlap 기본 128, 인용 K 1~10(기본 5). 메뉴명 매칭을 튜닝할 손잡이가 없다 | [공식] [개요](https://guide.ncloud-docs.com/docs/rag-overview), [사용 준비](https://guide.ncloud-docs.com/docs/rag-spec), [서비스 생성](https://guide.ncloud-docs.com/docs/rag-services-create), [릴리스 노트](https://guide.ncloud-docs.com/docs/rag-releasenote) |

판단 [추론]
- 개발 편의 1등은 SQLite(SDK) 또는 Chroma다. 어필 1등은 Cloud DB for PostgreSQL + pgvector다.
- Chroma를 계속 쓰면 BM25는 따로 붙여야 한다(Python `bm25s` + Kiwi). 그러면 Chroma가 주는 이점은 "벡터 저장" 하나뿐이다.
- 두 저장소를 모두 유지하면 4명 팀에 부담이다. **검색 함수 하나(`search(query, filters) -> [hit]`) 뒤에 저장소를 숨기고**, 하나만 고른다.

## 질문 2. 짧은 메뉴명 매칭에 맞는 검색 방식

문제 예시: "쌀국수"(메뉴 3개 후보), "취두부 쌀국수"(일반 번역이 "쌀국수"로 뭉갬), 이름만 냉면인 철판 요리, "보르시/보르쉬"(표기 흔들림), 전화 음성인식 오류.

| 방식 | 무엇을 잡나 | 놓치는 것 | 근거 | 우리 적용 |
|---|---|---|---|---|
| 벡터만(bge-m3 dense) | 뜻이 비슷한 말("국물 있는 면") | 짧은 고유명사, 철자 차이. 이름만 냉면인 메뉴를 "차가운 면"과 가깝게 둘 수 있음 | [추론]. CLOVA 임베딩 v2는 dense만 준다(16a) | 2순위 신호 |
| **BM25(키워드)** + 한국어 형태소 | 메뉴명 그대로, 재료명 | 띄어쓰기·조사 변형은 형태소 분석이 필요 | [공식] `bm25s`: 순수 Python, 사용자 토크나이저 지원, MIT. Kiwi: 사용자 사전(`add_user_word`), 오타 교정(`typos`), `pip` 설치, Apache-2.0 | 1순위 신호 |
| **음절/자모 n-gram** | 오타, 음성인식 오류, 외래어 표기 차이("보르시/보르쉬") | 너무 짧은 질의는 잡음이 많음 | [추론]. NCP에선 pg_trgm이 사용자 설치 가능([공식] NCP Extension). 한글이 trigram 대상 문자로 처리되는지는 DB 로케일에 따라 다름(미확인) | 이름 후보 찾기 보조 |
| **별칭(alias)·동의어 사전** | "쌀국수→[취두부 쌀국수, 소고기 쌀국수, …]", 러시아어 원어명, 손님이 부르는 말 | 사전에 없는 말 | [추론]. Kiwi 사용자 사전에 메뉴명을 넣으면 한 단어로 쪼개진다([공식] Kiwi) | **가장 먼저 조회.** 사장님 확인 필수 |
| **메타데이터 필터** | 카테고리(음료/케이크), 판매 여부, 옵션 가능 여부 | 없음(정확히 맞춤) | [공식] Chroma `where`, LanceDB prefilter, SQL WHERE | "디카페인 가능한 음료" 같은 질문 |
| 하이브리드 결합(RRF) | 위 신호를 순위로 합친다 | 점수 보정 불필요 | [공식] pgvector README, LanceDB 기본 RRF, Chroma Cloud 문서 | 기본 결합 방식 |
| 리랭커 | 후보 중 답할 근거 고르기 | 점수 없음, 생성 비용·지연 | [공식] CLOVA 리랭커(질문 3) | Part A 질의응답에만 [추론] |

메뉴는 청크 대신 "메뉴 1개 = 문서 1개" [추론]
- 메뉴는 이미 구조화된 데이터다. 800자로 자르면 메뉴 2~3개가 한 청크에 섞이거나, 이름과 옵션이 다른 청크로 갈라진다. 그러면 "어느 메뉴냐"에 답할 수 없다.
- 그래서 메뉴·가게 규칙·리뷰를 **서로 다른 문서 종류**로 저장한다. SDK의 800/100 청크 규칙은 긴 가게 소개 글에만 쓴다. 리뷰는 대부분 짧아서 1개 = 1문서로 둔다.
- 가격·옵션 가능 여부는 **필드 값으로 답한다**. LLM이 리뷰를 보고 추측하지 않게 한다. 출처 우선순위는 사장님 확인값 > 메뉴 필드 > 리뷰다.
- 후보가 여럿이고 점수 차가 작으면 되묻는다("쌀국수는 취두부·소고기 두 가지가 있어요"). 전화 주문(Part C)에서 특히 필요하다.
- 비교 기준선: 메뉴 100개 × 약 80토큰 = 약 8천 토큰이다. 검색 없이 전체 메뉴를 HCX-007 프롬프트에 넣는 방식도 된다. 한 번에 약 10원(입력 1.25원/1K, 16a 단가)이다. 골드셋으로 "검색 방식"과 "전체 투입 방식"을 같이 재 보는 것이 좋다.

## 질문 3. CLOVA Studio의 RAG용 공식 API 현황

16a에 있는 요금·기능 표는 반복하지 않는다. 이번에 새로 확인한 것만 적는다.

| API | 이번에 확인한 사실 | Part B에 주는 의미 | 출처 |
|---|---|---|---|
| 임베딩 v2 (bge-m3) | `POST /v1/api-tools/embedding/v2`, 본문은 `text` 하나(약 8,192토큰), 응답은 1,024개 float와 `inputTokens`. 배치 입력 언급 없음 | 문서 하나당 호출 1번. 정규화는 안 해 주므로 코사인을 쓰거나 우리가 정규화한다(16a) | [공식] [API](https://api.ncloud-docs.com/docs/clovastudio-embeddingv2) |
| 임베딩 v2 한도 | **테스트 키 60 QPM / 40,000 TPM, 서비스 앱 키 540 QPM / 960,000 TPM**(2025-07-17 기준) | 리뷰 2,000개를 테스트 키로 넣으면 최소 약 34분 [추론]. 해시로 캐시하고 바뀐 것만 다시 넣는다(SDK ADR-04) | [공식] [이용량 제어](https://guide.ncloud-docs.com/docs/clovastudio-ratelimiting) |
| 리랭커 | `POST /v1/api-tools/reranker`, 입력 `query` + `documents[{id, doc}]`, `maxTokens` < 4096(기본 1024). 입력 최대 128,000토큰. 응답은 답변(`<doc1>` 인용), `citedDocuments`, `suggestedQueries`, `usage`. **문서별 관련도 점수 필드는 없다.** 답을 못 찾으면 `citedDocuments`가 빈 배열이고 재검색어를 준다. 문서 개수 상한은 문서에 없음 | 순위 매기기용이 아니라 "근거 고르고 인용 답변하기"용이다. 빈 `citedDocuments` = "모른다" 신호로 쓴다 | [공식] [API](https://api.ncloud-docs.com/docs/clovastudio-reranker) |
| 리랭커 한도 | 테스트 키 60 QPM / 45,000 TPM, 서비스 앱 120 QPM / 180,000 TPM | 전화 주문처럼 매 턴 부르면 한도·지연이 문제 [추론] | [공식] 이용량 제어 |
| RAG Reasoning | 테스트 키 60 QPM / 30,000 TPM, 서비스 앱 180 / 120,000. 기능 설명은 16a | 사장님 비서(Part A)에서 "검색 도구를 모델이 고르게" 할 때 후보 | [공식] 이용량 제어, [API](https://api.ncloud-docs.com/docs/clovastudio-ragreasoning) |
| 문단 나누기 / 요약 | 문단 나누기 120 QPM(테스트), 요약 30 QPM / 30,000 TPM(테스트). 기능은 16a | 긴 가게 소개 글이나 리뷰 묶음 요약에만 쓴다. 메뉴에는 쓰지 않는다 [추론] | [공식] 이용량 제어 |
| 관리형 RAG(NCP RAG 서비스) | 질문 1 표 참고. 평가 기능도 있다: 유형은 "Question and Answer"만, 지표는 "기본 설정"만, 평가셋 자동 생성(추가 비용) 또는 업로드(.csv/.xlsx, 200MB) | 우리 골드셋을 같은 Q&A 형식으로 만들어 두면 비교 기준선으로 쓸 수 있다 [추론] | [공식] [평가 생성](https://guide.ncloud-docs.com/docs/rag-evaluations-create), [사용 준비](https://guide.ncloud-docs.com/docs/rag-spec) |

## 질문 4. 네이버 쪽 RAG 연구·사례와 "네이버 방식"의 근거

| 자료 | 내용 | "네이버 방식" 근거로 쓸 수 있나 | 출처 |
|---|---|---|---|
| HyperCLOVA X 기술 보고서(2024-04) | 원문 PDF를 검색해 보니 retrieval/RAG 장이 없다. 평가는 추론·지식·코딩·수학·번역·안전성 | **X.** RAG 구조 근거가 없다 | [공식] [arXiv 2404.01954](https://arxiv.org/abs/2404.01954) |
| HyperCLOVA X THINK 기술 보고서(2025-06) | 같은 방법으로 확인. RAG 항목 없음 | X | [공식] [arXiv 2506.22403](https://arxiv.org/abs/2506.22403) |
| 네이버 검색 AI 브리핑(2025-05-23 보도) | "자체 LLM 하이퍼클로바X 기반", "잘 정리된 요약과 출처", "후속 질문 제공", 리뷰와 최근 검색 데이터를 함께 분석. 검색·색인 구조는 공개하지 않음 | △. **"요약 + 출처 + 후속 질문" 화면 패턴**은 말할 수 있다. 내부 구조는 말할 수 없다 | [공식] [navercorp 보도](https://www.navercorp.com/story/storyDetail?seq=32525) |
| NAVER Cloud 포럼 쿡북 "(3부) CLOVA Studio로 RAG 구현하기"(2024-05-10, CLOVA Studio 운영자) | HTML → **문단 나누기 API** → 임베딩 API(1024차원) → **Milvus**(HNSW, M=8, efConstruction=200) → Chat Completions. v2(bge-m3)는 코사인 권장 | **O.** NAVER Cloud가 직접 안내한 표준 흐름 | [공식] [포럼 topic/307](https://www.ncloud-forums.com/topic/307/) (포럼 DNS 장애로 웹 아카이브 사본 확인) |
| 포럼 쿡북 "Advanced RAG 랭체인"(2024-11-26, CLOVA Studio 운영자) | 임베딩 v2 + **Chroma** + 문단 나누기(기본 후처리 1,000/300자) + reranking + 요약 v2 + **Multi-query**. 10개 검색 → 리랭크 → 상위 3개를 HCX에. 리랭커는 오픈소스 `BAAI/bge-reranker-v2-m3`(HuggingFace) | **O(구조).** 단 오픈소스 리랭커는 "AI는 네이버 클라우드로만" 규칙에 어긋난다. 우리는 CLOVA 리랭커로 바꾼다 | [공식] [포럼 topic/429](https://www.ncloud-forums.com/topic/429/) (웹 아카이브 사본) |
| 포럼 쿡북 Naive RAG(2024-11), Multimodal RAG(2025-05) | MMR 검색, 메타데이터 필터, 이미지→텍스트 후 임베딩 | O(보조) | [2차: 검색 요약] [topic/422](https://www.ncloud-forums.com/topic/422/), [topic/497](https://www.ncloud-forums.com/topic/497/) |
| CLOVA Studio 리랭커·RAG Reasoning(2025-07-17) | 인용형 RAG를 API로 제공 | **O.** "근거 문서 ID로 인용하는 RAG"를 네이버가 제품으로 낸 것 | [공식] 질문 3 |
| NAVER LABS Europe 연구 | SPLADE(희소 검색), BERGEN(RAG 벤치마크 라이브러리, CC BY-NC-SA 4.0, BM25·SPLADE 등 20+ 검색기), Provence(검색 문맥 가지치기, ICLR 2025) | △. "키워드 신호(희소 검색)를 버리지 않는 것"의 연구 배경으로만 인용. 모델을 직접 띄우면 NCP 규칙 위반 | [공식] [naver/bergen](https://github.com/naver/bergen), [Provence arXiv 2501.16214](https://arxiv.org/abs/2501.16214), [2차] [SPLADE 요약](https://frutik.github.io/awesome-search/Articles/SPLADE---Sparse-Bi-Encoder-BERT-Model-for-First-Stage-Ranking) |
| 네이버 검색의 하이브리드·dense 검색 발표(DEVIEW/DAN) | 이번 검색에서 찾지 못함 | 미확인 | — |

정리 [추론]
- "네이버 연구실이 쓰는 RAG 기술에 맞췄다"는 문장은 쓰지 않는다. 공식 근거가 없다.
- 대신 이렇게 말한다: "NAVER Cloud 공식 쿡북 구조(문단 나누기 → 임베딩 v2 → 벡터 DB → 리랭커 → HyperCLOVA X)를 따르고, CLOVA 리랭커의 인용 문서로 근거를 검증한다. 화면은 AI 브리핑처럼 요약·출처·후속 질문을 보여 준다."
- Multi-query(질문 바꿔 여러 번 검색)는 쿡북에 있는 기법이다. 전화 음성인식 오류 대응에 써 볼 만하다.

## 질문 5. 실제 가게 데이터

실제 가게 데이터를 모으는 방법은 이 레포에 두지 않는다. 팀 내부 문서(`private/README.md`, gitignore, 카톡으로 전달)를 본다. 레포·발표·공개 자료에는 `fixtures/`의 더미 데이터만 쓴다.

## 질문 6. RAG 평가

골드셋 만들기 [추론]
- 크기: 100~150문항. 메뉴마다 최소 1문항이 들어가게 한다(심사위원이 DB 안에서 아무 메뉴나 물을 수 있다).
- 작성자 분리: 검색을 만든 사람과 다른 팀원이 테스트용 30%를 쓰고, 개발 중엔 보지 않는다.
- HyperCLOVA X로 질문 변형(말투·오타·음성인식 오류)을 뽑고 사람이 고친다. NCP 규칙 안에서 된다.

| 문항 유형 | 예시 | 정답 형식 | 기대 행동 |
|---|---|---|---|
| 정확한 이름 | "메도빅 있어요?" | `{menu_ids: [m_012]}` | 답변 |
| 부분 이름·모호 | "쌀국수 주세요" | `{menu_ids: [m_003, m_004]}` | **되묻기** |
| 별칭·원어·표기 흔들림 | "보르쉬", "медовик" | `[m_020]` | 답변 |
| 오타·음성인식 오류 | "매도빅", "보르시 하나요" | `[m_012]` | 답변 |
| 옵션·규칙 | "고수 빼 주세요", "디카페인 돼요?" | `{rule_ids: [r_002]}` 또는 메뉴 필드 | 필드 값으로 답변 |
| 필터 | "디카페인 가능한 음료 뭐 있어요?" | id 집합 | 목록 답변 |
| 리뷰 기반 | "제일 인기 많은 케이크?" | `{review_ids: [...]}` | 출처 달고 답변 |
| **근거 없음** | "짜장면 있어요?", "주차돼요?"(정보 없음) | `answerable: false` | **"확인되지 않아요" + 사장님 확인 제안** |
| 플레이스 기능 | "리뷰에 답글 자동으로 달 수 있어?" | `{feature_ids: [f_007]}` | 기능 카드로 답변 |

| 지표 | 정의 | 목표(제안) | 근거 |
|---|---|---|---|
| Recall@1 / Recall@4 | 정답 id가 상위 1개 / 4개 안에 있는 비율 | @4 ≥ 0.95, @1 ≥ 0.85 | [추론]. Ragas `IDBasedContextRecall`이 id 비교로 같은 계산을 한다 [공식] |
| MRR | 첫 정답 순위의 역수 평균(1위=1, 2위=0.5) | ≥ 0.9 | [추론] |
| 되묻기 정확도 | 모호 문항에서 후보를 제시한 비율 | ≥ 0.9 | [추론] |
| 거절 정밀도·재현율 | 근거 없음 문항에서 "모른다"고 한 비율 / "모른다"가 맞았던 비율 | 둘 다 ≥ 0.9 | [추론] |
| 근거 충실도 | 답변 문장이 인용 문서로 뒷받침되는지 | 수작업 30문항 확인 | [공식] Ragas Faithfulness·Noise Sensitivity(LLM 판정자 필요 → HCX 사용) |

"근거 없으면 모른다" 검증 방법
1. 검색 단계: 별칭·키워드 일치가 없고, 벡터 점수가 기준 미만이면 "근거 없음"으로 바로 끝낸다. SDK 임계값 0.45는 시작값일 뿐이다(ADR-03). 골드셋으로 다시 정한다.
2. 생성 단계: CLOVA 리랭커가 `citedDocuments`를 빈 배열로 주면 "모른다"로 처리한다 [공식 동작, 활용은 추론].
3. 인용 검증: 답변의 문서 id가 실제로 넘긴 문서에 있는지 코드로 대조한다(SDK ADR-06의 `no_evidence`).
4. 골드셋의 "근거 없음" 문항을 매 변경마다 돌린다. 하나라도 지어내면 실패로 본다.

---

## PRD에 넣을 방향(확정 아님)

### 추천 스택

| 층 | 추천 | 대안 | 이유 |
|---|---|---|---|
| 임베딩 | CLOVA 임베딩 v2(bge-m3, 1024) | — | NCP 규칙. 쿡북 표준 |
| 키워드 검색 | Kiwi(사용자 사전에 메뉴명·별칭 등록) + `bm25s` | pg_trgm(NCP Postgres) | 짧은 메뉴명은 키워드가 1순위 신호 |
| 결합 | 별칭 정확 일치 → (BM25 + 벡터) RRF → 메타데이터 필터 | — | 저장소와 무관하게 같은 로직 |
| 저장소 | **로컬: SDK SQLite(또는 Docker Postgres) → 데모: NCP Cloud DB for PostgreSQL + pgvector** | Chroma만 쓰기(그때는 `space: cosine` 필수, BM25 별도) | "네이버 클라우드 위에서 동작" 어필. 규모상 성능 차이 없음 |
| 근거 선택·인용 | Part A: CLOVA 리랭커(빈 `citedDocuments` = 모름). Part C 전화: 리랭커 없이 검색 결과 + 필드 값으로 바로 답 | RAG Reasoning(Part A 도구형) | 전화는 지연·QPM이 민감 |
| 평가 | 골드셋 CSV(Q&A 형식) + id 기반 Recall@k·MRR 스크립트 | NCP RAG 서비스 평가(CBT 무료)를 기준선으로 | 심사가 DB 안 질문이라 검색 정확도가 곧 점수 |

결정이 필요한 것: Chroma 유지 vs PostgreSQL 전환. 11/10까지 Cloud DB 생성·접속(VPC 안 서버에서)이 안 되면 SQLite/Chroma로 고정한다.

### 데이터 스키마 초안

```yaml
menu:            # 메뉴 1개 = 문서 1개
  id: m_003
  name_ko: 취두부 쌀국수
  name_original: ""          # 원어 표기(러시아어 등)
  aliases: [쌀국수, 취두부국수, 처우더우펀]   # 사장님 확인
  category: 면
  price: 12000
  description_ko: "..."       # 짧게, 사장님 확인
  ingredients: [취두부, 쌀국수, 고수]
  allergens: [대두]
  options:                    # 필드로 답한다
    - {name: 고수 빼기, allowed: true, extra_price: 0}
    - {name: 디카페인, allowed: false}
  available: true
  embed_text: "취두부 쌀국수 | 쌀국수 취두부국수 | 면 | 짧은 설명"
  source: owner_provided      # owner_provided | manual_transcribed
  confirmed_by_owner: true
  updated_at: 2026-11-10

store_rule:      # 가게 규칙(옵션·정책 공통)
  id: r_002
  topic: 디카페인
  text: "모든 커피 디카페인 변경 가능, +500원"
  applies_to: [m_030, m_031]
  source: owner_provided

review:          # 리뷰 1개 = 문서 1개, 개인정보 제거
  id: rv_041               # 우리가 새로 매긴 id, 원 닉네임 없음
  text_masked: "메도빅 촉촉하고 덜 달아요"
  rating: 5
  visited_month: 2026-09
  menu_ids: [m_012]        # 수집 때 별칭 사전으로 연결
  source: manual_transcribed
  collected_at: 2026-11-08

place_feature:   # 플레이스 기능 RAG
  id: f_007
  name: 리뷰 답글
  summary_ko: "..."
  how_to: "스마트플레이스 > 리뷰 > ..."
  keywords: [답글, 리뷰 관리]
  source_url: https://help.naver.com/...
```

### 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| "네이버 연구실 방식" 주장에 근거 부족 | 심사 질문에 답 못 함 | "NAVER Cloud 공식 쿡북 구조 + CLOVA 인용형 RAG"로 표현 |
| 짧은 메뉴명 오매칭("쌀국수") | 심사에서 바로 감점 | 별칭 사전 + 되묻기 규칙 + 골드셋 모호 문항 |
| 임베딩 v2 테스트 키 60 QPM, 요청당 1건 | 재색인 30분 이상 | 내용 해시 캐시(SDK ADR-04), 서비스 앱 키 신청 검토 |
| Chroma 기본 L2 거리 | 점수 기준이 틀어져 임계값이 무의미 | 생성 시 `cosine` 지정, 테스트로 확인 |
| CLOVA 리랭커에 점수 없음·생성 지연 | 전화 응답 지연, 순위 판단 불가 | 순위는 RRF로, 리랭커는 Part A 근거 확인에만 |
| pgvector 버전·VPC 접속 방법 미확인 | 일정 지연 | 생성 직후 `SELECT extversion FROM pg_extension WHERE extname='vector';` 확인. 막히면 SQLite로 회귀 |
| 사장님 확인 지연(별칭·옵션) | 옵션 질문 오답 | `confirmed_by_owner` 필드로 미확인 값은 "확인 필요"로 답 |
| 리뷰 저작권·개인정보 | 민원 | 최소 수집, 닉네임 미저장, 원문 재게시 안 함, 대회 후 삭제 |

---

## 출처

공식
- NCP Cloud DB for PostgreSQL Extension: https://guide.ncloud-docs.com/docs/clouddbforpostgresql-postgresqlextension
- NCP Search Engine Service FAQ: https://guide.ncloud-docs.com/docs/ses-faq
- NCP RAG 개요 / 사용 준비 / 서비스 생성 / 평가 생성 / 릴리스 노트: https://guide.ncloud-docs.com/docs/rag-overview , https://guide.ncloud-docs.com/docs/rag-spec , https://guide.ncloud-docs.com/docs/rag-services-create , https://guide.ncloud-docs.com/docs/rag-evaluations-create , https://guide.ncloud-docs.com/docs/rag-releasenote
- CLOVA Studio 이용량 제어: https://guide.ncloud-docs.com/docs/clovastudio-ratelimiting
- CLOVA Studio 임베딩 v2 API: https://api.ncloud-docs.com/docs/clovastudio-embeddingv2
- CLOVA Studio 리랭커 API: https://api.ncloud-docs.com/docs/clovastudio-reranker
- CLOVA Studio RAG Reasoning API: https://api.ncloud-docs.com/docs/clovastudio-ragreasoning
- NAVER Cloud 포럼 쿡북(CLOVA Studio 운영자): https://www.ncloud-forums.com/topic/307/ (2024-05-10), https://www.ncloud-forums.com/topic/429/ (2024-11-26). 조사 시점 포럼 DNS가 응답하지 않아 웹 아카이브 사본(2026-03, 2026-06 저장)으로 읽었다.
- HyperCLOVA X Technical Report: https://arxiv.org/abs/2404.01954
- HyperCLOVA X THINK Technical Report: https://arxiv.org/abs/2506.22403
- 네이버 AI 브리핑 보도(2025-05-23): https://www.navercorp.com/story/storyDetail?seq=32525
- NAVER LABS Europe BERGEN: https://github.com/naver/bergen
- Provence(arXiv 2501.16214): https://arxiv.org/abs/2501.16214
- Chroma 컬렉션 설정 / 전문 검색 / 하이브리드(Cloud): https://docs.trychroma.com/docs/collections/configure , https://docs.trychroma.com/docs/querying-collections/full-text-search , https://docs.trychroma.com/cloud/search-api/hybrid-search
- sqlite-vec: https://github.com/asg017/sqlite-vec
- LanceDB 하이브리드 검색: https://docs.lancedb.com/search/hybrid-search
- pgvector: https://github.com/pgvector/pgvector
- OpenSearch 추가 플러그인(analysis-nori): https://docs.opensearch.org/3.5/install-and-configure/additional-plugins/index/
- Kiwi(kiwipiepy): https://github.com/bab2min/kiwipiepy
- bm25s: https://github.com/xhluca/bm25s
- Ragas 지표: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ , https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/ , https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/noise_sensitivity/
- 팀 SDK 문서: /Users/imdonghyeon/Downloads/sdk.md (4장 ADR-03·04·06, 부록 B-5)

2차
- Alex Garcia, sqlite-vec + FTS5 하이브리드 검색(2024-10-02): https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html
- SPLADE 요약: https://frutik.github.io/awesome-search/Articles/SPLADE---Sparse-Bi-Encoder-BERT-Model-for-First-Stage-Ranking

미확인 목록
- NCP Cloud DB for PostgreSQL의 pgvector 버전, 월 요금, VPC 밖(노트북)에서 접속하는 방법
- NCP Search Engine Service의 k-NN·nori 기본 포함 여부(`GET _cat/plugins`로 확인)
- NCP RAG 서비스의 임베딩 모델·검색 방식·CBT 이후 요금
- pg_trgm이 NCP DB 로케일에서 한글을 trigram 대상으로 처리하는지
- 스마트플레이스 사장님 화면의 리뷰 내보내기 기능
- 네이버 검색의 하이브리드·dense 검색 공식 발표 자료
- 개인정보위 안내서 원문(2025년 후속 개정 여부 포함)
