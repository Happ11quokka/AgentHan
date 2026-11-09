from agenthan.core.contracts import OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.cart import Cart, CartLine

STORE = FakeStoreKnowledge.from_fixture()
MEDOVIK = STORE.get_menu("m01")
AMERICANO = STORE.get_menu("m03")


def _option(item, name):
    return next(o for o in item.options if o.name == name)


def test_empty_cart() -> None:
    cart = Cart()
    assert cart.is_empty()
    assert cart.total() == 0
    assert cart.summary() == ""


def test_total_is_price_times_qty() -> None:
    cart = Cart().add(MEDOVIK, 1).add(AMERICANO, 2)
    assert cart.total() == 7500 + 4500 * 2


def test_total_includes_option_extra_price_per_unit() -> None:
    decaf = _option(AMERICANO, "디카페인 변경")
    shot = _option(AMERICANO, "샷 추가")
    cart = Cart().add(AMERICANO, 2, options=(decaf, shot))
    assert cart.total() == (4500 + 500 + 500) * 2


def test_add_returns_new_cart() -> None:
    empty = Cart()
    one = empty.add(MEDOVIK, 1)
    assert empty.is_empty()
    assert not one.is_empty()


def test_same_menu_same_options_merge() -> None:
    cart = Cart().add(MEDOVIK, 1).add(MEDOVIK, 2)
    assert len(cart.lines) == 1
    assert cart.lines[0].qty == 3


def test_different_options_stay_separate() -> None:
    decaf = _option(AMERICANO, "디카페인 변경")
    cart = Cart().add(AMERICANO, 1).add(AMERICANO, 1, options=(decaf,))
    assert len(cart.lines) == 2


def test_summary_uses_korean_counters() -> None:
    cart = Cart().add(MEDOVIK, 1).add(AMERICANO, 2)
    assert cart.summary() == "메도빅 하나, 아메리카노 두 잔"


def test_to_order_lines() -> None:
    decaf = _option(AMERICANO, "디카페인 변경")
    cart = Cart().add(MEDOVIK, 1).add(AMERICANO, 2, options=(decaf,))
    assert cart.to_order_lines() == [
        OrderLine(menu_id="m01", quantity=1, name="메도빅", unit_price=7500),
        OrderLine(menu_id="m03", quantity=2, options=("디카페인 변경",), name="아메리카노",
                  unit_price=5000),
    ]


# ---------------------------------------------------------------- 티켓 03: 줄 찾기·바꾸기·빼기

BLIN = STORE.get_menu("m04")


def test_index_of_ignores_qty() -> None:
    cart = Cart().add(MEDOVIK, 2).add(AMERICANO, 1)
    assert cart.index_of(CartLine(AMERICANO, 5)) == 1
    assert cart.index_of(CartLine(BLIN, 1)) is None


def test_indices_for_item() -> None:
    decaf = _option(AMERICANO, "디카페인 변경")
    cart = Cart().add(AMERICANO, 1).add(MEDOVIK, 1).add(AMERICANO, 1, options=(decaf,))
    assert cart.indices_for("m03") == [0, 2]
    assert cart.indices_for("m04") == []


def test_remove_item_drops_every_line_of_that_menu() -> None:
    decaf = _option(AMERICANO, "디카페인 변경")
    cart = Cart().add(AMERICANO, 2).add(BLIN, 1).add(AMERICANO, 1, options=(decaf,))
    assert [(line.item.id, line.qty) for line in cart.remove_item("m03").lines] == [("m04", 1)]


def test_replace_at_changes_one_line() -> None:
    cart = Cart().add(MEDOVIK, 1).add(AMERICANO, 2)
    new = cart.replace_at(1, CartLine(AMERICANO, 3))
    assert [(line.item.id, line.qty) for line in new.lines] == [("m01", 1), ("m03", 3)]
    assert cart.lines[1].qty == 2  # 원래 장바구니는 그대로


def test_replace_at_zero_qty_removes_line() -> None:
    cart = Cart().add(MEDOVIK, 1).add(AMERICANO, 2)
    assert [line.item.id for line in cart.replace_at(0, CartLine(MEDOVIK, 0)).lines] == ["m03"]


def test_replace_at_merges_when_it_becomes_same_kind() -> None:
    decaf = _option(AMERICANO, "디카페인 변경")
    cart = Cart().add(AMERICANO, 1, options=(decaf,)).add(AMERICANO, 2)
    merged = cart.replace_at(1, CartLine(AMERICANO, 2, (decaf,)))
    assert len(merged.lines) == 1
    assert merged.lines[0].qty == 3
    assert merged.lines[0].options == (decaf,)


def test_same_menu_different_request_stays_separate() -> None:
    cart = Cart().add(BLIN, 1).add(BLIN, 1, request="소스 따로 주세요")
    assert len(cart.lines) == 2


def test_option_order_does_not_split_lines() -> None:
    decaf = _option(AMERICANO, "디카페인 변경")
    shot = _option(AMERICANO, "샷 추가")
    cart = Cart().add(AMERICANO, 1, options=(decaf, shot)).add(AMERICANO, 1, options=(shot, decaf))
    assert len(cart.lines) == 1
    assert cart.total() == (4500 + 1000) * 2
