# 데이터베이스 (PostgreSQL 17 + pgvector)

가게 지식·벡터·전화 주문·비서 가게 파일을 한 DB에 담는다. 코드는 `src/agenthan/db/`.

## 정한 것 (2026-11-20)

| 무엇 | 정한 값 | 이유 |
|---|---|---|
| DB | PostgreSQL 17 + pgvector (로컬·예비 `pgvector/pgvector:0.8.7-pg17`, Cloud DB 0.8.0) | 어디서나 같은 SQL. 주소만 바꿔 옮긴다 |
| 위치 (2026-11-21~) | **NCP Cloud DB for PostgreSQL** `agenthan-db` (고가용성: Primary + Secondary, 자동 Failover) | 자동 백업(7일)·모니터링, 앱 서버 CPU와 분리. 아래 "NCP Cloud DB" |
| 예비 | 전화 서버 안 Docker `db` 서비스(11/21 전환 시점 데이터 그대로), 밖에 포트 안 염 | Cloud DB가 안 되면 주소만 되돌린다. 발표 뒤 정리 |
| 가게 | 모든 표에 `store_id`. 더미 19곳 + 실제 1곳(`real`) | 레포·발표는 더미, 실제 데이터는 DB와 서버 `data/`에만 |
| 검색 | 별칭·규칙·BM25는 지금 Python 그대로, 벡터만 pgvector | 검증된 순위(Recall@1 0.800)를 지킨다 |
| 벡터 | 메뉴·리뷰·가게 정보 조각, `embeddings` 표 하나 + HNSW(cosine) | 리뷰·가게 정보 의미 검색도 된다 |
| 장애 | DB가 없거나 죽으면 경고만 남기고 JSON 파일로 동작, 30초 뒤 다시 시도 | 전화 응답이 멈추면 안 된다 |

## 켜고 끄기

| 환경변수 | 뜻 |
|---|---|
| `AGENTHAN_DB_URL` | 서버: `postgresql://agenthan:<URL 인코딩한 비밀번호>@pg-4c803s.vpc-cdb-kr.ntruss.com:5432/agenthan` (Cloud DB). 예비 DB면 호스트가 `db`. 비우면 DB를 안 쓴다(테스트·로컬 기본) |
| `AGENTHAN_DB_PASSWORD` | compose의 `db` 서비스(예비) 비밀번호 (서버 `deploy/phone/.env`에만). Cloud DB 비밀번호는 로컬 `private/team-deploy/clouddb_password.txt` |
| `AGENTHAN_STORE_ID` | `RagStoreKnowledge.from_db()`·`from_env()`의 기본 가게. 비우면 `dummy_store`, 실제는 `real` |

DB를 켜면 바뀌는 것:

- 전화(`agenthan.serve`): 비서가 켜져 있으면(기본) 전화 메뉴는 비서가 게시한 전화용 가게다(전화 메뉴 티켓 02·03).
  게시할 때마다 게시 값(`serve.phone_store_doc`, 메뉴 id·이름·가격·설명·별칭·원어명·품절 JSON)을
  `stores`의 `phone_published` 줄 `raw`에 남기고, 서버가 다시 뜨면 비서 대화를 열지 않아도 이 값으로
  시작한다. 남은 값이 없으면 파일럿 가게(기본값, DB에 쓰지 않음). 메뉴 벡터는 `embeddings`
  (`store_id = phone_published`)에 두고, 게시가 바뀌면 다음 검색 때 바뀐 메뉴만 임베딩하고 없어진
  메뉴는 지운다. 시드(`db.seed`)와 벡터 채우기(`db.embed`)는 이 줄과 그 메뉴 벡터를 건드리지 않는다.
  전화 설정(영업시간·준비 시간·상한·가게 이름)과 리뷰는 지금처럼 `stores.raw`(PHONE_STORE_FIXTURE와
  같은 파일에서 넣은 것)에서 읽는다. 비서를 끄면(`AGENTHAN_ASSISTANT=off`) 메뉴도 그 가게 것이다.
  확정·넘김 주문은 사장님 화면과 함께 `phone_orders`에도 남는다(별도 스레드, 실패해도 통화는 계속).
  사장님 결정(수락·늦춘 수락·거절·자동 취소)도 같은 행의 결정 열에 남는다(`agenthan.serve`의
  `DecisionsToDb` → `PgOrderSink.record_decision`, ADR-0009).
