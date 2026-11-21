"""전화 주문을 phone_orders 표에 남기는 `OrderSink`. 사장님 화면(OwnerHub)과 나란히 붙는다.

계약대로 submit()은 바로 돌아온다: 주문을 큐에 넣고, 쓰기는 별도 스레드가 한다.
DB가 꺼져 있거나 실패하면 경고만 남기고 버린다(통화·사장님 화면은 영향 없음).

사장님 결정(주문 승인 티켓 10)도 같은 큐·스레드로 남긴다(`record_decision`, 행 모양은 이 모듈의
`OwnerDecisionRow`). 전화 허브의 결정 기록을 이 행으로 옮기는 일은 발표 서버(`agenthan.serve`)가
한다: db는 voice를 import하지 않는다(PRD §7).
결정은 주문 기록보다 먼저 올 수 있다(통화 중 수락 → 통화 끝 확정). 그래서 둘 다 upsert이고 서로
다른 열만 고친다: 주문은 주문 열, 결정은 `owner_status`·`reject_reason`·`decided_at`·
`owner_pickup_time`과 거절 내용(`reject_menu_ids`·`reject_text`·`reject_text_ko`, 마이그레이션
004). 통화가 거절·취소로 끝나 주문 기록이 오지 않으면 결정에 실린 주문서로 행을 채운다.

주문 현황 다시 열기(전화 메뉴·주문 현황·쿠폰 티켓 06): 서버가 다시 떠 사장님 허브에 카드가 없을 때
`find_order`가 주문 한 줄을 `PhoneOrderRow`로 읽는다. 손님 모양으로 바꾸는 일은 전화 쪽이 한다
(`agenthan.serve`가 잇는다).

쿠폰(쿠폰 티켓 09, 마이그레이션 006): 주문 열에 `coupon_id`·`coupon_name`·`discount`가 있고
`total`은 낼 금액(할인 뒤)이다. 할인 전 금액 = `total` + `discount`. 쿠폰 전 행은 ''·''·0.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

from psycopg.types.json import Jsonb

from agenthan.core.contracts import Order
from agenthan.db.conn import guarded

log = logging.getLogger(__name__)

_ORDER_COLUMNS = (
    "id, store_id, status, lines, total, pickup_time, customer_name, customer_phone,"
    " transcript, created_at, coupon_id, coupon_name, discount"
)
_ORDER_VALUES = ",".join(["%s"] * 13)


# 사장님 결정 열(002·004). `save_decision`이 이 열만 고친다
_OWNER_COLUMNS = (
    "owner_status",
    "reject_reason",
    "decided_at",
    "owner_pickup_time",
    "reject_menu_ids",
    "reject_text",
    "reject_text_ko",
)


def _order_values(order: Order, store_id: str) -> tuple:
    lines = [asdict(line) for line in order.lines]
    # 낼 금액(할인 뒤, `Order.total`). 할인액은 통화가 복창한 쿠폰 계산 결과다 (쿠폰 티켓 09)
    total = order.total
    return (
        order.id,
        store_id,
        order.status,
        Jsonb(lines),
        total,
        order.pickup_time,
        order.customer_name,
        order.customer_phone,
        Jsonb(list(order.transcript)),
        order.created_at,
        order.coupon_id,
        order.coupon_name,
        order.discount,
    )


@dataclass(frozen=True)
class OwnerDecisionRow:
    """phone_orders의 사장님 결정 열 한 벌(마이그레이션 002). 같은 주문이 다시 오면 덮어쓴다."""

    order_id: str
    # accepted · auto_accepted · rejected · expired · cancelled ·
    # customer_cancelled(손님 취소, 티켓 07)
    owner_status: str
    reject_reason: str = ""  # out_of_stock · busy · closed · free, 없으면 ""
    decided_at: str = ""  # ISO 8601(시간대 포함), 없으면 "" → NULL
    owner_pickup_time: str = ""  # 늦춘 수락의 바뀐 픽업 시각 ISO, 없으면 ""
    # 거절 내용(마이그레이션 004): 재료 소진의 떨어진 메뉴 id들, 직접 쓰기 글 원문과 한국어 번역
    reject_menu_ids: tuple[str, ...] = ()
    reject_text: str = ""
    reject_text_ko: str = ""
    # 행이 아직 없을 때(통화가 거절·취소로 끝나 주문 기록이 안 옴) 행을 채울 주문서
    order: Order | None = field(default=None, compare=False)


def save_order(conn, order: Order, store_id: str = "") -> None:
    conn.execute(
        f"INSERT INTO phone_orders ({_ORDER_COLUMNS}) VALUES ({_ORDER_VALUES})"
        " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, lines = EXCLUDED.lines,"
        " total = EXCLUDED.total, pickup_time = EXCLUDED.pickup_time,"
        " customer_name = EXCLUDED.customer_name, customer_phone = EXCLUDED.customer_phone,"
        " transcript = EXCLUDED.transcript, coupon_id = EXCLUDED.coupon_id,"
        " coupon_name = EXCLUDED.coupon_name, discount = EXCLUDED.discount, received_at = now()",
        _order_values(order, store_id),
    )


def save_decision(conn, decision: OwnerDecisionRow, store_id: str = "") -> None:
    """사장님 결정 열만 고친다. 행이 없고 주문서가 실려 있으면 주문서로 행을 만든다.

    결정으로 만든 행의 `status`는 draft다(통화 끝 주문 기록이 아직 안 옴). 확정 주문이 오면
    `save_order`가 confirmed로 바꾼다. 통화 중 거절처럼 주문 기록이 끝내 안 오면 draft로 남는다.
    """
    decided_at = datetime.fromisoformat(decision.decided_at) if decision.decided_at else None
    owner = (
        decision.owner_status,
        decision.reject_reason,
        decided_at,
        decision.owner_pickup_time,
        Jsonb(list(decision.reject_menu_ids)),
        decision.reject_text,
        decision.reject_text_ko,
    )
    if decision.order is None:
        conn.execute(
            f"UPDATE phone_orders SET {', '.join(f'{c} = %s' for c in _OWNER_COLUMNS)}"
            " WHERE id = %s",
            (*owner, decision.order_id),
        )
        return
    conn.execute(
        f"INSERT INTO phone_orders ({_ORDER_COLUMNS}, {', '.join(_OWNER_COLUMNS)})"
        f" VALUES ({_ORDER_VALUES}, {', '.join(['%s'] * len(_OWNER_COLUMNS))})"
        f" ON CONFLICT (id) DO UPDATE SET"
        f" {', '.join(f'{c} = EXCLUDED.{c}' for c in _OWNER_COLUMNS)}",
        (*_order_values(replace(decision.order, status="draft"), store_id), *owner),
    )


@dataclass(frozen=True)
class PhoneOrderRow:
    """phone_orders 한 줄 (주문 현황 다시 열기, 티켓 06). 주문 열(쿠폰 006 포함) + 사장님 결정
    열(002·004)."""

    id: str
    status: str  # draft · confirmed · handoff (통화가 어떻게 끝났나)
    lines: list[dict]  # `OrderLine` 칸: menu_id·quantity·options·request·name·unit_price
    pickup_time: str
    created_at: str  # 주문서를 만든 가게 시각 ISO (통화 뒤 확인 요청을 연 시각)
    owner_status: str  # '' = 아직 결정 전
    reject_reason: str
    decided_at: str  # ISO 8601, 없으면 ""
    owner_pickup_time: str
    reject_menu_ids: tuple[str, ...]
    reject_text: str
    reject_text_ko: str
    # 쓴 쿠폰(마이그레이션 006). 쿠폰 전 행·쿠폰 없는 주문은 ''·''·0
    coupon_id: str = ""
    coupon_name: str = ""
    discount: int = 0


def load_order(conn, order_id: str) -> PhoneOrderRow | None:
    """주문 번호(통화 id)로 한 줄. 없으면 None."""
    row = conn.execute(
        "SELECT id, status, lines, pickup_time, created_at, owner_status, reject_reason,"
        " decided_at, owner_pickup_time, reject_menu_ids, reject_text, reject_text_ko,"
        " coupon_id, coupon_name, discount"
        " FROM phone_orders WHERE id = %s",
        (order_id,),
    ).fetchone()
    if row is None:
        return None
    (
        id_,
        status,
        lines,
        pickup,
        created,
        owner,
        reason,
        decided,
        moved,
        menu_ids,
        text,
        text_ko,
        coupon_id,
        coupon_name,
        discount,
    ) = row
    return PhoneOrderRow(
        id=id_,
        status=status,
        lines=list(lines or []),
        pickup_time=pickup,
        created_at=created,
        owner_status=owner,
        reject_reason=reason,
        decided_at=decided.isoformat() if decided is not None else "",
        owner_pickup_time=moved,
        reject_menu_ids=tuple(str(m) for m in menu_ids or ()),
        reject_text=text,
        reject_text_ko=text_ko,
        coupon_id=coupon_id,
        coupon_name=coupon_name,
        discount=int(discount or 0),
    )


def find_order(order_id: str, url: str | None = None) -> PhoneOrderRow | None:
    """`load_order`를 DB가 꺼져 있거나 실패해도 부를 수 있게(None). 동기라 이벤트 루프 밖에서."""
    return guarded(lambda conn: load_order(conn, order_id), None, url)


class PgOrderSink:
    def __init__(self, store_id: str = "", url: str | None = None) -> None:
        self.store_id = store_id
        self.url = url
        self._queue: queue.Queue[Order | OwnerDecisionRow | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="pg-order-sink", daemon=True)
        self._thread.start()

    def submit(self, order: Order) -> None:
        self._queue.put(order)

    def record_decision(self, decision: OwnerDecisionRow) -> None:
        """사장님 결정. 주문과 같은 큐라 넣은 순서대로 쓴다. 바로 돌아온다."""
        self._queue.put(decision)

    def close(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout)

    def flush(self) -> None:
        """테스트용: 지금까지 넣은 주문을 다 쓸 때까지 기다린다."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                if isinstance(item, Order):
                    ok = guarded(
                        lambda conn, o=item: save_order(conn, o, self.store_id) or True,
                        False,
                        self.url,
                    )
                    if not ok:
                        log.warning("주문 %s를 DB에 남기지 못함(사장님 화면에는 있음)", item.id)
                    continue
                ok = guarded(
                    lambda conn, d=item: save_decision(conn, d, self.store_id) or True,
                    False,
                    self.url,
                )
                if not ok:
                    log.warning(
                        "주문 %s 사장님 결정(%s)을 DB에 남기지 못함(화면에는 있음)",
                        item.order_id,
                        item.owner_status,
                    )
            finally:
                self._queue.task_done()
