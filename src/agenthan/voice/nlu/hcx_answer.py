"""HCX로 손님 질문에 답하기 (5단계 티켓 11). 포트와 검사는 `dialog.answerer`.

지시문에는 가게 이름·메뉴 이름이 없다. 가게 자료 줄(`store_facts`)만 요청마다 넣는다.
답은 세 줄 글로 받는다(JSON보다 작은 모델이 덜 틀린다).

    종류: 답            ← 답 · 모름 · 질문아님
    메뉴: 메도빅        ← 질문이 가리키는 메뉴 (없으면 없음)
    근거: 설명: 꿀을 넣은 얇은 시트  ← 자료에서 그대로 옮긴 문구
    답: 네, 꿀이 들어간 케이크라 달콤한 편이에요.

실패(시간 초과·오류·형식 틀림)는 예외 없이 `UNSURE`다. 엔진은 그때 예전 규칙 답으로 돌아간다.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from agenthan.voice.dialog.answerer import UNSURE, AnswerKind, AnswerReply, AnswerRequest
from agenthan.voice.nlu.budget import UsageLog
from agenthan.voice.nlu.clova import ClovaClient, ClovaError, load_api_key

log = logging.getLogger(__name__)

MODEL = "HCX-005"
MODEL_ENV = "PHONE_ANSWER_MODEL"
MAX_TOKENS = 256
TIMEOUT_S = 4.0  # 전화에서 이보다 오래 기다리게 하지 않는다

SYSTEM = """\
너는 가게 전화를 받는 직원이다. 손님이 한 말에 [가게 자료]만 보고 답한다.
손님 말은 전화 음성을 글로 바꾼 것이라 글자가 틀릴 수 있다. 소리가 비슷한 말로 뜻을 헤아린다
(예: "많이 타나요" → "많이 다나요", "결과류" → "견과류", 메뉴 이름이 조금 틀려도 그 메뉴).

아래 네 줄로만 답한다. 다른 말은 쓰지 않는다.
종류: 답 | 모름 | 질문아님
메뉴: 질문이 가리키는 메뉴 이름 (자료에 적힌 이름 그대로, 없으면 없음)
근거: 답의 근거가 된 자료 문구를 한 군데만, 글자 그대로 옮긴다 (종류가 답이 아니면 없음)
답: 손님께 할 말 (종류가 답이 아니면 없음)

종류 고르기
- 답: 질문이고, 자료에 답이 적혀 있다.
- 모름: 질문인데 자료에 답이 없다. 추측하지 않는다. 자료에 없는 것(주차, 배달, 할인, 예약,
  보관, 칼로리, 원산지 등)은 모두 모름이다.
- 질문아님: 주문·수정·시간·대답·인사처럼 물어보는 말이 아니다. 무엇을 넣어 달라·빼 달라·바꿔
  달라는 말, 뜻을 알 수 없는 말도 질문아님. 너는 주문을 받거나 고치지 않는다.

답 쓰기
- 한두 문장, 전화로 말하듯 짧고 공손한 "~요" 말투("~습니다" 말고 "~요"). 묻는 것에 먼저 답한다.
- 묻는 것만 답하고 "~이에요.", "~해요.", "~하세요."로 끝낸다. "드셔 보세요", "참고해 주세요",
  "주문해 주세요" 같은 권하는 말과 되묻는 말은 쓰지 않는다.
- 자료에 적힌 사실만 말한다. 가격·시간 숫자는 자료 그대로 쓴다.
- 맛·식감 질문은 설명과 손님 후기로 답한다. 후기를 쓸 때는 "손님들은 ~라고 하세요"처럼 전한다.
- 재료·알레르기: "알레르기 표시"나 설명에 적혀 있으면 "들어가요"라고 답한다. 적혀 있지 않으면
  안 들어간다고 하지 말고 모름으로 한다. "사장님 메모"에 적힌 것은 그대로 답해도 된다.
- 무엇을 파는지·추천·인기 메뉴("뭐가 잘 나가요", "뭐가 맛있어요")를 물으면 종류는 답이다.
  어느 것이 제일 인기인지는 자료에 없으니 말하지 않고, "○○, ○○, ○○ 있어요"처럼 자료의 메뉴
  이름만 네 개까지 말한다. 근거에는 그중 한 메뉴의 "메뉴 ○○"를 옮긴다.
