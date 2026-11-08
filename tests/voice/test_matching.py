"""메뉴 매처: 정규화 → 정확 일치 → 자모 유사도 → 세 구간 판정 (spec §6, D5)."""

from dataclasses import replace

import pytest

from agenthan.core.contracts import MenuItem
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.matching import (
    ACCEPT_AT,
    REJECT_BELOW,
    MenuMatcher,
    normalize,
    query_forms,
    similarity,
)

MENU = FakeStoreKnowledge.from_fixture().list_menu()


@pytest.fixture
def matcher() -> MenuMatcher:
    return MenuMatcher(MENU)


# ---------------------------------------------------------------- 정규화


@pytest.mark.parametrize(
    ("text", "core"),
    [
        ("메도빅", "메도빅"),
        ("메도빅 하나요", "메도빅"),
        ("아메리카노 두개요", "아메리카노"),
        ("아메리카노 한 잔 주세요", "아메리카노"),
        ("블린이요", "블린"),
        ("메도빅이랑", "메도빅"),
        ("아메리카노랑", "아메리카노"),
        ("나폴레옹 2개요.", "나폴레옹"),
        ("꿀 케이크!", "꿀케이크"),
        ("메도빅도", "메도빅"),
    ],
)
def test_normalize_strips_particles_and_qty(text: str, core: str) -> None:
    assert normalize(text) == core


def test_query_forms_keep_unstripped_versions() -> None:
    # "이요" 앞 글자에 받침이 없으면 "이"를 조사로 보지 않는다
    assert normalize("파이요") == "파이"
    # 덜 뗀 형태도 남겨서 "~도"로 끝나는 메뉴명을 놓치지 않는다
    assert query_forms("포도") == ("포도", "포")


# ---------------------------------------------------------------- 유사도


def test_similarity_exact_is_one() -> None:
    assert similarity("메도빅", "메도빅") == 1.0


def test_similarity_uses_jamo_distance() -> None:
    assert similarity("메도빗", "메도빅") == pytest.approx(1 - (1 / 3) / 3)


def test_similarity_partial_when_query_is_longer() -> None:
    # "메도빗케이크"의 한 구간 "메도빗"이 "메도빅"과 가깝다
    assert similarity("메도빗케이크", "메도빅") == pytest.approx(similarity("메도빗", "메도빅"))


def test_two_letter_name_partial_needs_exact_window() -> None:
    assert similarity("블린두개", "블린") == 1.0
    assert similarity("블루베리", "블린") < 0.6


def test_similarity_no_partial_when_query_is_shorter() -> None:
    assert similarity("메도", "메도빅") == pytest.approx(1 - 1 / 3)


# ---------------------------------------------------------------- 변형 표기 셋


@pytest.mark.parametrize(
    ("text", "menu_id"),
    [
        ("메도빅", "m01"),
        ("메도비크", "m01"),
        ("메도비", "m01"),
        ("꿀케이크", "m01"),
        ("꿀 케이크", "m01"),
        ("허니케이크", "m01"),
        ("메도빗", "m01"),
        ("메도빅 하나요", "m01"),
        ("메도빅 케이크", "m01"),
        ("나뽈레옹", "m02"),
        ("나폴래옹", "m02"),
        ("나폴레옹 케이크", "m02"),
        ("아메리카노 두개요", "m03"),
        ("아메리까노", "m03"),
        ("아메리카노랑", "m03"),
        ("블리니", "m04"),
        ("블린이요", "m04"),
        ("러시아 팬케익", "m04"),
    ],
)
def test_variants_resolve_to_menu(matcher: MenuMatcher, text: str, menu_id: str) -> None:
    result = matcher.match(text)
    assert result.decision in ("accept", "confirm")
    assert result.item is not None
    assert result.item.id == menu_id


@pytest.mark.parametrize(
    ("text", "menu_id"),
    [
        ("메도빅", "m01"),
        ("메도비크", "m01"),
        ("꿀케이크", "m01"),
        ("블리니", "m04"),
        ("아메리카노 두개요", "m03"),
        ("블린이요", "m04"),
        ("메도빅 케이크", "m01"),
    ],
)
def test_exact_name_or_alias_is_accepted(matcher: MenuMatcher, text: str, menu_id: str) -> None:
    result = matcher.match(text)
    assert result.decision == "accept"
    assert result.item is not None and result.item.id == menu_id


