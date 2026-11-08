"""HCX-DASH-002 NLU (spec D2·D4, 티켓 07).

턴마다 HCX를 한 번 부른다. `parse_turn` 도구 하나를 주고 `acts[]`를 받는다.
프롬프트에는 메뉴명 목록, 장바구니 요약, 직전 AI 질문, 손님 발화 하나만 넣는다 (D4).

스파이크(티켓 07 Comments)에서 본 HCX-DASH-002의 동작:
- `toolChoice`를 객체로 강제해도 모델이 도구 대신 본문에 답을 쓰려 하면 HTTP 400
  `40009 Unsupported function`이 온다. 그래서 기본은 `"auto"`이고, 지시문에서
  parse_turn 호출을 요구한다
- `"auto"`에서는 도구를 부르기도 하고 본문에 같은 모양의 JSON(`{"acts": [...]}`)을 쓰기도 한다.
  둘 다 읽는다. 어느 쪽도 못 읽으면 `[Act("unknown")]`
- `"auto"`에서도 가끔 40009가 온다. 그때는 도구 없이 한 번 더 부른다
- 한 번 호출에 act가 여러 개 담긴다 (메뉴 둘, 메뉴 + 픽업 시간)

NLU는 조각만 뽑는다. 메뉴 판정은 매처, 옵션 판정은 options.py, 시각 계산은 timeparse가 한다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args

from agenthan.voice.matching import similarity
from agenthan.voice.nlu.base import Act, ActType, NluContext
from agenthan.voice.nlu.clova import ClovaClient, ClovaError, load_api_key

log = logging.getLogger(__name__)

MODEL = "HCX-DASH-002"
FORCE_PARSE_TURN = {"type": "function", "function": {"name": "parse_turn"}}
ACT_TYPES: tuple[str, ...] = get_args(ActType)
# 모델이 도구 호출을 못 만들었을 때 CLOVA가 주는 본문 코드 ("Unsupported function")
_TOOL_CALL_FAILED = "40009"

_TEXT_FIELDS = ("menu_text", "menu_guess", "option_text", "time_text", "free_text")
# 질문 답(menu_info.py)은 손님 말의 낱말을 본다. 모델이 바꿔 쓴 말 대신 원문을 넣는다.
_QUESTION_ACTS = frozenset({"ask_menu_info", "ask_other"})

PARSE_TURN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "parse_turn",
        "description": (
            "손님 발화 하나를 act 목록으로 나눈다. 모든 발화에 이 함수를 호출한다. "
            "주문과 상관없는 말이면 type이 unknown인 act 하나를 넣는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "acts": {
                    "type": "array",
                    "description": "발화에 나온 순서대로. 메뉴가 여러 개면 메뉴마다 act 하나",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": list(ACT_TYPES)},
                            "menu_text": {
                                "type": "string",
                                "description": "손님이 말한 메뉴 이름 그대로",
                            },
                            "menu_guess": {
                                "type": "string",
                                "description": "메뉴 목록 중 가장 가까운 이름. 모르면 빈 문자열",
                            },
                            "qty": {"type": "integer", "description": "수량. 말 안 했으면 생략"},
                            "option_text": {
                                "type": "string",
                                "description": "옵션 말 그대로 (아이스, 샷 추가, 사워크림 빼)",
                            },
                            "time_text": {"type": "string", "description": "시각 표현 그대로"},
                            "free_text": {"type": "string", "description": "요청사항·질문 원문"},
                        },
                        "required": ["type"],
                    },
                }
            },
            "required": ["acts"],
        },
    },
}

_INTRO = (
    "너는 빵집 전화 주문의 발화 분석기다. 손님 발화 하나를 읽고 parse_turn 함수를 딱 한 번 "
    "호출한다. 다른 함수는 없다. act 종류는 함수 이름이 아니라 acts 안의 type 값이다. "
    "대답 문장은 쓰지 않는다."
)

_GUIDE = """act 종류:
- add_item: 메뉴 주문. 메뉴마다 하나씩
- remove_item: 장바구니의 메뉴 빼기
- change_qty: 이미 담긴 메뉴의 수량 바꾸기. 새 수량은 qty
- set_option: 담긴 메뉴의 옵션 정하기 (온도, 샷 추가, 사워크림 빼기)
- set_pickup_time: 찾으러 오는 시각
- set_request: 옵션이 아닌 요청사항 (따로 담기, 포크·초 넣기, 잘라 주기)
- done_ordering: 주문을 다 했다는 말
- ask_menu_info: 메뉴에 대한 질문 (맛, 가격, 재료, 알레르기, 옵션)
- ask_other: 메뉴가 아닌 질문 (주차, 위치, 영업시간)
- yes / no: 직전 질문에 대한 예·아니요
- unknown: 주문과 상관없거나 알아들을 수 없는 말