- 근거는 반드시 이번 [가게 자료]에 있는 문구다. 예시 대화의 자료를 쓰지 않는다."""

# 형식을 굳히는 예시 대화. 실제 가게와 겹치지 않는 가상의 분식집이다(지시문에 가게를 박지 않는다)
_EXAMPLE_FACTS = (
    "가게: 예시분식 | 영업시간: 오전 11시부터 오후 8시까지 | 주문하면 준비에 10분쯤 걸림"
    " | 전화로는 찾으러 오는 주문(포장)만 받음",
    "메뉴 떡볶이 | 가격 4,000원 | 설명: 고추장 양념에 어묵을 넣은 매콤한 떡볶이"
    " | 옵션: 치즈 추가(+1,000원) | 알레르기 표시: 밀, 대두 | 손님 후기: 생각보다 안 맵고 달달해요",
    "메뉴 김밥 | 가격 3,500원 | 설명: 단무지·계란·시금치를 넣은 기본 김밥",
)
_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("떡보끼 마니 매어요", "종류: 답\n메뉴: 떡볶이\n근거: 생각보다 안 맵고 달달해요\n"
     "답: 매콤한 편인데, 손님들은 생각보다 안 맵고 달달하다고 하세요."),
    ("김밥에 깨 들어가요?", "종류: 모름\n메뉴: 김밥\n근거: 없음\n답: 없음"),
    ("몇 시에 문 닫아요", "종류: 답\n메뉴: 없음\n근거: 오후 8시까지\n답: 오후 8시까지 해요."),
    ("김밥 두 줄 주세요", "종류: 질문아님\n메뉴: 김밥\n근거: 없음\n답: 없음"),
    ("뭐가 맛있어요", "종류: 답\n메뉴: 없음\n근거: 메뉴 떡볶이\n답: 떡볶이랑 김밥 있어요."),
    ("떡볶이 하나는 치즈 넣고 하나는 그냥", "종류: 질문아님\n메뉴: 떡볶이\n근거: 없음\n답: 없음"),
)

# 모델이 "답변"·"질문 아님"처럼 조금 다르게 쓰기도 한다
_KIND: dict[str, AnswerKind] = {"답": "answer", "답변": "answer", "대답": "answer",
                                "모름": "unsure", "질문아님": "not_question"}
_FIELD = re.compile(r"^\s*(종류|메뉴|근거|답)\s*[:：]\s*(.*)$")
_NONE = {"", "없음", "없음.", "-"}


def _user(request: AnswerRequest) -> str:
    blocks = ["[가게 자료]", *request.facts]
    if request.recent:
        blocks += ["", "[직전 대화]", *request.recent]
    if request.focus:
        blocks += ["", f"[지금 이야기하던 메뉴] {request.focus}"]
    blocks += ["", f"[손님 말] {request.text}", "", "종류·메뉴·근거·답 네 줄로:"]
    return "\n".join(blocks)


def build_messages(request: AnswerRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM}]
    for text, reply in _EXAMPLES:
        messages.append({"role": "user", "content": _user(AnswerRequest(text, _EXAMPLE_FACTS))})
        messages.append({"role": "assistant", "content": reply})
    return [*messages, {"role": "user", "content": _user(request)}]


def parse_reply(content: str) -> AnswerReply:
    """모델 글 → `AnswerReply`. 형식이 틀리면 `UNSURE`."""
    fields: dict[str, str] = {}
    for line in content.splitlines():
        found = _FIELD.match(line)
        if found and found.group(1) not in fields:
            fields[found.group(1)] = found.group(2).strip()
    kind = _KIND.get(fields.get("종류", "").replace(" ", ""))
    if kind is None:
        return AnswerReply("unsure", reason="bad_format")

    def value(name: str) -> str:
        text = fields.get(name, "")
        return "" if text in _NONE else text

    if kind != "answer":
        return AnswerReply(kind, menu=value("메뉴"))
    return AnswerReply(kind, say=value("답"), evidence=value("근거"), menu=value("메뉴"))


class HcxAnswerer:
    def __init__(
        self, client: ClovaClient, *, model: str = MODEL, temperature: float = 0.1,
        usage: UsageLog | None = None,
    ) -> None:
        """`usage`를 주면 호출마다 사용량을 JSONL에 남긴다(6단계)."""
        self._client = client
        self._model = model
        self._temperature = temperature
        self._usage = usage

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env"), **kwargs: Any) -> HcxAnswerer:
        key = load_api_key(env_file)
        if not key:
            raise ValueError("CLOVASTUDIO_API_KEY가 없어요. 환경 변수나 .env에 넣어 주세요.")
        model = load_api_key(env_file, name=MODEL_ENV)
        if model:
            kwargs.setdefault("model", model)
        http = httpx.Client(timeout=httpx.Timeout(TIMEOUT_S, connect=2.0))
        return cls(ClovaClient(key, http=http), **kwargs)

    def payload(self, request: AnswerRequest) -> dict[str, Any]:
        return {"messages": build_messages(request), "maxTokens": MAX_TOKENS,
                "temperature": self._temperature}

    def answer(self, request: AnswerRequest) -> AnswerReply:
        started = time.monotonic()
        try:
            result = self._client.chat(self._model, self.payload(request))
        except ClovaError as err:
            log.warning("HCX 답변기 호출 실패: %s (code=%s)", err, err.code)
            self._record(err.code or "error", None, started)
            return UNSURE
        self._record("ok", result.get("usage"), started)
        message = result.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            return AnswerReply("unsure", reason="bad_format")
        return parse_reply(content)

    def _record(self, status: str, usage: Any, started: float) -> None:
        if self._usage is not None:
            self._usage.record("answer", status, usage=usage, max_tokens=MAX_TOKENS,
                               ms=round((time.monotonic() - started) * 1000))
