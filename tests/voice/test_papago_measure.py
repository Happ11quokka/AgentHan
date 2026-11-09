"""Papago 실측 스크립트의 순수 계산 (4단계 티켓 04). 네트워크 없이 통계·판정·표만 본다.

기대값은 손으로 셈한 값이다. 백분위는 가장 가까운 순위(ceil(q·n)번째 작은 값)다.
"""

from __future__ import annotations

from agenthan.voice.owner.protect import mask
from agenthan.voice.papago_measure import ko_tone, latency_summary, md_table, placeholder_verdict


def test_latency_summary_uses_nearest_rank_percentiles() -> None:
    # 정렬: 100 200 300 … 1000 (10개). P50 = 5번째, P90 = 9번째
    ms = [1000, 100, 900, 200, 800, 300, 700, 400, 600, 500]
    assert latency_summary(ms) == {"n": 10, "p50": 500, "p90": 900, "max": 1000}


def test_latency_summary_of_nothing_is_empty() -> None:
    assert latency_summary([]) == {"n": 0, "p50": None, "p90": None, "max": None}


MASKED = mask("메도빅 2개 주세요", names={"메도빅": "Медовик"}, terms={})
NUMERALS = {2: ("две",)}


def test_placeholder_verdict_separates_lost_placeholders_from_changed_numbers() -> None:
    assert placeholder_verdict("[1] 2 штуки, пожалуйста", MASKED, NUMERALS) == "ok"
    assert placeholder_verdict("Дайте две [1]", MASKED, NUMERALS) == "ok"
    assert placeholder_verdict("Медовик 2 штуки", MASKED, NUMERALS) == "placeholder"
    assert placeholder_verdict("[1] [1] 2 штуки", MASKED, NUMERALS) == "placeholder"
    assert placeholder_verdict("[1] 3 штуки", MASKED, NUMERALS) == "number"


def test_ko_tone_reads_the_ending_of_the_last_sentence() -> None:
    assert ko_tone("네, 가능해요.") == "해요체"
    assert ko_tone("가능합니다!") == "합쇼체"
    assert ko_tone("드실 수 있습니까?") == "합쇼체"
    assert ko_tone("알겠다. 넣지 않는다.") == "해라체"
    assert ko_tone("미안합니다. 다 팔렸어") == "그 밖"
    assert ko_tone("견과류 없음") == "그 밖"
    assert ko_tone("") == "그 밖"


def test_md_table_escapes_pipes_and_newlines_and_shows_missing_as_dash() -> None:
    table = md_table(["문장", "ms"], [["a|b", 120], ["줄\n바꿈", None]])
    assert table == (
        "| 문장 | ms |\n"
        "|---|---|\n"
        "| a\\|b | 120 |\n"
        "| 줄 바꿈 | - |"
    )