규칙:
- 발화에 나온 것만 쓴다. 장바구니나 예시의 메뉴를 지어내지 않는다
- menu_text는 손님이 말한 그대로 쓴다. 메뉴 목록에 없는 이름이어도 고치지 않는다
- 메뉴 목록에 없는 메뉴를 시켜도 add_item이다. menu_guess만 빈 문자열로 둔다
- menu_guess는 메뉴 목록에서 가장 가까운 이름. 없으면 빈 문자열
- 수량을 말했으면 qty에 숫자로 쓴다 (하나 → 1, 두 잔 → 2). 같은 메뉴를 qty만큼 반복하지 않는다
- 메뉴 이름 없이 수량만 말하면 change_qty이고 menu_text는 비운다
- 옵션과 시각은 따로 뗀다. 메뉴와 시각을 같이 말하면 add_item과 set_pickup_time 두 개다
- 주문을 다 했다는 말과 시각을 같이 말하면 done_ordering과 set_pickup_time 두 개다
- 담긴 메뉴에서 재료를 빼 달라는 말(사워크림 빼)은 remove_item이 아니라 set_option이다
- "따로", "포장", "잘라"처럼 담는 방법을 말하면 set_request다
- time_text는 시각 표현을 고치지 말고 손님 말 그대로 쓴다"""


def _item(name: str, guess: str, qty: int, **extra: str) -> dict[str, Any]:
    return {"type": "add_item", "menu_text": name, "menu_guess": guess, "qty": qty, **extra}


# (장바구니, 발화, acts). 골드셋(tests/voice/nlu_gold.json)과 겹치지 않는 문장만 쓴다.
_EXAMPLES: tuple[tuple[str, str, list[dict[str, Any]]], ...] = (
    ("", "나폴레옹 두 개 주세요", [_item("나폴레옹", "나폴레옹", 2)]),
    ("", "허니케익 하나랑 차가운 아메리카노 한 잔이요", [
        _item("허니케익", "메도빅", 1),
        _item("아메리카노", "아메리카노", 1, option_text="차가운"),
    ]),
    ("", "블린 하나 주시고 다섯 시에 갈게요", [
        _item("블린", "블린", 1),
        {"type": "set_pickup_time", "time_text": "다섯 시"},
    ]),
    ("블린 하나", "블린 네 개로 해 주세요", [
        {"type": "change_qty", "menu_text": "블린", "menu_guess": "블린", "qty": 4},
    ]),
    ("메도빅 하나", "메도빅은 잘라서 주세요", [
        {"type": "set_request", "menu_text": "메도빅", "menu_guess": "메도빅",
         "free_text": "잘라서 주세요"},
    ]),
    ("", "피자 한 판 주세요", [_item("피자", "", 1)]),
    ("", "됐어요 그거면 돼요", [{"type": "done_ordering"}]),
    ("", "화장실 있어요?", [{"type": "ask_other", "free_text": "화장실 있어요?"}]),
    ("", "어 그러니까", [{"type": "unknown"}]),
)


def _example_lines() -> str:
    lines = ["예시 (발화 → parse_turn 인자):"]
    for cart, said, acts in _EXAMPLES:
        args = json.dumps({"acts": acts}, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"- {f'(장바구니: {cart}) ' if cart else ''}\"{said}\" → {args}")
    return "\n".join(lines)


_SYSTEM = "\n\n".join((_INTRO, _GUIDE, _example_lines()))


def build_messages(text: str, ctx: NluContext) -> list[dict[str, str]]:
    """시스템(메뉴명·장바구니·직전 질문) + 손님 발화 하나 (D4). 대화 기록은 넣지 않는다."""
    system = (
        f"{_SYSTEM}\n\n"
        f"메뉴 목록: {', '.join(ctx.menu_names) or '없음'}\n"
        f"장바구니: {ctx.cart_summary or '비어 있음'}\n"
        f"직전 질문: {ctx.last_question or '없음'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def acts_from_result(result: Mapping[str, Any], text: str, ctx: NluContext) -> list[Act]:
    """CLOVA `result` → act 목록. 도구 호출 인자를 먼저, 없으면 본문의 JSON을 읽는다."""
    message = result.get("message")
    if not isinstance(message, dict):
        return _unknown(text)
    args = _tool_args(message.get("toolCalls"))
    if args is None:
        args = _content_json(message.get("content"))
    return acts_from_args(args, text, ctx)


def acts_from_args(args: Any, text: str, ctx: NluContext) -> list[Act]:
    """`parse_turn` 인자 검증. 틀린 act는 버리고, 남는 게 없으면 `[Act("unknown")]`."""
    raw = args.get("acts") if isinstance(args, dict) else None
    if not isinstance(raw, list):
        return _unknown(text)
    acts = [act for item in raw if (act := _to_act(item, text, ctx)) is not None]
    return acts or _unknown(text)


def _to_act(item: Any, text: str, ctx: NluContext) -> Act | None:
    """act 하나 검증 + 원문 대조. 버릴 것이면 None.

    모델이 발화에 없는 메뉴를 지어내거나("6시 반에 갈게요" → 아메리카노 추가) 시각을 바꿔 쓰는
    ("6시 반" → "6:30") 일이 스파이크에서 보였다. 그래서 원문에 근거가 없는 조각은 믿지 않는다.
    - menu_text는 발화의 원래 구간으로 바꾼다. 비슷한 구간이 없으면 menu_text·menu_guess를
      비우고, 메뉴 없는 add_item은 버린다
    - 줄을 고치는 act에서 모델이 쓴 메뉴가 발화에 없는데 손님이 다른 메뉴를 말했으면 unknown
    - time_text가 비었거나 발화에 없으면 발화 전체를 넣는다 (timeparse가 문장에서 시각을 찾는다)
    - 질문(ask_*)의 free_text는 늘 발화 원문이다 (menu_info.py가 손님 말의 낱말을 본다)
    - 메뉴 질문에 메뉴가 비었으면 발화에 그대로 나온 메뉴명을 넣는다
    """
    if not isinstance(item, dict) or item.get("type") not in ACT_TYPES:
        return None
    kind = item["type"]
    fields = {k: v.strip() for k in _TEXT_FIELDS if isinstance(v := item.get(k), str)}
    if fields.get("menu_guess") not in ctx.menu_names:
        fields.pop("menu_guess", None)  # 목록 밖 추정("없음" 등)은 버린다. 판정은 매처가 한다
    wrote_menu = bool(fields.get("menu_text"))
    if said := _said_span(fields.get("menu_text", ""), text):
        fields["menu_text"] = said
    else:
        fields.pop("menu_text", None)
        fields.pop("menu_guess", None)
        if kind == "add_item":
            return None
        if wrote_menu and kind in _LINE_ACTS and _names_some_menu(text, ctx):
            # 손님은 메뉴를 말했는데 모델이 쓴 메뉴가 발화에 없다. 메뉴 없는 act로 두면 엔진이
            # 직전 줄을 고치므로(엉뚱한 줄 빼기) 되묻게 한다 (리뷰 C12)
            return Act("unknown", free_text=text)
    time_text = _squash(fields.get("time_text", ""))
    if kind == "set_pickup_time" and (not time_text or time_text not in _squash(text)):
        fields["time_text"] = text  # 비었거나 바꿔 쓴 시각 (리뷰 C11)
    if kind == "ask_menu_info" and not fields.get("menu_text"):
        # "메도빅 많이 달아요"에서 모델이 메도빅을 놓친 때
        named = [n for n in ctx.menu_names if n in text]
        if named:
            fields["menu_text"] = fields["menu_guess"] = min(named, key=text.index)
    needs_text = kind in ("unknown", "set_request") and not fields.get("free_text")
    if kind in _QUESTION_ACTS or needs_text:
        fields["free_text"] = text
    return Act(kind, qty=_qty(item.get("qty")), **fields)


# 장바구니의 줄 하나를 고치는 act. 메뉴를 안 말하면 엔진이 직전 줄을 고친다
_LINE_ACTS = frozenset({"remove_item", "change_qty", "set_option", "set_request"})


def _names_some_menu(text: str, ctx: NluContext) -> bool:
    """발화에 메뉴 목록의 어떤 이름과 비슷한 구간이 있나 (손님이 메뉴를 말했나)."""
    return any(_said_span(name, text) for name in ctx.menu_names)


_SPACE = re.compile(r"[\s.,!?~]+")
_PUNCT = ".,!?~"
_SAID_AT = 0.5  # 이보다 낮으면 발화에 없는 메뉴로 본다 ("블리니" ↔ "블린" = 0.56)


def _squash(s: str) -> str:
    return _SPACE.sub("", s)


def _said_span(fragment: str, text: str) -> str:
    """모델이 쓴 메뉴 조각 → 손님이 실제로 말한 구간. 근거가 없으면 "".

    모델은 표기를 고쳐 쓴다("메도빜" → "메도빅"). 그대로 넘기면 매처가 보는 근거가 모델 추정
    둘(menu_text·menu_guess)뿐이 된다. 판정은 손님 말로 하므로(D5) 발화에서 가장 비슷한
    어절 묶음을 찾아 그 원문을 넘긴다. 모델 추정은 menu_guess로만 남는다.
    """
    f = _squash(fragment)
    if not f or f in _squash(text):
        return fragment if f else ""
    words = [w.strip(_PUNCT) for w in text.split()]
    k = len(fragment.split())
    best, best_score = "", 0.0
    for size in sorted({max(1, k - 1), k, k + 1}):
        for i in range(len(words) - size + 1):
            span = " ".join(words[i : i + size])
            if (score := similarity(_squash(span), f)) > best_score:
                best, best_score = span, score
    return best if best_score >= _SAID_AT else ""


def _qty(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdecimal():
        value = int(value)
    return value if isinstance(value, int) and 0 < value <= 99 else None


def _tool_args(calls: Any) -> Any:
    if not isinstance(calls, list):
        return None
    for call in calls:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict) and fn.get("name") == "parse_turn":
            args = fn.get("arguments")
            return _content_json(args) if isinstance(args, str) else args
    return None


def _content_json(content: Any) -> Any:
    """본문에서 첫 JSON 객체를 꺼낸다. 앞뒤 군말·코드펜스·닫는 괄호 찌꺼기는 무시한다."""
    if not isinstance(content, str) or (start := content.find("{")) < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(content[start:])
    except ValueError:
        return None
    return value


def _unknown(text: str) -> list[Act]:
    return [Act("unknown", free_text=text)]


class HcxNlu:
    """`Nlu` 구현. 호출이 실패하거나 응답을 못 읽으면 `[Act("unknown")]`을 돌려준다.

    엔진은 unknown을 실패로 세고 다시 묻는다. 같은 질문에 세 번이면 FALLBACK이다.

    `tools=True`(기본, spec D2)면 parse_turn 도구를 준다. 모델이 도구 호출에 실패해 `40009`가
    오면 도구 없이 한 번 더 부른다(프롬프트의 JSON 예시대로 본문에 답한다).
    `tools=False`면 처음부터 도구 없이 부른다. 스파이크에서 정확도는 비슷했고 지연은 절반 아래였다.
    """

    def __init__(
        self,
        client: ClovaClient,
        *,
        model: str = MODEL,
        tools: bool = True,
        tool_choice: str | Mapping[str, Any] = "auto",
        temperature: float = 0.1,
    ) -> None:
        self._client = client
        self._model = model
        self._tools = tools
        self._tool_choice = tool_choice
        self._temperature = temperature

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env"), **kwargs: Any) -> HcxNlu:
        key = load_api_key(env_file)
        if not key:
            raise ValueError("CLOVASTUDIO_API_KEY가 없어요. 환경 변수나 .env에 넣어 주세요.")
        return cls(ClovaClient(key), **kwargs)

    def payload(self, text: str, ctx: NluContext, *, tools: bool | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": build_messages(text, ctx)}
        if self._tools if tools is None else tools:
            payload |= {"tools": [PARSE_TURN_TOOL], "toolChoice": self._tool_choice}
        return payload | {"maxTokens": 1024, "temperature": self._temperature}  # FC 문서 권장값

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        for tools in (True, False) if self._tools else (False,):
            try:
                result = self._client.chat(self._model, self.payload(text, ctx, tools=tools))
            except ClovaError as err:
                log.warning("HCX NLU 호출 실패: %s (code=%s, tools=%s)", err, err.code, tools)
                if err.code == _TOOL_CALL_FAILED:
                    continue
                return _unknown(text)
            return acts_from_result(result, text, ctx)
        return _unknown(text)

