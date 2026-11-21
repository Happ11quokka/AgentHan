"""agenthan.db (PostgreSQL + pgvector).

DB가 필요한 테스트는 AGENTHAN_TEST_DB_URL이 있을 때만 돈다(그 DB의 표를 지우고 다시 만든다).
로컬:  docker run -d --name agenthan-pg -e POSTGRES_USER=agenthan -e POSTGRES_PASSWORD=devpass \\
         -e POSTGRES_DB=agenthan -p 54329:5432 pgvector/pgvector:0.8.7-pg17
       docker exec agenthan-pg psql -U agenthan -c "CREATE DATABASE agenthan_test"
       export AGENTHAN_TEST_DB_URL=postgresql://agenthan:devpass@localhost:54329/agenthan_test
       uv run pytest tests/test_db.py
DB 없이 도는 테스트: 꺼져 있거나 못 붙을 때 JSON으로 돌아가는지.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.db import conn as dbconn
from agenthan.rag import RagStoreKnowledge
from agenthan.rag.repository import DUMMY_DIR, JsonDirRepository, PgRepository

TEST_URL = os.environ.get("AGENTHAN_TEST_DB_URL", "")
needs_db = pytest.mark.skipif(not TEST_URL, reason="AGENTHAN_TEST_DB_URL 없음")
CLOSED_URL = "postgresql://nobody:nothing@127.0.0.1:1/none"
REPO = Path(__file__).resolve().parents[1]


def fake_embed(text: str) -> list[float]:
    """글마다 정해진 1024차원 단위 벡터(같은 글 = 같은 벡터)."""
    seed = hashlib.sha256(text.encode()).digest()
    raw = [((seed[i % 32] * (i + 1)) % 251) - 125.0 for i in range(1024)]
    n = math.sqrt(sum(x * x for x in raw))
    return [x / n for x in raw]


@pytest.fixture(scope="module")
def seeded():
    """빈 테스트 DB에 스키마 + 더미 가게 전부(실제 데이터는 넣지 않음)."""
    if not TEST_URL:
        pytest.skip("AGENTHAN_TEST_DB_URL 없음")
    from agenthan.db import seed
    from agenthan.db.migrate import migrate

    with dbconn.connect(TEST_URL) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
        migrate(c)
        for rows in seed.all_stores(include_real=False):
            seed.write_store(c, rows)
        seed.write_eval_cases(c, seed.eval_files())
    return TEST_URL


@pytest.fixture
def db_env(seeded, monkeypatch):
    monkeypatch.setenv("AGENTHAN_DB_URL", seeded)
    return seeded


# ------------------------------------------------------------------ DB 없이
def test_disabled_without_url() -> None:
    assert not dbconn.enabled()
    assert dbconn.guarded(lambda c: 1, "default") == "default"


def test_unreachable_db_falls_back_and_pauses(monkeypatch) -> None:
    monkeypatch.setenv("AGENTHAN_DB_URL", CLOSED_URL)
    assert dbconn.guarded(lambda c: 1, "default") == "default"
    assert not dbconn.available()  # 실패 뒤에는 잠깐 쉰다(다음 호출은 연결 시도 없이 바로)
    kb = RagStoreKnowledge.from_env(embed=False)
    assert kb.search_menu("나폴레옹")[0].item.id == "napoleon"  # JSON 더미로 동작


def test_dummy_store_works_when_db_is_down(tmp_path, monkeypatch) -> None:
    from agenthan.tools.dummy_store import DummyStore

    monkeypatch.setenv("AGENTHAN_DB_URL", CLOSED_URL)
    store = DummyStore(tmp_path / "store.json")
    data = store.read()
    store.write(data)
    assert (tmp_path / "store.json").exists()


def test_serve_without_db_keeps_files() -> None:
    from agenthan.serve import phone_db
    from agenthan.voice.settings import VoiceSettings

    assert phone_db(VoiceSettings.from_env({}, env_file=None)) == (None, [], None)


# ------------------------------------------------------------------ DB 있음
@needs_db
def test_migrate_is_idempotent(seeded) -> None:
    from agenthan.db.migrate import migrate

    with dbconn.connect(seeded) as c:
        assert migrate(c) == []
        assert c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()


@needs_db
def test_seed_loads_every_repo_store(seeded) -> None:
    with dbconn.connect(seeded) as c:
        ids = {r[0] for r in c.execute("SELECT id FROM stores")}
        kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM stores")}
        cases = c.execute("SELECT count(*) FROM eval_cases").fetchone()[0]
    assert {"dummy_store", "sample", "sample-pho", "pilot", "demo", "place_report"} <= ids
    assert kinds == {"dummy"}  # 실제 데이터는 테스트에 넣지 않았다
    assert cases > 0


@needs_db
def test_pg_repository_matches_json(seeded) -> None:
    a = JsonDirRepository(DUMMY_DIR).load()
    b = PgRepository("dummy_store", seeded).load()
    assert [m.item for m in a.menus] == [m.item for m in b.menus]
    assert [(m.family, m.tags, m.popularity, m.gift, m.taste) for m in a.menus] == [
        (m.family, m.tags, m.popularity, m.gift, m.taste) for m in b.menus
    ]
    assert a.families == b.families
    assert [(r.id, r.text, r.created_at, r.rating) for r in a.reviews] == [
        (r.id, r.text, r.created_at, r.rating) for r in b.reviews
    ]


@needs_db
def test_search_from_db_matches_json_on_goldset(seeded) -> None:
    json_kb = RagStoreKnowledge.from_fixture()
    db_kb = RagStoreKnowledge.from_db("dummy_store", embed=False, url=seeded)
    cases = [json.loads(x) for x in (DUMMY_DIR / "goldset.jsonl").read_text().splitlines() if x]
    for case in cases:
        q = case["query"]
        assert [h.item.id for h in db_kb.search_menu(q)] == [
            h.item.id for h in json_kb.search_menu(q)
        ], q


@needs_db
def test_pgvector_search_finds_menu_and_reviews(seeded) -> None:
    from agenthan.db.embed import embed_store
    from agenthan.rag.vector import PgEmbeddingCache

    with dbconn.connect(seeded) as c:
        written, calls = embed_store(c, "dummy_store", fake_embed, PgEmbeddingCache(seeded), pace=0)
        assert written > 0 and calls > 0
        again, calls2 = embed_store(c, "dummy_store", fake_embed, PgEmbeddingCache(seeded), pace=0)
        assert (again, calls2) == (0, 0)  # 바뀐 글이 없으면 아무것도 안 함
    kb = RagStoreKnowledge.from_db("dummy_store", embed=fake_embed, url=seeded)
    menu_id = next(iter(kb.records))
    hits = kb.vector.search(kb._embed_text(menu_id), k=3)
    assert hits[0][0] == menu_id and hits[0][1] > 0.99
    review = kb.recent_reviews(1)[0]
    found = kb.search_reviews(review.text, k=1)
    assert found and found[0][0].id == review.id


@needs_db
def test_order_sink_writes_phone_orders(seeded) -> None:
    from agenthan.db.orders import PgOrderSink

    sink = PgOrderSink(store_id="sample", url=seeded)
    order = Order(
        id="call-test-1",
        lines=[OrderLine("m01", 2, name="메도빅", unit_price=7500)],
        pickup_time="2026-11-25T15:00:00+09:00",
        status="confirmed",
        transcript=["메도빅 두 개요"],
    )
    sink.submit(order)
    sink.flush()
    sink.close()
    with dbconn.connect(seeded) as c:
        row = c.execute(
            "SELECT store_id, status, total, lines->0->>'name' FROM phone_orders WHERE id = %s",
            ("call-test-1",),
        ).fetchone()
    assert row == ("sample", "confirmed", 15000, "메도빅")


def _decision(order_id: str, status: str, *, order: Order | None = None, **kw):
    from agenthan.db.orders import OwnerDecisionRow

    return OwnerDecisionRow(order_id, status, order=order, **kw)


def _phone_order(order_id: str) -> Order:
    return Order(
        id=order_id,
        lines=[OrderLine("m01", 1, name="메도빅", unit_price=7500)],
        pickup_time="2026-11-25T18:30:00+09:00",
        status="confirmed",
    )


def _owner_row(url: str, order_id: str) -> tuple:
    with dbconn.connect(url) as c:
        return c.execute(
            "SELECT status, owner_status, reject_reason, to_char(decided_at AT TIME ZONE"
            " 'Asia/Seoul', 'HH24:MI:SS'), owner_pickup_time, total FROM phone_orders"
            " WHERE id = %s",
            (order_id,),
        ).fetchone()


def test_owner_decisions_reach_db_rows_through_serve() -> None:
    """전화 허브의 결정 기록(voice) → DB 행(db). 두 파트를 잇는 일은 serve만 한다(승인 리뷰)."""
    from agenthan.db.orders import OwnerDecisionRow
    from agenthan.serve import DecisionsToDb
    from agenthan.voice.owner.decisions import DecisionRecord

    rows: list[OwnerDecisionRow] = []

    class Rows:
        def record_decision(self, row: OwnerDecisionRow) -> None:
            rows.append(row)

    order = _phone_order("call-r")
    DecisionsToDb(Rows()).record(DecisionRecord(
        "call-r", "accepted", "", "2026-11-25T16:00:09+09:00", "2026-11-25T18:45:00+09:00",
        order=order))
    assert rows == [OwnerDecisionRow("call-r", "accepted", "", "2026-11-25T16:00:09+09:00",
                                     "2026-11-25T18:45:00+09:00")]
    assert rows[0].order is order


def test_decisions_without_db_are_dropped_quietly() -> None:
    from agenthan.db.orders import PgOrderSink

    sink = PgOrderSink(url="")  # DB 설정 없음: 메모리(사장님 화면)만
    sink.record_decision(_decision("call-x", "accepted", decided_at="2026-11-25T16:00:00+09:00"))
    sink.flush()
    sink.close()


@needs_db
def test_migration_002_gives_orders_empty_decision_columns(seeded) -> None:
    from agenthan.db.orders import PgOrderSink

    sink = PgOrderSink(url=seeded)
    sink.submit(_phone_order("call-d0"))
    sink.flush()
    sink.close()
    assert _owner_row(seeded, "call-d0") == ("confirmed", "", "", None, "", 7500)


@needs_db
def test_a_decision_before_the_order_record_survives_it(seeded) -> None:
    """통화 중 수락(결정)이 통화 끝 확정 주문보다 먼저 온다. 둘 다 남는다."""
    from agenthan.db.orders import PgOrderSink

    sink = PgOrderSink(url=seeded)
    order = _phone_order("call-d1")
    sink.record_decision(_decision("call-d1", "accepted", order=order,
                          decided_at="2026-11-25T16:00:09+09:00",
                          owner_pickup_time="2026-11-25T18:45:00+09:00"))
    sink.flush()
    assert _owner_row(seeded, "call-d1") == (
        "draft", "accepted", "", "16:00:09", "2026-11-25T18:45:00+09:00", 7500)
    sink.submit(order)
    sink.flush()
    sink.close()
    assert _owner_row(seeded, "call-d1") == (
        "confirmed", "accepted", "", "16:00:09", "2026-11-25T18:45:00+09:00", 7500)


@needs_db
def test_decisions_are_plain_values_and_the_last_one_wins(seeded) -> None:
    """거절(주문 기록이 안 오는 통화)은 draft 행으로 남고, 새 상태·사유 글도 그대로 들어간다."""
    from agenthan.db.orders import PgOrderSink

    sink = PgOrderSink(url=seeded)
    sink.record_decision(_decision("call-d2", "rejected", order=_phone_order("call-d2"),
                          reject_reason="out_of_stock", decided_at="2026-11-25T16:01:00+09:00"))
    sink.submit(_phone_order("call-d3"))
    sink.record_decision(_decision("call-d3", "expired", decided_at="2026-11-25T16:07:00+09:00"))
    sink.flush()
    sink.close()
    assert _owner_row(seeded, "call-d2") == ("draft", "rejected", "out_of_stock", "16:01:00", "",
                                             7500)
    assert _owner_row(seeded, "call-d3") == ("confirmed", "expired", "", "16:07:00", "", 7500)


@needs_db
def test_dummy_store_mirrors_to_db_and_restores(db_env, tmp_path) -> None:
    from agenthan.tools.dummy_store import DummyStore

    path = tmp_path / "store.json"
    store = DummyStore(path)
    data = store.read()
    data["shop"]["name"] = "DB 사본 시험"
    store.write(data)
    path.unlink()  # 재배포로 파일이 사라진 상황
    assert DummyStore(path).read()["shop"]["name"] == "DB 사본 시험"


@needs_db
def test_phone_store_comes_from_db(db_env) -> None:
    from agenthan.db.stores import phone_store_raw

    fixture = REPO / "fixtures" / "sample_store.json"
    assert phone_store_raw(fixture) == json.loads(fixture.read_text(encoding="utf-8"))


@needs_db
def test_serve_phone_db_uses_db_store(seeded) -> None:
    from agenthan.serve import phone_db
    from agenthan.voice.settings import VoiceSettings

    raw, sinks, _ = phone_db(VoiceSettings.from_env({"AGENTHAN_DB_URL": seeded}, env_file=None))
    assert raw["store"]["id"] == "sample"
    assert len(sinks) == 1
    sinks[0].close()


@needs_db
def test_phone_gets_part_b_knowledge_from_db(seeded) -> None:
    from agenthan.core.fakes import FakeStoreKnowledge
    from agenthan.serve import phone_db, phone_knowledge
    from agenthan.voice.settings import VoiceSettings

    settings = VoiceSettings.from_env({"AGENTHAN_DB_URL": seeded}, env_file=None)
    raw, sinks, _ = phone_db(settings)
    for sink in sinks:
        sink.close()
    kb = phone_knowledge(raw, settings)
    assert isinstance(kb, RagStoreKnowledge)
    assert kb.list_menu() == FakeStoreKnowledge.from_fixture().list_menu()
    assert kb.search_menu("꿀 들어간 케이크")[0].item.name_ko == "메도빅"


def test_phone_knowledge_without_db_store_is_none() -> None:
    from agenthan.serve import phone_knowledge
    from agenthan.voice.settings import VoiceSettings

    assert phone_knowledge(None, VoiceSettings.from_env({}, env_file=None)) is None
