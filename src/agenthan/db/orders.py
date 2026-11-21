"""전화 주문을 phone_orders 표에 남기는 `OrderSink`. 사장님 화면(OwnerHub)과 나란히 붙는다.

계약대로 submit()은 바로 돌아온다: 주문을 큐에 넣고, 쓰기는 별도 스레드가 한다.
DB가 꺼져 있거나 실패하면 경고만 남기고 버린다(통화·사장님 화면은 영향 없음).

사장님 결정(주문 승인 티켓 10)도 같은 큐·스레드로 남긴다(`record`, 허브의 `DecisionSink`).
결정은 주문 기록보다 먼저 올 수 있다(통화 중 수락 → 통화 끝 확정). 그래서 둘 다 upsert이고 서로
다른 열만 고친다: 주문은 주문 열, 결정은 `owner_status`·`reject_reason`·`decided_at`·
`owner_pickup_time`. 통화가 거절·취소로 끝나 주문 기록이 오지 않으면 결정에 실린 주문서로
행을 채운다.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import asdict, replace
from datetime import datetime
from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb

from agenthan.core.contracts import Order
from agenthan.db.conn import guarded

if TYPE_CHECKING:  # 실행 때는 voice를 import하지 않는다(모양만 맞으면 된다)
    from agenthan.voice.owner.decisions import DecisionRecord

log = logging.getLogger(__name__)

_ORDER_COLUMNS = (
    "id, store_id, status, lines, total, pickup_time, customer_name, customer_phone,"
    " transcript, created_at"
)


def _order_values(order: Order, store_id: str) -> tuple:
    lines = [asdict(line) for line in order.lines]
    total = sum(line.unit_price * line.quantity for line in order.lines)
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
    )


def save_order(conn, order: Order, store_id: str = "") -> None:
    conn.execute(
        f"INSERT INTO phone_orders ({_ORDER_COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, lines = EXCLUDED.lines,"
        " total = EXCLUDED.total, pickup_time = EXCLUDED.pickup_time,"
        " customer_name = EXCLUDED.customer_name, customer_phone = EXCLUDED.customer_phone,"
        " transcript = EXCLUDED.transcript, received_at = now()",
        _order_values(order, store_id),
    )


def save_decision(conn, decision: DecisionRecord, store_id: str = "") -> None:
    """사장님 결정 열만 고친다. 행이 없고 주문서가 실려 있으면 주문서로 행을 만든다.

    결정으로 만든 행의 `status`는 draft다(통화 끝 주문 기록이 아직 안 옴). 확정 주문이 오면
    `save_order`가 confirmed로 바꾼다. 통화 중 거절처럼 주문 기록이 끝내 안 오면 draft로 남는다.
    """
    decided_at = datetime.fromisoformat(decision.decided_at) if decision.decided_at else None
    owner = (decision.owner_status, decision.reject_reason, decided_at, decision.owner_pickup_time)
    if decision.order is None:
        conn.execute(
            "UPDATE phone_orders SET owner_status = %s, reject_reason = %s, decided_at = %s,"
            " owner_pickup_time = %s WHERE id = %s",
            (*owner, decision.order_id),
        )
        return
    conn.execute(
        f"INSERT INTO phone_orders ({_ORDER_COLUMNS}, owner_status, reject_reason, decided_at,"
        " owner_pickup_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (id) DO UPDATE SET owner_status = EXCLUDED.owner_status,"
        " reject_reason = EXCLUDED.reject_reason, decided_at = EXCLUDED.decided_at,"
        " owner_pickup_time = EXCLUDED.owner_pickup_time",
        (*_order_values(replace(decision.order, status="draft"), store_id), *owner),
    )


class PgOrderSink:
    def __init__(self, store_id: str = "", url: str | None = None) -> None:
        self.store_id = store_id
        self.url = url
        self._queue: queue.Queue[Order | DecisionRecord | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="pg-order-sink", daemon=True)
        self._thread.start()

    def submit(self, order: Order) -> None:
        self._queue.put(order)

    def record(self, decision: DecisionRecord) -> None:
        """`DecisionSink`: 사장님 결정. 주문과 같은 큐라 넣은 순서대로 쓴다."""
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
