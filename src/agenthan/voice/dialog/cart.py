"""장바구니. 복창·총액은 대화 기록이 아니라 이 객체에서 만든다 (spec D6).

불변 객체다. 바꾸는 메서드는 새 `Cart`를 돌려준다. 그래서 `TurnResult.cart`를
턴마다 그대로 넘겨도 다음 턴에 바뀌지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agenthan.core.contracts import MenuItem, MenuOption, OrderLine
from agenthan.voice.dialog import templates


@dataclass(frozen=True)
class CartLine:
    item: MenuItem
    qty: int
    options: tuple[MenuOption, ...] = ()
    request: str = ""

    def unit_price(self) -> int:
        return self.item.price + sum(o.extra_price for o in self.options)

    def subtotal(self) -> int:
        return self.unit_price() * self.qty

    def same_kind(self, other: CartLine) -> bool:
        """같은 메뉴·같은 옵션·같은 요청이면 한 줄로 합친다."""
        return (
            self.item.id == other.item.id
            and set(self.options) == set(other.options)
            and self.request == other.request
        )

    def phrase(self) -> str:
        return templates.line_phrase(
            self.item.name_ko,
            self.qty,
            templates.counter_for(self.item.category),
            [o.name for o in self.options],
        )

    def to_order_line(self) -> OrderLine:
        return OrderLine(
            menu_id=self.item.id,
            quantity=self.qty,
            options=tuple(o.name for o in self.options),
            request=self.request,
        )


@dataclass(frozen=True)
class Cart:
    lines: tuple[CartLine, ...] = ()

    def is_empty(self) -> bool:
        return not self.lines

    def add(
        self,
        item: MenuItem,
        qty: int = 1,
        *,
        options: tuple[MenuOption, ...] = (),
        request: str = "",
    ) -> Cart:
        new = CartLine(item, qty, options, request)
        for i, line in enumerate(self.lines):
            if line.same_kind(new):
                merged = replace(line, qty=line.qty + qty)
                return Cart(self.lines[:i] + (merged,) + self.lines[i + 1 :])
        return Cart((*self.lines, new))

    def index_of(self, line: CartLine) -> int | None:
        """`line`과 같은 종류(메뉴·옵션·요청)인 줄의 위치. 수량은 보지 않는다."""
        return next((i for i, mine in enumerate(self.lines) if mine.same_kind(line)), None)

    def indices_for(self, item_id: str) -> list[int]:
        return [i for i, line in enumerate(self.lines) if line.item.id == item_id]

    def remove_item(self, item_id: str) -> Cart:
        """그 메뉴의 줄을 옵션과 상관없이 모두 뺀다."""
        return Cart(tuple(line for line in self.lines if line.item.id != item_id))

    def replace_at(self, index: int, line: CartLine) -> Cart:
        """`index` 줄을 `line`으로 바꾼다. 수량이 0 이하면 뺀다.

        바꾼 뒤 다른 줄과 같은 종류가 되면 그 줄에 수량을 합친다.
        """
        rest = self.lines[:index] + self.lines[index + 1 :]
        if line.qty <= 0:
            return Cart(rest)
        for i, other in enumerate(rest):
            if other.same_kind(line):
                merged = replace(other, qty=other.qty + line.qty)
                return Cart(rest[:i] + (merged,) + rest[i + 1 :])
        return Cart(rest[:index] + (line,) + rest[index:])

    def total(self) -> int:
        return sum(line.subtotal() for line in self.lines)

    def summary(self) -> str:
        """복창용 한 줄. 예: "메도빅 하나, 아메리카노 두 잔"."""
        return ", ".join(line.phrase() for line in self.lines)

    def to_order_lines(self) -> list[OrderLine]:
        return [line.to_order_line() for line in self.lines]