- 비서(`tools/dummy_store.py`): 대화마다 쓰는 가게 파일을 쓸 때마다 `assistant_store_docs`에 한 벌 남긴다.
  파일이 없어지면(재배포) DB 사본을 읽는다.
- Part B(`RagStoreKnowledge.from_db()`): 메뉴·묶음·리뷰를 DB에서 읽고 벡터는 pgvector에서 찾는다.
  `search_reviews()`·`search_store_info()`로 리뷰·가게 정보 의미 검색도 된다.

## Part B 검색을 쓰는 곳 (2026-11-21)

| 어디 | 무엇 |
|---|---|
| 전화 가게 지식 (`serve.phone_knowledge`) | DB에 전화 가게가 있으면 `RagStoreKnowledge.from_db(그 가게)`. 메뉴 목록은 JSON과 같고 검색만 Part B |
| 전화용 가게 지식 (`serve.PhoneStores`, 비서 켬) | 게시 값 메뉴로 `RagStoreKnowledge`, 벡터는 `PgVectorIndex(phone_published)`. DB가 없으면 벡터 없이 |
| 전화: 메뉴 이름을 못 알아들음 (`engine._searched_menus`) | 매처가 거절하면 검색 후보로 되묻는다("꿀 들어간 거" → 메도빅?). 매처가 애매하게(confirm) 고른 메뉴와 검색이 다르면 둘 다 묻는다("얇은 팬케이크" → 메도빅이랑 블린 중에?) |
| 전화: 손님 질문 답 (`engine._with_related_reviews`) | 질문과 가까운 후기(리뷰 의미 검색) 3개를 답변기 자료 한 줄로. 답변기 스레드에서 |
| 비서 가게 지식 (`app/pilot.py`) | 대화마다 그 가게 파일 메뉴로 `LiveShopKnowledge`(메뉴 사진 매칭). 메뉴가 바뀌면 다시 색인 |

검색이 실패하거나 DB가 없으면 예전 동작(전화 기본 가게 지식, 없는 메뉴 안내)으로 돌아간다.

## 표

| 표 | 내용 |
|---|---|
| `stores` | 가게. `kind`(real·dummy), `source_format`(rag·phone·owner·snapshot·report·published), `info`, `raw`(원본). `published`는 게시된 전화용 가게 한 줄(`id = phone_published`, kind real, `raw` = 게시 값, 아직 게시가 없으면 NULL). 서버가 띄울 때 없으면 만든다 |
| `menus`, `menu_families` | 메뉴·묶음. 원본 순서 `position`, 원본 한 건 `raw` |
| `reviews` | 리뷰(작성자는 마스킹된 값만) |
| `store_docs` | 가게 정보 조각(영업시간·위치·편의·리뷰 요약·문의) — 벡터 검색 대상 |
| `owner_records` | 비서 가게의 톡톡·주문·예약·통화·쿠폰·정산·통계 |
| `place_stats` | 플레이스 리포트 주간·일간 통계 |
| `embedding_cache` | 내용 해시 → 벡터(메뉴·리뷰·질문 공통). 같은 글은 CLOVA를 다시 안 부른다 |
| `embeddings` | 검색 대상 벡터 `(store_id, kind=menu·review·doc, ref_id)` + HNSW 인덱스 |
| `eval_cases` | 골드셋·holdout 채점 문항 |
| `phone_orders` | 전화 주문(확정·넘김)과 사장님 결정(마이그레이션 002): `owner_status`(accepted·auto_accepted·rejected·expired·cancelled, 결정 전 ''), `reject_reason`(out_of_stock·busy·closed·free, 없으면 ''), `decided_at`(timestamptz, 없으면 NULL), `owner_pickup_time`(늦춘 수락의 바뀐 픽업 시각 ISO, 없으면 ''). 결정이 주문 기록보다 먼저 오면 `status` draft 행으로 만든다 |
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
# (seed·embed는 .env의 AGENTHAN_DB_URL = 지금 Cloud DB로 간다. 조회는 아래 "NCP Cloud DB")

