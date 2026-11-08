"""자모 분해·자모 편집거리 단위 테스트."""

import pytest

from agenthan.voice.jamo import decompose, jamo_levenshtein, levenshtein


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
