-- AgentHan 데이터베이스 첫 스키마. PostgreSQL 17 + pgvector (NCP Cloud DB for PostgreSQL에서도 같은 SQL).
-- 가게 여러 곳을 한 DB에 담는다: 모든 가게 데이터 표는 store_id를 갖는다.
-- raw 열에는 원본 JSON 한 건을 그대로 둔다. 코드가 원본 모양 그대로 다시 읽을 수 있게(검색 결과가 JSON 때와 같게).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 가게. kind: real(실제 데이터, 커밋 금지) | dummy(레포 더미)
-- source_format: rag(menus.json 폴더) | phone(store/menu/phone JSON) | owner(shop/menus/... 비서 가게)
--                | snapshot(리뷰 분류 스냅숏) | report(플레이스 리포트)
CREATE TABLE stores (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('real', 'dummy')),
    source_format text NOT NULL,
    source_path   text NOT NULL DEFAULT '',
    info          jsonb NOT NULL DEFAULT '{}'::jsonb,   -- 가게 정보(주소·영업시간·편의·리뷰 요약 등)
    raw           jsonb,                                 -- 원본 파일 전체(phone·owner 형식은 화면·설정이 그대로 읽는다)
    loaded_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX stores_source_path ON stores (source_path);

CREATE TABLE menu_families (
    store_id       text NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    id             text NOT NULL,
    position       integer NOT NULL DEFAULT 0,             -- 원본 순서 (같은 별칭이면 앞 묶음이 이긴다)
    name_ko        text NOT NULL DEFAULT '',
    representative text NOT NULL DEFAULT '',
    members        text[] NOT NULL DEFAULT '{}',
    aliases        text[] NOT NULL DEFAULT '{}',
    popularity     jsonb,
    gift           jsonb,
    raw            jsonb NOT NULL,
    PRIMARY KEY (store_id, id)
);

CREATE TABLE menus (
    store_id    text NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    id          text NOT NULL,
    position    integer NOT NULL DEFAULT 0,            -- 원본 순서 (목록·검색 동점 순서가 JSON 때와 같게)
    name_ko     text NOT NULL,
    name_native text NOT NULL DEFAULT '',
    name_en     text NOT NULL DEFAULT '',
    price       integer,
    category    text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    aliases     text[] NOT NULL DEFAULT '{}',
    options     jsonb NOT NULL DEFAULT '[]'::jsonb,
    allergens   text[] NOT NULL DEFAULT '{}',
    sold_out    boolean NOT NULL DEFAULT false,
    family_id   text NOT NULL DEFAULT '',
    tags        text[] NOT NULL DEFAULT '{}',
    popularity  jsonb,
    extra       jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw         jsonb NOT NULL,
    PRIMARY KEY (store_id, id)
);
CREATE INDEX menus_name_trgm ON menus USING gin (name_ko gin_trgm_ops);

CREATE TABLE reviews (
    store_id    text NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    id          text NOT NULL,
    position    integer NOT NULL DEFAULT 0,
    text        text NOT NULL DEFAULT '',
    created_at  timestamptz,
    visit_date  date,
    rating      real,
    author      text NOT NULL DEFAULT '',                -- 마스킹된 닉네임만
    menu_ids    text[] NOT NULL DEFAULT '{}',
    features    jsonb,                                   -- 리뷰 분류 결과(비서 형식)
    owner_reply text,
    language    text NOT NULL DEFAULT '',
    extra       jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw         jsonb NOT NULL,
    PRIMARY KEY (store_id, id)
);
CREATE INDEX reviews_recent ON reviews (store_id, created_at DESC);
CREATE INDEX reviews_text_trgm ON reviews USING gin (text gin_trgm_ops);

-- 가게 정보 조각(영업시간·편의·리뷰 요약·리포트 통계 등). 벡터 검색 대상
CREATE TABLE store_docs (
    store_id text NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    id       text NOT NULL,
    kind     text NOT NULL,
    title    text NOT NULL DEFAULT '',
    body     text NOT NULL DEFAULT '',
    data     jsonb,
    PRIMARY KEY (store_id, id)
);

-- 비서 가게 형식(owner)의 나머지 모음: talktalk·orders·reservations·calls·coupons·settlements·statistics
CREATE TABLE owner_records (
    store_id   text NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    collection text NOT NULL,
    id         text NOT NULL,
    position   integer NOT NULL DEFAULT 0,
    data       jsonb NOT NULL,
    PRIMARY KEY (store_id, collection, id)
);

-- 플레이스 리포트 주간·일간 통계
CREATE TABLE place_stats (
    store_id text NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    period   text NOT NULL CHECK (period IN ('week', 'day')),
    label    text NOT NULL,
    data     jsonb NOT NULL,
    PRIMARY KEY (store_id, period, label)
);

-- 임베딩 원장: 내용 해시 → 벡터. 메뉴·리뷰·질문 모두 같은 표를 캐시로 쓴다(같은 글은 다시 부르지 않음)
CREATE TABLE embedding_cache (
    content_hash text PRIMARY KEY,
    model        text NOT NULL,
    embedding    vector(1024) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- 검색 대상 벡터: kind = menu | review | doc. ref_id = 메뉴·리뷰·조각 id
CREATE TABLE embeddings (
    store_id     text NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('menu', 'review', 'doc')),
    ref_id       text NOT NULL,
    model        text NOT NULL,
    content_hash text NOT NULL,
    content      text NOT NULL,
    embedding    vector(1024) NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, kind, ref_id)
);
CREATE INDEX embeddings_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops);

-- 검색 채점 문항(골드셋·holdout)
CREATE TABLE eval_cases (
    dataset      text NOT NULL,
    idx          integer NOT NULL,
    store_id     text NOT NULL DEFAULT '',
    query        text NOT NULL DEFAULT '',
    expected_ids text[] NOT NULL DEFAULT '{}',
    primary_id   text NOT NULL DEFAULT '',
    type         text NOT NULL DEFAULT '',
    channel      text NOT NULL DEFAULT '',
    data         jsonb NOT NULL,
    PRIMARY KEY (dataset, idx)
);

-- 전화 주문(확정·넘김). id = 통화 id
CREATE TABLE phone_orders (
    id             text PRIMARY KEY,
    store_id       text NOT NULL DEFAULT '',
    status         text NOT NULL,
    lines          jsonb NOT NULL,
    total          integer NOT NULL DEFAULT 0,
    pickup_time    text NOT NULL DEFAULT '',
    customer_name  text NOT NULL DEFAULT '',
    customer_phone text NOT NULL DEFAULT '',
    transcript     jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at     text NOT NULL DEFAULT '',
    received_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX phone_orders_recent ON phone_orders (received_at DESC);

-- 비서 대화마다 쓰는 가게 파일(DummyStore)의 사본. doc_key = 파일 경로
CREATE TABLE assistant_store_docs (
    doc_key    text PRIMARY KEY,
    shop_id    text NOT NULL DEFAULT '',
    data       jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
