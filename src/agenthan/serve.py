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
- 전화용 가게(전화 메뉴 티켓 02): 비서가 켜져 있으면 전화는 비서 메뉴로 주문을 받는다. 대화가 가게를
  저장할 때마다 그 가게가 게시되고 가장 최근에 저장한 대화가 이긴다(`PhoneStores`). 저장한 대화가
  없으면 파일럿 가게. 전화 설정(영업시간·준비 시간·상한·가게 이름)과 리뷰는 위의 전화 가게 것이다.
  비서가 꺼져 있으면 전화 가게(DB·PHONE_STORE_FIXTURE)를 그대로 쓴다.
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

        # 전화용 가게: 비서 메뉴. 저장한 대화가 없으면 파일럿 가게 (전화 메뉴 티켓 02)
        phone_stores = PhoneStores(store, db_url=env.get("AGENTHAN_DB_URL", "").strip() or None)
        phone_stores.publish(pilot.seed())
    app = voice_server.create_app(
        settings,
        stores=phone_stores or fixed_store(store),
        order_sinks=sinks,
        decision_sink=decisions,  # 사장님 결정도 DB에 (주문 승인 티켓 10)
        order_archive=phone_order_archive(settings),  # 주문 현황 다시 열기 (티켓 06)
    )
    if phone_stores is not None:
        include_assistant(app, env, on_save=phone_stores.publish)
    else:
        log.info("%s=off라 사장님 비서(/assistant)는 붙이지 않음", ASSISTANT_ENV)
    return app


def phone_store_doc(data: Mapping) -> dict:
    """비서 가게 한 벌(DummyStore JSON) → 전화용 가게(게시하는 값, JSON).

    메뉴는 id·이름·가격·설명·품절과 손님이 부르는 다른 이름(별칭·원어명)만 옮긴다. 사진·AI 표시
    같은 화면 값은 빼서, 사진을 채우는 저장으로는 게시 값이 바뀌지 않는다. 비서 메뉴에는 옵션이
    없다(전화는 옵션 말을 그 줄 요청 사항으로 적는다).
    """
    return {"menus": [
        {"id": str(m["id"]), "name": m["name"], "price": m["price"],
         "description": m.get("description") or "", "aliases": list(m.get("aliases") or ()),
         "name_native": m.get("name_native") or "", "sold_out": bool(m.get("sold_out"))}
        for m in data.get("menus") or ()
    ]}


class PhoneStores:
    """전화용 가게 게시판 (전화 메뉴 티켓 02). 전화가 읽는 가게는 하나이고 가장 최근 게시가 이긴다.

        stores = PhoneStores(base)        # base: 전화 가게 스냅숏(전화 설정·리뷰)
        stores.publish(pilot.seed())      # 비서 가게 한 벌 → 게시 (저장한 대화가 없을 때의 가게)
        stores()                          # 지금 스냅숏 (`StoreProvider`: 통화 시작·사장님 허브)

    - `publish`는 저장한 스레드에서 부른다. 게시 값(`phone_store_doc`)으로 검색 색인까지 만든 뒤
      스냅숏을 통째로 바꿔 끼운다. 같은 게시 값은 다시 만들지 않는다(내용 해시). 진행 중인 통화는
      시작할 때 받은 스냅숏을 끝까지 쓴다
    - 메뉴가 빈 가게는 게시하지 않는다(메뉴판 사진 시연의 빈 가게가 전화를 비우지 않게)
    - 전화 설정은 `base` 그대로다. 꼭 고를 것(`required_options`)은 비서 메뉴에 없으므로 비운다
    """

    KEEP = 8  # 만들어 둔 스냅숏을 이만큼 들고 있다(두 대화를 오갈 때 다시 만들지 않게)

    def __init__(self, base: StoreSnapshot, *, db_url: str | None = None) -> None:
        self._config = replace(base.config, required_options={})
        self._reviews = tuple(base.store.recent_reviews(limit=1_000_000))
        self._db_url = db_url
        self._current = base
        self._doc: dict | None = None
        self._built: OrderedDict[str, StoreSnapshot] = OrderedDict()
        self._lock = threading.Lock()  # 게시는 한 번에 하나. 늦게 들어온 게시가 이긴다

    def __call__(self) -> StoreSnapshot:
        return self._current  # 이벤트 루프에서 부른다: 만들어 둔 것만 돌려준다

    @property
    def doc(self) -> dict | None:
        """지금 게시된 값. 아직 없으면 None(전화 가게 그대로)."""
        return self._doc

    def publish(self, data: Mapping) -> bool:
        """비서 가게 한 벌을 전화용 가게로 게시한다. 메뉴가 비었으면 게시하지 않고 False."""
        return self.put(phone_store_doc(data))

    def put(self, doc: dict) -> bool:
        """게시 값을 그대로 게시한다. 메뉴가 비었으면 게시하지 않고 False."""
        if not doc.get("menus"):
            log.info("전화용 가게: 메뉴가 빈 가게라 게시하지 않음 (지금 가게 그대로)")
            return False
        key = hashlib.sha1(
            json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self._lock:
            snapshot = self._built.pop(key, None) or self._build(doc)
            self._built[key] = snapshot
            while len(self._built) > self.KEEP:
                self._built.popitem(last=False)
            changed = snapshot is not self._current
            self._current, self._doc = snapshot, doc
        if changed:
            log.info("전화용 가게 게시: 메뉴 %d개", len(doc["menus"]))
        return True

    def _build(self, doc: dict) -> StoreSnapshot:
        from agenthan.rag import RagStoreKnowledge
        from agenthan.rag.vector import PgEmbeddingCache

        knowledge = RagStoreKnowledge(_PublishedMenus(doc, self._reviews))
        if knowledge.vector.enabled and self._db_url:
            knowledge.vector.cache = PgEmbeddingCache(self._db_url)
        ensure_usable(knowledge.list_menu(), self._config, log)  # 경고는 로그에
        return StoreSnapshot.of(knowledge, self._config)


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
                        created_at=row.created_at),
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
    app: FastAPI, env: Mapping[str, str], *, on_save: Callable[[dict], None] | None = None
) -> None:
    """on_save: 대화가 가게 파일을 저장할 때마다 그 가게 한 벌로 부른다(전화용 가게 게시)."""
    from agenthan.app import pilot
    from agenthan.app.server import DEFAULT_ROOT
    from agenthan.app.server import create_app as create_assistant_app

    root = env.get(SESSIONS_ENV, "").strip() or DEFAULT_ROOT
    app.include_router(
        create_assistant_app(make_assistant=pilot.assistant, root=root, on_save=on_save).router)
    log.info("사장님 비서(/assistant) 붙임, 대화 파일은 %s", root)


def main(argv: Sequence[str] | None = None) -> None:
    voice_server.main(argv, factory="agenthan.serve:create_app")


if __name__ == "__main__":
    main()
