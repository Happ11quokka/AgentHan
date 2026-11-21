"""가게 스냅숏과 공급자 (전화 메뉴 티켓 01). 통화 하나 = 스냅숏 하나.

    stores = fixed_store(StoreSnapshot.of(store, config))   # 지금은 고정 가게
    phone_router(stores=stores, ...)                         # 통화마다 시작할 때 한 번 부른다
    OwnerHub(stores=stores, ...)                             # 통화 밖은 읽을 때마다 부른다

- 스냅숏은 가게 지식(`StoreKnowledge`: 메뉴·검색·리뷰)과 전화 설정(`StoreConfig`), 그리고 거기서
  만든 것(STT 부스팅 단어)을 한데 묶는다. 바꾸지 않는다. 통화는 시작할 때 받은 스냅숏을 끝까지
  쓴다(메뉴 매칭·NLU 메뉴 목록·검색·가게 정보 답변). 가게가 바뀌어도 진행 중인 통화는 그대로다
- 공급자(`StoreProvider`)는 "지금의 가게 스냅숏"을 돌려주는 함수다. 이벤트 루프에서 부르므로
  막지 않아야 한다. 무거운 일(메뉴 읽기, 검색 색인)은 스냅숏을 만들 때(`of`, 게시 때) 하고
  공급자는 만들어 둔 것을 돌려주기만 한다
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agenthan.core.contracts import StoreKnowledge
from agenthan.voice.dialog.coupons import Coupon
from agenthan.voice.store_config import StoreConfig
from agenthan.voice.stt.boost import menu_boost_words


@dataclass(frozen=True, eq=False)
class StoreSnapshot:
    """통화가 쓰는 가게 한 벌. `of`로 만든다."""

    store: StoreKnowledge
    config: StoreConfig
    boost_words: tuple[str, ...] = ()  # CLOVA 키워드 부스팅 단어 (`stt.boost`)
    # 가게 쿠폰(저장한 순서, 쿠폰 티켓 09). 게시된 전화용 가게에서 온다. 고정 가게는 없음
    coupons: tuple[Coupon, ...] = ()

    @classmethod
    def of(cls, store: StoreKnowledge, config: StoreConfig,
           coupons: tuple[Coupon, ...] = ()) -> StoreSnapshot:
        """메뉴를 한 번 읽어 파생 값을 만든다. 동기라 이벤트 루프 밖(시작·게시 때)에서 부른다."""
        return cls(store=store, config=config, boost_words=menu_boost_words(store.list_menu()),
                   coupons=coupons)


StoreProvider = Callable[[], StoreSnapshot]


def fixed_store(snapshot: StoreSnapshot) -> StoreProvider:
    """늘 같은 스냅숏을 돌려주는 공급자 (개발 서버·시험·비서 메뉴가 없을 때)."""
    return lambda: snapshot
