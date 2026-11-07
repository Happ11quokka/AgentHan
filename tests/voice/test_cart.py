from agenthan.core.contracts import OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.cart import Cart

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
        OrderLine(menu_id="m01", quantity=1),
        OrderLine(menu_id="m03", quantity=2, options=("디카페인 변경",)),
    ]
