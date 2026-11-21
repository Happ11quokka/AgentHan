"""사장님 허브 (4단계 spec "사장님 허브"). `OrderSink`와 `OwnerChannel`을 구현해 카드를 메모리에
두고 구독자에게 보낸다.

    hub = OwnerHub(stores=stores, clock=clock)  # 가게 스냅숏 공급자 (`store_snapshot.py`)
    phone_router(..., order_sink=hub, owner=hub, ...)  # 통화 세션이 주문·통화 상황을 넘긴다
    app.include_router(owner_router(hub, key=...))  # 사장님 화면 SSE·POST (`http.py`)

- 가게 하나, 메모리 저장, uvicorn 워커 1개 전제. 서버를 다시 띄우면 카드가 사라진다
- 카드는 통화 id(= `Order.id`)마다 하나다. 이벤트는 카드 전체를 덮어쓴다(바뀐 칸만 보내지 않는다)
- 주문 카드(`order.update`): 확정 주문. 처리 상태 new(확인 대기) → accepted(수락) (주문 승인 티켓
  02). 복창에 "네"가 오면 통화 세션이 확인 요청을 연다(`open_approval`). 그때 주문 카드를 바로
  만든다: `request`(요청 번호), `stage`(in_call 통화 중 · after_call 통화 뒤. 통화 중 대기 0인
  기본 설정의 요청은 처음부터 after_call, 전화 메뉴·주문 현황 티켓 04), `requested_at`,
  `deadline_at`(통화 중 = 연 시각 + `approval_wait_s`, 통화 뒤 = 연 시각 + `approval_expire_s`),
  `decided_at`, `delay_min`, `withdrawn`(기다리는 중에 손님이 고침 changed · 취소 cancelled, 결정을
  안 받는다). 결정은 한 번만 받는다(두 번째는 `AlreadyDecided`). 수락하면 확정 문자를 보내고(통화
  중이면 아래처럼 통화가 말한 뒤) 통화 세션의 받는 곳으로 결정을 넘긴다. 통화 끝 `submit`은 같은
  id의 확인 카드를 그대로 둔다. 확인
  요청 없이 온 확정 주문(승인 꺼짐·사장님 화면 없음)은 `submit`에서 accepted + `auto_accepted`로
  만들고 확정 문자를 그때 보낸다. 사장님 화면 연결 수(`events` 구독자)가 `approval_ready`다
- 통화 중 결정은 통화가 말해야 굳는다 (주문 승인 리뷰 경합): 통화 중에 받은 수락·늦춤·거절은
  카드를 바로 결정으로 바꾸고(화면에 보이고 두 번째 결정은 409) 세션에 넘기지만, 손님 문자·결정
  기록은 세션이 그 결정을 손님에게 말하는 턴을 열 때(`approval_said`, 질문 카드 `answer_said`와
  같은 모양)까지 미룬다. 그 전에 손님이 고치거나 취소해 요청을 거두면 거둔 쪽이 이긴다: 결정 칸을
  비우고 "고치는 중"·손님 취소로 둔다(확정 문자·수락 기록 없음). 말하지 못하고 통화가 끝나면(시간
  초과 대사가 먼저 나감·손님이 끊음) 통화 뒤 결정처럼 굳힌다. 결정 → 할 일 표는 `_settle` 한 곳이다
- 늦춘 수락 (주문 승인 티켓 04, `accept(..., delay_min=15|30)`): 카드는 accepted + `delay_min`,
  `owner_pickup_time`(바뀐 픽업 시각 ISO)·`owner_pickup_label`(`18:45`). 통화 뒤면 늦춘 수락
  문자를 바로 보낸다. 통화 중이면 결정을 세션에 넘기고, 통화가 말하면
  `delay_reply` asking(손님이 바뀐 시각을 듣는 중)으로 둔다. 손님이 "네"면 통화 끝
  `submit`으로 확정 주문이 오고 늦춘 수락 문자를 보낸다(yes). "아니요"·취소면 세션이
  그 요청을 취소로 거두고 카드는 처리 상태 cancelled(no, 문자 없음)가 된다. 답 없이
  통화가 끝나면(끊음·무응답) 통화 뒤 늦춘 수락과 같게 문자를 보낸다("").
  `submit`과 통화 끝 중 먼저 온 쪽이 한 번만 보낸다
- 결정 기록(티켓 10, `decisions.py`): `order.update`를 보낼 때마다 카드의 결정(처리 상태·사유·
  결정 시각·바뀐 픽업 시각)이 지난번과 달라졌으면 `decision_sink`로 넘긴다. 결정을 바꾸는 코드는
  카드 칸만 채우고 `order.update`를 보내면 된다. sink가 없으면 메모리(카드)만
- 통화 뒤 대기와 자동 취소 (주문 승인 티켓 03): 결정 없이 통화가 끝나면(어떤 이유든, 손님이 대기
  중 고치거나 취소한 요청은 빼고. 고치는 중에 끝난 카드는 손님 취소로 닫는다) 카드는 `stage`
  after_call, 마감은 요청을 연 시각 + `approval_expire_s`(기본 5분)다. 그 안에 수락하면
  확정 문자만 간다(통화는 끝났다). 마감이 지나면 처리 상태
  expired(자동 취소, 결정을 안 받는다 → `AlreadyDecided`)가 되고 손님에게 자동 취소 문자를 남긴다.
  마감은 허브 시계(`clock.now()`)로 잰다: 허브가 가장 이른 마감에 루프 타이머를 걸어
  `expire_due`를 부르고, 결정(`accept`)·새 구독(`events`) 앞에서도 한 번 부른다(타이머가 늦어도
  마감 지난 카드에 결정이 들어가지 않게). 시험은 가짜 시계를 옮기고 `expire_due`를 부른다
- 거절 (주문 승인 티켓 05, `reject`): 확인 대기 카드 → rejected + `reject_reason`(busy 바빠서 못
  맞춤 · closed 영업·가게 사정 · free 직접 쓰기 · out_of_stock 재료 소진, 아래). 손님에게
  사유별 취소 문자(kind rejected)를 남기고, 통화 중이면 결정(사유)을 세션에 넘긴다(손님은 사과를
  듣고 통화가 취소로 끝난다. 통화 카드는 거절 카드가 대신한다). 직접 쓰기 글은 `reject_text`, 번역
  포트로 옮긴 한국어는 `reject_text_ko`. 문자는 번역 인용 틀(번역이 안 되면 원문 +
  `translate_failed`). 결정 한 번 규칙은 수락과 같다
- 재료 소진과 오늘 품절 (주문 승인 티켓 06): 재료 소진 거절은 주문에 든 메뉴 중 떨어진 것
  (`reject_menu_ids`, 하나 이상)을 받는다. 그 메뉴들은 그 자리에서 오늘 품절이 된다: 허브가 메뉴
  id 집합과 날짜(허브 시계 = 가게 시간대)를 들고, 날짜가 바뀌면 비운다(자정 루프 타이머와
  `roll_day`). 바뀔 때마다 `owner.sold_out`(`{"date", "items": [{"menu_id", "name_ko",
  "name_owner"}]}`)을 보내고 `snapshot`의 `sold_out`에도 같은 모양으로 넣는다. 카드의 `sold_out`
  (같은 줄 모양)은 그 카드에서 품절로 바꾼 메뉴다("품절로 바꿨어요 [되돌리기]", 되돌리기는
  `undo_sold_out`). 통화 세션은 `sold_out_today`를 통화 시작·결정 직전에 엔진 메뉴에 덮어 쓴다.
  비서 "내 가게" 메뉴·DB 메뉴 표는 건드리지 않는다. 통화 중 재료 소진이면 손님은 빼거나 바꿀 수
  있어 취소 문자를 바로 보내지 않는다: 손님이 떨어진 메뉴만 빼 확정하면(세션이 남은 메뉴의
  확정 주문을 `submit`) 그 카드가 남은 메뉴로 accepted가 되고 확정 문자가 간다. 바꿔서 새 번호로
  다시 요청하면 새 확인 카드가 대신한다(품절 표시는 이어 받는다). 둘 다 아니고 통화가 끝나면
  그때 메뉴 이름이 든 취소 문자를 보낸다. 통화 뒤 재료 소진 거절은 그 자리에서 취소 문자
- 통화 카드(`call.update`, 티켓 02): 통화 중(live) → 확정되면 ordered(같은 id의 주문 카드가 생기고
  통화 카드는 빠진다) · 넘김이면 handoff("손님 연락 필요", 장바구니 + 마지막 손님 말 3줄) · 확정
  없이 끝나면 missed(회색 미완료, 담긴 메뉴 + 끝난 이유). handoff·missed는 사장님이 닫는다(closed)
- 질문 카드(`owner.question` 새 카드 · `owner.question.update` 바뀐 카드, 티켓 05·06): 근거 없는
  메뉴 질문. 상태: 열림(open) · 답함(answered) · 시간 초과(timeout, 세션이 알림) · 통화 끝
  (call_ended, 열린 채 통화가 끝남). 시간 초과·통화 끝 카드도 답을 받는다(늦은 답 → 손님 문자,
  티켓 07). 자유 입력 답(free)은 사장님 글을 `answer_text`, 번역을 `answer_text_ko`에 둔다. 답은
  한 번만 받고 통화 세션의 받는 곳(`open_question`의 `reply`)으로 넘긴다. 카드 열람
  (`seen_at`)도 답 전이면 세션에 한 번 알린다. 마감 = 보낸 시각 + `question_wait_s`
- 통화 자막(`call.caption`, 티켓 10): 통화 중(live) 카드의 손님 말(`customer_said`)·AI 말
  (`bot_said`) 최근 4줄. data는 `{"call_id", "lines": [{"n", "who": customer|bot, "text",
  "text_owner"}]}`로 그 통화 자막 전체다. 손님 말은 원문으로 먼저 보내고 번역이 오면 같은 번호의
  줄을 덮어써 다시 보낸다(실패하면 원문만). AI 말은 번역하지 않는다(`text_owner` = "").
  CLOVA Speech `translation.targets`는 쓰지 않는다(응답 형식이 문서에 없다, spec "Papago 실측").
  통화가 끝나면(`call_ended`) 자막을 버린다. ordered·handoff가 된 통화도 snapshot에 자막을 안 넣는다
- 손님 주문 현황 (전화 메뉴·주문 현황 티켓 04, `order_status`): 주문 번호(통화 id)로 주문 카드
  하나를 손님 모양(줄·금액·픽업 시각·단계·결과·사유)으로 돌려준다. 단계·결과 규칙은
  `order_status.py`. 카드가 없으면(서버가 다시 뜸·상한으로 지움) 전화 주문 기록(`archive`, DB)의
  마지막 상태로 같은 모양을 만든다(`lookup_order_status`, 티켓 06, `order_archive.py`). 주문 현황
  구독(`watch_order`, 티켓 05)은 처음에 `order.status`(지금 현황), 그 뒤 손님 현황이 달라질 때마다
  `order.status`(현황 전체)를 받는다. 주문 카드가 바뀌면(`order.update`를 보낼 때) 다시 재서
  지난번 보낸 것과 다를 때만 보낸다
- 손님 문자 미리보기 (티켓 07, `sms.py` 문장): 실제 발송은 없다. 통화 id마다 보낸 순서로 쌓고
  주문 카드·통화 카드의 `sms`에 싣는다(문자가 생기면 그 카드를 다시 보낸다). 종류: 확정 주문
  (order), 확인 요청 자동 취소(expired, 주문 승인 티켓 03), 늦은 버튼 답(late_answer, 시간
  초과·통화 끝 카드에 온 답), 자유 입력 답(free_answer,
  제시간이든 늦든. 사장님 글을 한국어로 번역해 넣고, 안 되면 원문 + `translate_failed`).
  손님 통화 화면은 `call.ended`(세션이 `customer_texts`로 담음)와 `watch_texts` 스트림으로 받는다
- "손님이 궁금해한 것" 카드(`knowledge.update`, 티켓 13): AI가 답하지 못한 메뉴 질문. 보충 요청
  (`knowledge_wanted`, 손님은 "사장님께 전해 둘게요"를 들음)·질문 카드 모르겠어요·시간 초과가
  만든다. 같은 메뉴·종류·대상(`KnowledgeKey`)의 열린 카드 하나로 묶고 `count`를 늘린다(문구는
  마지막 질문). 사장님 글(ru) → `preview_knowledge`(ko 번역 미리보기, 저장 안 함) →
  `save_knowledge`(한국어 그대로 보충 지식 저장소에, 카드 open → filled). 질문 카드의
  돼요/안 돼요는 "들어가요?"류 질문(`OwnerQuestion.yes_no`)이면 저장소에 사장님 확인으로 넣는다.
  자유 입력·통화 끝 카드는 넣지 않는다. 저장소는 `self.knowledge`(통화 엔진이 읽는다)
- 확정 주문 카드의 `owner_check`: 그 통화에 답을 못 받은 질문(열림·시간 초과)이 있다. 손님은 문자
  약속을 들었다 ("사장님 확인 필요", 티켓 06). 늦은 답이 오면 다시 재서 내린다(리뷰 C9)
- 제시간 버튼 답이라도 통화가 말하지 못하고 끝나면(손님이 말하는 중이라 미룸, 세션이
  `answer_said`를 안 보냄) 통화 끝에 늦은 답과 같은 문자로 보낸다(리뷰 C1). 통화가 끝나면 세션의
  받는 곳을 놓는다(끝난 세션이 GC 되게, 리뷰 C5)
- 구독자(`events`)는 먼저 `snapshot`(`{"orders": [...], "calls": [...], "questions": [...],
  "captions": [...], "knowledge": [...], "sold_out": {...}, "now": 허브 시계 ISO}`, 통화 카드는
  ordered·closed 빼고, 자막은 통화 중 카드 것만)을 받고, 그 뒤 `order.update`·`call.update`·
  `call.caption`·`owner.question*`·`knowledge.update`·`owner.sold_out`을 받는다.
  이벤트마다 늘어나는 번호(`id`)를 단다. 재연결 때 이어 받기 버퍼는 없다(늘 snapshot부터)
- `submit`·통화 알림은 이벤트 루프에서 불리면 바로 처리한다(구독자 큐에 `put_nowait`만, 막히지
  않음). 다른 스레드에서 불리면 `call_soon_threadsafe`로 루프에 넘긴다
- 상한(`HubLimits`, 6단계): 끝난 통화 카드(미완료·넘김·닫음·주문됨)는 최근 50개·24시간, 확정 주문
  카드는 최근 200개·72시간만 둔다(0이면 제한 없음). 통화가 시작·끝나거나 주문이 올 때 오래된
  것부터 지운다. 화면에 떠 있던 통화 카드는 `closed`로 한 번 보내 화면에서도 빠지게 한다. 통화 중
  카드는 건드리지 않는다. 카드가 다 지워진 통화의 문자·질문 카드도 같이 지운다
- 구독자 큐는 크기 제한이 없다. SSE를 안 읽는 동안에도 통화 쪽이 막히지 않게 (V1 판정 6)

카드의 한국어 메뉴명·단가·받은 시각은 주문에 담긴 값(`OrderLine.name`·`unit_price`,
`Order.created_at`, 계약 v1 제안 6·7)을 쓴다. 비어 있으면 메뉴명·단가는 `menu_id`로 가게 메뉴에서
찾고, 받은 시각은 허브 시계로 찍는다. 시각·금액은 번역 없이 코드가 고정 표기(`18:30`, `25,000₩`)로
넣는다.

주문 카드 금액 (쿠폰 티켓 09): `subtotal`(할인 전, 줄 합계) · `coupon_id`·`coupon_name`·
`discount`(주문서에 적힌 쿠폰, 없으면 ""·""·0) · `total`(낼 금액 = 할인 전 - 할인). 할인액은 통화가
복창한 쿠폰 계산 결과(`Order.discount`)이고 허브는 다시 셈하지 않는다. 확정 문자·주문 현황도
`total`을 쓴다.

사장님 언어 (티켓 03, `OwnerHub(..., translator=..., owner_lang="ru")`).
카드 줄(주문·통화 카드 같음):
- `name_owner`: `menu_id`로 찾은 원어 이름(`MenuItem.name_native`, 번역기 안 씀). 비면 `name_ko`
- `options_owner`: 옵션 이름을 정적 사전(`lexicon.py`)으로. 사전에 없으면 한국어 그대로
- `request`(한국어 원문) + `request_owner`(번역, 아직·실패·버림이면 "") + `badges`(`remove`·
  `allergy`, 한국어 원문 규칙 `badges.py`)
- 통화 카드는 `heard`(넘김 카드 손님 말 원문) 옆에 `heard_owner`(줄마다 번역 또는 "")
- 질문 카드는 `menu_name_owner`(원어 이름, 비면 한국어)와 `text_owner`(손님 질문 번역 또는 "")
- 번역은 비동기다. 카드는 원문으로 먼저 보내고, 번역이 오면 같은 카드를 다시 보낸다(주문 카드와,
  ordered·closed가 아닌 통화 카드, 질문 카드는 `owner.question.update`). 같은 문장은 통화 동안
  한 번만 번역한다(`card_texts.py`). 번역기가 없으면(키 없음) 원문만
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from agenthan.core.contracts import Order, OrderLine, StoreKnowledge
from agenthan.voice.call.defaults import APPROVAL_EXPIRE_S, APPROVAL_WAIT_S, OWNER_WAIT_S
from agenthan.voice.call.ports import Clock
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.approval import (
    ACCEPT_DELAYS,
    REJECT_REASONS,
    ApprovalRequest,
    OwnerDecision,
    RejectReason,
    WithdrawReason,
    only_the_rest,
)
from agenthan.voice.dialog.knowledge import KnowledgeKey, KnowledgeRequest, KnowledgeStore
from agenthan.voice.dialog.owner_question import (
    OWNER_BUTTONS,
    OWNER_POSTS,
    OwnerNote,
    OwnerQuestion,
    OwnerReply,
    OwnerSeen,
)
from agenthan.voice.owner import sms
from agenthan.voice.owner.badges import request_badges
from agenthan.voice.owner.card_texts import SOURCE_LANG, CardTexts
from agenthan.voice.owner.decisions import DecisionRecord, DecisionSink, decision_of
from agenthan.voice.owner.lexicon import lexicon_for
from agenthan.voice.owner.order_archive import ArchivedOrder, OrderArchive, archived_order_status
from agenthan.voice.owner.order_status import order_status
from agenthan.voice.owner.translate import Translator
from agenthan.voice.store_snapshot import StoreProvider

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerEvent:
    """사장님 화면으로 가는 이벤트 하나. HTTP 쪽이 SSE `event`·`data`·`id`로 옮긴다."""

    name: str  # snapshot · order.update · call.update · call.caption · owner.question(.update)
    data: dict[str, Any]
    id: int


class UnknownCard(KeyError):
    """그런 카드가 없다 (HTTP 404)."""


class CardBusy(Exception):
    """그 카드는 지금 닫을 수 없다: 통화 중이거나 주문이 됐다 (HTTP 409)."""


class AlreadyAnswered(Exception):
    """이미 답한 질문이다. 답은 한 번만 받는다 (HTTP 409)."""


class AlreadyDecided(Exception):
    """이미 결정한(또는 확인 대기가 아닌) 주문 카드다. 결정은 한 번만 받는다 (HTTP 409)."""


# 넘김 카드에 붙이는 마지막 손님 말 줄 수 (spec 이야기 49)
HEARD_LINES = 3
# 통화 자막 줄 수 (spec "Papago 실측": 최근 4줄, 티켓 10)
CAPTION_LINES = 4
# 사장님이 닫을 수 있는 통화 카드 상태
_CLOSABLE = frozenset({"handoff", "missed"})
# 화면에서 빠진 통화 카드 상태: 주문 카드가 됨 · 사장님이 닫음. 다시 보내지 않는다 (리뷰 S5)
_OFF_SCREEN = frozenset({"ordered", "closed"})
# 사장님 답을 아직 못 받은 질문 카드 상태 (확정 주문에 "사장님 확인 필요")
_UNANSWERED = frozenset({"open", "timeout"})
# 이 상태에서 받은 답은 늦은 답이다. 통화가 말하지 않으니 손님 문자로 간다 (티켓 07)
_LATE = frozenset({"timeout", "call_ended"})
# 결정 칸의 빈 값(`_approval_card`와 같다). 통화가 말하기 전에 손님이 거둔 결정을 지울 때 쓴다
_UNDECIDED: dict[str, Any] = {
    "status": "new", "decided_at": "", "delay_min": 0, "owner_pickup_time": "",
    "owner_pickup_label": "", "delay_reply": "", "reject_reason": "", "reject_menu_ids": [],
    "reject_text": "", "reject_text_ko": ""}


@dataclass(frozen=True)
class HubLimits:
    """메모리에 두는 카드 상한 (6단계). 0이면 그 제한을 끈다."""

    max_call_cards: int = 50  # 끝난 통화 카드 수
    call_card_hours: float = 24.0  # 끝난 통화 카드 보관 시간 (걸려 온 때부터)
    max_order_cards: int = 200  # 확정 주문 카드 수
    order_card_hours: float = 72.0  # 확정 주문 카드 보관 시간 (받은 때부터). 통화 카드보다 길게


def _store_reader(
    store: StoreKnowledge | None, stores: StoreProvider | None
) -> Callable[[], StoreKnowledge]:
    """허브가 메뉴를 읽을 때 부르는 함수: 공급자가 있으면 그 지금 가게, 없으면 고정 가게."""
    if stores is not None:
        return lambda: stores().store
    if store is None:
        raise TypeError("OwnerHub에는 store나 stores가 있어야 한다")
    return lambda: store


class OwnerHub:
    """`OrderSink`·`OwnerChannel` 구현 + 사장님 화면 구독. 이벤트 루프 하나에 묶여 쓴다."""

    def __init__(
        self,
        *,
        store: StoreKnowledge | None = None,
        stores: StoreProvider | None = None,
        clock: Clock,
        question_wait_s: float = OWNER_WAIT_S,
        approval_wait_s: float = APPROVAL_WAIT_S,
        approval_expire_s: float = APPROVAL_EXPIRE_S,
        translator: Translator | None = None,
        owner_lang: str = "ru",
        knowledge: KnowledgeStore | None = None,
        limits: HubLimits | None = None,
        decision_sink: DecisionSink | None = None,
        archive: OrderArchive | None = None,
    ) -> None:
        # 가게 메뉴(원어 이름·품절 되돌리기·번역 때 가릴 메뉴명). 통화 밖이라 읽을 때마다 공급자에게
        # 지금 가게를 묻는다(전화 메뉴 티켓 01). `store`만 주면 늘 그 가게
        self._store = _store_reader(store, stores)
        # 사장님 결정 기록(티켓 10): 받는 곳, 주문별 마지막으로 넘긴 결정, 행을 채울 주문서
        self._decision_sink = decision_sink
        self._recorded: dict[str, DecisionRecord] = {}
        self._order_of: dict[str, Order] = {}
        # 전화 주문 기록(티켓 06): 카드가 없는 주문의 손님 주문 현황을 읽는 곳. 없으면 허브만
        self._archive = archive
        self._limits = limits or HubLimits()
        # 보충 지식 저장소(티켓 13). 허브가 쓰고, 통화 엔진이 읽는다(`phone_router(knowledge=...)`)
        self.knowledge = knowledge if knowledge is not None else KnowledgeStore()
        self._wanted: dict[str, dict[str, Any]] = {}  # 카드 id → "손님이 궁금해한 것" 카드
        self._asked: dict[str, OwnerQuestion] = {}  # 질문 id → 연 질문 (종류·대상을 본다)
        self._clock = clock
        self._question_wait = timedelta(seconds=question_wait_s)
        # 확인 요청 (주문 승인 티켓 02): 통화 중 마감·통화 뒤 마감과, 결정을 아직 안 넘긴 통화의
        # 받는 곳(통화 id → 세션). 통화가 끝나면 놓는다
        self._approval_wait = timedelta(seconds=approval_wait_s)
        self._approval_expire = timedelta(seconds=approval_expire_s)
        self._deciders: dict[str, Callable[[OwnerDecision], None]] = {}
        # 통화 중에 받은 결정 중 통화가 아직 손님에게 말하지 않은 것(통화 id → 요청 번호, 리뷰
        # 경합). 말하면(`approval_said`) 굳히고, 그 전에 손님이 거두면 거둔 쪽이 이긴다
        self._unspoken: dict[str, int] = {}
        # 가장 이른 자동 취소 마감에 건 루프 타이머 (주문 승인 티켓 03, `_arm_expiry`)
        self._expiry: asyncio.TimerHandle | None = None
        self._questions: dict[str, dict[str, Any]] = {}  # 질문 id → 질문 카드 (온 순서)
        self._replies: dict[str, Callable[[OwnerNote], None]] = {}  # 답을 아직 안 넘긴 질문
        self._said: set[str] = set()  # 통화가 손님에게 답을 말한 질문 id (리뷰 C1)
        self._passed_on: set[str] = set()  # 보충 카드로 보낸 질문 id (한 질문은 한 번, 리뷰 C6)
        self._lexicon = lexicon_for(owner_lang)
        self._translator = translator  # 사장님 자유 입력 글 → 한국어 (티켓 07)
        # 카드 자유 문장 번역 캐시 (티켓 03, `card_texts.py`)
        self._translations = CardTexts(translator, current_store=self._store,
                                      lexicon=self._lexicon)
        self._tasks: set[asyncio.Task[None]] = set()  # 진행 중인 번역 (GC 막기)
        self._orders: dict[str, dict[str, Any]] = {}  # 통화 id → 주문 카드 (넣은 순서 = 온 순서)
        self._calls: dict[str, dict[str, Any]] = {}  # 통화 id → 통화 카드 (넣은 순서 = 걸려 온 순)
        self._heard: dict[str, deque[str]] = {}  # 통화 중인 id → 마지막 손님 말들
        self._captions: dict[str, _Captions] = {}  # 통화 중인 id → 자막 (티켓 10)
        # 통화 id → 손님에게 보낸 문자 미리보기, 보낸 순서 (티켓 07)
        self._customer_sms: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: set[asyncio.Queue[OwnerEvent]] = set()
        # 통화 id → 손님 통화 화면의 문자 스트림 구독자 (티켓 07)
        self._text_watchers: dict[str, set[asyncio.Queue[OwnerEvent]]] = {}
        # 주문 번호 → 손님 주문 현황 구독자와 마지막으로 보낸 현황 (전화 주문 현황 티켓 05)
        self._order_watchers: dict[str, set[asyncio.Queue[OwnerEvent]]] = {}
        self._order_sent: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        # 오늘 품절 (주문 승인 티켓 06): 메뉴 id → 화면 줄(`menu_id`·`name_ko`·`name_owner`, 건
        # 순서), 그 목록의 날짜(허브 시계), 자정에 비울 루프 타이머
        self._sold_out: dict[str, dict[str, str]] = {}
        self._sold_out_day: date = clock.now().date()
        self._midnight: asyncio.TimerHandle | None = None
        # 통화 중 재료 소진 거절을 받고 손님 답을 기다리는 통화 id. 통화가 끝날 때까지 남은
        # 메뉴 확정·새 요청이 없으면 그때 취소 문자를 보낸다
        self._stock_out_open: set[str] = set()

    # --- OrderSink ---------------------------------------------------------------------

    def submit(self, order: Order) -> None:
        """통화 세션이 주문 확정 때 부른다. 바로 돌아온다(계약 제안 8)."""
        log.info("주문 %s: %s, 항목 %d개, 픽업 %s",
                 order.id, order.status, len(order.lines), order.pickup_time)
        self._on_loop(self._take_order, order)

    # --- OwnerChannel (통화 세션이 부른다, 티켓 02) ------------------------------------

    def call_started(self, call_id: str) -> None:
        self._on_loop(self._start_call, call_id)

    def cart_changed(self, call_id: str, lines: Sequence[OrderLine]) -> None:
        self._on_loop(self._change_cart, call_id, tuple(lines))

    def customer_said(self, call_id: str, text: str) -> None:
        self._on_loop(self._hear, call_id, text)

    def bot_said(self, call_id: str, text: str) -> None:
        """AI 말은 카드에 쓰지 않고 자막에만 둔다(번역 안 함, 티켓 10)."""
        self._on_loop(self._caption, call_id, "bot", text)

    def call_ended(self, call_id: str, reason: str) -> None:
        self._on_loop(self._end_call, call_id, reason)

    def open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerNote], None]
    ) -> None:
        self._on_loop(self._open_question, call_id, question, reply)

    def question_timed_out(self, call_id: str, number: int) -> None:
        self._on_loop(self._time_out, f"{call_id}-q{number}")

    def answer_said(self, call_id: str, number: int) -> None:
        self._on_loop(self._said.add, f"{call_id}-q{number}")

    def knowledge_wanted(self, call_id: str, request: KnowledgeRequest) -> None:
        self._on_loop(self._want, call_id, request.key, request.menu_name, request.text)

    def approval_ready(self) -> bool:
        """사장님 화면(`events` 구독자)이 하나 이상 붙어 있다. 손님 문자 스트림은 세지 않는다."""
        return bool(self._subscribers)

    def open_approval(
        self, call_id: str, request: ApprovalRequest, reply: Callable[[OwnerDecision], None]
    ) -> None:
        self._on_loop(self._open_approval, call_id, request, reply)

    def withdraw_approval(self, call_id: str, number: int, reason: WithdrawReason) -> None:
        self._on_loop(self._withdraw_approval, call_id, number, reason)

    def approval_said(self, call_id: str, number: int) -> None:
        self._on_loop(self._approval_said, call_id, number)

    def sold_out_today(self) -> frozenset[str]:
        """오늘 품절 메뉴 id들(주문 승인 티켓 06). 날짜(허브 시계)가 바뀌었으면 빈 집합.

        읽기만 한다(어느 스레드에서 불러도 된다). 목록을 실제로 비우고 화면에 알리는 것은 루프의
        `roll_day`다.
        """
        if self._sold_out_day != self._clock.now().date():
            return frozenset()
        return frozenset(self._sold_out)

    # --- 사장님 화면 ---------------------------------------------------------------------

    def accept(self, order_id: str, delay_min: int = 0) -> dict[str, Any]:
        """수락 버튼(주문 승인 티켓 02): 확인 대기(new) → accepted. 바뀐 카드를 돌려준다.

        통화 뒤면 확정 문자를 바로 보낸다. 통화 중이면 결정을 통화 세션에 넘기고, 문자는 통화가
        그 결정을 손님에게 말한 뒤에 보낸다(`_decide`). 409 규칙은 `_pending_card`. 늦춘 분은
        0·15·30만 받는다(`ValueError`). 늦춘 수락(티켓 04)은 통화 중이면 문자를 손님 답 뒤로 미룬다
        (모듈 설명).
        """
        card = self._pending_card(order_id)
        if delay_min not in ACCEPT_DELAYS:
            raise ValueError(f"늦춘 분은 {ACCEPT_DELAYS} 중 하나예요: {delay_min!r}")
        decided = {**card, "status": "accepted", "delay_min": delay_min,
                   "decided_at": self._clock.now().isoformat()}
        if delay_min:
            moved = _later(card["pickup_time"], delay_min)
            decided |= {"owner_pickup_time": moved, "owner_pickup_label": clock_label(moved)}
        kind = "delay" if delay_min else "accept"
        return self._decide(order_id, decided, OwnerDecision(card["request"], kind, delay_min))

    def expire_due(self) -> list[str]:
        """자동 취소 (주문 승인 티켓 03): 확인 요청을 연 때 + `approval_expire_s`가 허브 시계로
        지난 확인 대기 카드를 expired로 바꾸고 손님에게 자동 취소 문자를 남긴다. 바꾼 카드 id들.

        허브가 마감 타이머로 스스로 부른다. 결정·새 구독 앞에서도 부른다. 여러 번 불러도 된다.
        손님이 고치는 중·취소한 요청(`withdrawn`)은 결정을 기다리는 카드가 아니라 건드리지 않는다.
        """
        now = self._clock.now()
        due = [card["id"] for card in self._orders.values()
               if _awaiting(card) and self._expires_at(card) <= now]
        for call_id in due:
            self._deciders.pop(call_id, None)  # 아직 통화 중이면 그 세션에는 결정이 가지 않는다
            self._orders[call_id] = {**self._orders[call_id], "status": "expired",
                                     "decided_at": now.isoformat()}
            log.info("확인 요청 자동 취소: %s", call_id)
            self._text_customer(call_id, "expired", sms.expired_text())  # 카드도 다시 보낸다
        self._arm_expiry()
        return due

    def reject(self, order_id: str, reason: RejectReason, *, menu_ids: Sequence[str] = (),
               text: str = "") -> dict[str, Any]:
        """거절 버튼 → 사유 시트 (주문 승인 티켓 05): 확인 대기(new) → rejected. 바뀐 카드.

        손님에게 사유별 취소 문자를 남기고(통화 중에 결정해 손님이 사과를 들어도 같다), 통화 중이면
        결정을 통화 세션에 넘긴다(손님이 사유별 사과를 듣고 통화가 취소로 끝난다. 문자는 통화가
        말한 뒤, `_decide`). 직접 쓰기(free) 글은 번역 포트로 한국어로 옮겨 문자 인용 틀에
        넣는다(번역은 비동기, 문자는 번역 뒤에 온다. 안 되면 원문). 409 규칙은 수락과 같다
        (`_pending_card`, 글 검사보다 먼저). 모르는 사유·글 없는 직접 쓰기는 `ValueError`.

        재료 소진(out_of_stock, 티켓 06)은 주문에 든 메뉴 중 떨어진 것(`menu_ids`, 하나 이상, 아니면
        `ValueError`)을 받아 오늘 품절에 더한다. 통화 중이면 취소 문자는 미룬다(손님이 빼거나 바꿀
        수 있다, 모듈 설명). 통화 뒤면 메뉴 이름이 든 취소 문자를 바로 남긴다.
        """
        card = self._pending_card(order_id)  # 결정·거둠 검사(409)가 글 검사(422)보다 먼저다
        if reason not in REJECT_REASONS:
            raise ValueError(f"모르는 거절 사유: {reason!r}")
        menu_ids = self._stock_out_ids(card, menu_ids) if reason == "out_of_stock" else []
        text = text.strip() if reason == "free" else ""
        if reason == "free" and not text:
            raise ValueError("직접 쓴 거절 사정이 비었어요")
        self._orders[order_id] = {**card, "status": "rejected", "reject_reason": reason,
                                  "reject_menu_ids": list(menu_ids), "reject_text": text,
                                  "decided_at": self._clock.now().isoformat()}
        log.info("확인 요청 거절: %s (%s)", order_id, reason)
        if menu_ids:  # 통화에 넘기기 전에 품절을 건다(세션이 결정 직전에 목록을 읽는다)
            self._mark_sold_out(order_id, menu_ids)
        return self._decide(order_id, self._orders[order_id], OwnerDecision(
            card["request"], "reject", reason=reason, menu_ids=tuple(menu_ids)))

    def _pending_card(self, order_id: str) -> dict[str, Any]:
        """결정을 받을 확인 대기 카드. 마감이 지난 카드는 먼저 자동 취소한다(타이머가 늦었어도
        409). 없으면 `UnknownCard`, 이미 결정했거나 자동 취소됐으면 `AlreadyDecided`(두 화면에서
        같이 눌러도 한 번), 손님이 고치는 중·취소한 요청이면 `CardBusy`."""
        self.expire_due()
        card = self._orders.get(order_id)
        if card is None:
            raise UnknownCard(order_id)
        if card["status"] != "new":
            raise AlreadyDecided(order_id)
        if card["withdrawn"]:
            raise CardBusy(order_id)
        return card

    def _decide(self, order_id: str, decided: dict[str, Any],
                decision: OwnerDecision) -> dict[str, Any]:
        """사장님 결정을 카드에 적는다. 바뀐 카드를 돌려준다.

        통화 뒤면 바로 굳힌다(`_settle`). 통화 중이면 결정을 세션에 넘기고, 통화가 그
        결정을 손님에게 말할 때(`approval_said`)까지 손님 문자·결정 기록을 미룬다(리뷰
        경합). 그 전에 손님이 요청을 거두면 결정은 없던 일이 되고(`_withdraw_approval`),
        말하지 못하고 통화가 끝나면 통화 뒤
        결정처럼 굳힌다(`_end_call`). 화면 카드는 바로 결정으로 보인다(두 번째 결정은 409).
        """
        self._orders[order_id] = decided
        reply = self._deciders.pop(order_id, None)
        if reply is None:
            self._settle(order_id, said=False)
        else:
            self._unspoken[order_id] = decision.request
            self._publish("order.update", decided)
            self._tell_call(reply, decision, order_id)
        self._arm_expiry()
        return self._orders[order_id]

    def _settle(self, call_id: str, *, said: bool) -> None:
        """결정을 굳힌다: 결정 종류마다 손님 문자(또는 손님 답 기다림)와 카드 한 번 보내기(결정
        기록이 같이 간다). 결정 → 할 일 표는 여기 한 곳이다.

        `said`: 통화가 손님에게 결정을 말했다. 아니면 통화 뒤 결정이거나 통화가 말하지 못하고
        끝났다. 둘이 다른 것은 늦춘 수락(말했으면 손님 답을 기다림, 티켓 04)과 재료 소진(말했으면
        손님이 빼거나 바꿀 수 있어 취소 문자를 미룸, 티켓 06)뿐이다.
        """
        self._unspoken.pop(call_id, None)
        card = self._orders[call_id]
        reason = card["reject_reason"]
        if card["status"] == "accepted" and not card["delay_min"]:
            self._text_order(call_id)  # 확정 문자 + 카드 한 번 보내기
        elif card["status"] == "accepted" and said:
            # 손님이 바뀐 시각을 듣는 중. 문자는 손님 답 뒤(`_take_order`·`_withdraw_approval`)
            self._orders[call_id] = {**card, "delay_reply": "asking"}
            self._publish("order.update", self._orders[call_id])
        elif card["status"] == "accepted":
            self._text_delayed(call_id)  # 늦춘 수락 문자 + 카드 한 번 보내기
        elif reason == "out_of_stock" and said:
            self._stock_out_open.add(call_id)  # 손님 답을 기다린다. 취소 문자는 통화 끝에
            self._publish("order.update", card)
        elif reason == "out_of_stock":
            self._text_customer(call_id, "rejected", self._stock_out_text(call_id))
        elif reason == "free":
            self._publish("order.update", card)  # 문자는 번역 뒤에
            self._text_rejected_soon(call_id)
        else:
            self._text_customer(call_id, "rejected", sms.rejected_text(reason))  # 카드도 보낸다

    def _approval_said(self, call_id: str, number: int) -> None:
        """통화가 그 요청의 사장님 결정을 손님에게 말하기 시작했다. 결정을 굳힌다."""
        if self._unspoken.get(call_id) == number and call_id in self._orders:
            self._settle(call_id, said=True)

    def undo_sold_out(self, menu_id: str) -> dict[str, Any]:
        """품절 되돌리기 (주문 승인 티켓 06): 그 메뉴를 오늘 품절에서 뺀다. 오늘 품절 전체를 준다.

        다음 통화부터(진행 중인 통화는 다음 사장님 결정 때부터) 다시 받는다. 가게 메뉴에 없는 id면
        `UnknownCard`. 이미 빠진 메뉴면 그대로 돌려준다(두 화면에서 같이 눌러도 된다).
        """
        self.roll_day()
        if menu_id not in self._sold_out and self._store().get_menu(menu_id) is None:
            raise UnknownCard(menu_id)
        if self._sold_out.pop(menu_id, None) is not None:
            log.info("품절 되돌림: %s", menu_id)
            self._publish_sold_out()
        return self._sold_out_view()

    def roll_day(self) -> bool:
        """날짜(허브 시계 = 가게 시간대)가 바뀌었으면 오늘 품절을 비우고 화면에 알린다(티켓 06).
        비웠으면 True. 허브가 자정 루프 타이머로 스스로 부르고, 품절을 바꾸거나 새 구독 앞에서도
        부른다. 시험은 가짜 시계를 옮기고 부른다.
        """
        today = self._clock.now().date()
        if today == self._sold_out_day:
            return False
        self._sold_out_day = today
        had = bool(self._sold_out)
        self._sold_out.clear()
        if had:
            log.info("날짜가 바뀌어 오늘 품절을 비움 (%s)", today)
            self._publish_sold_out()
        self._arm_midnight()
        return had

    def dismiss(self, call_id: str) -> dict[str, Any]:
        """닫기 버튼(넘김·미완료 카드): → closed. 바뀐 카드를 돌려준다.

        없으면 `UnknownCard`, 통화 중이거나 주문이 된 카드면 `CardBusy`. 이미 닫은 카드면 그대로
        돌려준다(두 화면에서 같이 눌러도 된다).
        """
        card = self._calls.get(call_id)
        if card is None:
            raise UnknownCard(call_id)
        if card["status"] == "closed":
            return card
        if card["status"] not in _CLOSABLE:
            raise CardBusy(call_id)
        return self._put_call({**card, "status": "closed"})

    def answer_question(self, question_id: str, answer: str, text: str = "") -> dict[str, Any]:
        """질문 카드 답: 버튼(yes·no·unsure) 또는 자유 입력(free + 사장님 글 `text`, 티켓 07).
        바뀐 카드를 돌려주고 답 종류를 통화 세션에 넘긴다(사장님 글은 통화에 넘기지 않는다).

        늦은 버튼 답(시간 초과·통화 끝 카드)은 고정 문장 손님 문자가 된다. 자유 입력은 언제든
        사장님 글을 한국어로 번역해 손님 문자가 된다(번역은 비동기, 안 되면 원문).
        없는 질문이면 `UnknownCard`, 이미 답했으면 `AlreadyAnswered`, 모르는 답이거나 자유 입력
        글이 비면 `ValueError`. 세션의 받는 곳이 실패해도(통화가 이미 끝남 등) 카드는 답함으로
        남는다.
        """
        card = self._questions.get(question_id)
        if card is None:
            raise UnknownCard(question_id)
        if answer not in OWNER_POSTS:
            raise ValueError(f"모르는 답: {answer!r}")
        written = text.strip() if answer == "free" else ""
        if answer == "free" and not written:
            raise ValueError("자유 입력 답에 글이 없어요")
        if card["status"] == "answered":
            raise AlreadyAnswered(question_id)
        # 시간 초과·통화 끝 카드의 늦은 답도 받는다. 세션은 번호로 버리고 손님에게는 문자로 간다
        late = card["status"] in _LATE
        card = self._put_question({**card, "status": "answered", "answer": answer,
                                   "answer_text": written})
        reply = self._replies.pop(question_id, None)
        if reply is not None:
            self._tell_call(reply, OwnerReply(card["number"], answer), question_id)
        if answer == "free":
            self._text_written_answer_soon(question_id)
        elif late:
            self._text_late_answer(question_id)
        self._recheck_order(card["call_id"])  # "사장님 확인 필요"를 내린다 (리뷰 C9)
        self._learn(question_id, answer)  # 보충 지식 (티켓 13): 카드 이벤트는 문자 뒤에
        return card

    def see_question(self, question_id: str) -> dict[str, Any]:
        """질문 카드 열람(티켓 06). 처음 본 시각을 찍고, 답 전이면 통화 세션에 알린다.

        없는 질문이면 `UnknownCard`. 두 번째부터는 카드를 그대로 돌려준다(이벤트·알림 없음).
        """
        card = self._questions.get(question_id)
        if card is None:
            raise UnknownCard(question_id)
        if card["seen_at"]:
            return card
        card = self._put_question({**card, "seen_at": self._clock.now().isoformat()})
        reply = self._replies.get(question_id)
        if reply is not None and card["status"] == "open":
            self._tell_call(reply, OwnerSeen(card["number"]), question_id)
        return card

    def knowledge_cards(self) -> list[dict[str, Any]]:
        """"손님이 궁금해한 것" 카드 전체(만든 순서, 채운 카드 포함, 티켓 13)."""
        return list(self._wanted.values())

    async def preview_knowledge(self, card_id: str, text: str) -> dict[str, Any]:
        """사장님 답(사장님 언어) → 한국어 번역 미리보기(티켓 13). 저장하지 않는다.

        `{"text": 사장님 글, "text_ko": 번역, "translate_failed": bool}`. 번역기가 없거나 실패하면
        `text_ko`가 비고 `translate_failed`가 참이다(사장님이 한국어를 직접 적을 수 있다).
        없는 카드면 `UnknownCard`, 빈 글이면 `ValueError`.
        """
        if card_id not in self._wanted:
            raise UnknownCard(card_id)
        text = text.strip()
        if not text:
            raise ValueError("미리 볼 글이 없어요")
        # 번역이 안 되면 사장님이 한국어를 직접 적는다
        translated = await self._to_korean(text, f"보충 답 {card_id}")
        return {"text": text, "text_ko": translated, "translate_failed": not translated}

    def save_knowledge(self, card_id: str, text_ko: str, text: str = "") -> dict[str, Any]:
        """사장님이 "저장"을 눌렀다(티켓 13, HITL). 한국어 글을 그대로 보충 지식에 넣고 카드를
        채움으로 바꾼다. 다시 저장하면 덮어쓴다. 없는 카드면 `UnknownCard`, 빈 글이면 `ValueError`.
        """
        card = self._wanted.get(card_id)
        if card is None:
            raise UnknownCard(card_id)
        text_ko = text_ko.strip()
        self.knowledge.save_note(_key(card), text_ko, question=card["text"])  # 빈 글이면 ValueError
        return self._put_wanted({**card, "status": "filled", "answer_ko": text_ko,
                                 "answer_text": text.strip(),
                                 "saved_at": self._clock.now().isoformat()})

    def order_status(self, order_id: str) -> dict[str, Any]:
        """손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04, 모양은 `order_status.py`). 주문 번호는
        통화 id다. 주문 카드가 없으면(확정 없이 끝난 통화·모르는 번호·지워진 카드) `UnknownCard`.

        마감 지난 확인 대기 카드는 먼저 자동 취소한다(타이머가 늦어도 "확인 중"으로 보이지 않게).
        """
        self.expire_due()
        card = self._orders.get(order_id)
        if card is None:
            raise UnknownCard(order_id)
        return order_status(card, unspoken=order_id in self._unspoken)

    async def lookup_order_status(self, order_id: str) -> dict[str, Any]:
        """손님 주문 현황 조회 (전화 메뉴·주문 현황·쿠폰 티켓 04·06, `GET /api/phone/orders/{id}`).

        허브의 주문 카드가 먼저다(`order_status`). 없으면 전화 주문 기록(`archive`)의 마지막 상태로
        같은 모양을 만든다(`order_archive.py`: 마감 지난 결정 전 기록은 자동 취소, 손님 취소 없음).
        둘 다 없거나 기록 읽기가 없으면 `UnknownCard`. 기록 읽기(DB)는 루프를 막지 않게 스레드에서.
        """
        try:
            return self.order_status(order_id)
        except UnknownCard:
            if self._archive is None:
                raise
        record = await asyncio.to_thread(self._read_archive, order_id)
        if order_id in self._orders:  # 읽는 사이 카드가 생겼다(새 통화가 같은 번호일 리는 없다)
            return self.order_status(order_id)
        status = None if record is None else archived_order_status(
            record, now=self._clock.now(), expire=self._approval_expire)
        if status is None:
            raise UnknownCard(order_id)
        return status

    def _read_archive(self, order_id: str) -> ArchivedOrder | None:
        """루프 밖 스레드에서 돈다. 허브 상태는 건드리지 않는다."""
        assert self._archive is not None
        try:
            return self._archive.load(order_id)
        except Exception:
            log.exception("주문 %s 기록 읽기 실패 (%s)", order_id, type(self._archive).__name__)
            return None

    def watch_order(self, order_id: str) -> AsyncIterator[OwnerEvent]:
        """손님 주문 현황 구독 (전화 메뉴·주문 현황 티켓 05). 처음에 `order.status`(지금 현황,
        조회와 같은 모양), 그 뒤 현황이 달라질 때마다 `order.status`. 닫으면 구독을 푼다.

        주문 카드가 없으면 바로 `UnknownCard`(조회와 같다). 시간 제한은 HTTP 쪽이 둔다.
        """
        self.order_status(order_id)  # 없으면 UnknownCard
        return self._watch_order(order_id)

    async def _watch_order(self, order_id: str) -> AsyncIterator[OwnerEvent]:
        self._bind()
        queue: asyncio.Queue[OwnerEvent] = asyncio.Queue()
        watchers = self._order_watchers.setdefault(order_id, set())
        watchers.add(queue)
        try:
            card = self._orders.get(order_id)
            if card is not None:  # 구독을 연 사이 상한으로 지워졌으면 첫 현황 없이 기다린다
                status = order_status(card, unspoken=order_id in self._unspoken)
                self._order_sent[order_id] = status
                yield OwnerEvent("order.status", status, self._seq)
            while True:
                yield await queue.get()
        finally:
            watchers.discard(queue)
            if not watchers:
                self._order_watchers.pop(order_id, None)
                self._order_sent.pop(order_id, None)

    def _tell_order_watchers(self, card: dict[str, Any]) -> None:
        """주문 카드가 바뀌었다. 손님 현황이 지난번 보낸 것과 다르면 그 주문 구독자에게 보낸다."""
        order_id = card["id"]
        watchers = self._order_watchers.get(order_id)
        if not watchers:
            return
        status = order_status(card, unspoken=order_id in self._unspoken)
        if self._order_sent.get(order_id) == status:
            return
        self._order_sent[order_id] = status
        self._seq += 1
        for queue in watchers:
            queue.put_nowait(OwnerEvent("order.status", status, self._seq))

    def customer_texts(self, call_id: str) -> list[dict[str, Any]]:
        """그 통화의 손님에게 보낸 문자(보낸 순서). 없거나 모르는 통화면 빈 목록 (티켓 07)."""
        return list(self._customer_sms.get(call_id, ()))

    def watch_texts(self, call_id: str) -> AsyncIterator[OwnerEvent]:
        """손님 통화 화면의 "받은 문자" 구독 (티켓 07). 처음에 `sms.snapshot`(`{"call_id", "sms":
        [...]}`, 지금까지의 문자), 그 뒤 새 문자마다 `sms`(문자 하나). 닫으면 구독을 푼다.

        허브가 모르는 통화(카드도 문자도 없음)면 바로 `UnknownCard`. 시간 제한은 HTTP 쪽이 둔다.
        """
        if call_id not in self._calls and call_id not in self._orders \
                and call_id not in self._customer_sms:
            raise UnknownCard(call_id)
        return self._watch_texts(call_id)

    async def _watch_texts(self, call_id: str) -> AsyncIterator[OwnerEvent]:
        self._bind()
        queue: asyncio.Queue[OwnerEvent] = asyncio.Queue()
        watchers = self._text_watchers.setdefault(call_id, set())
        watchers.add(queue)
        try:
            yield OwnerEvent("sms.snapshot",
                             {"call_id": call_id, "sms": self.customer_texts(call_id)}, self._seq)
            while True:
                yield await queue.get()
        finally:
            watchers.discard(queue)
            if not watchers:
                self._text_watchers.pop(call_id, None)

    async def events(self) -> AsyncIterator[OwnerEvent]:
        """구독. 처음에 `snapshot` 하나, 그 뒤 바뀐 카드마다 이벤트 하나. 닫으면 구독을 푼다."""
        self._bind()
        self.expire_due()  # 새 화면이 마감 지난 카드를 확인 대기로 보지 않게
        self.roll_day()  # 어제 품절을 오늘 것으로 보이지 않게
        queue: asyncio.Queue[OwnerEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield OwnerEvent("snapshot", self._snapshot(), self._seq)
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    # --- 안쪽 ---------------------------------------------------------------------------

    def _take_order(self, order: Order) -> None:
        if order.status == "handoff":
            self._hand_off(order)
            return
        if order.status != "confirmed":
            return
        self._order_of[order.id] = order
        waiting = self._orders.get(order.id)
        marked: list[dict[str, str]] = []  # 재료 소진 카드의 품절 표시 (티켓 06)
        if waiting is not None and order.id in self._stock_out_open:
            self._stock_out_open.discard(order.id)
            marked = waiting.get("sold_out", [])
            if _rest_of(waiting, order):
                self._accept_rest(waiting, order)  # 재료 소진 뒤 떨어진 메뉴만 뺐다 (티켓 06)
                waiting = self._orders[order.id]
            else:  # 바꾼 주문이 확인 요청 없이 확정됐다(그새 사장님 화면이 꺼짐): 자동 확정
                waiting = None
        if waiting is None or waiting["withdrawn"]:
            # 확인 요청 없이 온 확정 주문(승인 꺼짐·사장님 화면 없음): 자동 확정 카드 + 확정 문자
            now = self._clock.now().isoformat()
            self._orders[order.id] = {
                **self._approval_card(order, number=0, stage=""), "status": "accepted",
                "auto_accepted": True, "decided_at": now, "sold_out": marked}
            self._text_order(order.id)
            self._translate_later(self._orders[order.id])
        elif waiting.get("delay_reply") == "asking":
            # 늦춘 시각을 묻던 통화가 확정으로 끝났다(티켓 04). 손님이 "네"라고 했으면 주문서의
            # 픽업이 바뀐 시각이다. 아니면(시간 초과와 겹침 등) 답 없이 끝난 것과 같다
            said_yes = order.pickup_time == waiting["owner_pickup_time"]
            self._orders[order.id] = {**waiting, "delay_reply": "yes" if said_yes else ""}
            self._text_delayed(order.id)
        # 그 밖의 확인 카드(수락·대기 중)는 그대로 둔다. 결정과 문자는 확인 카드 몫이다
        call = self._calls.get(order.id)
        if call is not None and call["status"] == "live":
            self._put_call({**call, "status": "ordered"})  # 화면은 통화 카드를 빼고 주문 카드만
        self._prune()

    def _accept_rest(self, card: dict[str, Any], order: Order) -> None:
        """통화 중 재료 소진 거절 뒤 손님이 떨어진 메뉴만 빼고 확정했다(주문 승인 티켓 06). 그
        카드를 남은 메뉴로 accepted로 바꾸고 확정 문자를 보낸다(재승인 없음). 거절 칸은 비우고
        품절 표시(`sold_out`)는 남긴다."""
        rest = self._approval_card(order, number=card["request"], stage=card["stage"])
        self._orders[order.id] = {
            **rest, "status": "accepted", "requested_at": card["requested_at"],
            "deadline_at": card["deadline_at"], "decided_at": self._clock.now().isoformat(),
            "sold_out": card.get("sold_out", [])}
        log.info("재료 소진 뒤 남은 메뉴로 수락: %s", order.id)
        self._text_order(order.id)
        self._translate_later(self._orders[order.id])

    def _stock_out_ids(self, card: dict[str, Any], menu_ids: Sequence[str]) -> list[str]:
        """재료 소진 거절의 떨어진 메뉴 id들(중복 빼고 온 순서). 비었거나 주문에 없는 메뉴면
        `ValueError`(422)."""
        ids = list(dict.fromkeys(str(menu_id) for menu_id in menu_ids))
        if not ids:
            raise ValueError("재료가 떨어진 메뉴를 하나 이상 골라 주세요")
        ordered = {line["menu_id"] for line in card["lines"]}
        if unknown := [menu_id for menu_id in ids if menu_id not in ordered]:
            raise ValueError(f"주문에 없는 메뉴: {unknown!r}")
        return ids

    def _mark_sold_out(self, call_id: str, menu_ids: Sequence[str]) -> None:
        """재료 소진: 그 메뉴들을 오늘 품절에 더하고 카드에 품절 표시(`sold_out`)를 단다."""
        self.roll_day()
        card = self._orders[call_id]
        by_id = {line["menu_id"]: line for line in card["lines"]}
        items = [{"menu_id": menu_id, "name_ko": by_id[menu_id]["name_ko"],
                  "name_owner": by_id[menu_id]["name_owner"]} for menu_id in menu_ids]
        marked = [item for item in card.get("sold_out", []) if item["menu_id"] not in by_id]
        self._orders[call_id] = {**card, "sold_out": marked + items}
        for item in items:
            self._sold_out[item["menu_id"]] = item
        log.info("오늘 품절: %s (%s)", ", ".join(menu_ids), call_id)
        self._publish_sold_out()
        self._arm_midnight()

    def _stock_out_text(self, call_id: str) -> str:
        """재료 소진 취소 문자(떨어진 메뉴 이름)."""
        card = self._orders[call_id]
        names = {line["menu_id"]: line["name_ko"] for line in card["lines"]}
        return sms.rejected_text("out_of_stock",
                                 [names[menu_id] for menu_id in card["reject_menu_ids"]])

    def _sold_out_view(self) -> dict[str, Any]:
        """`owner.sold_out` 이벤트·snapshot `sold_out`: 날짜와 품절 메뉴(건 순서)."""
        return {"date": self._sold_out_day.isoformat(), "items": list(self._sold_out.values())}

    def _publish_sold_out(self) -> None:
        self._publish("owner.sold_out", self._sold_out_view())

    def _arm_midnight(self) -> None:
        """오늘 품절이 있으면 다음 자정(허브 시계)에 `roll_day` 루프 타이머를 건다. 루프 밖이면
        걸지 않는다(시험은 `roll_day`를 직접 부른다)."""
        if self._midnight is not None:
            self._midnight.cancel()
            self._midnight = None
        if not self._sold_out:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        now = self._clock.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=now.tzinfo)
        self._midnight = loop.call_later(max(0.0, (midnight - now).total_seconds()),
                                         self._on_midnight)

    def _on_midnight(self) -> None:
        self._midnight = None
        if not self.roll_day():
            self._arm_midnight()  # 시계가 아직 자정 전이면 다시 건다

    def _approval_card(self, order: Order, *, number: int, stage: str) -> dict[str, Any]:
        """주문 카드: 주문 내용 + 처리 상태·확인 요청 칸 (주문 승인 티켓 02). 상태는 new."""
        now = self._clock.now()
        wait = self._approval_wait if stage == "in_call" else self._approval_expire
        return {
            **self._dressed(self._order_card(order)),
            "request": number,  # 확인 요청 번호 (통화 안 1부터). 자동 확정 카드는 0
            "stage": stage,  # in_call(통화 중 대기) · after_call(통화 뒤 대기) · ""(자동 확정)
            "requested_at": now.isoformat() if number else "",
            # 마감. 화면 카운트다운과 같은 시계
            "deadline_at": (now + wait).isoformat() if number else "",
            "decided_at": "",  # 사장님이 결정한(자동 확정은 받은) 시각 (허브 시계 ISO)
            "delay_min": 0,  # 늦춘 수락의 분 (티켓 04)
            # 늦춘 수락의 바뀐 픽업 시각 ISO·`18:45`. 없으면 "" (DB `owner_pickup_time`)
            "owner_pickup_time": "",
            "owner_pickup_label": "",
            # 통화 중 늦춘 수락에 손님 답: asking(묻는 중) · yes · no(주문 취소). 그 밖은 ""
            "delay_reply": "",
            "auto_accepted": False,  # 확인 요청 없이 확정됨 ("자동 확정(사장님 화면 꺼짐)")
            "withdrawn": "",  # 기다리는 중에 손님이 고침(changed)·취소(cancelled). 결정을 안 받는다
            # 거절 (티켓 05): 사유(out_of_stock·busy·closed·free, DB `reject_reason`), 재료 소진의
            # 떨어진 메뉴 id들(티켓 06), 직접 쓰기 글(사장님 언어)과 그 한국어 번역(아직·실패면 "")
            "reject_reason": "",
            "reject_menu_ids": [],
            "reject_text": "",
            "reject_text_ko": "",
            # 이 카드에서 오늘 품절로 바꾼 메뉴(티켓 06): [{"menu_id", "name_ko", "name_owner"}].
            # 지금도 품절인지는 오늘 품절 목록(`owner.sold_out`)으로 본다(되돌리기 버튼)
            "sold_out": [],
            "owner_check": self._owner_check(order.id),
            "sms": self.customer_texts(order.id),
        }

    def _open_approval(
        self, call_id: str, request: ApprovalRequest, reply: Callable[[OwnerDecision], None]
    ) -> None:
        """확인 요청 → 확인 대기(new) 주문 카드. 같은 통화에 새 번호로 오면 앞 카드를 대신한다.

        통화 뒤 확인 요청(`request.in_call` 거짓, 통화 중 대기 0)은 처음부터 통화 뒤 단계(마감 =
        연 시각 + `approval_expire_s`)다. 결정을 기다리는 통화가 없으니 받는 곳을 두지 않는다:
        결정은 바로 굳는다(통화 뒤 결정과 같다).
        """
        stage = "in_call" if request.in_call else "after_call"
        card = self._approval_card(request.order, number=request.number, stage=stage)
        if (before := self._orders.get(call_id)) is not None:
            card["sold_out"] = before.get("sold_out", [])  # 재료 소진 뒤 다시 요청 (티켓 06)
        self._stock_out_open.discard(call_id)  # 바꿔서 다시 요청했다. 취소 문자는 없다
        self._order_of[call_id] = request.order
        self._orders[call_id] = card
        if request.in_call:
            self._deciders[call_id] = reply
        else:
            self._deciders.pop(call_id, None)
        self._publish("order.update", card)
        self._translate_later(card)
        self._prune()
        self._arm_expiry()

    def _expires_at(self, card: dict[str, Any]) -> datetime:
        """자동 취소 마감: 확인 요청을 연 시각 + `approval_expire_s` (통화 중이든 뒤든 같다)."""
        return datetime.fromisoformat(card["requested_at"]) + self._approval_expire

    def _arm_expiry(self) -> None:
        """가장 이른 자동 취소 마감에 루프 타이머를 다시 건다. 기다리는 카드가 없으면 끈다.

        마감까지의 시간은 허브 시계로 잰다(`PHONE_NOW` 시연 시계도 실제 초로 흐른다). 루프 밖(동기
        시험)이면 걸지 않는다. 그때는 `expire_due`를 직접 부른다.
        """
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        due = [self._expires_at(card) for card in self._orders.values() if _awaiting(card)]
        if not due:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        delay = max(0.0, (min(due) - self._clock.now()).total_seconds())
        self._expiry = loop.call_later(delay, self._on_expiry)

    def _on_expiry(self) -> None:
        self._expiry = None
        self.expire_due()  # 마감 전이면(시계가 덜 갔으면) 아무것도 안 바꾸고 다시 건다

    def _withdraw_approval(self, call_id: str, number: int, reason: WithdrawReason) -> None:
        """손님이 고치거나 취소해 요청을 거뒀다. 카드는 결정을 안 받는다(새 요청이 대신한다).

        사장님이 이미 결정했어도 통화가 아직 말하지 않았으면 거둔 쪽이 이긴다(리뷰 경합): 결정
        칸을 비우고 "고치는 중"·손님 취소로 둔다. 문자·결정 기록은 아직 안 나갔다. 재료 소진으로 건
        오늘 품절과 카드 품절 표시는 남긴다(재료가 떨어진 것은 그대로다).
        """
        card = self._orders.get(call_id)
        if card is None or card["request"] != number:
            return
        if self._unspoken.get(call_id) == number:
            del self._unspoken[call_id]
            log.info("말하기 전에 손님이 거둔 결정을 버림: %s (%s)", call_id, reason)
            card = {**card, **_UNDECIDED}
        elif card.get("delay_reply") == "asking" and reason == "cancelled":
            # 늦춘 시각에 손님이 "아니요"·취소 (티켓 04). 사장님 결정은 남기고 주문은 취소로
            self._orders[call_id] = {**card, "status": "cancelled", "delay_reply": "no"}
            self._publish("order.update", self._orders[call_id])
            return
        elif card["status"] != "new":
            return
        self._deciders.pop(call_id, None)
        self._orders[call_id] = {**card, "withdrawn": reason}
        self._publish("order.update", self._orders[call_id])

    def _text_order(self, call_id: str) -> None:
        """확정 문자(메뉴·합계·픽업 시각)를 남기고 주문 카드를 한 번 보낸다(통화 카드는 그대로)."""
        card = self._orders[call_id]
        self._add_text(call_id, "order", sms.order_text(
            [(line["name_ko"], line["quantity"]) for line in card["lines"]],
            card["total"], card["pickup_label"]))
        self._orders[call_id] = {**card, "sms": self.customer_texts(call_id),
                                 "owner_check": self._owner_check(call_id)}
        self._publish("order.update", self._orders[call_id])

    def _text_delayed(self, call_id: str) -> None:
        """늦춘 수락 문자(메뉴·합계·바뀐 픽업 시각·"어려우시면 전화 주세요", 티켓 04)를 남기고
        주문 카드를 한 번 보낸다."""
        card = self._orders[call_id]
        self._text_customer(call_id, "delayed", sms.delayed_text(
            [(line["name_ko"], line["quantity"]) for line in card["lines"]],
            card["total"], card["owner_pickup_label"]))

    def _owner_check(self, call_id: str) -> bool:
        """그 통화에 답을 못 받은 질문(열림·시간 초과)이 있다 → 주문 카드 "사장님 확인 필요"."""
        return any(q["call_id"] == call_id and q["status"] in _UNANSWERED
                   for q in self._questions.values())

    def _recheck_order(self, call_id: str) -> None:
        """질문 상태가 바뀌었다. 주문 카드의 `owner_check`가 달라졌으면 다시 보낸다 (리뷰 C9)."""
        order = self._orders.get(call_id)
        if order is not None and order["owner_check"] != (check := self._owner_check(call_id)):
            self._orders[call_id] = {**order, "owner_check": check}
            self._publish("order.update", self._orders[call_id])

    def _add_text(self, call_id: str, kind: str, text: str, *, question_id: str = "",
                  translate_failed: bool = False) -> dict[str, Any]:
        """손님 문자 미리보기 하나를 남긴다(실제 발송 없음)."""
        texts = self._customer_sms.setdefault(call_id, [])
        now = self._clock.now()
        entry = {
            "id": f"{call_id}-s{len(texts) + 1}",
            "call_id": call_id,
            "kind": kind,  # order · expired · rejected · late_answer · free_answer
            "text": text,  # 손님이 받는 한국어 문장
            "sent_at": now.isoformat(),
            "sent_label": now.strftime("%H:%M"),
            "question_id": question_id,  # 사장님 답 문자면 그 질문 id
            "translate_failed": translate_failed,  # 자유 입력 글을 번역하지 못해 원문을 넣었다
        }
        texts.append(entry)
        self._seq += 1
        for queue in self._text_watchers.get(call_id, ()):
            queue.put_nowait(OwnerEvent("sms", entry, self._seq))
        return entry

    def _text_customer(self, call_id: str, kind: str, text: str, **kw: Any) -> None:
        """문자를 남기고 그 통화의 사장님 카드(주문 카드, 화면에 있는 통화 카드)를 다시 보낸다."""
        self._add_text(call_id, kind, text, **kw)
        texts = self.customer_texts(call_id)
        order = self._orders.get(call_id)
        if order is not None:
            self._orders[call_id] = {**order, "sms": texts,
                                    "owner_check": self._owner_check(call_id)}
            self._publish("order.update", self._orders[call_id])
        call = self._calls.get(call_id)
        if call is not None:
            if call["status"] in _OFF_SCREEN:
                self._calls[call_id] = {**call, "sms": texts}  # 화면에 없는 카드: 보내지 않는다
            else:
                self._put_call({**call, "sms": texts})

    def _start_call(self, call_id: str) -> None:
        if call_id in self._calls:
            return
        self._heard[call_id] = deque(maxlen=HEARD_LINES)
        now = self._clock.now()
        self._put_call({
            "id": call_id,
            "status": "live",
            "started_at": now.isoformat(),
            "started_label": now.strftime("%H:%M"),
            "reason": "",  # 끝난 이유 (`CallSession._end`): handoff·hangup·cancelled·no_input·error
            **self._cart_fields(()),
            "heard": [],  # 넘김 카드만: 마지막 손님 말 (한국어 원문, 오래된 것부터)
            "sms": self.customer_texts(call_id),  # 손님에게 보낸 문자 (티켓 07)
        })

    def _change_cart(self, call_id: str, lines: tuple[OrderLine, ...]) -> None:
        call = self._calls.get(call_id)
        if call is None or call["status"] != "live":
            return
        changed = self._dressed({**call, **self._cart_fields(lines)})
        if changed["lines"] != call["lines"]:
            self._put_call(changed)

    def _hear(self, call_id: str, text: str) -> None:
        heard = self._heard.get(call_id)
        if heard is not None:
            heard.append(text)
        self._caption(call_id, "customer", text)

    # --- 통화 자막 (티켓 10) --------------------------------------------------------------

    def _caption(self, call_id: str, who: str, text: str) -> None:
        """자막 한 줄을 더하고 그 통화 자막 전체(`call.caption`)를 보낸다. 통화 중 카드만."""
        call = self._calls.get(call_id)
        if not text or call is None or call["status"] != "live":
            return
        captions = self._captions.setdefault(call_id, _Captions())
        captions.add(who, text)
        self._publish_captions(call_id)
        if who == "customer":  # 손님 말만 번역한다. 캐시라 넘김 카드 손님 말에도 그대로 쓰인다
            self._translate_soon(call_id, [text])

    def _publish_captions(self, call_id: str) -> None:
        """그 통화 자막 전체를 보낸다. 지난번 보낸 것과 같으면(번역이 안 바뀜) 보내지 않는다."""
        captions = self._captions.get(call_id)
        if captions is None:
            return
        event = {"call_id": call_id, "lines": [
            {"n": n, "who": who, "text": text,
             "text_owner": self._translations.known(call_id, text) if who == "customer" else ""}
            for n, who, text in captions.lines]}
        if event != captions.sent:
            captions.sent = event
            self._publish("call.caption", event)

    def _hand_off(self, order: Order) -> None:
        """넘김 주문 → "손님 연락 필요" 카드. 통화 카드가 없었으면(알림 없이 온 주문) 새로 만든다"""
        if order.id not in self._calls:
            self._start_call(order.id)
        call = self._calls[order.id]
        heard = list(self._heard.get(order.id, ()))
        self._put_call({**call, **self._cart_fields(order.lines), "status": "handoff",
                        "reason": "handoff", "heard": heard})

    def _end_call(self, call_id: str, reason: str) -> None:
        self._heard.pop(call_id, None)
        self._captions.pop(call_id, None)  # 자막은 통화 중에만 (화면도 통화 중 카드 아래에만 둔다)
        self._deciders.pop(call_id, None)  # 끝난 세션을 놓는다. 통화 뒤 결정은 문자로만 간다
        if call_id in self._unspoken:
            # 통화 중 결정을 통화가 말하지 못하고 끝났다(시간 초과 대사가 먼저 나감·손님이 끊음).
            # 통화 뒤 결정과 같게 굳힌다(재료 소진이면 메뉴 이름이 든 취소 문자, 리뷰 경합)
            self._settle(call_id, said=False)
        if call_id in self._stock_out_open:
            # 재료 소진 안내 뒤 손님이 남은 메뉴로 확정하지도 바꿔 다시 요청하지도 않고 끝났다
            self._stock_out_open.discard(call_id)
            self._text_customer(call_id, "rejected", self._stock_out_text(call_id))
        order = self._orders.get(call_id)
        waiting = order is not None and order["status"] == "new" and not order["withdrawn"]
        asking = order is not None and order.get("delay_reply") == "asking"
        if waiting and order is not None and order["stage"] == "in_call":
            # 결정을 못 받고 통화가 끝났다(손님 끊음·오류·시간 초과 문자 약속 모두): 통화 뒤 대기.
            # 마감은 요청을 연 때부터. 지나면 `expire_due`가 자동 취소한다 (티켓 03)
            self._orders[call_id] = {**order, "stage": "after_call",
                                     "deadline_at": self._expires_at(order).isoformat()}
            self._publish("order.update", self._orders[call_id])
        elif asking and order is not None:
            # 늦춘 시각을 묻는 중에 통화가 끝났다(끊음·무응답, 티켓 04): 통화 뒤 늦춘 수락과 같다
            self._orders[call_id] = {**order, "delay_reply": ""}
            self._text_delayed(call_id)
        elif order is not None and order["status"] == "new" and order["withdrawn"] == "changed":
            # 손님이 고치는 중(다시 복창·"주문 취소할까요?")에 통화가 끝났다. 새 요청은 오지
            # 않는다: "고치는 중"으로 남기지 않고 손님 취소와 같게 닫는다(통화 뒤 대기·문자 없음)
            self._orders[call_id] = {**order, "withdrawn": "cancelled"}
            self._publish("order.update", self._orders[call_id])
        # 확인을 기다리는 주문, 늦춘 시각을 묻던 주문(티켓 04), 수락·거절한 주문(티켓 05)이 있으면
        # 그 주문 카드가 통화를 대신한다
        stands_in = waiting or asking or (
            order is not None and order["status"] in ("accepted", "rejected"))
        call = self._calls.get(call_id)
        if call is not None and call["status"] == "live":
            self._put_call({**call, "status": "ordered" if stands_in else "missed",
                            "reason": reason})
        for question in list(self._questions.values()):  # 열린 질문은 "통화 끝" (답은 계속 받는다)
            if question["call_id"] != call_id:
                continue
            question_id = question["id"]
            self._replies.pop(question_id, None)  # 끝난 세션을 놓는다 (리뷰 C5)
            if question["status"] == "open":
                self._put_question({**question, "status": "call_ended"})
            elif self._unsaid(question):  # 제시간 답을 통화가 말하지 못했다 (리뷰 C1)
                self._text_late_answer(question_id)
            self._said.discard(question_id)
        self._prune()

    # --- 상한 (6단계): 오래된 카드부터 지운다 ---------------------------------------------

    def stats(self) -> dict[str, int]:
        """메모리에 든 카드 수 (상태 확인용)."""
        return {"calls": len(self._calls), "orders": len(self._orders),
                "questions": len(self._questions), "texts": len(self._customer_sms)}

    def _prune(self) -> None:
        limits, now = self._limits, self._clock.now()
        dropped: set[str] = set()
        done = [c for c in self._calls.values() if c["status"] != "live"]
        # 화면에 떠 있는 카드(넘김·미완료)와 이미 내려간 카드를 따로 센다. 같이 세면 처리 안 한
        # 넘김 카드가 뒤에 온 주문 통화들에 밀려 화면에서 사라진다 (6단계 리뷰 4번)
        shown = [c for c in done if c["status"] not in _OFF_SCREEN]
        hidden = [c for c in done if c["status"] in _OFF_SCREEN]
        over = (_over(shown, "started_at", limits.max_call_cards, limits.call_card_hours, now)
                + _over(hidden, "started_at", limits.max_call_cards, limits.call_card_hours, now))
        for card in over:
            del self._calls[card["id"]]
            dropped.add(card["id"])
            if card["status"] not in _OFF_SCREEN:  # 화면에 떠 있던 카드: 화면에서도 뺀다
                self._publish("call.update", {**card, "status": "closed"})
        orders = list(self._orders.values())
        for card in _over(orders, "received_at", limits.max_order_cards,
                          limits.order_card_hours, now):
            del self._orders[card["id"]]
            self._recorded.pop(card["id"], None)
            self._order_of.pop(card["id"], None)
            self._unspoken.pop(card["id"], None)
            dropped.add(card["id"])
            twin = self._calls.get(card["id"])
            if twin is not None and twin["status"] == "ordered":  # 화면에 없는 짝 통화 카드
                del self._calls[card["id"]]
        # 통화 카드도 주문 카드도 안 남은 통화의 문자·질문을 같이 지운다
        gone = {call_id for call_id in dropped
                if call_id not in self._calls and call_id not in self._orders}
        if not gone:
            return
        for call_id in gone:
            self._customer_sms.pop(call_id, None)
        for question_id in [q["id"] for q in self._questions.values() if q["call_id"] in gone]:
            del self._questions[question_id]
            self._asked.pop(question_id, None)
            self._replies.pop(question_id, None)
            self._said.discard(question_id)
            self._passed_on.discard(question_id)

    def _unsaid(self, question: dict[str, Any]) -> bool:
        """받은 버튼 답을 통화도 문자도 손님에게 전하지 않았다."""
        return (question["status"] == "answered" and question["answer"] in OWNER_BUTTONS
                and question["id"] not in self._said
                and not any(t["question_id"] == question["id"]
                            for t in self._customer_sms.get(question["call_id"], ())))

    def _text_late_answer(self, question_id: str) -> None:
        """버튼 답을 손님 문자로(늦은 답·통화가 말하지 못한 답, 티켓 07). 통화 문장과 같은 틀."""
        card = self._questions[question_id]
        asked = self._asked[question_id]
        text = sms.late_answer_text(card["menu_name"], card["answer"],
                                    subject=asked.subject if asked.yes_no else "")
        self._text_customer(card["call_id"], "late_answer", text, question_id=question_id)

    def _time_out(self, question_id: str) -> None:
        question = self._questions.get(question_id)
        if question is not None and question["status"] == "open":
            self._put_question({**question, "status": "timeout"})
            self._learn(question_id, "timeout")

    # --- 보충 지식 (티켓 13): "손님이 궁금해한 것" 카드, 질문 카드 버튼 답 ------------------

    def _learn(self, question_id: str, answer: str) -> None:
        """질문 카드 답에서 배운다. 돼요/안 돼요는 "들어가요?"류 질문이면 사장님 확인으로 저장하고,
        모르겠어요·시간 초과는 보충 요청 카드로 보낸다. 자유 입력(그 손님 문자)은 넣지 않는다.
        """
        asked = self._asked.get(question_id)
        if asked is None:
            return
        if answer in ("yes", "no"):
            if asked.yes_no:
                contains = answer == "yes"
                self.knowledge.save_check(asked.key, contains)
                self._fill_from_check(asked, contains)
        elif answer in ("unsure", "timeout") and question_id not in self._passed_on:
            # 한 질문은 보충 카드에 한 번만 센다(시간 초과 뒤 늦은 모르겠어요, 리뷰 C6)
            self._passed_on.add(question_id)
            self._want(self._questions[question_id]["call_id"], asked.key, asked.menu_name,
                       asked.text)

    def _fill_from_check(self, asked: OwnerQuestion, contains: bool) -> None:
        """사장님 확인을 저장했다. 같은 열쇠의 열린 보충 카드를 채움으로 닫는다 (리뷰 P5).

        `answer_ko`에는 다음 손님에게 AI가 할 고정 문장(`templates.owner_checked`)을 넣는다.
        """
        found = self._open_wanted(asked.key)
        if found is not None:
            said = templates.owner_checked(asked.menu_name, asked.subject, contains)
            self._put_wanted({**found, "status": "filled", "answer_ko": said, "answer_text": "",
                              "saved_at": self._clock.now().isoformat()})

    def _open_wanted(self, key: KnowledgeKey) -> dict[str, Any] | None:
        return next((c for c in self._wanted.values()
                     if c["status"] == "open" and _key(c) == key), None)

    def _want(self, call_id: str, key: KnowledgeKey, menu_name: str, text: str) -> None:
        """보충 요청 하나. 같은 열쇠의 열린 카드가 있으면 횟수를 늘리고 문구를 마지막 질문으로."""
        now = self._clock.now().isoformat()
        found = self._open_wanted(key)
        asked = {"call_id": call_id, "text": text,
                 "text_owner": self._translations.known(call_id, text), "last_asked_at": now}
        if found is not None:
            self._put_wanted({**found, **asked, "count": found["count"] + 1})
        else:
            card_id = f"k{len(self._wanted) + 1}"
            self._put_wanted({
                "id": card_id,
                "menu_id": key.menu_id,
                "menu_name": menu_name,  # 한국어
                "menu_name_owner": self._owner_name(key.menu_id, menu_name),
                "kind": key.kind,  # `menu_info.QuestionKind`
                "subject": key.subject,  # 알레르기 성분·이름 붙은 재료·그 밖 질문 요약
                "yes_no": key.yes_no,  # 들어가는지만 묻는 질문 (열쇠의 한 칸, 리뷰 C4)
                **asked,  # 마지막 질문: 통화 id, 원문(한국어), 번역(아직·실패면 "")
                "count": 1,
                "asked_at": now,
                "last_asked_at": now,
                "status": "open",  # open · filled(사장님이 저장함)
                "answer_text": "",  # 사장님 글 원문 (사장님 언어)
                # 저장한 한국어 (엔진이 인용 틀로 그대로 말한다). 질문 카드 사장님 확인으로 채웠으면
                # 엔진이 할 고정 문장 (리뷰 P5)
                "answer_ko": "",
                "saved_at": "",
            })
        self._translate_soon(call_id, [text])

    def _put_wanted(self, card: dict[str, Any]) -> dict[str, Any]:
        self._wanted[card["id"]] = card
        self._publish("knowledge.update", card)
        return card

    def _open_question(
        self, call_id: str, question: OwnerQuestion, reply: Callable[[OwnerNote], None]
    ) -> None:
        question_id = f"{call_id}-q{question.number}"
        now = self._clock.now()
        card = {
            "id": question_id,
            "call_id": call_id,
            "number": question.number,
            "menu_id": question.menu_id,
            "menu_name": question.menu_name,  # 한국어
            "menu_name_owner": self._owner_name(question.menu_id, question.menu_name),  # 티켓 03
            "text": question.text,  # 손님 질문 원문 (한국어)
            "text_owner": self._translations.known(call_id, question.text),  # 번역, 아직·실패면 ""
            # 들어가는지만 묻는 질문: 화면 버튼 yes·no가 들어가요/안 들어가요 뜻이다 (리뷰 P2)
            "yes_no": question.yes_no,
            "sent_at": now.isoformat(),
            "deadline_at": (now + self._question_wait).isoformat(),  # 화면 카운트다운과 같은 시계
            "status": "open",  # open · answered · timeout · call_ended (티켓 06)
            "answer": "",  # yes · no · unsure · free (자유 입력, 티켓 07)
            "seen_at": "",  # 사장님이 카드를 처음 본 시각 (허브 시계 ISO, 티켓 06)
            "answer_text": "",  # 자유 입력 답의 사장님 글 원문 (사장님 언어, 티켓 07)
            "answer_text_ko": "",  # 그 글의 한국어 번역 (손님 문자에 들어감). 아직·실패면 ""
        }
        self._questions[question_id] = card
        self._asked[question_id] = question
        self._replies[question_id] = reply
        self._publish("owner.question", card)
        self._translate_soon(call_id, [question.text])

    def _put_question(self, card: dict[str, Any]) -> dict[str, Any]:
        self._questions[card["id"]] = card
        self._publish("owner.question.update", card)
        return card

    def _tell_call(self, reply: Callable[[Any], None], note: OwnerNote | OwnerDecision,
                   question_id: str) -> None:
        try:
            reply(note)
        except Exception:
            log.exception("사장님 쪽 알림을 통화에 넘기지 못함 (%s)", question_id)

    def _put_call(self, card: dict[str, Any]) -> dict[str, Any]:
        card = self._dressed(card)
        self._calls[card["id"]] = card
        self._publish("call.update", card)
        self._translate_later(card)
        return card

    # --- 번역 (티켓 03): 카드는 원문으로 먼저, 번역이 오면 같은 카드를 다시 ---------------

    def _dressed(self, card: dict[str, Any]) -> dict[str, Any]:
        """카드의 자유 문장(요청사항, 넘김 카드 손님 말)에 캐시에 있는 번역을 붙인다(없으면 "")."""
        call_id = card["id"]
        lines = [{**line, "request_owner": self._translations.known(call_id, line["request"])}
                 for line in card["lines"]]
        dressed = {**card, "lines": lines}
        if "heard" in card:
            dressed["heard_owner"] = [self._translations.known(call_id, t) for t in card["heard"]]
        return dressed

    def _translate_later(self, card: dict[str, Any]) -> None:
        texts = [line["request"] for line in card["lines"]] + list(card.get("heard", ()))
        self._translate_soon(card["id"], texts)

    def _translate_soon(self, call_id: str, texts: list[str]) -> None:
        todo = self._translations.claim(call_id, texts)
        if not todo:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 루프 밖(구독자 없이 부른 동기 코드): 번역 없이 원문만
            self._translations.release(call_id, todo)
            return
        task = loop.create_task(self._translate(call_id, todo))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _translate(self, call_id: str, texts: list[str]) -> None:
        if not await self._translations.fetch(call_id, texts):
            return  # 실패·버림: 원문 카드 그대로, 다시 보내지 않는다
        order = self._orders.get(call_id)
        if order is not None and (dressed := self._dressed(order)) != order:
            self._orders[call_id] = dressed
            self._publish("order.update", dressed)
        call = self._calls.get(call_id)
        if (call is not None and call["status"] not in _OFF_SCREEN
                and (dressed := self._dressed(call)) != call):
            self._calls[call_id] = dressed
            self._publish("call.update", dressed)
        self._publish_captions(call_id)  # 자막 손님 줄도 같은 자리에서 번역으로 덮어쓴다
        for question_id, question in list(self._questions.items()):
            text_owner = self._translations.known(call_id, question["text"])
            if question["call_id"] == call_id and question["text_owner"] != text_owner:
                self._questions[question_id] = {**question, "text_owner": text_owner}
                self._publish("owner.question.update", self._questions[question_id])
        for card in list(self._wanted.values()):  # "손님이 궁금해한 것" 카드 (티켓 13)
            text_owner = self._translations.known(call_id, card["text"])
            if card["call_id"] == call_id and card["text_owner"] != text_owner:
                self._put_wanted({**card, "text_owner": text_owner})

    # --- 자유 입력 답 → 손님 문자 (티켓 07): 사장님 글(ru) → ko. 메뉴명 보호 없음, 다듬지 않음 ---

    def _text_written_answer_soon(self, question_id: str) -> None:
        self._translate_then(lambda: self._translate_written_answer(question_id),
                             lambda: self._text_written_answer(question_id, ""))

    def _translate_then(self, translate: Callable[[], Coroutine[Any, Any, None]],
                        untranslated: Callable[[], None]) -> None:
        """사장님 글 번역 작업(`translate()`가 만드는 코루틴)을 루프에 건다. 번역기가 없거나
        (키 없음) 루프 밖(동기 코드)이면 번역 없이 `untranslated()`(원문 문자)를 바로 부른다."""
        if self._translator is None:
            untranslated()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            untranslated()
            return
        task = loop.create_task(translate())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _translate_written_answer(self, question_id: str) -> None:
        original = self._questions[question_id]["answer_text"]
        # 번역이 안 되면 원문으로 문자를 보낸다
        translated = await self._to_korean(original, f"자유 입력 답 {question_id}")
        if translated:
            card = self._questions[question_id]
            self._put_question({**card, "answer_text_ko": translated})
        self._text_written_answer(question_id, translated)

    async def _to_korean(self, text: str, what: str) -> str:
        """사장님 글(사장님 언어) → 한국어. 보충 답 미리보기·자유 입력 답 문자가 같이 쓴다
        (리뷰 C10).

        메뉴명 보호·다듬기 없이 그대로 옮긴다. 번역기가 없거나 실패하면 빈 글.
        """
        if self._translator is None:
            return ""
        try:
            [translated] = await self._translator.translate([text], self._lexicon.lang, SOURCE_LANG)
        except Exception as err:  # noqa: BLE001 — 번역이 안 되면 부른 쪽이 원문·직접 입력으로
            log.warning("사장님 글 번역 실패(%s): %s", type(err).__name__, what)
            return ""
        return translated.strip()

    # --- 직접 쓴 거절 사정 → 손님 문자 (주문 승인 티켓 05): 자유 입력 답과 같은 번역 길 ---------

    def _text_rejected_soon(self, order_id: str) -> None:
        self._translate_then(lambda: self._translate_rejected(order_id),
                             lambda: self._text_rejected(order_id, ""))

    async def _translate_rejected(self, order_id: str) -> None:
        original = self._orders[order_id]["reject_text"]
        translated = await self._to_korean(original, f"거절 사정 {order_id}")
        if translated and order_id in self._orders:
            self._orders[order_id] = {**self._orders[order_id], "reject_text_ko": translated}
        self._text_rejected(order_id, translated)

    def _text_rejected(self, order_id: str, translated: str) -> None:
        """직접 쓴 거절 사정 문자. 번역이 있으면 번역 인용 틀, 없으면 원문 + `translate_failed`."""
        card = self._orders.get(order_id)
        if card is None:  # 번역을 기다리는 사이 상한으로 지워졌다
            return
        if translated:
            text = sms.rejected_free_text(translated)
        else:
            text = sms.untranslated_rejected_text(card["reject_text"])
        self._text_customer(order_id, "rejected", text, translate_failed=not translated)

    def _text_written_answer(self, question_id: str, translated: str) -> None:
        card = self._questions[question_id]
        if translated:
            text = sms.free_answer_text(card["menu_name"], translated)
        else:
            text = sms.untranslated_answer_text(card["menu_name"], card["answer_text"])
        self._text_customer(card["call_id"], "free_answer", text, question_id=question_id,
                            translate_failed=not translated)

    def _cart_fields(self, lines: Sequence[OrderLine]) -> dict[str, Any]:
        card_lines = self._card_lines(lines)
        total = sum(line["subtotal"] for line in card_lines)
        return {"lines": card_lines, "total": total, "total_label": won_label(total)}

    def _order_card(self, order: Order) -> dict[str, Any]:
        lines = self._card_lines(order.lines)
        subtotal = sum(line["subtotal"] for line in lines)
        total = subtotal - order.discount  # 낼 금액: 통화가 복창한 쿠폰 할인 (쿠폰 티켓 09)
        received_at = order.created_at or self._clock.now().isoformat()
        return {
            "id": order.id,
            "status": "new",
            "received_at": received_at,
            "received_label": clock_label(received_at),  # 주문 시각 `16:05` (이야기 25, 리뷰 P1)
            # 손님 이름(성). 지금 통화 엔진은 이름을 받지 않아 비어 있다. 차 있으면 카드에 보인다
            "customer_name": order.customer_name,
            "pickup_time": order.pickup_time,
            "pickup_label": clock_label(order.pickup_time),
            "lines": lines,
            "total": total,
            "total_label": won_label(total),
            # 쿠폰 (쿠폰 티켓 09). 쿠폰이 없으면 할인 0·이름 "" (카드에 할인 줄이 없다)
            "subtotal": subtotal,
            "subtotal_label": won_label(subtotal),
            "coupon_id": order.coupon_id,
            "coupon_name": order.coupon_name,
            "discount": order.discount,
            "discount_label": won_label(order.discount),
        }

    def _card_lines(self, order_lines: Sequence[OrderLine]) -> list[dict[str, Any]]:
        """주문 카드·통화 카드가 같이 쓰는 줄 모양."""
        lines = []
        for line in order_lines:
            name, unit = self._name_and_unit_price(line)
            lines.append({
                "menu_id": line.menu_id,
                "name_ko": name,
                "name_owner": self._owner_name(line.menu_id, name),
                "quantity": line.quantity,
                "options": list(line.options),
                "options_owner": [self._lexicon.option(o) for o in line.options],
                "request": line.request,
                "badges": request_badges(line.request),
                "subtotal": unit * line.quantity,
            })
        return lines

    def _owner_name(self, menu_id: str, name_ko: str) -> str:
        """사장님이 쓰는 원어 이름(`MenuItem.name_native`, 번역기 안 씀). 비면 한국어 이름."""
        item = self._store().get_menu(menu_id)
        return (item.name_native if item else "") or name_ko

    def _name_and_unit_price(self, line: OrderLine) -> tuple[str, int]:
        """줄의 한국어 이름·한 개 가격. 주문에 담긴 값(손님이 들은 값)을 먼저 쓴다.

        이름이 비어 있으면(계약 v1 전 주문·다른 구현) 가게 메뉴에서 찾는다: 가격 + 고른 옵션
        추가금. 메뉴도 없으면 이름 = menu_id, 단가 0.
        """
        if line.name:
            return line.name, line.unit_price
        item = self._store().get_menu(line.menu_id)
        if item is None:
            return line.menu_id, 0
        extra = {o.name: o.extra_price for o in item.options}
        return item.name_ko, item.price + sum(extra.get(o, 0) for o in line.options)

    def _snapshot(self) -> dict[str, Any]:
        calls = [c for c in self._calls.values() if c["status"] not in _OFF_SCREEN]
        captions = [c.sent for call_id, c in self._captions.items()
                    if c.sent is not None and self._calls[call_id]["status"] == "live"]
        # `now`: 허브 시계 지금 시각. 화면이 기기 시계와의 차이를 재서 카운트다운·픽업 색에 쓴다
        # (`PHONE_NOW` 시연·기기 시계가 틀린 태블릿, 리뷰 C2)
        return {"orders": list(self._orders.values()), "calls": calls,
                "questions": list(self._questions.values()), "captions": captions,
                "knowledge": self.knowledge_cards(), "sold_out": self._sold_out_view(),
                "now": self._clock.now().isoformat()}

    def _publish(self, name: str, data: dict[str, Any]) -> None:
        if name == "order.update":
            self._record_decision(data)
            self._tell_order_watchers(data)
        self._seq += 1
        event = OwnerEvent(name, data, self._seq)
        for queue in self._subscribers:
            queue.put_nowait(event)

    def _record_decision(self, card: dict[str, Any]) -> None:
        """주문 카드의 결정이 지난번 넘긴 것과 다르면 결정 기록으로 넘긴다 (티켓 10).

        모든 결정(수락·늦춘 수락·거절·자동 취소·자동 확정)이 `order.update`를 거치므로 여기 한 곳이
        이음매다. 받는 곳이 실패해도 화면·통화는 계속한다.
        """
        if self._decision_sink is None or card["id"] in self._unspoken:
            return  # 통화가 아직 말하지 않은 결정은 굳기 전이다(손님이 거둘 수 있다)
        record = decision_of(card, self._order_of.get(card["id"]))
        if record is None or self._recorded.get(record.order_id) == record:
            return
        self._recorded[record.order_id] = record
        if not record.decided_at:  # 결정 시각이 카드에 없는 결정(자동 취소 등): 기록하는 시각
            record = replace(record, decided_at=self._clock.now().isoformat())
        try:
            self._decision_sink.record(record)
        except Exception:
            log.exception("주문 %s 결정 기록 실패 (%s)", record.order_id,
                          type(self._decision_sink).__name__)

    def _bind(self) -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

    def _on_loop(self, fn: Any, *args: Any) -> None:
        """허브의 루프에서 `fn`을 부른다. 지금 그 루프 위면 바로, 다른 스레드면 넘긴다."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and self._loop is None:
            self._loop = running
        if self._loop is None or running is self._loop:
            fn(*args)  # 루프에 아직 안 묶였으면(구독자 없음) 그냥 부른다
        else:
            self._loop.call_soon_threadsafe(fn, *args)


