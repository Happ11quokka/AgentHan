"""전화 엔진이 가게 지식 검색(Part B)을 쓰는 두 곳.

1. 매처가 거절한 메뉴 말 → `search_menu`로 한 번 더 찾아 되묻기 ("꿀 들어간 케이크" → 메도빅?)
2. 답변기 자료에 질문과 가까운 후기 한 줄 (`search_reviews`가 있는 가게 지식일 때만)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from agenthan.core.contracts import MenuHit
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.answerer import AnswerReply, AnswerRequest
from agenthan.voice.dialog.engine import RELATED_REVIEW_LABEL, DialogEngine
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 21, 16, 0, tzinfo=KST)
HONEY = "꿀 들어간 거 하나요"
LAYERED = "층층이 쌓은 거 하나요"
THIN = "얇은 팬케이크 하나요"
TIRAMISU = "티라미수 하나요"
WAIT_Q = "웨이팅 길어요?"


class ScriptedNlu:
    script = {
        HONEY: [Act("add_item", menu_text="꿀 들어간 거", qty=1)],
        LAYERED: [Act("add_item", menu_text="층층이 쌓은 거", qty=1)],
        THIN: [Act("add_item", menu_text="얇은 팬케이크", qty=1)],
        TIRAMISU: [Act("add_item", menu_text="티라미수", qty=1)],
        WAIT_Q: [Act("ask_other", free_text=WAIT_Q)],
    }

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        if text in self.script:
            return list(self.script[text])
        from agenthan.voice.nlu.fake import FakeNlu

        return FakeNlu().parse(text, ctx)


class SearchingStore(FakeStoreKnowledge):
    """샘플 가게 + 정해 둔 검색 결과 (Part B 대신)."""

    def __init__(self, hits: dict[str, list[str]], *, reviews: bool = False, broken=False):
        base = FakeStoreKnowledge.from_fixture()
        super().__init__(base.list_menu(), base.recent_reviews(50))
        self.hits = hits
        self.broken = broken
        self.queries: list[str] = []
        if reviews:
            self.search_reviews = self._search_reviews

    def search_menu(self, query: str, k: int = 5) -> list[MenuHit]:
        self.queries.append(query)
        if self.broken:
            raise RuntimeError("search down")
        return [MenuHit(self.get_menu(i), 0.7, "keyword") for i in self.hits.get(query, [])][:k]

    def _search_reviews(self, query: str, k: int = 5):
        if self.broken:
            raise RuntimeError("search down")
        review = replace(self.recent_reviews(1)[0], text="웨이팅 10분 정도 했어요")
        return [(review, 0.7)] if "웨이팅" in query else []


class RecordingAnswerer:
    def __init__(self) -> None:
        self.requests: list[AnswerRequest] = []

    def answer(self, request: AnswerRequest) -> AnswerReply:
        self.requests.append(request)
        return AnswerReply("answer", "손님들은 10분 정도 기다렸다고 하세요.",
                           "웨이팅 10분 정도 했어요")


def make(store: FakeStoreKnowledge, answerer=None) -> DialogEngine:
    engine = DialogEngine(store, ScriptedNlu(), StoreConfig.from_fixture(), order_id="o-partb",
                          answerer=answerer)  # type: ignore[arg-type]
    engine.start(NOW)
    return engine


def test_described_menu_is_found_by_search_and_confirmed() -> None:
    store = SearchingStore({"꿀 들어간 거": ["m01"]})
    engine = make(store)
    r = engine.handle(HONEY, NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == [templates.confirm_menu("메도빅")]
    assert store.queries == ["꿀 들어간 거"]
    r = engine.handle("네", NOW)
    assert [(line.item.id, line.qty) for line in r.cart.lines] == [("m01", 1)]


def test_two_search_hits_ask_which_one() -> None:
    engine = make(SearchingStore({"층층이 쌓은 거": ["m02", "m04"]}))
    r = engine.handle(LAYERED, NOW)
    assert r.state is State.CLARIFY
    assert r.utterances == [templates.choose_menu(["나폴레옹", "블린"])]


def test_unsure_name_match_that_search_disagrees_with_asks_both() -> None:
    engine = make(SearchingStore({"얇은 팬케이크": ["m04"]}))
    r = engine.handle(THIN, NOW)  # 매처는 '케이크' 글자로 메도빅을 확인하려 한다
    assert r.utterances == [templates.choose_menu(["메도빅", "블린"])]
    r = engine.handle("블린이요", NOW)
    assert [(line.item.id, line.qty) for line in r.cart.lines] == [("m04", 1)]


def test_unsure_name_match_that_search_agrees_with_is_confirmed_as_before() -> None:
    engine = make(SearchingStore({"얇은 팬케이크": ["m01"]}))
    assert engine.handle(THIN, NOW).utterances == [templates.confirm_menu("메도빅")]


def test_no_search_hit_still_says_not_on_menu() -> None:
    engine = make(SearchingStore({}))
    r = engine.handle(TIRAMISU, NOW)
    assert r.state is State.ORDERING
    assert r.utterances[0] == templates.not_on_menu(["메도빅", "나폴레옹", "아메리카노", "블린"])


def test_broken_search_falls_back_to_not_on_menu() -> None:
    engine = make(SearchingStore({"티라미수": ["m01"]}, broken=True))
    r = engine.handle(TIRAMISU, NOW)
    assert r.utterances[0].startswith(templates.not_on_menu(["메도빅"])[:5])
    assert r.state is State.ORDERING


def test_sold_out_hits_are_not_suggested() -> None:
    store = SearchingStore({"꿀 들어간 거": ["m01"]})
    store._menu["m01"] = replace(store._menu["m01"], sold_out=True)
    r = make(store).handle(HONEY, NOW)
    assert r.state is State.ORDERING  # 품절인 메뉴만 찾았으면 없는 것과 같다


def test_related_reviews_join_the_answer_facts() -> None:
    answerer = RecordingAnswerer()
    engine = make(SearchingStore({}, reviews=True), answerer)
    engine.handle(WAIT_Q, NOW)
    facts = answerer.requests[-1].facts
    assert facts[-1] == f"{RELATED_REVIEW_LABEL}: 웨이팅 10분 정도 했어요"


def test_without_review_search_the_facts_are_unchanged() -> None:
    plain, searching = RecordingAnswerer(), RecordingAnswerer()
    make(SearchingStore({}), plain).handle(WAIT_Q, NOW)
    make(SearchingStore({}, reviews=True, broken=True), searching).handle(WAIT_Q, NOW)
    assert plain.requests[-1].facts == searching.requests[-1].facts
    assert not any(RELATED_REVIEW_LABEL + ": 웨이팅" in f for f in plain.requests[-1].facts)
