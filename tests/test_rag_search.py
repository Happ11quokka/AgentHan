"""Part B 검색 테스트 (fixtures/dummy_store 더미, 벡터 끔)."""

from datetime import UTC, datetime, timedelta

import pytest

from agenthan.rag import RagStoreKnowledge
from agenthan.rag.normalize import drop_correction, key, normalize, split_items
from agenthan.rag.repository import DATA_DIR_ENV, DUMMY_DIR, resolve_data_dir

MEDOVIK_FAMILY = ["medovik", "medovik-choco", "medovik-sour-berry", "medovik-whole"]


@pytest.fixture(scope="module")
def kb() -> RagStoreKnowledge:
    return RagStoreKnowledge.from_fixture()


def ids(kb: RagStoreKnowledge, query: str, k: int = 10) -> list[str]:
    return [h.item.id for h in kb.search_menu(query, k=k)]


# ---------------------------------------------------------------- 전처리
def test_normalize_spelling_and_spaces() -> None:
    assert key("꿀 케잌!") == key("꿀케익") == key("꿀캐이크") == "꿀케이크"
    assert normalize("  Medovik,  Honey  ") == "medovik honey"


def test_correction_keeps_last_part() -> None:
    assert drop_correction("나폴레옹, 아 아니 초코 메도빅으로요") == "초코 메도빅으로요"
    assert (
        drop_correction("나폴레옹 하나… 아 잠깐만요, 초코 메도빅으로 할게요")
        == "초코 메도빅으로 할게요"
    )
    assert drop_correction("나폴레옹 말고 프라하요") == "프라하요"
    assert drop_correction("나폴레옹 있어요?") == "나폴레옹 있어요"


def test_split_items() -> None:
    assert split_items("메도빅 하나랑 아메리카노 두 잔") == ["메도빅 하나", "아메리카노 두 잔"]


# ---------------------------------------------------------------- 정확 일치·묶음
def test_exact_name_only_that_menu(kb: RagStoreKnowledge) -> None:
    hits = kb.search_menu("나폴레옹 있어요?")
    assert [h.item.id for h in hits] == ["napoleon"]
    assert hits[0].matched_by == "exact" and hits[0].score == 1.0


def test_alias_and_native_name(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "초코 꿀케이크 하나요") == ["medovik-choco"]
    assert ids(kb, "Сколько стоит наполеон?") == ["napoleon"]
    assert ids(kb, "아아 한 잔이요") == ["americano"]


def test_family_alias_returns_family_with_representative_first(kb: RagStoreKnowledge) -> None:
    for q in ["꿀케이크 있어요?", "medovik", "Есть медовик?", "매도빅 하나요"]:
        got = ids(kb, q)
        assert got[0] == "medovik", q
        assert sorted(got) == sorted(MEDOVIK_FAMILY), q


def test_family_narrowed_by_distinguishing_word(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "초코로 된 꿀케이크 하나 주세요") == ["medovik-choco"]


def test_compare_question_uses_representative(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "메도빅이랑 나폴레옹 차이가 뭐예요?") == ["medovik", "napoleon"]


def test_multi_item_order(kb: RagStoreKnowledge) -> None:
    got = ids(kb, "메도빅 하나랑 아메리카노 두 잔")
    assert got[:2] == ["medovik", "americano"]
    assert set(got) == set(MEDOVIK_FAMILY) | {"americano"}


def test_correction_sentence(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "나폴레옹, 아 아니 초코 메도빅으로요") == ["medovik-choco"]


def test_typo(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "나폴래옹 두 개 되나요?")[0] == "napoleon"
    assert ids(kb, "호두케잌 있어요?") == ids(kb, "호두케이크 있어요?")


# ---------------------------------------------------------------- 빈 목록이 정답
@pytest.mark.parametrize(
    "query",
    [
        "티라미수 있어요?",
        "짜장면",
        "견과류 알레르기 있는데 괜찮은 케이크 있어요?",
        "비건 케이크 있어요?",
        "레터링 돼요?",
        "몇 시까지 해요?",
        "주차 돼요?",
        "덜 단 거 있어요?",
        "마카롱은 안 파세요?",
    ],
)
def test_no_evidence_returns_empty(kb: RagStoreKnowledge, query: str) -> None:
    assert kb.search_menu(query) == []


# ---------------------------------------------------------------- 종류·태그·의도
def test_category(kb: RagStoreKnowledge) -> None:
    ades = {m.id for m in kb.list_menu() if m.category == "Ade"}
    assert set(ids(kb, "에이드 뭐 있어요?")) == ades