class _Captions:
    """통화 하나의 자막: 최근 `CAPTION_LINES`줄 (번호, 누가, 한국어 원문). 번호는 통화 안 1부터."""

    def __init__(self) -> None:
        self.lines: deque[tuple[int, str, str]] = deque(maxlen=CAPTION_LINES)
        self.sent: dict[str, Any] | None = None  # 마지막으로 보낸 `call.caption` data
        self._count = 0

    def add(self, who: str, text: str) -> None:
        self._count += 1
        self.lines.append((self._count, who, text))


def _rest_of(card: dict[str, Any], order: Order) -> bool:
    """재료 소진 거절 카드에 온 확정 주문이 떨어진 메뉴만 뺀 나머지다(새 메뉴 없음, 티켓 06)."""
    if card["status"] != "rejected" or card["reject_reason"] != "out_of_stock":
        return False
    return only_the_rest((line["menu_id"] for line in card["lines"]), card["reject_menu_ids"],
                         (line.menu_id for line in order.lines))


def _awaiting(card: dict[str, Any]) -> bool:
    """사장님 결정을 기다리는 확인 요청 카드: 확인 대기(new), 요청 있음, 손님이 거두지 않음."""
    return card["status"] == "new" and bool(card.get("request")) and not card.get("withdrawn")


