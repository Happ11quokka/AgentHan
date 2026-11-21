"""발표 서버: 전화 주문(Part C)과 사장님 비서(Part A)를 한 앱·한 주소로 낸다.

    uv run --env-file .env python -m agenthan.serve --port 8000

- 전화 쪽(`agenthan.voice.server`)이 `/api/*`·WS·화면(`web/dist`)을 내고, 여기서 비서 API
  (`/assistant/*`, `agenthan.app.server`)를 같은 앱에 붙인다.
  화면은 주소로 갈린다(`web/src/main.tsx`).
- voice는 core 말고 다른 파트를 import하지 않는 규칙이라, 둘을 묶는 일은 이 파일이 한다.
- AGENTHAN_DB_URL이 있으면 전화 가게 메뉴를 DB에서 읽고 확정·넘김 주문과 사장님 결정(수락·거절 …)을
  phone_orders 표에 남긴다
  (`agenthan.db`). 서버가 다시 떠 허브에 카드가 없는 주문의 손님 주문 현황도 이 표에서 읽는다
  (전화 메뉴·주문 현황·쿠폰 티켓 06, `OrdersFromDb`).
  비서 가게 파일도 쓸 때마다 DB에 한 벌 남는다(tools/dummy_store.py).
- AGENTHAN_ASSISTANT=off면 비서를 붙이지 않는다(전화만). 대화마다 가게 파일을
  AGENTHAN_SESSIONS_DIR(기본 .agenthan/sessions)에 쓴다.
- 전화용 가게(전화 메뉴 티켓 02): 비서가 켜져 있으면 전화는 비서 메뉴로 주문을 받는다. 대화가
  메뉴나 쿠폰을 바꿔 저장할 때마다 그 가게가 게시되고 가장 최근에 저장한 대화가 이긴다
  (`PhoneStores`). 저장한 대화가 없으면 파일럿 가게. 전화 설정(영업시간·준비 시간·상한·가게
  이름)과 리뷰는 위의 전화 가게 것이다.
  비서가 꺼져 있으면 전화 가게(DB·PHONE_STORE_FIXTURE)를 그대로 쓴다.
  DB가 있으면 게시 값을 DB에 남겨 서버가 다시 떠도 마지막 게시 메뉴로 시작하고, 메뉴 벡터는
  pgvector에 둔다(전화 메뉴 티켓 03).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from fastapi import FastAPI

from agenthan.voice import server as voice_server
from agenthan.voice.owner.decisions import DecisionRecord, DecisionSink
from agenthan.voice.owner.order_archive import ArchivedOrder, OrderArchive
from agenthan.voice.settings import VoiceSettings
from agenthan.voice.store_check import ensure_usable
from agenthan.voice.store_snapshot import StoreSnapshot, fixed_store

log = logging.getLogger(__name__)

ASSISTANT_ENV = "AGENTHAN_ASSISTANT"
SESSIONS_ENV = "AGENTHAN_SESSIONS_DIR"


def create_app(settings: VoiceSettings | None = None) -> FastAPI:
    settings = settings or VoiceSettings.from_env()
    store_raw, sinks, decisions = phone_db(settings)
    knowledge = phone_knowledge(store_raw, settings)
    # 전화 가게: 전화 설정·리뷰의 바탕. 통화는 시작할 때 공급자에게 가게 스냅숏을 받는다
    store = voice_server.load_store(settings, store_raw=store_raw, knowledge=knowledge)
    env = settings.env  # from_env면 os.environ·.env를 합친 값
    phone_stores = None
    if assistant_on(env):
        from agenthan.app import pilot

        # 전화용 가게: 비서 메뉴. DB에 남은 마지막 게시 값, 없으면 파일럿 가게 (티켓 02·03)
        phone_stores = PhoneStores(store, db_url=env.get("AGENTHAN_DB_URL", "").strip() or None)
        phone_stores.start(pilot.seed())
    app = voice_server.create_app(
        settings,
        stores=phone_stores or fixed_store(store),
        order_sinks=sinks,
        decision_sink=decisions,  # 사장님 결정도 DB에 (주문 승인 티켓 10)
        order_archive=phone_order_archive(settings),  # 주문 현황 다시 열기 (티켓 06)
    )
    if phone_stores is not None:
        include_assistant(app, env, on_save=phone_stores.saved)
    else:
        log.info("%s=off라 사장님 비서(/assistant)는 붙이지 않음", ASSISTANT_ENV)
    return app


def phone_store_doc(data: Mapping) -> dict:
    """비서 가게 한 벌(DummyStore JSON) → 전화용 가게(게시하는 값, JSON).

    메뉴는 id·이름·가격·설명·품절과 손님이 부르는 다른 이름(별칭·원어명)만 옮긴다. 사진·AI 표시
    같은 화면 값은 빼서, 사진을 채우는 저장으로는 게시 값이 바뀌지 않는다. 비서 메뉴에는 옵션이
    없다(전화는 옵션 말을 그 줄 요청 사항으로 적는다). 메뉴는 저장한 순서 그대로다: 사장님이 메뉴
    탭에서 정한 순서가 전화가 손님에게 메뉴를 알려 주는 순서이고, 순서만 바뀌어도 게시 값이 바뀌어
    다시 게시된다.

    쿠폰(쿠폰 티켓 09)은 저장한 순서 그대로 계산에 쓰는 칸만 옮긴다(게시 상태는 보지 않는다).
    조건 없는 쿠폰은 `min_order` 키 없이 저장돼 있어 0으로 적는다(티켓 08).

    가게 이름(`store_name`)도 옮긴다. 전화 첫인사가 비서 가게 이름을 읽는다(가게 이름을 바꾸면
    전화도 바뀐다, 2026-11-22 "한양케이크"). 이름이 없으면 키를 넣지 않는다(전화 가게 이름 그대로).
    """
    name = str((data.get("shop") or {}).get("name") or "").strip()
    return {**({"store_name": name} if name else {}), "menus": [
        {"id": str(m["id"]), "name": m["name"], "price": m["price"],
         "description": m.get("description") or "", "aliases": list(m.get("aliases") or ()),
         "name_native": m.get("name_native") or "", "sold_out": bool(m.get("sold_out"))}
        for m in data.get("menus") or ()
    ], "coupons": [
        {"id": str(c.get("id") or ""), "title": c.get("title") or "",
         "discount_type": c.get("discount_type"), "discount_value": c.get("discount_value"),
         "min_order": c.get("min_order") or 0, "start_date": c.get("start_date"),
         "end_date": c.get("end_date")}
        for c in data.get("coupons") or ()
    ]}


class PhoneStores:
    """전화용 가게 게시판 (전화 메뉴 티켓 02). 전화가 읽는 가게는 하나이고 가장 최근 게시가 이긴다.

        stores = PhoneStores(base)        # base: 전화 가게 스냅숏(전화 설정·리뷰)
        stores.start(pilot.seed())        # DB에 남은 마지막 게시 값, 없으면 이 가게
        stores.saved(chat, data)          # 대화의 저장: 메뉴·쿠폰이 바뀌었으면 게시
        stores.publish(data)              # 비서 가게 한 벌 → 게시
        stores()                          # 지금 스냅숏 (`StoreProvider`: 통화 시작·사장님 허브)

    - 대화의 저장(`saved`)은 그 대화의 메뉴·쿠폰(게시 값)이 바뀌었을 때만 게시한다(리뷰). 리뷰
      답글·설정 같은 다른 저장이 그 대화의 옛 메뉴(파일럿 가게 그대로인 다른 대화 등)로 전화를
      되돌리지 않게. 대화는 파일럿 가게(`start`의 `default`)로 시작하므로 그 값이 첫 비교 대상이다
    - `publish`는 저장한 스레드에서 부른다. 게시 값(`phone_store_doc`)으로 검색 색인까지 만든 뒤
      스냅숏을 통째로 바꿔 끼운다. 같은 게시 값은 다시 만들지 않는다(내용 해시). 진행 중인 통화는
      시작할 때 받은 스냅숏을 끝까지 쓴다
    - 메뉴가 빈 가게는 게시하지 않는다(메뉴판 사진 시연의 빈 가게가 전화를 비우지 않게)
    - 전화 설정은 `base` 그대로다. 꼭 고를 것(`required_options`)은 비서 메뉴에 없으므로 비운다
    - DB(`db_url`)가 있으면 (티켓 03) 게시 값을 DB에 남기고(`db.stores`), 메뉴 벡터는 pgvector
      (`PgVectorIndex`)에 둔다. 메뉴 한 벌마다 벡터 자리가 따로다(`published_vector_store_id`):
      진행 중인 통화의 옛 메뉴가 늦게 첫 검색을 해도 새 메뉴 벡터를 건드리지 않는다(리뷰). 새 벌은
      첫 검색 때 임베딩 캐시 표에 없는 글만 임베딩한다. 들고 있는 스냅숏(`KEEP`)에 없는 벌의
      자리는 게시할 때 지운다. DB가 없으면 벡터 없이 이름·별칭·오타 매칭만
    - 게시 값에는 비서 쿠폰도 실린다(쿠폰 티켓 09). 쿠폰만 바뀐 게시는 검색 색인을 다시 만들지 않고
      스냅숏의 쿠폰만 바꾼다. DB에 남긴 게시 값도 쿠폰을 들고 있어 다시 떠도 쿠폰이 그대로다
      (쿠폰 전에 남긴 게시 값은 쿠폰 없음)
    """

    KEEP = 8  # 만들어 둔 스냅숏을 이만큼 들고 있다(두 대화를 오갈 때 다시 만들지 않게)

    def __init__(
        self, base: StoreSnapshot, *, db_url: str | None = None, embed=None
    ) -> None:
        """embed: 임베딩 함수(테스트용). None이면 CLOVASTUDIO_API_KEY가 있을 때 CLOVA,
        False면 끔."""
        self._config = replace(base.config, required_options={})
        self._reviews = tuple(base.store.recent_reviews(limit=1_000_000))
        self._db_url = db_url
        self._embed = embed
        self._current = base
        self._doc: dict | None = None
        self._saved: str | None = None  # DB에 남긴 게시 값의 내용 해시
        self._built: OrderedDict[str, StoreSnapshot] = OrderedDict()
        self._built_docs: dict[str, dict] = {}  # 만들어 둔 스냅숏의 게시 값 (벡터 자리 정리)
        self._lock = threading.Lock()  # 게시는 한 번에 하나. 늦게 들어온 게시가 이긴다
        self._fresh: str | None = None  # 새 대화가 시작하는 가게(파일럿)의 게시 값 해시
        self._chats: dict[str, str] = {}  # 대화마다 마지막으로 저장한 게시 값 해시

    def __call__(self) -> StoreSnapshot:
        return self._current  # 이벤트 루프에서 부른다: 만들어 둔 것만 돌려준다

    @property
    def doc(self) -> dict | None:
        """지금 게시된 값. 아직 없으면 None(전화 가게 그대로)."""
        return self._doc

    def start(self, default: Mapping) -> None:
        """서버를 띄울 때 한 번. DB에 남은 마지막 게시 값으로, 없으면(DB가 없거나 아직 게시가
        없으면) 비서 가게 한 벌 `default`(파일럿 가게)로 시작한다. 기본 가게는 DB에 쓰지 않는다:
        게시가 없을 때의 기본값일 뿐이라 다음 배포의 기본 가게가 그대로 들어온다."""
        self._fresh = _content_key(phone_store_doc(default))
        saved = None
        if self._db_url:
            from agenthan.db.stores import load_published_phone_store

            saved = load_published_phone_store(self._db_url)
        if saved and "store_name" not in saved and (name := phone_store_doc(default).get(
                "store_name")):
            saved = {"store_name": name, **saved}  # 이름 전의 게시 값: 파일럿 가게 이름으로
        if saved and self.put(saved, save=False):
            self._saved = _content_key(saved)
            log.info("전화용 가게: DB에 남은 마지막 게시 값으로 시작 (메뉴 %d개)",
                     len(saved["menus"]))
            return
        self.put(phone_store_doc(default), save=False)

    def saved(self, chat: str, data: Mapping) -> bool:
        """대화 `chat`이 가게 파일을 저장했다. 그 대화의 메뉴·쿠폰이 지난 저장(처음이면 대화가
        시작한 파일럿 가게)과 같으면 게시하지 않고 False. 다르면 `put`."""
        doc = phone_store_doc(data)
        key = _content_key(doc)
        with self._lock:
            last = self._chats.get(chat, self._fresh)
            self._chats[chat] = key
        if key == last:
            return False  # 리뷰 답글·설정 같은 메뉴·쿠폰 밖 저장
        return self.put(doc)

    def publish(self, data: Mapping) -> bool:
        """비서 가게 한 벌을 전화용 가게로 게시한다. 메뉴가 비었으면 게시하지 않고 False."""
        return self.put(phone_store_doc(data))

    def put(self, doc: dict, *, save: bool = True) -> bool:
        """게시 값을 그대로 게시한다. 메뉴가 비었으면 게시하지 않고 False.
        save: DB가 있으면 게시 값을 남긴다(실패해도 게시는 된다. 다음 게시 때 다시 남긴다)."""
        if not doc.get("menus"):
            log.info("전화용 가게: 메뉴가 빈 가게라 게시하지 않음 (지금 가게 그대로)")
            return False
        key = _content_key(doc)
        with self._lock:
            snapshot = self._built.pop(key, None) or self._build(doc)
            self._built[key] = snapshot
            self._built_docs[key] = doc
            while len(self._built) > self.KEEP:
                old, _ = self._built.popitem(last=False)
                self._built_docs.pop(old, None)
            changed = snapshot is not self._current
            self._current, self._doc = snapshot, doc
            if changed and self._db_url:
                from agenthan.db.stores import prune_published_vector_stores

                # 들고 있지 않은 메뉴 벌의 벡터 자리를 지운다(다시 뜬 서버면 지난 실행의 것도)
                prune_published_vector_stores(
                    {_vector_store(d) for d in self._built_docs.values()}, self._db_url)
            if save and self._db_url and key != self._saved:
                from agenthan.db.stores import save_published_phone_store

                # 잠근 채로 남긴다: 늦게 들어온 게시가 DB에서도 이긴다
                if save_published_phone_store(doc, self._db_url):
                    self._saved = key
        if changed:
            log.info("전화용 가게 게시: 메뉴 %d개", len(doc["menus"]))
        return True

    def _build(self, doc: dict) -> StoreSnapshot:
        from agenthan.rag import RagStoreKnowledge

        coupons = _coupons(doc)
        config = self._config
        if doc.get("store_name"):
            config = replace(config, store_name=doc["store_name"])
        if self._doc is not None and self._doc.get("menus") == doc["menus"]:
            # 쿠폰·이름만 바뀐 게시: 검색 색인은 그대로 쓴다 (쿠폰 티켓 09)
            return replace(self._current, coupons=coupons, config=config)
        # DB가 없으면 벡터 없이 (이름·별칭·오타 매칭)
        knowledge = RagStoreKnowledge(_PublishedMenus(doc, self._reviews), embed=False)
        if self._db_url:
            from agenthan.db.stores import ensure_published_vector_store
            from agenthan.rag.vector import PgVectorIndex

            # 메뉴 벡터는 pgvector에, 이 메뉴 한 벌의 자리에. 첫 검색 때 임베딩 캐시 표에 없는 글만
            # 임베딩한다. 자리를 못 만들면 벡터 검색이 쉬고 이름·별칭·오타 매칭으로 간다
            vector_store = _vector_store(doc)
            ensure_published_vector_store(vector_store, self._db_url)
            knowledge.vector = PgVectorIndex(
                knowledge.vector.docs, vector_store, self._embed, self._db_url)
        ensure_usable(knowledge.list_menu(), config, log)  # 경고는 로그에
        return StoreSnapshot.of(knowledge, config, coupons)


def _coupons(doc: dict) -> tuple:
    """게시 값의 쿠폰 → 전화 쿠폰(`Coupon`, 쿠폰 티켓 09). 읽을 수 없는 쿠폰은 로그를 남기고
    뺀다. 쿠폰 전에 저장된 게시 값(키 없음)은 쿠폰 없음."""
    from agenthan.voice.dialog.coupons import Coupon

    coupons = []
    for raw in doc.get("coupons") or ():
        coupon = Coupon.from_json(raw)
        if coupon is None:
            log.warning("전화용 가게: 읽을 수 없는 쿠폰은 뺌 %s", raw)
        else:
            coupons.append(coupon)
    return tuple(coupons)


def _content_key(doc: dict) -> str:
    return hashlib.sha1(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _vector_store(doc: dict) -> str:
    """게시 값의 메뉴 한 벌 → 그 벡터 자리 id. 쿠폰만 다른 게시는 같은 자리를 쓴다."""
    from agenthan.db.stores import published_vector_store_id

    return published_vector_store_id(_content_key({"menus": doc["menus"]}))


class _PublishedMenus:
    """게시 값의 메뉴 + 전화 가게의 리뷰 → Part B 검색 데이터 (`rag` 저장소 모양)."""

    def __init__(self, doc: dict, reviews: Sequence) -> None:
        self._doc = doc
        self._reviews = reviews

    def load(self):  # -> rag.repository.StoreData
        from agenthan.rag.repository import ShopDataRepository

        data = ShopDataRepository({"menus": self._doc["menus"]}).load()
        data.reviews = list(self._reviews)
        return data


def phone_db(settings: VoiceSettings) -> tuple[dict | None, list, DecisionSink | None]:
    """AGENTHAN_DB_URL이 있으면 전화 가게를 DB(stores.raw)에서 읽고, 주문과 사장님 결정을
    phone_orders에 남긴다. (가게 raw, 주문 받는 곳들, 결정 받는 곳). DB가 없으면 (None, [], None).
    DB가 없거나 그 가게가 DB에 없으면 지금처럼 PHONE_STORE_FIXTURE 파일을 읽는다."""
    from agenthan import db

    url = settings.env.get(db.DB_URL_ENV, "").strip()
    if not url:
        return None, [], None
    from agenthan.core.fakes import SAMPLE_STORE
    from agenthan.db.orders import PgOrderSink
    from agenthan.db.stores import phone_store_raw

    fixture = settings.store_fixture or SAMPLE_STORE
    raw = phone_store_raw(fixture, url)
    store_id = str(((raw or {}).get("store") or {}).get("id") or "")
    log.info("DB 켬: 전화 가게 %s, 주문은 phone_orders에도 남김",
             f"DB({store_id})" if raw else f"파일({fixture.name}, DB에 없음)")
    sink = PgOrderSink(store_id=store_id, url=url)
    return raw, [sink], DecisionsToDb(sink)


class DecisionsToDb:
    """전화 허브의 결정 기록(voice `DecisionRecord`) → DB 행(db `OwnerDecisionRow`).

    두 파트는 서로를 import하지 않는다(PRD §7). 둘을 잇는 일은 이 파일이 한다. 받는 곳은
    `record_decision`을 가진 것(운영은 `PgOrderSink`, 주문과 같은 큐로 넣은 순서대로 쓴다).
    """

    def __init__(self, sink) -> None:
        self._sink = sink

    def record(self, decision: DecisionRecord) -> None:
        from agenthan.db.orders import OwnerDecisionRow

        self._sink.record_decision(OwnerDecisionRow(
            order_id=decision.order_id, owner_status=decision.owner_status,
            reject_reason=decision.reject_reason, decided_at=decision.decided_at,
            owner_pickup_time=decision.owner_pickup_time,
            reject_menu_ids=decision.reject_menu_ids, reject_text=decision.reject_text,
            reject_text_ko=decision.reject_text_ko, order=decision.order))


def phone_order_archive(settings: VoiceSettings) -> OrderArchive | None:
    """AGENTHAN_DB_URL이 있으면 phone_orders를 읽는 주문 기록, 없으면 None(허브만)."""
    from agenthan import db

    url = settings.env.get(db.DB_URL_ENV, "").strip()
    return OrdersFromDb(url) if url else None


class OrdersFromDb:
    """전화 주문 기록 읽기: DB 행(db `PhoneOrderRow`) → voice `ArchivedOrder` (티켓 06).

    `DecisionsToDb`의 거꾸로다. 두 파트는 서로를 import하지 않는다(PRD §7). 동기 DB 읽기라 허브가
    루프 밖 스레드에서 부른다. DB가 꺼져 있거나 실패하면 None(손님 화면은 "찾을 수 없어요").
    """

    def __init__(self, url: str) -> None:
        self._url = url

    def load(self, order_id: str) -> ArchivedOrder | None:
        from agenthan.core.contracts import Order, OrderLine
        from agenthan.db.orders import find_order

        row = find_order(order_id, self._url)
        if row is None:
            return None
        lines = [
            OrderLine(menu_id=str(line.get("menu_id", "")), quantity=int(line.get("quantity", 0)),
                      options=tuple(line.get("options") or ()),
                      request=str(line.get("request") or ""), name=str(line.get("name") or ""),
                      unit_price=int(line.get("unit_price") or 0))
            for line in row.lines
        ]
        status = row.status if row.status in ("draft", "confirmed", "handoff") else "draft"
        return ArchivedOrder(
            order=Order(id=row.id, lines=lines, pickup_time=row.pickup_time, status=status,
                        created_at=row.created_at, coupon_id=row.coupon_id,
                        coupon_name=row.coupon_name, discount=row.discount),
            owner_status=row.owner_status, reject_reason=row.reject_reason,
            reject_menu_ids=row.reject_menu_ids, reject_text=row.reject_text,
            reject_text_ko=row.reject_text_ko, decided_at=row.decided_at,
            owner_pickup_time=row.owner_pickup_time)


def phone_knowledge(store_raw: dict | None, settings: VoiceSettings):
    """전화 가게를 Part B 검색(RagStoreKnowledge)으로 연다.

    DB에 그 가게가 있으면 DB(벡터는 pgvector, 리뷰 의미 검색 포함), 없으면 None(전화 기본 가게
    지식). 메뉴 목록은 가게 JSON과 같다."""
    store_id = str(((store_raw or {}).get("store") or {}).get("id") or "")
    if not store_id:
        return None
    from agenthan.rag import RagStoreKnowledge

    url = settings.env.get("AGENTHAN_DB_URL", "").strip() or None
    try:
        knowledge = RagStoreKnowledge.from_db(store_id, url=url)
    except Exception as error:
        log.warning("전화 가게 지식을 Part B로 못 열어 기본으로: %s", type(error).__name__)
        return None
    log.info("전화 가게 지식: Part B 검색 (DB %s, 벡터 %s)", store_id,
             "켬" if knowledge.vector.enabled else "끔(CLOVASTUDIO_API_KEY 없음)")
    return knowledge


def assistant_on(env: Mapping[str, str]) -> bool:
    return env.get(ASSISTANT_ENV, "on").strip().lower() != "off"


def include_assistant(
    app: FastAPI, env: Mapping[str, str], *, on_save: Callable[[str, dict], None] | None = None
) -> None:
    """on_save: 대화가 가게 파일을 저장할 때마다 (대화 id, 그 가게 한 벌)로 부른다(전화용 가게
    게시)."""
    from agenthan.app import pilot
    from agenthan.app.server import DEFAULT_ROOT
    from agenthan.app.server import create_app as create_assistant_app
    from agenthan.core.embedding import embed_text

    root = env.get(SESSIONS_ENV, "").strip() or DEFAULT_ROOT
    assistant = create_assistant_app(
        make_assistant=pilot.assistant, root=root, illustration_embed=embed_text, on_save=on_save
    )
    app.include_router(assistant.router)
    log.info("사장님 비서(/assistant) 붙임, 대화 파일은 %s", root)
    # 메뉴판 사진의 흰 배경 사진(rembg)을 미리 읽는다. 첫 사진이 import·모델 읽기를 떠안으면
    # 메뉴 사진 칸이 모두 묶여 한꺼번에 끝난다(image_studio.warm). rembg를 안 켰으면
    # 아무것도 하지 않는다.
    from agenthan.tools import image_studio

    image_studio.warm_in_background()


def main(argv: Sequence[str] | None = None) -> None:
    voice_server.main(argv, factory="agenthan.serve:create_app")


if __name__ == "__main__":
    main()
