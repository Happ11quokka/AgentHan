"""대사 → TTS 문장 나누기 (spec TTS: 문장 단위, 150자 이하)."""

from agenthan.voice.call.sentences import split_sentences


def test_splits_at_sentence_ends() -> None:
    assert split_sentences("메도빅 하나, 6시 반 픽업, 총 7,500원이에요. 맞으세요?") == [
        "메도빅 하나, 6시 반 픽업, 총 7,500원이에요.", "맞으세요?"
    ]


def test_long_sentence_is_cut_at_commas_then_spaces_within_limit() -> None:
    text = (
        "메도빅 하나, 나폴레옹 두 개, 아메리카노 차갑게 세 잔, "
        "블린 하나 포장으로 해서 총 금액이에요."
    )
    parts = split_sentences(text, limit=30)
    assert all(len(p) <= 30 for p in parts)
    assert " ".join(parts) == text
    assert parts[0] == "메도빅 하나, 나폴레옹 두 개,"


def test_text_without_any_break_is_cut_by_length() -> None:
    parts = split_sentences("가" * 320)
    assert [len(p) for p in parts] == [150, 150, 20]
