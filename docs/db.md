# 데이터베이스 (PostgreSQL 17 + pgvector)

가게 지식·벡터·전화 주문·비서 가게 파일을 한 DB에 담는다. 코드는 `src/agenthan/db/`.

## 정한 것 (2026-11-20)

| 무엇 | 정한 값 | 이유 |
|---|---|---|
| DB | PostgreSQL 17 + pgvector 0.8.7 (`pgvector/pgvector:0.8.7-pg17`) | NCP Cloud DB for PostgreSQL과 같은 SQL. 나중에 주소만 바꿔 옮길 수 있다 |
| 위치 | 전화 서버(`agenthan-phone`) 안 Docker `db` 서비스, 밖에 포트 안 염 | 추가 요금 없음. Cloud DB는 콘솔 비밀번호 입력이 필요해 보류 |
| 가게 | 모든 표에 `store_id`. 더미 19곳 + 실제 1곳(`real`) | 레포·발표는 더미, 실제 데이터는 DB와 서버 `data/`에만 |
| 검색 | 별칭·규칙·BM25는 지금 Python 그대로, 벡터만 pgvector | 검증된 순위(Recall@1 0.800)를 지킨다 |
| 벡터 | 메뉴·리뷰·가게 정보 조각, `embeddings` 표 하나 + HNSW(cosine) | 리뷰·가게 정보 의미 검색도 된다 |
| 장애 | DB가 없거나 죽으면 경고만 남기고 JSON 파일로 동작, 30초 뒤 다시 시도 | 전화 응답이 멈추면 안 된다 |

## 켜고 끄기

| 환경변수 | 뜻 |
|---|---|
| `AGENTHAN_DB_URL` | `postgresql://agenthan:<비밀번호>@db:5432/agenthan`. 비우면 DB를 안 쓴다(테스트·로컬 기본) |
| `AGENTHAN_DB_PASSWORD` | compose의 `db` 서비스 비밀번호 (서버 `deploy/phone/.env`에만) |
| `AGENTHAN_STORE_ID` | `RagStoreKnowledge.from_db()`·`from_env()`의 기본 가게. 비우면 `dummy_store`, 실제는 `real` |

DB를 켜면 바뀌는 것:

- 전화(`agenthan.serve`): 가게 메뉴를 `stores.raw`(PHONE_STORE_FIXTURE와 같은 파일에서 넣은 것)에서 읽는다.
  확정·넘김 주문은 사장님 화면과 함께 `phone_orders`에도 남는다(별도 스레드, 실패해도 통화는 계속).
- 비서(`tools/dummy_store.py`): 대화마다 쓰는 가게 파일을 쓸 때마다 `assistant_store_docs`에 한 벌 남긴다.
  파일이 없어지면(재배포) DB 사본을 읽는다.
- Part B(`RagStoreKnowledge.from_db()`): 메뉴·묶음·리뷰를 DB에서 읽고 벡터는 pgvector에서 찾는다.
  `search_reviews()`·`search_store_info()`로 리뷰·가게 정보 의미 검색도 된다.

## Part B 검색을 쓰는 곳 (2026-11-21)

| 어디 | 무엇 |
|---|---|
| 전화 가게 지식 (`serve.phone_knowledge`) | DB에 전화 가게가 있으면 `RagStoreKnowledge.from_db(그 가게)`. 메뉴 목록은 JSON과 같고 검색만 Part B |
| 전화: 메뉴 이름을 못 알아들음 (`engine._searched_menus`) | 매처가 거절하면 검색 후보로 되묻는다("꿀 들어간 거" → 메도빅?). 매처가 애매하게(confirm) 고른 메뉴와 검색이 다르면 둘 다 묻는다("얇은 팬케이크" → 메도빅이랑 블린 중에?) |
| 전화: 손님 질문 답 (`engine._with_related_reviews`) | 질문과 가까운 후기(리뷰 의미 검색) 3개를 답변기 자료 한 줄로. 답변기 스레드에서 |
| 비서 가게 지식 (`app/pilot.py`) | 대화마다 그 가게 파일 메뉴로 `LiveShopKnowledge`(메뉴 사진 매칭). 메뉴가 바뀌면 다시 색인 |

검색이 실패하거나 DB가 없으면 예전 동작(전화 기본 가게 지식, 없는 메뉴 안내)으로 돌아간다.

## 표

| 표 | 내용 |
|---|---|
| `stores` | 가게. `kind`(real·dummy), `source_format`(rag·phone·owner·snapshot·report), `info`, `raw`(원본) |
| `menus`, `menu_families` | 메뉴·묶음. 원본 순서 `position`, 원본 한 건 `raw` |
| `reviews` | 리뷰(작성자는 마스킹된 값만) |
| `store_docs` | 가게 정보 조각(영업시간·위치·편의·리뷰 요약·문의) — 벡터 검색 대상 |
| `owner_records` | 비서 가게의 톡톡·주문·예약·통화·쿠폰·정산·통계 |
| `place_stats` | 플레이스 리포트 주간·일간 통계 |
| `embedding_cache` | 내용 해시 → 벡터(메뉴·리뷰·질문 공통). 같은 글은 CLOVA를 다시 안 부른다 |
| `embeddings` | 검색 대상 벡터 `(store_id, kind=menu·review·doc, ref_id)` + HNSW 인덱스 |
| `eval_cases` | 골드셋·holdout 채점 문항 |
| `phone_orders` | 전화 주문(확정·넘김) |
| `assistant_store_docs` | 비서 대화 가게 파일 사본 |

## 명령

```bash
# 로컬 DB (한 번)
docker run -d --name agenthan-pg -e POSTGRES_USER=agenthan -e POSTGRES_PASSWORD=devpass \
  -e POSTGRES_DB=agenthan -p 54329:5432 pgvector/pgvector:0.8.7-pg17
export AGENTHAN_DB_URL=postgresql://agenthan:devpass@localhost:54329/agenthan

uv run python -m agenthan.db.migrate                    # 스키마만
uv run python -m agenthan.db.seed                       # 스키마 + 가게 전부(+ data/clean 있으면 실제)
uv run --env-file .env python -m agenthan.db.embed      # 벡터(CLOVA 키, 캐시 우선)

# 서버 (저장소 루트, data/는 scp로 올려 둔 것)
docker compose -f deploy/phone/compose.yaml run --rm -v "$PWD/data:/app/data:ro" app python -m agenthan.db.seed
docker compose -f deploy/phone/compose.yaml run --rm -v "$PWD/data:/app/data:ro" app python -m agenthan.db.embed
docker compose -f deploy/phone/compose.yaml exec db psql -U agenthan -c "select * from phone_orders order by received_at desc limit 5"

# 테스트 (DB 있는 것은 AGENTHAN_TEST_DB_URL이 있을 때만)
AGENTHAN_TEST_DB_URL=postgresql://agenthan:devpass@localhost:54329/agenthan_test uv run pytest tests/test_db.py
```

시드는 가게마다 지우고 다시 넣는다(여러 번 돌려도 같다). 주문(`phone_orders`)·비서 사본은 시드가 건드리지 않는다.

## NCP Cloud DB로 옮기려면

콘솔에서 Cloud DB for PostgreSQL(17, Standalone, High CPU 2vCPU·4GB, 시간 요금 약 158원/시간)을 `agenthan-vpc`에
만들고 Extension 관리에서 `vector`를 켠다(사용자 비밀번호 입력은 사람이 한다). 그다음
`AGENTHAN_DB_URL`만 새 주소로 바꾸고 `seed`·`embed`를 다시 돌린다. 주문 기록은 `pg_dump -t phone_orders`로 옮긴다.