def test_tag(kb: RagStoreKnowledge) -> None:
    tagged = {
        "medovik",
        "medovik-choco",
        "medovik-sour-berry",
        "medovik-whole",
        "napoleon",
        "praga",
        "ptichye-moloko",
        "kievsky",
    }
    assert set(ids(kb, "러시아 전통 케이크 뭐 있는지 정리해 줘")) == tagged


def test_intents(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "제일 잘 나가는 거 뭐예요?") == ["medovik", "napoleon"]
    assert ids(kb, "요즘 리뷰에 많이 나오는 메뉴 뭐야?") == ["medovik", "meringue-roll"]
    assert ids(kb, "제일 단 메뉴가 뭐야?") == ["pavlova"]
    assert ids(kb, "제일 싼 케이크 뭐예요?") == ["cheesecake"]
    assert set(ids(kb, "선물용으로 추천할 만한 메뉴 뭐야?")) == {"medovik-whole", "oreshki"}
    assert all(
        m.price <= 8000 for m in (h.item for h in kb.search_menu("8천 원 이하 케이크", k=20))
    )


def test_sold_out_and_options_are_kept(kb: RagStoreKnowledge) -> None:
    hits = kb.search_menu("키예프 케이크 있어요?")
    assert hits[0].item.id == "kievsky" and hits[0].item.sold_out
    americano = kb.search_menu("아메리카노")[0].item
    assert any(o.name == "디카페인 변경" for o in americano.options)


def test_scores_sorted_and_in_range(kb: RagStoreKnowledge) -> None:
    for q in [
        "메도빅 하나랑 아메리카노 두 잔",
        "케이크",
        "레몬 들어간 음료 뭐뭐 있지?",
        "제일 싼 케이크",
    ]:
        scores = [h.score for h in kb.search_menu(q, k=10)]
        assert scores == sorted(scores, reverse=True), q
        assert all(0.0 <= s <= 1.0 for s in scores), q


# ---------------------------------------------------------------- 나머지 계약
def test_recent_reviews_since(kb: RagStoreKnowledge) -> None:
    newest = kb.recent_reviews(limit=1)[0]
    since = newest.created_at - timedelta(days=7)
    rows = kb.recent_reviews(limit=100, since=since)
    assert rows and all(r.created_at >= since for r in rows)
    assert kb.recent_reviews(limit=100, since=datetime(2100, 1, 1, tzinfo=UTC)) == []


def test_get_menu_unknown(kb: RagStoreKnowledge) -> None:
    assert kb.get_menu("없는-메뉴") is None
    assert kb.get_menu("napoleon").name_ko == "나폴레옹"


def test_data_dir_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert resolve_data_dir() == DUMMY_DIR
    monkeypatch.setenv(DATA_DIR_ENV, "fixtures/dummy_store")
    assert resolve_data_dir() == DUMMY_DIR


def test_vector_is_off_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    kb = RagStoreKnowledge.from_dir(DUMMY_DIR)
    assert not kb.vector.enabled
    assert ids(kb, "나폴레옹 있어요?") == ["napoleon"]


def test_vector_slot_with_fake_embedding() -> None:
    """가짜 임베딩으로 벡터 단계가 RRF에 들어가는지만 본다 (실제 CLOVA 호출 없음)."""

    def fake_embed(text: str) -> list[float]:
        return [1.0 if "수플레" in text or "폭신" in text else 0.0, 1.0]

    kb = RagStoreKnowledge.from_dir(DUMMY_DIR, embed=fake_embed)
    hits = kb.search_menu("폭신한 수플레 같은 거")
    assert hits and hits[0].item.id == "ptichye-moloko"
    assert hits[0].matched_by == "hybrid"  # 키워드와 벡터가 같이 찾음


# ---------------------------------------------------------------- 일반 규칙 (holdout과 다른 문장)
def test_generic_word_alone_is_not_evidence(kb: RagStoreKnowledge) -> None:
    got = ids(kb, "레몬 들어간 거 있어요?")
    assert "lemon-ade" in got and "honey-lemon-tea" in got
    assert "mors" not in got  # '음료'·'베리' 같은 말이 아니라 '레몬'이 맞아야 한다


def test_attached_name_is_searched_in_names_only(kb: RagStoreKnowledge) -> None:
    # 나폴레옹·키예프 설명에 '버터'가 있어도, 붙여 쓴 '버터케이크'는 이름·별칭에서만 찾는다
    assert kb.search_menu("버터케이크 있어요?") == []
    assert kb.search_menu("버터 들어간 케이크 있어요?")  # 띄어 쓰면 설명에서도 찾는다