# 테스트 (DB 있는 것은 AGENTHAN_TEST_DB_URL이 있을 때만)
AGENTHAN_TEST_DB_URL=postgresql://agenthan:devpass@localhost:54329/agenthan_test uv run pytest tests/test_db.py
```

시드는 가게마다 지우고 다시 넣는다(여러 번 돌려도 같다). 주문(`phone_orders`)·비서 사본은 시드가 건드리지 않는다.

## NCP Cloud DB (2026-11-21 전환)

| 무엇 | 값 |
|---|---|
| 서비스 | `agenthan-db` = `agenthan-pg-001`(Primary) + `agenthan-pg-002`(Secondary), PostgreSQL 17.11, High CPU 2vCPU·4GB ×2, 시간 요금 |
| 주소 | Private 도메인 `pg-4c803s.vpc-cdb-kr.ntruss.com:5432` (Failover 때 도메인이 새 Primary를 가리킨다. IP를 적지 않는다) |
| 네트워크 | `agenthan-vpc` / `agenthan-public`, Public 도메인 없음. 접근 제어 CIDR `10.0.1.0/24` |
| ACG | 자동 생성 `cloud-postgresql-2fk2gp`에 Inbound TCP 5432 ← `10.0.1.6/32`(전화 서버) 한 줄을 추가했다 |
| 확장 | `pg_trgm`은 SQL로, `vector`는 콘솔 상세보기 → Extension 관리 → pgvector 설치(서버 재시작·Failover가 한 번 일어난다). `vector`는 `cdb_admin` 스키마에 깔려서 `ALTER ROLE agenthan SET search_path = "$user", public, cdb_admin`을 해 두었다 |
| 백업 | 매일 자동, 7일 보관 (콘솔 Backup) |

옮긴 방법 (다시 할 때도 같다):

1. 빈 Cloud DB에 `python -m agenthan.db.migrate` (컨테이너에 `-e AGENTHAN_DB_URL=<Cloud DB 주소>`).
2. 예전 DB에서 `pg_dump -Fc --no-owner --no-acl` → `pg_restore --data-only --single-transaction -L <목록>`.
   목록은 `TABLE DATA`만, `schema_migrations` 빼고, 외래 키 순서
   (stores → menu_families → menus → reviews → store_docs → owner_records → place_stats → embedding_cache →
   embeddings → eval_cases → phone_orders → assistant_store_docs). 시퀀스는 없다.
3. 표마다 행 수 비교 → 서버 `deploy/phone/.env`의 `AGENTHAN_DB_URL`만 바꾸고 `docker compose up -d --force-recreate --no-deps app`.

되돌리기: 서버 `/root/.env.before-clouddb`(전환 전 `.env`)의 `AGENTHAN_DB_URL`로 바꾸고 app만 다시 띄운다.
예비 DB에는 전환 뒤 주문·비서 사본이 없으니, 필요하면 위 2번을 반대 방향으로 한다.

서버에서 Cloud DB를 psql로 볼 때 (예비 db 컨테이너의 psql을 빌려 쓴다):

```bash
docker compose -f deploy/phone/compose.yaml exec db psql "$(grep ^AGENTHAN_DB_URL= deploy/phone/.env | cut -d= -f2-)" \
  -c "select * from phone_orders order by received_at desc limit 5"
```
