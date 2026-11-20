"""전화 주문을 phone_orders 표에 남기는 `OrderSink`. 사장님 화면(OwnerHub)과 나란히 붙는다.

계약대로 submit()은 바로 돌아온다: 주문을 큐에 넣고, 쓰기는 별도 스레드가 한다.
DB가 꺼져 있거나 실패하면 경고만 남기고 버린다(통화·사장님 화면은 영향 없음).
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import asdict

from psycopg.types.json import Jsonb

from agenthan.core.contracts import Order
from agenthan.db.conn import guarded

log = logging.getLogger(__name__)


def save_order(conn, order: Order, store_id: str = "") -> None:
    lines = [asdict(line) for line in order.lines]
    total = sum(line.unit_price * line.quantity for line in order.lines)
    conn.execute(
        "INSERT INTO phone_orders (id, store_id, status, lines, total, pickup_time, customer_name,"
        " customer_phone, transcript, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, lines = EXCLUDED.lines,"
        " total = EXCLUDED.total, pickup_time = EXCLUDED.pickup_time,"
        " customer_name = EXCLUDED.customer_name, customer_phone = EXCLUDED.customer_phone,"
        " transcript = EXCLUDED.transcript, received_at = now()",
        (
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
        ),
    )


class PgOrderSink:
    def __init__(self, store_id: str = "", url: str | None = None) -> None:
        self.store_id = store_id
        self.url = url
        self._queue: queue.Queue[Order | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="pg-order-sink", daemon=True)
        self._thread.start()

    def submit(self, order: Order) -> None:
        self._queue.put(order)

    def close(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout)

    def flush(self) -> None:
        """테스트용: 지금까지 넣은 주문을 다 쓸 때까지 기다린다."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            order = self._queue.get()
            try:
                if order is None:
                    return
                ok = guarded(
                    lambda conn, o=order: save_order(conn, o, self.store_id) or True,
                    False,
                    self.url,
                )
                if not ok:
                    log.warning("주문 %s를 DB에 남기지 못함(사장님 화면에는 있음)", order.id)
            finally:
                self._queue.task_done()
