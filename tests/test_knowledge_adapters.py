"""Part A가 StoreKnowledge 계약(가짜 구현)만으로 리뷰 서브에이전트를 돌릴 수 있는지."""

from datetime import date

from agenthan.agent.review.analyze import analyze_reviews
from agenthan.agent.review.topic import resolve_from_utterance
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.tools.knowledge_adapters import KnowledgeMenuCatalog, KnowledgeReviewRepo


def _repo() -> KnowledgeReviewRepo:
    return KnowledgeReviewRepo(FakeStoreKnowledge.from_fixture(), store_name="샘플 러시안 베이커리")


def test_reviews_newest_first_with_review_row_schema() -> None:
    rows = _repo().get_reviews("샘플")
    assert [r["review_id"] for r in rows] == ["r03", "r02", "r01"]
    assert rows[0]["visit_date"] == "2026-11-05"
    assert rows[0]["store"] == "샘플 러시안 베이커리"
    assert rows[0]["features"] == [] and rows[0]["has_owner_reply"] is None


def test_review_date_range_is_inclusive() -> None:
    rows = _repo().get_reviews("샘플", date(2026, 11, 3), date(2026, 11, 3))
    assert [r["review_id"] for r in rows] == ["r02"]


def test_features_for_fills_features() -> None:
    feats = [{"feature": "맛", "highlights": [], "score": None}]
    repo = KnowledgeReviewRepo(FakeStoreKnowledge.from_fixture(), features_for=lambda _: feats)
    assert all(r["features"] == feats for r in repo.get_reviews("샘플"))


def test_menu_catalog_includes_native_name_and_aliases() -> None:
    catalog = KnowledgeMenuCatalog(FakeStoreKnowledge.from_fixture())
    medovik = next(m for m in catalog.menus("샘플") if m["name"] == "메도빅")
    assert "꿀케이크" in medovik["aliases"] and "Медовик" in medovik["aliases"]
    topic = resolve_from_utterance("Медовик 리뷰 어때?", "샘플", catalog)
    assert topic["menus"] == ["메도빅"]


def test_analyze_reviews_runs_on_contract_data() -> None:
    out = analyze_reviews(
        store_id="샘플",
        today=date(2026, 11, 8),
        repo=_repo(),
        catalog=KnowledgeMenuCatalog(FakeStoreKnowledge.from_fixture()),
        topic="블린",
    )
    assert out["review_count"] == 1
