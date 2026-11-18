"""손님 질문에 가게 자료로 답하기 (5단계 티켓 11).

예전에는 질문 문장을 정규식으로 갈라 답을 골랐다(`menu_info`). 실기기 통화에서 STT가 조금만 틀려도
("많이 타나요", "불린의결과류 들어가") 종류를 못 잡았고, "홀케이크면 달다는 거야?"에 옵션 가격을
답했다. 이제 질문의 뜻은 LLM이 읽고, 코드는 답이 가게 자료에 있는 말인지만 검사한다.

    facts = store_facts(config, menu, reviews, notes)   # 가게 데이터 → 자료 줄
    reply = answerer.answer(AnswerRequest(text, facts, focus="메도빅", recent=(...)))
    reply = checked(reply, request)                      # 근거·숫자·"안 들어가요" 검사
    reply.kind  # "answer"(말해도 됨) · "unsure"(질문인데 자료에 없음) · "not_question"

가게마다 다른 것은 자료 줄뿐이다. 코드에 가게 이름·메뉴 이름이 없다.

검사(`checked`)는 LLM 답을 믿지 않고 셋을 본다. 하나라도 걸리면 "unsure"로 낮춘다.
1. 근거: LLM이 댄 근거 문구가 자료에 그대로 있어야 한다
2. 숫자: 답에 나온 숫자(가격·시각)는 자료나 손님 말에 있어야 한다
3. 없다는 말: "안 들어가요"·"없어요"는 사장님 메모가 근거일 때만 (알레르기 표시에 없다고 안
   들어간다는 뜻이 아니다, 4단계 티켓 05)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Literal, Protocol, runtime_checkable

from agenthan.core.contracts import MenuItem, Review
from agenthan.voice.dialog import templates
from agenthan.voice.store_config import RequiredOption, StoreConfig

AnswerKind = Literal["answer", "unsure", "not_question"]

OWNER_NOTE = "사장님 메모"  # 자료 줄에서 사장님이 채운 지식의 표시 (`checked`의 3번)
MAX_ANSWER_CHARS = 140  # 전화로 듣기에 두 문장쯤
MAX_REVIEWS_PER_MENU = 2
# 근거가 자료와 글자 그대로 같지 않을 때: 이어서 겹치는 글자 수와 근거에서 차지하는 몫
EVIDENCE_MIN_RUN = 3
EVIDENCE_MIN_SHARE = 0.5
# 메뉴가 이보다 많으면 지금 말하는 메뉴만 자세히, 나머지는 이름·가격만 준다
FULL_FACTS_MENUS = 40


@dataclass(frozen=True)
class AnswerRequest:
    text: str  # 손님 말 (STT 글 그대로)
    facts: tuple[str, ...]  # 가게 자료 줄 (`store_facts`)
    focus: str = ""  # 지금 말하는 메뉴 이름 (한국어). 없으면 ""
    recent: tuple[str, ...] = ()  # 직전 대화 몇 줄 ("손님: …", "AI: …")


@dataclass(frozen=True)
class AnswerReply:
    kind: AnswerKind
    say: str = ""  # kind가 "answer"일 때 손님께 할 말
    evidence: str = ""  # LLM이 댄 근거 (자료의 문구)
    menu: str = ""  # 질문이 가리키는 메뉴 이름 (자료에 있는 이름, 없으면 "")
    reason: str = ""  # "unsure"로 낮춘 까닭 (로그용)


UNSURE = AnswerReply("unsure")


class Answerer(Protocol):
    """가게 자료로만 답하는 것. 실패(시간 초과 등)면 예외 대신 `UNSURE`를 돌려준다."""

    def answer(self, request: AnswerRequest) -> AnswerReply: ...


@runtime_checkable
class Notes(Protocol):
    """사장님이 채운 지식을 자료 줄로 (`knowledge.KnowledgeStore.notes`). 메뉴 id ""는 가게 전체."""

    def notes(self, menu_id: str) -> tuple[str, ...]: ...


# ------------------------------------------------------------ 가게 자료


def _clock(hour: int, minute: int) -> str:
    half = "오전" if hour < 12 else "오후"
    text = f"{half} {hour % 12 or 12}시"
    return text + (f" {minute}분" if minute else "")


def store_facts(
    config: StoreConfig, menu: Sequence[MenuItem], reviews: Iterable[Review] = (),
    notes: Notes | None = None, *, focus_id: str = "",
) -> tuple[str, ...]:
    """가게 데이터를 LLM에 줄 자료 줄로. 첫 줄은 가게, 다음은 메뉴마다 한 줄."""
    by_menu: dict[str, list[str]] = {}
    for review in reviews:
        for menu_id in review.menu_ids:
            by_menu.setdefault(menu_id, []).append(review.text)
    store = [
        f"가게: {config.store_name}",
        f"영업시간: {_clock(config.open_time.hour, config.open_time.minute)}부터 "
        f"{_clock(config.close_time.hour, config.close_time.minute)}까지",
        f"주문하면 준비에 {config.prep_minutes}분쯤 걸림",
        "전화로는 찾으러 오는 주문(포장)만 받음",
    ]
    store += [f"{OWNER_NOTE}: {n}" for n in (notes.notes("") if notes else ())]
    brief = len(menu) > FULL_FACTS_MENUS
    lines = [" | ".join(store)]
    for item in menu:
        full = not brief or item.id == focus_id
        lines.append(_menu_line(
            item, config.required_options.get(item.id, ()),
            by_menu.get(item.id, ())[:MAX_REVIEWS_PER_MENU] if full else (),
            notes.notes(item.id) if notes and full else (), full=full))
    return tuple(lines)


def _menu_line(item: MenuItem, required: Sequence[RequiredOption], reviews: Sequence[str],
               notes: Sequence[str], *, full: bool) -> str:
    parts = [f"메뉴 {item.name_ko}" + (" (오늘 품절)" if item.sold_out else ""),
             f"가격 {templates.won(item.price)}"]
    if not full:
        return " | ".join(parts)
    if item.category:
        parts.append(f"종류 {item.category}")
    if item.aliases:
        parts.append(f"다른 이름 {', '.join(item.aliases)}")
    if item.description:
        parts.append(f"설명: {item.description}")
    for group in required:
        parts.append(f"꼭 고를 것 {group.name}: {'/'.join(c.name for c in group.choices)}")
    if item.options:
        parts.append("옵션: " + ", ".join(_option(o.name, o.extra_price, o.available)
                                        for o in item.options))
    if item.allergens:
        parts.append(f"알레르기 표시: {', '.join(item.allergens)}")
    parts += [f"손님 후기: {text}" for text in reviews]
    parts += [f"{OWNER_NOTE}: {note}" for note in notes]
    return " | ".join(parts)


def _option(name: str, extra: int, available: bool) -> str:
    text = name + (f"(+{templates.won(extra)})" if extra else "(추가 금액 없음)")
    return text + ("" if available else " (오늘 안 됨)")


# ------------------------------------------------------------ 답 검사

_SQUASH = re.compile(r"[\s.,!?~\"'“”‘’()·:|+/-]")
_NUMBER = re.compile(r"\d[\d,]*")
# 들어 있지 않다·없다·안 된다는 말. 자료에 "없다"는 근거가 있을 때만 해도 된다
_DENIES = re.compile(
    r"안\s*들어|들어가지\s*않|들어\s*있지\s*않|들어있지\s*않|안\s*넣|넣지\s*않|안\s*써|쓰지\s*않"
    r"|없어요|없습니다|없고|안\s*돼요|안\s*됩니다|불가")
# 주문을 받거나 고치겠다는 말. 답변기는 장바구니를 못 바꾸므로 이런 답은 버린다
_PROMISES = re.compile(r"드릴게요|드리겠|해\s*드릴|빼\s*드|담아|준비해\s*둘|바꿔\s*드")
# 묻지 않은 권하는 말. 지시문으로 막아도 가끔 붙어서 그 문장만 뗀다
_PUSHY = re.compile(r"드셔\s*보|추천\s*드려요|어떠세요|어떠실까요|참고|좋을\s*(?:거|것)\s*같"
                    r"|주문해\s*주|걱정")
# 한 문장 끝에 붙은 권하는 말("…촉촉하다고 하니 참고 부탁드려요")은 그 꼬리만 고친다
_PUSHY_TAIL = re.compile(r"(다고|라고) 하니 참고 (?:부탁드려요|해\s*주세요|하세요)\.")
# 메뉴 이름만 늘어놓은 답("○○, ○○ 있어요")에서 이름을 빼고 남아도 되는 말
_LIST_WORDS = re.compile(
    r"저희|가게|메뉴|로는|에는|이렇게|그리고|있어요|있습니다|이랑|랑|하고|케이크|[은는이가과와도요]")
_MENU_NAME = re.compile(r"^메뉴 ([^|(]+?)(?: \(오늘 품절\))? \|")
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")


def _squash(text: str) -> str:
    return _SQUASH.sub("", text)


def _numbers(text: str) -> set[int]:
    return {int(n.replace(",", "")) for n in _NUMBER.findall(text)}


# 자료 칸의 머리말. 이것만 옮긴 근거("알레르기 표시")는 근거가 아니다
_LABELS = re.compile(
    r"알레르기\s*표시|손님\s*후기|사장님\s*메모|영업시간|다른\s*이름|꼭\s*고를\s*것"
    r"|설명|옵션|가격|종류|메뉴|가게")


def _fact_with(evidence: str, facts: Sequence[str]) -> str:
    """근거 문구가 든 자료 조각(` | `로 나눈 한 칸). 없으면 ""."""
    wanted = _squash(evidence)
    if len(_squash(_LABELS.sub("", evidence))) < 2:
        return ""
    parts = [part for line in facts for part in line.split(" | ")]
    for part in parts:
        if wanted in _squash(part):
            return part
    # 모델이 한두 낱말을 바꿔 옮기기도 한다("너무 달지 않고" → "많이 달지 않고"). 근거의 대부분이
    # 한 칸에 이어서 나오면 그 칸을 근거로 본다
    for part in parts:
        fact = _squash(part)
        same = SequenceMatcher(None, wanted, fact, autojunk=False).find_longest_match().size
        if same >= EVIDENCE_MIN_RUN and same >= EVIDENCE_MIN_SHARE * len(wanted):
            return part
    return ""


def checked(reply: AnswerReply, request: AnswerRequest) -> AnswerReply:
    """LLM 답을 가게 자료에 대어 본다(모듈 설명 1~3). 걸리면 "unsure"와 까닭."""
    if reply.kind != "answer":
        return reply
    say = " ".join(reply.say.replace("+", "").split())
    say = _PUSHY_TAIL.sub(r"\1 하세요.", say)
    say = " ".join(part for part in _SENTENCE_END.split(say) if not _PUSHY.search(part))
    if not say:
        return replace(UNSURE, reason="empty")
    if len(say) > MAX_ANSWER_CHARS:
        say = " ".join(_SENTENCE_END.split(say)[:2])
        if len(say) > MAX_ANSWER_CHARS:
            return replace(UNSURE, reason="too_long")
    if _PROMISES.search(say):
        return AnswerReply("not_question", reason="promises")
    source = _fact_with(reply.evidence, request.facts)
    if not source and _only_lists_menus(say, request.facts):
        return replace(reply, say=say)
    if not source:
        return replace(UNSURE, evidence=reply.evidence, reason="no_evidence")
    known = _numbers(" ".join(request.facts)) | _numbers(request.text)
    if not _numbers(say) <= known:
        return replace(UNSURE, evidence=reply.evidence, reason="number")
    denies_in_fact = bool(_DENIES.search(source)) or "안 됨" in source or "품절" in source
    if _DENIES.search(say) and not (source.startswith(OWNER_NOTE) or denies_in_fact):
        return replace(UNSURE, evidence=reply.evidence, reason="denies")
    return replace(reply, say=say)


def _only_lists_menus(say: str, facts: Sequence[str]) -> bool:
    """답이 자료의 메뉴 이름 둘 이상을 늘어놓기만 한다("메도빅, 블린 있어요"). 근거 문구가
    없어도 이름이 다 자료에 있으니 말해도 된다(무엇을 파는지·추천 질문)."""
    names = [m.group(1) for line in facts if (m := _MENU_NAME.match(line))]
    rest, found = _squash(say), 0
    for name in sorted(names, key=len, reverse=True):
        if (squashed := _squash(name)) in rest:
            rest, found = rest.replace(squashed, ""), found + 1
    return found >= 2 and not _LIST_WORDS.sub("", rest)


def notes_from(known: Mapping[str, Sequence[str]]) -> Notes:
    """테스트·도구용: 메뉴 id → 메모 줄."""

    class _Fixed:
        def notes(self, menu_id: str) -> tuple[str, ...]:
            return tuple(known.get(menu_id, ()))

    return _Fixed()
