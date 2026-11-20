# Part B · 가게 지식 (`rag/`)

- 담당: 정민서
- 하는 일: 가게 1곳의 메뉴·리뷰를 `core.contracts.StoreKnowledge`로 제공한다. 비서(A)와 전화 주문(C)이 같이 쓴다.
- 다른 파트는 `agenthan.rag` 내부 모듈을 import하지 말고 `StoreKnowledge` 계약만 쓴다.
- 실제 데이터와 수집 코드는 `data/`·`private/`에만 둔다 (커밋 금지). 레포·발표는 `fixtures/dummy_store/` 더미만.

## 쓰는 법

```python
from agenthan.rag import RagStoreKnowledge

kb = RagStoreKnowledge.from_dir()      # AGENTHAN_DATA_DIR 폴더, 없으면 fixtures/dummy_store (더미)
kb = RagStoreKnowledge.from_fixture()  # 테스트·데모: 더미, 벡터 끔 (네트워크 없이 항상 같은 결과)
kb = RagStoreKnowledge.from_db("real")  # PostgreSQL(AGENTHAN_DB_URL)의 가게, 벡터는 pgvector (docs/db.md)
kb = RagStoreKnowledge.from_env()       # AGENTHAN_DB_URL이 있으면 DB, 못 읽으면 JSON 폴더
kb.search_reviews("양 많다는 얘기 있어?")  # DB일 때만: 리뷰 의미 검색 → [(Review, 점수)]

kb.search_menu("꿀케이크 있어요?", k=5)  # → list[MenuHit] (점수 높은 순, 근거 없으면 [])
kb.get_menu("napoleon")                 # → MenuItem | None
kb.list_menu()                          # → list[MenuItem]
kb.recent_reviews(limit=10, since=None) # → list[Review] (최신순, 작성자 마스킹됨)
```

| 환경변수 | 뜻 |
|---|---|
| `AGENTHAN_DATA_DIR` | 데이터 폴더. 비우면 더미, `data/clean`이면 실제 데이터 |
| `AGENTHAN_DB_URL` | 있으면 `from_db`·`from_env`가 DB를 읽는다. 가게는 `AGENTHAN_STORE_ID`(기본 dummy_store) |
| `CLOVASTUDIO_API_KEY` | 있으면 CLOVA 임베딩 v2 벡터 검색을 켠다. 없거나 실패하면 별칭·BM25만으로 동작 |

라이브러리는 `.env`를 직접 읽지 않는다. 앱·스크립트가 환경변수를 넣어 준다
(`scripts/`는 `agenthan.rag.env.load_env_file()`을 쓴다).

## 검색 동작 (`search_menu`)

순서: 정확 일치(메뉴명·원어명·별칭) → 묶음 일치 → 답할 수 없는 질문 → 의도 규칙 → 태그 → 종류 → 오타 허용 → BM25 + 벡터(RRF).
키워드·기준값은 전부 `rules.py` 한 곳에 있다.

- **품절은 거르지 않는다.** `MenuItem.sold_out`, `options`(ICE ONLY 등)를 그대로 담아 준다. 답할 때 표시만 하면 된다.
- **묶음 질문은 묶음 전체.** "메도빅", "꿀케이크", "Медовик" → 메도빅 4종, 대표(오리지널)가 1등. "초코 메도빅"처럼 구체적이면 그 메뉴만.
- **여러 메뉴 주문**("메도빅 하나랑 아메리카노 두 잔")은 말한 순서대로 각 메뉴를 앞에 둔다. 말 바꾸기("나폴레옹, 아 아니 초코 메도빅")는 뒤쪽만.
- **빈 목록이 정답인 질문**: 없는 메뉴(티라미수), 데이터에 없는 정보·서비스(견과류·비건·초·레터링), 가게 정보(몇 시·주차).
  빈 목록이면 "모른다"로 답하거나 사장님께 넘긴다.
- **목록 거르기**(네이버에 안 올라간 메뉴, 가격대별 전체 등)는 검색이 아니라 `list_menu()`로 받아서 거른다.
- `matched_by`: 메뉴명·원어명 `exact`, 별칭·묶음·오타 `alias`, 규칙·종류·태그·BM25 `keyword`, 벡터만 `vector`, 둘 다 `hybrid`.

## 데이터 형식

`menus.json`·`reviews.json` 각 항목 = 계약의 `MenuItem`/`Review` + `extra`.
검색이 쓰는 `extra`: `family`(묶음), `tags`(러시아 전통·홍차), `popularity`(리뷰 언급 수·Best 표시), `gift`(선물 언급), `taste`(단맛 라벨).
`families.json`은 묶음(대표 메뉴·멤버·묶음 별칭).

## 스크립트

```bash
uv run python scripts/eval_rag.py                    # 더미 골드셋·holdout 채점 (Recall@1·@3, MRR)
uv run python scripts/eval_rag.py --data data/clean  # 실제 데이터
uv run python scripts/eval_rag.py --vector           # 벡터 켜고 채점 (.env 키)
uv run python scripts/build_embeddings.py --data data/clean fixtures/dummy_store  # 데모 전 임베딩 캐시
```

임베딩 캐시는 `data/cache/<폴더 이름>_embeddings.json` (커밋 안 됨). CLOVA 키 하나당 1분에 약 60회 제한이 있어 데모 전에 미리 만든다.

## 정확도 (처음 보는 질문 holdout2, 2026-11-16)

Recall@1: 실제 0.800, 더미 0.800. 자세한 표는 `fixtures/dummy_store/README.md`.
