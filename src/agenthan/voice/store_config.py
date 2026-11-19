"""계약(`StoreKnowledge`)에 없는 가게 정보 (spec D13).

준비 시간, 영업시간, 필수 옵션 표, 데모 발신번호. 가게 이름은 fixture의 `store.name`에서 읽는다.

가게마다 다른 값은 코드가 아니라 가게 JSON의 `phone` 묶음에 둔다(5단계 티켓 05). 모두 생략 가능.

    "phone": {
      "prep_minutes": 15, "open": "10:00", "close": "21:00",
      "max_reasks": 5, "confirm_qty_from": 10,
      "required_options": {
        "<메뉴 id>": [{"name": "온도", "default": "",
                       "choices": ["따뜻하게", {"name": "차갑게", "extra_price": 0}]}]
      }
    }

`required_options`는 손님이 꼭 하나 골라야 하는 것(음료 온도, 국수 양 등). `default`가 있으면
묻지 않고 그 값으로 담는다. 없는 메뉴는 묻지 않는다.

6단계 품질: `max_reasks`는 통화 전체에서 못 알아듣거나 되물은 횟수가 이만큼이면 사장님께 넘기는
값(0이면 끔), `confirm_qty_from`은 한 줄 수량이 이 값 이상이면 "○○ 12개 맞으세요?"로 따로
확인하는 값(0이면 끔).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from agenthan.core.contracts import MenuOption

KST = timezone(timedelta(hours=9), "KST")


@dataclass(frozen=True)
class RequiredOption:
    """꼭 하나 골라야 하는 선택. 예: 아메리카노 온도(따뜻하게/차갑게).

    계약 v1 제안 4의 `RequiredOption`과 같은 모양이다. v1이 들어오면
    `MenuItem.required_options`에서 읽고 이 표는 지운다.
    """

    name: str  # 묻는 항목. 예: "온도"
    choices: tuple[MenuOption, ...]  # 고를 값. 추가 금액은 MenuOption.extra_price
    default: str = ""  # 손님이 안 골랐을 때 쓸 값. ""이면 꼭 묻는다

    def choice(self, name: str) -> MenuOption | None:
        return next((c for c in self.choices if c.name == name), None)


_NO_REQUIRED: Mapping[str, tuple[RequiredOption, ...]] = MappingProxyType({})


@dataclass(frozen=True)
class StoreConfig:
    store_name: str
    prep_minutes: int = 15
    open_time: time = time(10, 0)
    close_time: time = time(21, 0)
    # 픽업 규칙 (티켓 04). 이보다 멀면 "오후 2시에 오시는 거죠?"로 확인한다.
    confirm_after_minutes: int = 180
    pickup_step_minutes: int = 5  # 제안 시각을 이 단위로 올린다. 17:10 + 15분 → 17:25
    # 메뉴 id → 필수 옵션들. 엔진이 비어 있는 것을 하나씩 묻는다.
    required_options: Mapping[str, tuple[RequiredOption, ...]] = field(
        default_factory=lambda: _NO_REQUIRED
    )
    # 6단계 품질. 통화 전체의 못 알아들음·되묻기가 이만큼이면 사장님께 넘긴다(같은 슬롯 3번과
    # 따로 센다). 0이면 끔. 조사 노트의 예는 4지만 말했는데 글 없음(`not_heard`)도 세므로 5로 둔다
    max_reasks: int = 5
    # 한 줄 수량이 이 값 이상이면 바로 담지 않고 따로 확인한다(장난·오인식). 0이면 끔
    confirm_qty_from: int = 10
    demo_phone: str = "010-0000-0000"  # 전화번호는 묻지 않고 미리 채운다 (spec §2)
    # 주문 가드 (6단계): 한 통화의 총 수량·총액 상한. 넘는 주문은 담지 않고 알린다. 0이면 제한 없음.
    # 기본은 넉넉하다(한 줄 상한 99개가 그대로 담긴다). 가게 JSON `phone.max_order_qty`·
    # `max_order_total`이나 서버 설정 `PHONE_MAX_ORDER_QTY`·`PHONE_MAX_ORDER_TOTAL`로 줄인다
    max_order_qty: int = 100
    max_order_total: int = 1_000_000
    tz: timezone = KST

    @classmethod
    def from_fixture(cls, path: Path | None = None) -> StoreConfig:
        """더미 가게 JSON의 `store.name`으로 만든다. 경로가 없으면 `core.fakes`의 샘플 가게.

        개발·테스트용이다. 운영에서는 만드는 쪽이 `StoreConfig(store_name=...)`를 넣는다
        (1단계 리뷰 S6). 그래서 샘플 경로는 여기서만 늦게 import한다.
        """
        if path is None:
            from agenthan.core.fakes import SAMPLE_STORE

            path = SAMPLE_STORE
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_store_json(raw)

    @classmethod
    def from_store_json(cls, raw: Mapping[str, object]) -> StoreConfig:
        """가게 JSON(`store`·`phone` 묶음) → 설정. `phone`에 없는 값은 기본값."""
        store = raw["store"]
        phone = raw.get("phone") or {}
        if not isinstance(store, Mapping) or not isinstance(phone, Mapping):
            raise ValueError("가게 JSON의 store·phone은 객체여야 해요")
        values: dict[str, object] = {"store_name": store["name"]}
        if "prep_minutes" in phone:
            values["prep_minutes"] = int(phone["prep_minutes"])
        if "open" in phone:
            values["open_time"] = time.fromisoformat(str(phone["open"]))
        if "close" in phone:
            values["close_time"] = time.fromisoformat(str(phone["close"]))
        for name in ("max_order_qty", "max_order_total"):
            if name in phone:
                values[name] = max(0, int(phone[name]))
        for name in ("max_reasks", "confirm_qty_from"):
            if name in phone:
                number = int(phone[name])
                if number < 0:
                    raise ValueError(f"가게 JSON phone.{name}은 0 이상이어야 해요")
                values[name] = number
        if "required_options" in phone:
            values["required_options"] = MappingProxyType({
                menu_id: tuple(_required(group) for group in groups)
                for menu_id, groups in dict(phone["required_options"]).items()
            })
        return cls(**values)  # type: ignore[arg-type]


def _required(group: Mapping[str, object]) -> RequiredOption:
    choices = tuple(_choice(c) for c in group["choices"])  # type: ignore[union-attr]
    return RequiredOption(str(group["name"]), choices, str(group.get("default", "")))


def _choice(raw: object) -> MenuOption:
    if isinstance(raw, str):
        return MenuOption(raw)
    if isinstance(raw, Mapping):
        return MenuOption(str(raw["name"]), extra_price=int(raw.get("extra_price", 0)))
    raise ValueError(f"꼭 고를 것의 값은 이름이나 {{name, extra_price}}여야 해요: {raw!r}")
