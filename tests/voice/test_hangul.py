"""자모 분해·자모 편집거리 단위 테스트."""

import pytest

from agenthan.voice.hangul import (
    confusable_distance,
    decompose,
    final_consonant,
    has_batchim,
    jamo_levenshtein,
    levenshtein,
)


def test_decompose_syllable() -> None:
    assert decompose("빅") == ("ㅂ", "ㅣ", "ㄱ")
    assert decompose("비") == ("ㅂ", "ㅣ", " ")
    assert decompose("가") == ("ㄱ", "ㅏ", " ")
    assert decompose("힣") == ("ㅎ", "ㅣ", "ㅎ")


def test_decompose_lone_jamo() -> None:
    assert decompose("ㄱ") == ("ㄱ", " ", " ")
    assert decompose("ㅏ") == (" ", "ㅏ", " ")


@pytest.mark.parametrize("c", ["a", "1", " ", "?"])
def test_decompose_non_korean_is_none(c: str) -> None:
    assert decompose(c) is None


def test_plain_levenshtein() -> None:
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "abc") == 0


@pytest.mark.parametrize(
    ("a", "b", "dist"),
    [
        ("메도빅", "메도빅", 0),
        ("메도빅", "메도빗", 1 / 3),  # 종성 하나
        ("메도빅", "메도비", 1 / 3),  # 종성 빠짐
        ("나폴레옹", "나뽈레옹", 1 / 3),  # 초성 ㅍ→ㅃ
        ("블린", "블리", 1 / 3),
        ("메도빅", "메도", 1),  # 음절 하나 삭제
        ("메도빅", "", 3),
        ("", "메도빅", 3),
        ("아메리카노", "아메리까노", 1 / 3),
        ("a", "메", 1),  # 한글이 아니면 대체 비용 1
    ],
)
def test_jamo_levenshtein(a: str, b: str, dist: float) -> None:
    assert jamo_levenshtein(a, b) == pytest.approx(dist)


def test_jamo_levenshtein_is_symmetric() -> None:
    forward = jamo_levenshtein("메도비크", "메도빅")
    assert forward == pytest.approx(jamo_levenshtein("메도빅", "메도비크"))


def test_jamo_distance_is_finer_than_plain() -> None:
    assert jamo_levenshtein("메도빅", "메도빗") < levenshtein("메도빅", "메도빗")


@pytest.mark.parametrize("c", ["", "메도", "ab"])
def test_decompose_needs_exactly_one_char(c: str) -> None:
    assert decompose(c) is None


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("메도빅", "메도빗"),
        ("블린", "블리니"),
        ("아메리카노", "아이스"),
        ("a메", "메b"),
        ("", "블린"),
    ],
)
def test_jamo_levenshtein_symmetric_cases(a: str, b: str) -> None:
    assert jamo_levenshtein(a, b) == pytest.approx(jamo_levenshtein(b, a))


def test_substitution_counts_each_differing_jamo_as_a_third() -> None:
    assert jamo_levenshtein("가", "개") == pytest.approx(1 / 3)  # 중성
    assert jamo_levenshtein("가", "객") == pytest.approx(2 / 3)  # 중성 + 종성
    assert jamo_levenshtein("가", "뷁") == pytest.approx(1)  # 셋 다
    assert jamo_levenshtein("ㄱ", "가") == pytest.approx(1 / 3)  # 낱자음 = 빈 중성


@pytest.mark.parametrize(
    ("word", "batchim", "final"),
    [
        ("블린", True, "ㄴ"),
        ("아메리카노", False, ""),
        ("5시 25분", True, "ㄴ"),
        ("6시", False, ""),
        ("메도빅", True, "ㄱ"),
        ("일", True, "ㄹ"),
        ("ㄱ", False, ""),  # 낱자는 음절이 아니다
        ("abc", False, ""),
        ("", False, ""),
    ],
)
def test_batchim_of_last_syllable(word: str, batchim: bool, final: str) -> None:
    assert has_batchim(word) is batchim
    assert final_consonant(word) == final


def test_plain_levenshtein_works_on_tuples() -> None:
    assert levenshtein(("ㅂ", "ㅣ", "ㄱ"), ("ㅂ", "ㅣ", " ")) == 1


# ---------------------------------------------------------------- STT 혼동 비용 (티켓 10)


@pytest.mark.parametrize(
    ("a", "b", "dist"),
    [
        ("메도빅", "맥도빅", (0.15 + 0.4) / 3),  # ㅔ/ㅐ + 받침 ㄱ 넣기
        ("빅", "비", 0.4 / 3),  # 받침 ㄱ 빼기
        ("사", "샤", 0.35 / 3),  # ㅏ/ㅑ
        ("나", "라", 0.5 / 3),  # 초성 ㄴ/ㄹ
        ("사", "싸", 0.3 / 3),  # 평음/경음
        ("가", "카", 0.5 / 3),  # 평음/격음
        ("블", "볼", 0.5 / 3),  # ㅡ/ㅗ
        ("린", "링", 0.5 / 3),  # 받침 ㄴ/ㅇ (콧소리끼리)
        ("메도빅", "메도빗", 1 / 3),  # 표에 없는 쌍은 그대로 1
        ("빅", "빌", 1 / 3),  # 받침 ㄹ 넣기·빼기는 싸지 않다
        ("메도빅", "메도", 1),  # 음절 넣기·빼기는 그대로 1
    ],
)
def test_confusable_distance_makes_common_stt_slips_cheap(a: str, b: str, dist: float) -> None:
    assert confusable_distance(a, b) == pytest.approx(dist)
    assert confusable_distance(b, a) == pytest.approx(dist)


def test_confusable_distance_never_exceeds_plain_jamo_distance() -> None:
    for a, b in [("메도빅", "맥도비"), ("나폴레옹", "나폴내용"), ("블루베리", "블리니")]:
        assert confusable_distance(a, b) <= jamo_levenshtein(a, b)