@pytest.mark.parametrize(
    ("text", "guess", "menu_id"),
    [
        ("메도비", "메도빅", "m01"),
        ("나뽈레옹", "나폴레옹", "m02"),
        ("아메리까노", "아메리카노", "m03"),
    ],
)
def test_fuzzy_match_alone_asks_but_with_agreeing_guess_accepts(
    matcher: MenuMatcher, text: str, guess: str, menu_id: str
) -> None:
    # 신호가 하나(자모 유사도)뿐이면 되묻고, NLU 추정이 같은 메뉴를 가리키면 수락한다
    alone = matcher.match(text)
    assert alone.decision == "confirm"
    assert alone.item is not None and alone.item.id == menu_id
    agreed = matcher.match(text, menu_guess=guess)
    assert agreed.decision == "accept"
    assert agreed.item is not None and agreed.item.id == menu_id


def test_fuzzy_accept_without_guess_can_be_allowed(matcher: MenuMatcher) -> None:
    lenient = MenuMatcher(MENU, fuzzy_accept_needs_guess=False)
    assert lenient.match("메도비").decision == "accept"


@pytest.mark.parametrize("text", ["티라미수", "짜장면", "카푸치노", "라떼", "커피", "pizza", ""])
def test_unknown_menu_is_rejected(matcher: MenuMatcher, text: str) -> None:
    result = matcher.match(text)
    assert result.decision == "reject"
    assert result.item is None


@pytest.mark.parametrize("text", ["블루베리", "케이크", "치즈케이크"])
def test_loose_matches_are_never_accepted(matcher: MenuMatcher, text: str) -> None:
    # 잘못 수락하는 비용이 잘못 거절하는 비용보다 크다 (R-Q3 Bohus & Rudnicky)
    assert matcher.match(text).decision in ("confirm", "choose", "reject")


def test_exact_name_scores_one(matcher: MenuMatcher) -> None:
    assert matcher.match("나폴레옹").score == 1.0


# ---------------------------------------------------------------- 세 구간


def test_middle_score_asks_to_confirm(matcher: MenuMatcher) -> None:
    result = matcher.match("꿀케익")
    assert REJECT_BELOW <= result.score < ACCEPT_AT
    assert result.decision == "confirm"
    assert result.item is not None and result.item.id == "m01"


def test_two_close_candidates_ask_to_choose() -> None:
    menu = [MenuItem("x1", "메도빅", 7500), MenuItem("x2", "메도빗", 7000)]
    result = MenuMatcher(menu).match("메도빋")
    assert result.decision == "choose"
    assert {m.id for m in result.candidates} == {"x1", "x2"}
    assert result.item is None


def test_shared_alias_asks_to_choose() -> None:
    menu = [
        MenuItem("x1", "초코케이크", 7500, aliases=("케이크",)),
        MenuItem("x2", "치즈케이크", 7000, aliases=("케이크",)),
    ]
    result = MenuMatcher(menu).match("케이크")
    assert result.decision == "choose"
    assert [m.id for m in result.candidates] == ["x1", "x2"]


def test_thresholds_can_be_overridden() -> None:
    # "메도비"는 혼동 비용(받침 ㄱ 빼기 0.4)으로 0.96이다 (티켓 10 전에는 0.89)
    strict = MenuMatcher(MENU, accept_at=0.97, fuzzy_accept_needs_guess=False)
    assert strict.match("메도비").decision == "confirm"
    assert MenuMatcher(MENU, fuzzy_accept_needs_guess=False).match("메도비").decision == "accept"


# ---------------------------------------------------------------- menu_guess


def test_menu_guess_raises_score(matcher: MenuMatcher) -> None:
    plain = matcher.match("꿀케익")
    guessed = matcher.match("꿀케익", menu_guess="메도빅")
    assert guessed.score > plain.score
    assert guessed.item is not None and guessed.item.id == "m01"


def test_menu_guess_for_other_menu_does_not_help(matcher: MenuMatcher) -> None:
    assert matcher.match("꿀케익", menu_guess="블린").score == matcher.match("꿀케익").score


def test_menu_guess_alone_cannot_accept_unrelated_text(matcher: MenuMatcher) -> None:
    assert matcher.match("짜장면", menu_guess="메도빅").decision == "reject"


