"""계약 테스트. StoreKnowledge 구현이 지켜야 할 최소 약속.

Part B는 진짜 구현을 만들면 IMPLEMENTATIONS에 추가한다. 같은 테스트가 통과하면 합칠 수 있다.
"""

from collections.abc import Callable

import pytest

from agenthan.core.contracts import MenuHit, StoreKnowledge
from agenthan.core.fakes import FakeStoreKnowledge, MemoryOrderSink

IMPLEMENTATIONS: list[Callable[[], StoreKnowledge]] = [FakeStoreKnowledge.from_fixture]


@pytest.fixture(params=IMPLEMENTATIONS, ids=lambda f: getattr(f, "__qualname__", str(f)))
def knowledge(request: pytest.FixtureRequest) -> StoreKnowledge:
    return request.param()


def test_exact_menu_name_comes_first(knowledge: StoreKnowledge) -> None:
    hits = knowledge.search_menu("메도빅")
    assert hits and hits[0].item.name_ko == "메도빅"


def test_alias_finds_menu(knowledge: StoreKnowledge) -> None:
    hits = knowledge.search_menu("꿀케이크")
    assert any(h.item.name_ko == "메도빅" for h in hits)


def test_unknown_menu_returns_empty(knowledge: StoreKnowledge) -> None:
    assert knowledge.search_menu("짜장면") == []


def test_hits_are_sorted_and_scored(knowledge: StoreKnowledge) -> None:
    hits: list[MenuHit] = knowledge.search_menu("케이크", k=10)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_get_menu_and_options(knowledge: StoreKnowledge) -> None:
    americano = next(m for m in knowledge.list_menu() if m.name_ko == "아메리카노")
    assert knowledge.get_menu(americano.id) == americano
    assert any(o.name == "디카페인 변경" and o.available for o in americano.options)


def test_recent_reviews_newest_first(knowledge: StoreKnowledge) -> None:
    reviews = knowledge.recent_reviews(limit=2)
    assert len(reviews) == 2
    assert reviews[0].created_at >= reviews[1].created_at


def test_memory_order_sink_keeps_orders() -> None:
    from agenthan.core.contracts import Order, OrderLine

    sink = MemoryOrderSink()
    sink.submit(Order(id="o1", lines=[OrderLine(menu_id="m03", quantity=2)]))
    assert len(sink.orders) == 1
