"""끝말 규칙: 손님 말이 끝났나 (3단계 spec "끝말 규칙", 티켓 02). 순수 함수, 상태 없음.

`dialog/rules.py`는 "무슨 말인가"를, 이 모듈은 "말이 끝났나"를 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from agenthan.voice.dialog.rules import WAIT_REQUEST, yes_or_no
from agenthan.voice.hangul import BLANK, decompose

Label = Literal["end", "continue", "wait_request"]
CONT_WAIT_S = 2.0  # 이어짐 상한 기본값(초): 마지막 말 끝부터 (grilling Q3)
# 직전 질문 종류 (Raux & Eskenazi 2008): 확인(예/아니요) · 닫힌(고르기·시각) · 열린
QuestionKind = Literal["confirm", "closed", "open"]

_PUNCT = re.compile(r"[.,!?~…·]+")

# 연결어미 단서의 단어 예외 (검증 노트 "규칙 단서 반례"). 끝 토큰이 이 말이면 이어짐 단서가 아니다
_NOT_GO = frozenset({"뭐라고", "최고", "창고", "재고", "참고", "광고", "사고", "망고"})
_NOT_RANG = frozenset({"사랑", "자랑", "노랑", "파랑"})
_NOT_SEO = frozenset({"여기서", "거기서", "저기서", "어디서"})
# -면으로 끝나는 명사. 받침 있는 앞 글자(짜장면·냉면·쫄면)는 규칙이 이미 거른다
_NOT_MYEON = frozenset({"라면", "소면", "쌀면", "화면", "표면", "생면", "건면"})
# -아서/-어서의 앞 글자 모음 (해서·데워서·넣어서·와서·빼서·드셔서). 받침이 없어야 한다
_A_EO = frozenset("ㅏㅓㅐㅘㅝㅕㅙㅔ")


# 접속사로 끝나면 이어짐. 끝 토큰일 때만 본다("그리고 끝이에요"는 끝, "그리고요"는 끝)
_CONJUNCTIONS = frozenset({
    "그리고", "그다음", "그다음에", "또", "근데", "그런데", "그래서", "그럼", "그러면", "아니면",
})
# 필러. 띄어쓰기로 본다(STT가 "음맞는"처럼 붙여 쓰면 필러가 아니다)
_FILLERS = frozenset({
    "음", "으음", "음음", "어", "어어", "으", "그", "저", "저기", "뭐더라", "그러니까",
})
# 단위 없이 끝나면 이어지는 수 (관형형). "하나·둘·셋"은 수량 답이라 끝이다.
# "네"는 대답일 수 있어 뺀다
_BARE_NUMBERS = frozenset({
    "한", "두", "세", "서", "석", "너", "넉", "다섯", "여섯", "일곱", "여덟", "아홉", "열",
    "스무", "열한", "열두", "열세", "열네",
})
_DIGITS_AT_END = re.compile(r"\d+$")
# 맞장구 목록 (spec: 하나로 둔다). 듣는 중이면 답, 봇이 말하는 중이면 맞장구로 본다
BACKCHANNELS = frozenset({"네", "예", "응", "어", "아", "음", "그래요", "네네"})
# 해요체 '요'가 연결어미·명사 뒤에 붙은 꼴과 -는데·-거든요. 나열 중간일 때가 많아 애매로 둔다
_AMBIGUOUS = ("고요", "랑요", "서요", "면요", "는데", "은데", "거든요")
_FINAL_ENDINGS = ("요", "다", "까", "죠")


@dataclass(frozen=True)
class TurnEndVerdict:
    label: Label
    reason: str
    # 확실한 판정. 확실한 끝(확인 질문 뒤 예/아니요, 마침표, 종결어미)은 턴 모델보다 앞선다
    firm: bool = False
    filler_only: bool = False  # 필러만 있는 말. 상한까지 안 이어지면 엔진에 넘기지 않는다
    backchannel: bool = False  # 맞장구로 보이는 말 (기록용)
    wait_s: float = 0.0  # 추가 대기: 이어짐이면 마지막 말 끝부터 기다릴 상한(초), 아니면 0


# 엔진 템플릿(`dialog/templates.py`)의 질문 끝. 예/아니요로 답하는 질문
_CONFIRM_TAILS = ("맞으세요?", "괜찮으세요?", "거죠?", "할까요?", "어떠세요?")
_CLOSED_TAILS = ("드릴까요?", "오세요?", "어떤 거요?")


def question_kind(last_question: str, state: str) -> QuestionKind:
    """엔진의 직전 질문과 상태 → 질문 종류. 복창(CONFIRM)은 늘 확인 질문이다."""
    q = last_question.strip()
    if state == "confirm" or q.endswith(_CONFIRM_TAILS):
        return "confirm"
    if q.endswith(_CLOSED_TAILS):
        return "closed"
    return "open"


def _tokens(text: str) -> list[str]:
    return _PUNCT.sub(" ", text).split()


def _before_last(token: str) -> tuple[str, str, str] | None:
    """끝 글자 앞 음절의 (초성, 중성, 종성)."""
    return decompose(token[-2]) if len(token) >= 2 else None


def _connective(token: str) -> str | None:
    """끝 토큰이 연결어미·연결 조사로 끝나면 그 어미, 아니면 None."""
    if token.endswith("랑") and len(token) >= 2 and token not in _NOT_RANG:
        return "랑"
    if token.endswith("고") and len(token) >= 2 and token not in _NOT_GO:
        return "고"
    jamo = _before_last(token)
    if token.endswith("서") and token not in _NOT_SEO:
        if jamo is not None and jamo[2] == BLANK and jamo[1] in _A_EO:
            return "서"
    if token.endswith("면") and token not in _NOT_MYEON:
        if jamo is not None and jamo[2] == BLANK:
            return "면"
    return None


def _menu_prefix(tokens: list[str], menu_names: tuple[str, ...]) -> str | None:
    """끝 토큰(또는 끝 두 토큰을 붙인 것)이 메뉴 이름의 앞부분일 뿐이면 그 메뉴 이름."""
    names = [n.replace(" ", "") for n in menu_names]
    tails = [tokens[-1]] + (["".join(tokens[-2:])] if len(tokens) >= 2 else [])
    for tail in tails:
        if len(tail) < 2 or tail in names:
            continue
        for name in names:
            if name.startswith(tail):
                return name
    return None


def classify_turn_end(
    text: str,
    *,
    state: str,
    question_kind: str,
    menu_names: tuple[str, ...],
    cont_wait_s: float = CONT_WAIT_S,
) -> TurnEndVerdict:
    """확정 글 하나가 끝난 말인가.

    입력은 STT 확정 글, 엔진의 대화 상태(`NluContext.state`), 직전 질문 종류(`question_kind`),
    메뉴 이름, 이어짐 상한. 규칙의 목표는 이어짐의 정밀도다. 틀려서 이어짐이면 최대 상한만큼
    늦고, 틀려서 끝남이면 말을 자른다. 그래서 기본은 끝남이고 이어짐은 글에 단서가 보일 때만 건다.
    `state`는 지금 규칙에서 질문 종류를 거쳐서만 쓴다(상태별 대기 표를 넣을 자리).
    """
    tokens = _tokens(text)
    if not tokens:
        return TurnEndVerdict("end", "empty")
    verdict = _judge(text, tokens, question_kind, menu_names)
    if verdict.label == "continue":
        verdict = replace(verdict, wait_s=cont_wait_s)
    if is_backchannel(text):
        verdict = replace(verdict, backchannel=True)
    return verdict


def is_backchannel(text: str) -> bool:
    """맞장구로 보이는 말("네", "네네", "응", "음")인가. 듣는 중이면 답이고, 기록에만 쓴다."""
    tokens = _tokens(text)
    return bool(tokens) and all(t in BACKCHANNELS for t in tokens)


def _judge(
    text: str, tokens: list[str], question_kind: str, menu_names: tuple[str, ...]
) -> TurnEndVerdict:
    last = tokens[-1]
    if WAIT_REQUEST.search(" ".join(tokens)):
        return TurnEndVerdict("wait_request", "wait_request", firm=True)
    if all(t in _FILLERS for t in tokens):
        return TurnEndVerdict("continue", "filler_only", firm=True, filler_only=True)
    if question_kind == "confirm" and yes_or_no(text) is not None:
        return TurnEndVerdict("end", "yes_no_after_confirm", firm=True)
    if last in _FILLERS:
        return TurnEndVerdict("continue", f"filler:{last}")
    if last in _CONJUNCTIONS:
        return TurnEndVerdict("continue", f"conjunction:{last}")
    if last in _BARE_NUMBERS or _DIGITS_AT_END.search(last):
        return TurnEndVerdict("continue", f"bare_number:{last}")
    prefix_of = _menu_prefix(tokens, menu_names)
    if prefix_of is not None:
        return TurnEndVerdict("continue", f"menu_prefix:{prefix_of}")
    ending = _connective(last)
    if ending is not None:
        return TurnEndVerdict("continue", f"connective:{ending}")
    for tail in _AMBIGUOUS:
        if last.endswith(tail):
            return TurnEndVerdict("end", f"ambiguous:{tail}")
    if text.rstrip().endswith("."):
        return TurnEndVerdict("end", "period", firm=True)  # CLOVA usePeriodEpd (검증 교정 10)
    if last.endswith(_FINAL_ENDINGS):
        return TurnEndVerdict("end", "final_ending", firm=True)
    return TurnEndVerdict("end", "default")


# ---------------------------------------------------------------- 규칙 + 턴 모델 합치기

Path = Literal["rule", "model", "cap", "button"]
MODEL_LOW = 0.3  # 턴 모델(Smart Turn) 끝남 확률이 이 값 이하면 이어짐 쪽
MODEL_HIGH = 0.7  # 이 값 이상이면 끝남 쪽. 그 사이는 끝말 규칙이 정한다


@dataclass(frozen=True)
class TurnDecision:
    """한 번의 말 끝 판정. 세션이 이것으로 기다릴지 넘길지 정하고 턴 기록에 남긴다.

    `path`: rule(끝말 규칙) · model(턴 모델 점수) · cap(이어짐 상한까지 말이 없음) ·
    button("말 끝" 버튼)
    """

    label: Label
    reason: str
    path: Path
    score: float | None  # 턴 모델 끝남 확률. 모델이 없거나 늦거나 실패하면 None
    verdict: TurnEndVerdict
    wait_s: float = 0.0  # 이어짐이면 마지막 말 끝부터 기다릴 상한(초)

    def as_record(self) -> dict[str, object]:
        return {
            "label": self.label, "reason": self.reason, "path": self.path, "score": self.score,
            "wait_s": self.wait_s, "backchannel": self.verdict.backchannel,
        }


def combine_turn_end(
    verdict: TurnEndVerdict,
    score: float | None,
    *,
    low: float = MODEL_LOW,
    high: float = MODEL_HIGH,
    cont_wait_s: float = CONT_WAIT_S,
) -> TurnDecision:
    """끝말 규칙 판정 + 턴 모델 점수 → 판정 (spec "규칙과 모델 합치기").

    확실한 판정(`firm`: 확인 질문 뒤 예/아니요, 마침표·종결어미, 기다림 요청, 필러만)은 모델보다
    앞선다. 점수가 없으면(모델 꺼짐·늦음·실패) 규칙만 쓴다. 티켓 02에서는 늘 `score=None`이다.
    Smart Turn(티켓 03)은 세션이 점수를 받아 여기에 넘기기만 하면 된다.
    """
    if score is None or verdict.firm or low < score < high:
        return TurnDecision(verdict.label, verdict.reason, "rule", score, verdict, verdict.wait_s)
    if score >= high:
        return TurnDecision("end", f"model>={high}", "model", score, verdict)
    return TurnDecision("continue", f"model<={low}", "model", score, verdict, cont_wait_s)
