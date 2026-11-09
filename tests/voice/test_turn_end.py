"""끝말 규칙 (3단계 spec "끝말 규칙", 티켓 02). 순수 함수라 표로 시험한다.

반례는 `research/s3-verify-turn-end.md` "규칙 단서 반례" 표에서 가져왔다.
"""

from __future__ import annotations

import pytest

from agenthan.voice.call.turn_end import classify_turn_end, combine_turn_end, question_kind

MENU = ("메도빅", "나폴레옹", "아메리카노", "블린", "꿀케이크")


def judge(text: str, *, kind: str = "open", state: str = "ordering"):
    return classify_turn_end(text, state=state, question_kind=kind, menu_names=MENU)


@pytest.mark.parametrize(
    "text",
    [
        "아메리카노 두 잔하고",
        "메도빅 두 개랑",
        "블린이랑",
        "사장님이랑",
        "블린 하나 하고",
    ],
)
def test_connective_endings_continue(text: str) -> None:
    assert judge(text).label == "continue"


@pytest.mark.parametrize(
    "text",
    ["차갑게 해서", "데워서", "따뜻하게 하면", "아이스면", "포장으로 하면"],
)
def test_seo_and_myeon_connectives_continue(text: str) -> None:
    assert judge(text).label == "continue"


@pytest.mark.parametrize(
    "text",
    [
        "아무거나",  # -거나: 완결된 대답 (검증 노트 반례)
        "뭐든지",
        "뭐라고",  # 다시 말해 달라는 뜻
        "짜장면",  # -면으로 끝나는 명사 (net59k "짜장면있나요.")
        "냉면",
        "라면 있어요",
        "여기서",  # 장소 답
        "김민서",  # 사람 이름
        "이현서",
        "그리고요",  # "그리고"에 요가 붙으면 끝
        "그리고 끝이에요",  # 접속사는 끝 토큰일 때만
        "사랑",
    ],
)
def test_counterexamples_do_not_continue(text: str) -> None:
    assert judge(text).label == "end"


@pytest.mark.parametrize(
    "text",
    ["메도빅 하나 그리고", "그리고", "아 그리고", "또", "근데", "그런데", "그래서", "그럼",
     "그러면"],
)
def test_trailing_conjunctions_continue(text: str) -> None:
    assert judge(text).label == "continue"


@pytest.mark.parametrize(
    "text",
    ["음", "음…", "어", "어어", "그", "저기", "음 저기", "음..."],
)
def test_filler_only_utterances_continue_and_say_so(text: str) -> None:
    verdict = judge(text)
    assert (verdict.label, verdict.filler_only) == ("continue", True)


def test_a_trailing_filler_continues_but_is_not_filler_only() -> None:
    verdict = judge("아메리카노 어")
    assert (verdict.label, verdict.filler_only) == ("continue", False)


def test_a_filler_glued_to_a_word_is_not_a_filler() -> None:
    # STT가 필러를 붙여 쓴다("음맞는"). 필러는 띄어쓰기로 본다 (검증 노트)
    assert judge("음맞는 것 같아 네 맞아요.").label == "end"


@pytest.mark.parametrize(
    "text",
    ["메도빅 두", "블린 세", "아메리카노 다섯", "메도빅 2", "메도빅2", "010"],
)
def test_a_number_without_a_unit_continues(text: str) -> None:
    # CLOVA는 수를 아라비아 숫자로 적는다("2") (s2-stt-misrecognition)
    assert judge(text).label == "continue"


@pytest.mark.parametrize(
    "text",
    ["메도빅 하나", "블린 둘", "메도빅 2개", "두 개", "6시 반"],
)
def test_a_number_with_a_unit_or_a_count_word_ends(text: str) -> None:
    assert judge(text).label == "end"


@pytest.mark.parametrize(
    "text",
    ["나폴", "메도빅 하나랑 나폴", "아메리", "꿀케"],
)
def test_a_menu_name_prefix_continues(text: str) -> None:
    # 실측 잘림 "나폴"(나폴레옹) (net59k, 티켓 10)
    verdict = judge(text)
    assert verdict.label == "continue"
    assert verdict.reason.startswith("menu_prefix")