def _over(cards: list[dict[str, Any]], stamp: str, max_count: int, max_hours: float,
          now: datetime) -> list[dict[str, Any]]:
    """상한을 넘은 카드(오래된 것부터). `cards`는 온 순서다."""
    old: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for card in cards:
        (old if max_hours > 0 and _hours_since(card[stamp], now) > max_hours else kept).append(card)
    if 0 < max_count < len(kept):
        old += kept[: len(kept) - max_count]
    return old


def _hours_since(iso: str, now: datetime) -> float:
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=now.tzinfo)
        return (now - then).total_seconds() / 3_600
    except (ValueError, TypeError):
        return 0.0  # 시각을 못 읽는 카드는 시간으로 지우지 않는다(수 상한이 지운다)


def _key(card: dict[str, Any]) -> KnowledgeKey:
    return KnowledgeKey(card["menu_id"], card["kind"], card["subject"], card["yes_no"])


def _later(iso: str, minutes: int) -> str:
    """ISO 시각 + 분 → ISO (늦춘 수락의 바뀐 픽업 시각). 비면 빈 글."""
    if not iso:
        return ""
    return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat()


def clock_label(iso: str) -> str:
    """ISO 8601 시각 → `18:30` (가게 시각 그대로, 24시간제). 비면 빈 글."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).strftime("%H:%M")


def won_label(amount: int) -> str:
    """원 금액 → `25,000₩` (언어와 상관없는 고정 표기)."""
    return f"{amount:,}₩"
