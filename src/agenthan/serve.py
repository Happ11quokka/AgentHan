"""발표 서버: 전화 주문(Part C)과 사장님 비서(Part A)를 한 앱·한 주소로 낸다.

    uv run --env-file .env python -m agenthan.serve --port 8000

- 전화 쪽(`agenthan.voice.server`)이 `/api/*`·WS·화면(`web/dist`)을 내고, 여기서 비서 API
  (`/assistant/*`, `agenthan.app.server`)를 같은 앱에 붙인다.
  화면은 주소로 갈린다(`web/src/main.tsx`).
- voice는 core 말고 다른 파트를 import하지 않는 규칙이라, 둘을 묶는 일은 이 파일이 한다.
- AGENTHAN_DB_URL이 있으면 전화 가게 메뉴를 DB에서 읽고 확정·넘김 주문과 사장님 결정(수락·거절 …)을
  phone_orders 표에 남긴다
  (`agenthan.db`). 비서 가게 파일도 쓸 때마다 DB에 한 벌 남는다(tools/dummy_store.py).
- AGENTHAN_ASSISTANT=off면 비서를 붙이지 않는다(전화만). 대화마다 가게 파일을
  AGENTHAN_SESSIONS_DIR(기본 .agenthan/sessions)에 쓴다.
- 전화용 가게(전화 메뉴 티켓 02): 비서가 켜져 있으면 전화는 비서 메뉴로 주문을 받는다. 대화가 가게를
  저장할 때마다 그 가게가 게시되고 가장 최근에 저장한 대화가 이긴다(`PhoneStores`). 저장한 대화가
  없으면 파일럿 가게. 전화 설정(영업시간·준비 시간·상한·가게 이름)과 리뷰는 위의 전화 가게 것이다.
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
        stores.start(pilot.seed())        # DB에 남은 마지막 게시 값, 없으면 이 가게
        stores.publish(data)              # 비서 가게 한 벌 → 게시
        stores()                          # 지금 스냅숏 (`StoreProvider`: 통화 시작·사장님 허브)

    - `publish`는 저장한 스레드에서 부른다. 게시 값(`phone_store_doc`)으로 검색 색인까지 만든 뒤
      스냅숏을 통째로 바꿔 끼운다. 같은 게시 값은 다시 만들지 않는다(내용 해시). 진행 중인 통화는
      시작할 때 받은 스냅숏을 끝까지 쓴다
    - 메뉴가 빈 가게는 게시하지 않는다(메뉴판 사진 시연의 빈 가게가 전화를 비우지 않게)
    - 전화 설정은 `base` 그대로다. 꼭 고를 것(`required_options`)은 비서 메뉴에 없으므로 비운다
    - DB(`db_url`)가 있으면 (티켓 03) 게시 값을 DB에 남기고(`db.stores`), 메뉴 벡터는 pgvector
      (`PgVectorIndex`, 게시된 가게 id 하나)에 둔다. 게시가 바뀌면 다음 검색 때 바뀐 메뉴만
      임베딩하고 없어진 메뉴는 지운다. DB가 없으면 벡터 없이 이름·별칭·오타 매칭만
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
        self._lock = threading.Lock()  # 게시는 한 번에 하나. 늦게 들어온 게시가 이긴다

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
        saved = None
        if self._db_url:
            from agenthan.db.stores import open_published_phone_store

            saved = open_published_phone_store(self._db_url)
        if saved and self.put(saved, save=False):
            self._saved = _content_key(saved)
            log.info("전화용 가게: DB에 남은 마지막 게시 값으로 시작 (메뉴 %d개)",
                     len(saved["menus"]))
            return
        self.put(phone_store_doc(default), save=False)

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
            while len(self._built) > self.KEEP:
                self._built.popitem(last=False)
            changed = snapshot is not self._current
            if changed:
                # 만들어 둔 스냅숏으로 돌아왔으면 pgvector 색인을 다른 게시가 채웠을 수 있다
                snapshot.store.vector.reset()
            self._current, self._doc = snapshot, doc
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

        # DB가 없으면 벡터 없이 (이름·별칭·오타 매칭)
        knowledge = RagStoreKnowledge(_PublishedMenus(doc, self._reviews), embed=False)
        if self._db_url:
            from agenthan.db.stores import PUBLISHED_STORE_ID
            from agenthan.rag.vector import PgVectorIndex

            # 메뉴 벡터는 pgvector에. 첫 검색 때 바뀐 메뉴만 임베딩한다(캐시 표 우선)
            knowledge.vector = PgVectorIndex(
                knowledge.vector.docs, PUBLISHED_STORE_ID, self._embed, self._db_url)
        ensure_usable(knowledge.list_menu(), self._config, log)  # 경고는 로그에
        return StoreSnapshot.of(knowledge, self._config)


def _content_key(doc: dict) -> str:
    return hashlib.sha1(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


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
            owner_pickup_time=decision.owner_pickup_time, order=decision.order))


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