def test_menu_guess_used_when_text_is_empty(matcher: MenuMatcher) -> None:
    result = matcher.match("", menu_guess="블린")
    assert result.decision == "accept"
    assert result.item is not None and result.item.id == "m04"


# ---------------------------------------------------------------- 품절


def _with_sold_out(*ids: str) -> list[MenuItem]:
    return [replace(m, sold_out=m.id in ids) for m in MENU]


def test_sold_out_item_is_reported_separately() -> None:
    result = MenuMatcher(_with_sold_out("m01")).match("메도빅")
    assert result.decision == "sold_out"
    assert result.item is not None and result.item.id == "m01"


def test_sold_out_on_confirm_band_is_sold_out() -> None:
    result = MenuMatcher(_with_sold_out("m01")).match("꿀케익")
    assert result.decision == "sold_out"


def test_choose_drops_sold_out_candidate() -> None:
    menu = [MenuItem("x1", "메도빅", 7500, sold_out=True), MenuItem("x2", "메도빗", 7000)]
    result = MenuMatcher(menu).match("메도빋")
    assert result.decision == "confirm"
    assert result.item is not None and result.item.id == "x2"


def test_alternatives_prefer_same_category_and_skip_sold_out() -> None:
    menu = _with_sold_out("m01")
    m01 = next(m for m in menu if m.id == "m01")
    alts = MenuMatcher(menu).alternatives(m01)
    assert [m.id for m in alts] == ["m02", "m03"]


def test_available_lists_only_items_not_sold_out() -> None:
    assert [m.id for m in MenuMatcher(_with_sold_out("m03")).available()] == ["m01", "m02", "m04"]


# ---------------------------------------------------------------- STT 오류 셋 (티켓 10)
# 07 실측(실제 CLOVA, 동현 녹음)에서 나온 받아쓰기 변형과, 걸리면 안 되는 무관 단어.
# 숫자는 자모 혼동 비용을 넣기 전 점수(맥도빅 0.78, 맥도비 0.67, 나폴내용 0.75, 볼링 0.67).


@pytest.mark.parametrize(
    ("text", "menu_id"),
    [
        ("맥도빅", "m01"),  # 메도빅: ㅔ→ㅐ, 받침 ㄱ 넣기
        ("맥도비", "m01"),  # 위 + 끝 받침 ㄱ 빼기
        ("맥도빅 두개랑", "m01"),
        ("나폴내용", "m02"),  # 나폴레옹: ㄹ→ㄴ, ㅔ→ㅐ, ㅗ→ㅛ
        ("나폴내용도 하나요", "m02"),
    ],
)
def test_measured_stt_variants_score_in_the_accept_band(
    matcher: MenuMatcher, text: str, menu_id: str
) -> None:
    # 신호 하나(자모 유사도)라 아직 되묻지만, 점수는 수락 구간이다. NLU 추정이 맞으면 수락한다
    alone = matcher.match(text)
    assert alone.item is not None and alone.item.id == menu_id
    assert alone.score >= ACCEPT_AT
    assert alone.decision == "confirm"
    name = next(m.name_ko for m in MENU if m.id == menu_id)
    assert matcher.match(text, menu_guess=name).decision == "accept"


def test_bowling_for_blin_is_asked_back(matcher: MenuMatcher) -> None:
    # 블린 → "볼링"(이전 실행 B4): ㅡ→ㅗ, 받침 ㄴ→ㅇ. 되물을 수 있는 점수여야 한다
    result = matcher.match("볼링")
    assert result.decision == "confirm"
    assert result.item is not None and result.item.id == "m04"


@pytest.mark.parametrize("text", ["메뉴", "멕시칸", "블루베리", "맥도날드", "맥주", "3잔", "6시"])
def test_unrelated_words_from_the_error_set_are_rejected(matcher: MenuMatcher, text: str) -> None:
    assert matcher.match(text).decision == "reject"


@pytest.mark.parametrize("guess", ["메도빅", "나폴레옹", "블린"])
def test_unrelated_word_is_never_accepted_even_with_a_menu_guess(
    matcher: MenuMatcher, guess: str
) -> None:
    # 무관 단어 + 엉뚱한 NLU 추정이어도 확인 없이 받지 않는다
    assert matcher.match("맥도날드", menu_guess=guess).decision != "accept"
    assert matcher.match("블루베리", menu_guess=guess).decision != "accept"