@pytest.mark.parametrize(
    "text",
    ["나폴레옹", "블린", "메도빅", "메", "블"],
)
def test_a_whole_menu_name_or_one_syllable_is_not_a_prefix(text: str) -> None:
    assert judge(text).label == "end"


@pytest.mark.parametrize(
    "text",
    ["네", "네 네", "예", "맞아요", "아니요", "아뇨", "아니요."],
)
def test_yes_or_no_after_a_confirm_question_is_a_firm_end(text: str) -> None:
    verdict = judge(text, kind="confirm", state="confirm")
    assert (verdict.label, verdict.firm, verdict.reason) == ("end", True, "yes_no_after_confirm")


def test_yes_after_an_open_question_ends_but_not_firmly() -> None:
    # "더 필요하신 거 있으세요?" → "네" 뒤에는 메뉴가 이어질 수 있다. 모델이 들어오면 모델이 본다
    verdict = judge("네", kind="open")
    assert (verdict.label, verdict.firm) == ("end", False)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("메도빅 하나요", "final_ending"),
        ("두 개요", "final_ending"),
        ("그게 다예요", "final_ending"),
        ("포장해 갈게요", "final_ending"),
        ("이거 맛있나", "default"),
        ("메도빅 많이 달아요?", "final_ending"),
        ("6시 반", "default"),
        ("짜장면있나요.", "period"),
    ],
)
def test_end_reasons(text: str, reason: str) -> None:
    verdict = judge(text)
    assert (verdict.label, verdict.reason) == ("end", reason)
    assert verdict.firm is ("메도빅" in text)  # 열린 질문 뒤에는 메뉴가 든 말만 확실 (리뷰 P2)


@pytest.mark.parametrize(
    "text",
    ["하나하고요", "포장이고요", "메도빅이랑요", "차갑게 해서요", "하나 하고 싶은데"],
)
def test_ambiguous_endings_end_but_not_firmly(text: str) -> None:
    # "-고요"류는 나열 중간일 때가 많다. 규칙은 애매로 두고 모델·상한에 맡긴다 (spec)
    verdict = judge(text)
    assert (verdict.label, verdict.firm) == ("end", False)
    assert verdict.reason.startswith("ambiguous")


def test_a_continuation_cue_beats_the_period() -> None:
    assert judge("메도빅 두 개랑.").label == "continue"


@pytest.mark.parametrize(
    "text",
    ["잠깐만요", "잠시만요", "잠깐만", "잠깐만 기다려 주세요", "아메리카노 한 잔이랑 잠시만요"],
)
def test_wait_requests(text: str) -> None:
    verdict = judge(text)
    assert (verdict.label, verdict.firm) == ("wait_request", True)


@pytest.mark.parametrize(
    ("text", "backchannel"),
    [("네", True), ("네네", True), ("응", True), ("음", True), ("그래요", True), ("예 예", True),
     ("네 두 개요", False), ("메도빅", False)],
)
def test_backchannel_looking_utterances_are_flagged(text: str, backchannel: bool) -> None:
    # 듣는 중이면 답으로 처리하지만 기록은 남긴다(끼어들기 판정을 맞출 근거, spec 이야기 14)
    assert judge(text).backchannel is backchannel


@pytest.mark.parametrize(
    ("question", "state", "kind"),
    [
        ("메도빅 하나, 총 3만 2천 원이에요. 6시 반에 오시는 거 맞으세요?", "confirm", "confirm"),
        ("준비에 20분 정도 걸려요. 오후 4시 20분 괜찮으세요?", "ask_pickup", "confirm"),
        ("혹시 메도빅 말씀하신 거 맞으세요?", "clarify", "confirm"),
        ("오후 6시에 오시는 거죠?", "ask_pickup", "confirm"),
        ("주문 취소할까요?", "ordering", "confirm"),
        ("나폴레옹은 어떠세요?", "ordering", "confirm"),
        ("아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?", "clarify", "closed"),
        ("몇 시쯤 오세요?", "ask_pickup", "closed"),
        ("메도빅이랑 나폴레옹 중에 어떤 거요?", "clarify", "closed"),
        ("메도빅 하나요. 더 필요하신 거 있으세요?", "ordering", "open"),
        ("", "ordering", "open"),
    ],
)
def test_question_kinds(question: str, state: str, kind: str) -> None:
    assert question_kind(question, state) == kind


