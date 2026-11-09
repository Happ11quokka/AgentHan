"""사장님 언어(러시아어) 카드 (4단계 티켓 03): HTTP 없이 허브 공개 인터페이스만 본다
(spec 이음매 3).

카드 줄의 원어 이름(비면 한국어)·옵션 사전·고정 표기, 손님 요청의 번역 + 원문 + 배지,
넘김 카드 손님 말 번역을 본다. 번역은 가짜 번역기로 정한다. 가짜 번역기는 Papago 자리에 서서
허브가 보낸 글(메뉴명·헷갈리는 재료 낱말을 `[1]` 같은 자리표시자로 바꾼 한국어)을 받는다.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.owner_question import OwnerQuestion
from agenthan.voice.owner.fakes import FakeTranslator
from agenthan.voice.owner.hub import OwnerEvent, OwnerHub

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
WAIT = 2.0
PICKUP = "2026-11-08T18:30:00+09:00"


def _hub(**kw) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW), **kw)


def _order(*lines: OrderLine, call_id: str = "call-1", status: str = "confirmed") -> Order:
    return Order(id=call_id, lines=list(lines), pickup_time=PICKUP, status=status,
                 created_at="2026-11-08T16:05:00+09:00")


class Screen:
    """사장님 화면 하나: 구독해서 받은 이벤트를 모은다.

    기다리다 시간이 지나도 구독을 끊지 않는다(`wait_for`로 `anext`를 취소하면 생성기가 닫힌다).
    """

    QUIET = 0.1

    def __init__(self, hub: OwnerHub) -> None:
        self._stream = hub.events()
        self._next: asyncio.Future[OwnerEvent] | None = None

    async def _get(self, timeout: float) -> OwnerEvent:
        if self._next is None:
            self._next = asyncio.ensure_future(anext(self._stream))
        done, _ = await asyncio.wait({self._next}, timeout=timeout)
        if not done:
            raise TimeoutError
        event, self._next = self._next.result(), None
        return event

    async def take(self, count: int = 1) -> list[OwnerEvent]:
        return [await self._get(WAIT) for _ in range(count)]

    async def one(self) -> OwnerEvent:
        (event,) = await self.take()
        return event

    async def rest(self) -> list[OwnerEvent]:
        """더 오지 않을 때까지(`QUIET`초 조용할 때까지) 받는다."""
        got = []
        while True:
            try:
                got.append(await self._get(self.QUIET))
            except TimeoutError:
                return got

    async def nothing_more(self) -> None:
        with pytest.raises(TimeoutError):
            await self._get(self.QUIET)

    async def close(self) -> None:
        if self._next is not None:
            self._next.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await self._next
        await self._stream.aclose()


def test_menu_names_come_in_the_owner_language_with_korean_beside() -> None:
    order = _order(
        OrderLine("m01", 2, options=("홀케이크 변경",), name="메도빅", unit_price=45_500),
        OrderLine("m03", 1, options=("디카페인 변경", "샷 추가"), name="아메리카노",
                  unit_price=5_500),
        OrderLine("m04", 1, options=("사워크림 빼기",), name="블린", unit_price=9_000),
    )

    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.one()  # snapshot
        hub.submit(order)
        card = (await screen.one()).data
        await screen.close()
        return card

    card = asyncio.run(scenario())

    assert [(x["name_owner"], x["name_ko"]) for x in card["lines"]] == [
        ("Медовик", "메도빅"),
        ("아메리카노", "아메리카노"),  # 원어 이름이 빈 메뉴는 한국어 이름
        ("Блины", "블린"),
    ]
    assert [x["options_owner"] for x in card["lines"]] == [
        ["Целый торт"],
        ["Без кофеина", "Доп. шот эспрессо"],
        ["Без сметаны"],
    ]
    assert [x["options"] for x in card["lines"]][1] == ["디카페인 변경", "샷 추가"]
    assert (card["pickup_label"], card["total_label"]) == ("18:30", "105,500₩")


def test_a_request_shows_first_in_korean_then_again_with_its_translation() -> None:
    """번역은 비동기로 붙는다: 원문 카드가 먼저, 번역이 오면 같은 카드를 다시 보낸다.
    헷갈리는 재료 낱말(고수 = 명인·고수풀)은 번역기에 보내지 않고 사전 값(кинза)으로 넣는다."""
    papago = FakeTranslator({"[1] 빼 주세요": "[1] Уберите, пожалуйста."})
    order = _order(OrderLine("m04", 1, request="고수는 빼 주세요", name="블린", unit_price=9_000))

    async def scenario():
        hub = _hub(translator=papago)
        screen = Screen(hub)
        await screen.one()
        hub.submit(order)
        first, second = await screen.take(2)
        await screen.nothing_more()
        await screen.close()
        return first, second

    first, second = asyncio.run(scenario())

    assert (first.name, second.name) == ("order.update", "order.update")
    assert first.data["lines"][0]["request"] == "고수는 빼 주세요"
    assert first.data["lines"][0]["request_owner"] == ""
    assert second.data["id"] == "call-1"
    assert second.data["lines"][0]["request"] == "고수는 빼 주세요"
    assert second.data["lines"][0]["request_owner"] == "кинза Уберите, пожалуйста."
    assert papago.calls == [(("[1] 빼 주세요",), "ko", "ru")]


def _order_events(translator, order: Order) -> list[OwnerEvent]:
    """주문 하나를 넣고 화면이 받은 이벤트(snapshot 뒤)를 조용해질 때까지 모은다."""

    async def scenario():
        hub = _hub(translator=translator)
        screen = Screen(hub)
        await screen.one()
        hub.submit(order)
        got = await screen.rest()
        await screen.close()
        return got

    return asyncio.run(scenario())


def _request_events(translator, request: str) -> list[tuple[str, str]]:
    """요청사항 하나짜리 주문을 넣고 화면에 간 주문 카드의 (요청 원문, 번역)을 차례로."""
    order = _order(OrderLine("m01", 1, request=request, name="메도빅", unit_price=7_500))
    return [(e.data["lines"][0]["request"], e.data["lines"][0]["request_owner"])
            for e in _order_events(translator, order) if e.name == "order.update"]


def test_a_translation_that_lost_the_menu_name_is_thrown_away() -> None:
    """메뉴명 자리표시자가 사라진 번역은 버린다: 원문 카드만 가고 덮어쓰지 않는다."""
    papago = FakeTranslator({"[1] 따로 포장해 주세요": "Упакуйте отдельно, пожалуйста."})

    assert _request_events(papago, "메도빅은 따로 포장해 주세요") == [
        ("메도빅은 따로 포장해 주세요", ""),
    ]
    assert papago.calls == [(("[1] 따로 포장해 주세요",), "ko", "ru")]


def test_a_menu_name_in_a_request_comes_back_as_the_owner_name() -> None:
    """별칭(꿀케이크)도 메뉴명이다. 번역기는 메뉴명을 보지 않고, 카드에는 원어 이름이 들어간다."""
    papago = FakeTranslator({"[1] 따로 포장해 주세요": "[1] Упакуйте отдельно, пожалуйста."})

    assert _request_events(papago, "꿀케이크는 따로 포장해 주세요")[-1] == (
        "꿀케이크는 따로 포장해 주세요", "Медовик Упакуйте отдельно, пожалуйста.",
    )


def test_a_placeholder_that_appears_twice_is_thrown_away() -> None:
    papago = FakeTranslator({"[1] 따로 포장해 주세요": "[1] [1] Упакуйте отдельно."})

    assert _request_events(papago, "메도빅 따로 포장해 주세요")[-1][1] == ""


@pytest.mark.parametrize(("translated", "kept"), [
    ("Положите 2 открытки.", "Положите 2 открытки."),
    ("Положите две открытки.", "Положите две открытки."),  # Papago가 숫자를 낱말로 풀어도 같은 수
    ("Положите 3 открытки.", ""),  # 숫자가 바뀜 → 버림
    ("Положите открытки.", ""),  # 숫자가 사라짐 → 버림
    ("Положите 2 открытки на 10 минут.", ""),  # 원문에 없는 숫자 → 버림
])
def test_a_translation_whose_numbers_differ_from_the_korean_is_thrown_away(
    translated: str, kept: str
) -> None:
    papago = FakeTranslator({"생일 카드 2장 넣어 주세요": translated})

    assert _request_events(papago, "생일 카드 2장 넣어 주세요")[-1] == (
        "생일 카드 2장 넣어 주세요", kept,
    )


def test_when_translation_fails_the_card_stays_in_korean() -> None:
    assert _request_events(FakeTranslator(fail=True), "덜 달게 해 주세요") == [
        ("덜 달게 해 주세요", ""),
    ]


def test_without_papago_keys_the_card_is_korean_only() -> None:
    assert _request_events(None, "덜 달게 해 주세요") == [("덜 달게 해 주세요", "")]


# 배지는 한국어 원문만 보고 붙인다(번역 결과와 상관없다). remove = "빼기", allergy = "알레르기"
BADGE_TABLE = [
    ("고수 빼 주세요", ["remove"]),
    ("고수는 빼고 주세요", ["remove"]),
    ("고수빼주세요", ["remove"]),
    ("얼음 없이 주세요", ["remove"]),
    ("아이스 말고 따뜻하게요", ["remove"]),
    ("양파 넣지 말아 주세요", ["remove"]),
    ("고수 안 넣어 주세요", ["remove"]),
    ("포크는 필요 없어요", ["remove"]),
    ("땅콩 빼 주세요", ["remove", "allergy"]),  # 알레르기 성분 + 빼기
    ("견과류 알레르기 있어요", ["allergy"]),
    ("우유 알러지가 있어요", ["allergy"]),
    ("계란 못 먹어요", ["allergy"]),
    ("아이가 밀가루 먹으면 안 돼요", ["allergy"]),
    ("덜 달게 해 주세요", []),  # "덜"은 빼기가 아니다
    ("초 2개 넣어 주세요", []),
    ("우유 많이 넣어 주세요", []),  # 성분 이름만으로는 알레르기 배지를 안 붙인다
    ("따로 포장해 주세요", []),
    ("", []),
]


@pytest.mark.parametrize(("request_ko", "badges"), BADGE_TABLE)
def test_badges_come_from_the_korean_request_only(request_ko: str, badges: list[str]) -> None:
    # 번역이 부정을 놓쳐도("고수 넣어 주세요") 배지는 원문대로다
    papago = FakeTranslator(fail=False, table={})
    order = _order(OrderLine("m04", 1, request=request_ko, name="블린", unit_price=9_000))

    events = _order_events(papago, order)

    assert {tuple(e.data["lines"][0]["badges"]) for e in events} == {tuple(badges)}


def test_the_handoff_card_translates_the_last_three_customer_lines() -> None:
    papago = FakeTranslator({
        "[1] 하나요": "[1] одну.",
        "아니 그게 아니라": "Нет, не то.",
        "사장님이랑 통화할래요": "Я хочу поговорить с хозяином.",
    })

    async def scenario():
        hub = _hub(translator=papago)
        screen = Screen(hub)
        await screen.one()
        hub.call_started("call-1")
        for said in ("여보세요", "메도빅 하나요", "아니 그게 아니라", "사장님이랑 통화할래요"):
            hub.customer_said("call-1", said)
        hub.submit(_order(status="handoff"))
        got = await screen.rest()
        await screen.close()
        return got

    events = asyncio.run(scenario())

    handoff = [e.data for e in events
               if e.name == "call.update" and e.data["status"] == "handoff"]
    assert {tuple(c["heard"]) for c in handoff} == {
        ("메도빅 하나요", "아니 그게 아니라", "사장님이랑 통화할래요")}
    assert handoff[0]["heard_owner"] == ["", "", ""]  # 원문 카드가 먼저
    assert handoff[-1]["heard_owner"] == [
        "Медовик одну.", "Нет, не то.", "Я хочу поговорить с хозяином.",
    ]
    # 손님 말은 들을 때(자막, 티켓 10) 한 줄씩 번역을 맡긴다. 넘김 카드는 같은 캐시를 쓴다
    sent = [texts for texts, _, _ in papago.calls]
    assert sorted(sent) == sorted(
        [("여보세요",), ("[1] 하나요",), ("아니 그게 아니라",), ("사장님이랑 통화할래요",)])


def test_the_same_sentence_is_translated_once_per_call() -> None:
    """통화 중 카드에서 번역한 요청은 확정 카드에서 다시 보내지 않는다(통화 동안 캐시). 번역이
    통화 중에 늦게 와도 그사이 생긴 주문 카드에 붙는다."""
    gate = asyncio.Event
    line = OrderLine("m04", 1, request="고수는 빼 주세요", name="블린", unit_price=9_000)
    papago_table = {"[1] 빼 주세요": "[1] Уберите, пожалуйста."}

    async def scenario():
        papago = FakeTranslator(papago_table, gate=gate())
        hub = _hub(translator=papago)
        screen = Screen(hub)
        await screen.one()
        hub.call_started("call-1")
        hub.cart_changed("call-1", [line])
        hub.cart_changed("call-1", [line, OrderLine("m01", 1, name="메도빅", unit_price=7_500)])
        hub.submit(_order(line))  # 번역이 오기 전에 확정
        before = await screen.rest()
        papago.gate.set()
        after = await screen.rest()
        await screen.close()
        return papago, before, after

    papago, before, after = asyncio.run(scenario())

    assert [(e.name, e.data["status"]) for e in before] == [
        ("call.update", "live"), ("call.update", "live"), ("call.update", "live"),
        ("order.update", "new"), ("call.update", "ordered"),
    ]
    assert [(e.name, e.data["lines"][0]["request_owner"]) for e in after] == [
        ("order.update", "кинза Уберите, пожалуйста."),
    ]
    assert len(papago.calls) == 1


def test_a_live_call_card_gets_the_translation_too() -> None:
    line = OrderLine("m04", 1, request="고수는 빼 주세요", name="블린", unit_price=9_000)
    papago = FakeTranslator({"[1] 빼 주세요": "[1] Уберите, пожалуйста."})

    async def scenario():
        hub = _hub(translator=papago)
        screen = Screen(hub)
        await screen.one()
        hub.call_started("call-1")
        hub.cart_changed("call-1", [line])
        got = await screen.rest()
        hub.cart_changed("call-1", [line])  # 번역이 붙은 뒤에도 안 바뀐 장바구니는 다시 안 보낸다
        await screen.nothing_more()
        await screen.close()
        return got

    events = asyncio.run(scenario())

    assert [(e.name, e.data["lines"][0]["request_owner"]) for e in events[1:]] == [
        ("call.update", ""), ("call.update", "кинза Уберите, пожалуйста."),
    ]


def test_a_question_card_shows_the_owner_menu_name_and_then_the_translated_question() -> None:
    """질문 카드(티켓 05)도 원어 메뉴 이름이 붙고, 손님 질문 번역이 오면 같은 카드를 다시 보낸다."""
    papago = FakeTranslator({"[1]에 견과류 들어가요?": "В [1] идут орехи?"})
    nuts = OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅",
                         text="메도빅에 견과류 들어가요?")

    async def scenario():
        hub = _hub(translator=papago)
        screen = Screen(hub)
        await screen.one()
        hub.call_started("call-1")
        hub.open_question("call-1", nuts, lambda reply: None)
        got = await screen.rest()
        await screen.close()
        return got

    events = [e for e in asyncio.run(scenario()) if e.name.startswith("owner.question")]

    assert [(e.name, e.data["menu_name"], e.data["menu_name_owner"], e.data["text"],
             e.data["text_owner"]) for e in events] == [
        ("owner.question", "메도빅", "Медовик", "메도빅에 견과류 들어가요?", ""),
        ("owner.question.update", "메도빅", "Медовик", "메도빅에 견과류 들어가요?",
         "В Медовик идут орехи?"),
    ]
    assert events[1].data["status"] == "open"