def test_category_only_when_category_itself_is_asked(kb: RagStoreKnowledge) -> None:
    cakes = {m.id for m in kb.list_menu() if m.category == "Cake"}
    drinks = {m.id for m in kb.list_menu() if m.category in ("Coffee", "Tea", "Ade")}
    assert set(ids(kb, "케이크 뭐 있어요?", k=50)) == cakes
    assert set(ids(kb, "음료 뭐 있어요?", k=50)) == drinks
    choco = ids(kb, "초코 케이크 있어요?", k=50)
    assert "medovik-choco" in choco and set(choco) != cakes


def test_typo_with_spaced_generic_word(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "제부라 케이크 있어요?")[0] == "zebra"


def test_intent_filters_by_content_word_first(kb: RagStoreKnowledge) -> None:
    got = ids(kb, "꿀 들어간 것 중에 인기 있는 거")
    assert got[0] == "medovik"
    assert set(MEDOVIK_FAMILY) <= set(got[:4])


def test_options_are_searchable(kb: RagStoreKnowledge) -> None:
    assert ids(kb, "디카페인 되는 커피 있어요?")[0] == "americano"


@pytest.mark.parametrize("query", ["초 몇 개 같이 주세요", "포장지 따로 파나요?", "쇼핑백 있어요?"])
def test_service_only_questions_are_empty(kb: RagStoreKnowledge, query: str) -> None:
    assert kb.search_menu(query) == []


def test_one_char_word_is_kept_but_not_inside_other_words(kb: RagStoreKnowledge) -> None:
    assert kb.keyword.words("꿀 들어간 거") == ["꿀"]
    assert kb.search_menu("초코 케이크 있어요?")  # '초코'의 '초'는 서비스 단어 '초'가 아니다


def test_rest_words_prefer_matching_menu_in_same_category() -> None:
    """찾은 메뉴(아메리카노)에 없는 말(카페인)이 같은 종류 다른 메뉴 이름에 있으면
    그 메뉴를 앞에 둔다."""
    from agenthan.core.contracts import MenuItem
    from agenthan.rag.repository import MenuRecord, StoreData

    class Repo:
        def load(self) -> StoreData:
            menus = [
                MenuRecord(
                    MenuItem("ame", "아메리카노", 4500, category="Coffee", aliases=("아아",))
                ),
                MenuRecord(MenuItem("decaf", "디카페인", 5000, category="Coffee")),
                MenuRecord(MenuItem("cake", "카페인 케이크", 8000, category="Cake")),
            ]
            return StoreData(menus, [], [])

    kb = RagStoreKnowledge(Repo(), embed=False)
    assert ids(kb, "아아 카페인 없이요") == ["decaf", "ame"]  # 다른 종류(cake)는 안 넣는다
    assert ids(kb, "아아 두 잔이요") == ["ame"]
    assert ids(kb, "카페인아아 주세요")[0] == "ame"  # 찾은 말에 붙은 말은 보지 않는다


# ------------------------------------------- 벡터 캐시·실패 (가짜 임베딩, 네트워크 없음)
def _fake_vec(text: str) -> list[float]:
    return [1.0 if "수플레" in text or "폭신" in text else 0.0, 1.0, 0.5]


def test_embedding_cache_skips_repeat_calls(tmp_path) -> None:
    cache = tmp_path / "emb.json"
    calls = []

    def embed(text: str) -> list[float]:
        calls.append(text)
        return _fake_vec(text)

    kb = RagStoreKnowledge.from_dir(DUMMY_DIR, embed=embed, cache_path=cache)
    first = kb.vector.warm()
    assert first == len(kb.records) and cache.exists()
    kb2 = RagStoreKnowledge.from_dir(DUMMY_DIR, embed=embed, cache_path=cache)
    assert kb2.vector.warm() == 0  # 내용이 같으면 다시 부르지 않는다
    assert len(calls) == len(kb.records)


def test_embedding_text_has_name_native_aliases_description_options(kb: RagStoreKnowledge) -> None:
    text = kb._embed_text("americano")
    for part in ["아메리카노", "Americano", "고소한 원두", "디카페인 변경"]:
        assert part in text


def test_vector_failure_falls_back_and_pauses() -> None:
    def broken(text: str) -> list[float]:
        raise RuntimeError("429")

    kb = RagStoreKnowledge.from_dir(DUMMY_DIR, embed=broken)
    assert ids(kb, "나폴레옹 있어요?") == ["napoleon"]  # 정확 일치는 벡터를 안 탄다
    assert kb.search_menu("폭신한 수플레 같은 거")  # 벡터가 실패해도 BM25로 답한다
    assert kb.vector.paused