# ---------------------------------------------------------------- 규칙 + 턴 모델 합치기


def combined(text: str, score: float | None, *, kind: str = "open"):
    return combine_turn_end(judge(text, kind=kind), score)


def test_without_a_model_score_the_rule_decides() -> None:
    decision = combined("메도빅 두 개랑", None)
    assert (decision.label, decision.path, decision.score) == ("continue", "rule", None)


@pytest.mark.parametrize(
    ("text", "score", "label", "path"),
    [
        ("메도빅 두 개랑", 0.8, "end", "model"),  # 0.7 이상이면 끝남 쪽
        ("메도빅 두 개랑", 0.5, "continue", "rule"),  # 가운데는 규칙
        ("6시 반", 0.2, "continue", "model"),  # 0.3 이하면 이어짐 쪽
        ("6시 반", 0.5, "end", "rule"),
        ("하나하고요", 0.1, "continue", "model"),  # 애매는 모델이 가른다
        ("메도빅 하나요", 0.1, "end", "rule"),  # 확실한 끝은 모델보다 앞선다
        ("잠깐만요", 0.1, "wait_request", "rule"),
        ("음", 0.9, "continue", "rule"),  # 필러만 있는 말은 끝난 주문이 아니다
    ],
)
def test_model_score_bands(text: str, score: float, label: str, path: str) -> None:
    decision = combined(text, score)
    assert (decision.label, decision.path, decision.score) == (label, path, score)


def test_a_continue_verdict_carries_the_extra_wait_and_an_end_carries_none() -> None:
    # 추가 대기 = 마지막 말 끝부터 기다릴 상한 (spec "끝말 규칙" 출력, grilling Q3)
    assert judge("메도빅 두 개랑").wait_s == 2.0
    assert judge("메도빅 하나요").wait_s == 0.0
    tuned = classify_turn_end("메도빅 두", state="ordering", question_kind="closed",
                              menu_names=MENU, cont_wait_s=1.5)
    assert tuned.wait_s == 1.5
    assert combined("6시 반", 0.2).wait_s == 2.0  # 모델이 이어짐으로 돌리면 같은 상한
    assert combined("메도빅 두 개랑", 0.9).wait_s == 0.0


def test_yes_after_confirm_beats_a_low_model_score() -> None:
    assert combined("네", 0.05, kind="confirm").label == "end"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("메도빅 하나요", "open"),  # 메뉴가 든 주문
        ("아메리카노 두 잔 주세요.", "open"),
        ("메도빅 많이 달아요?", "open"),
        ("두 개요", "closed"),  # 닫힌 질문(몇 개·언제)에 대한 답
        ("6시 반에 갈게요.", "closed"),
    ],
)
def test_a_firm_end_needs_a_complete_order(text: str, kind: str) -> None:
    # 리뷰 P2: 확실한 끝 = 마침표·종결어미 + 완결된 주문(메뉴가 들었거나 닫힌·확인 질문의 답)
    verdict = judge(text, kind=kind)
    assert (verdict.label, verdict.firm) == ("end", True)
    assert combined(text, 0.1, kind=kind).path == "rule"


@pytest.mark.parametrize("text", ["그게 다예요", "포장해 갈게요", "짜장면있나요.", "제가 어제요."])
def test_a_final_ending_without_an_order_ends_but_the_model_can_hold_it(text: str) -> None:
    # 리뷰 P2: 열린 질문 뒤 메뉴 없는 "-요"·마침표는 끝이지만 확실하지 않다. 낮은 점수면 기다린다
    verdict = judge(text, kind="open")
    assert (verdict.label, verdict.firm) == ("end", False)
    decision = combined(text, 0.1)
    assert (decision.label, decision.path) == ("continue", "model")
    assert combined(text, 0.5).label == "end"  # 가운데 점수면 규칙대로 끝
