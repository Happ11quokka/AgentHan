"""계약(`StoreKnowledge`)에 없는 가게 정보 (spec D13).

준비 시간, 영업시간, 필수 옵션 표, 데모 발신번호. 가게 이름은 fixture의 `store.name`에서 읽는다.
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


_DEFAULT_REQUIRED: Mapping[str, tuple[RequiredOption, ...]] = MappingProxyType(
    {"m03": (RequiredOption("온도", (MenuOption("따뜻하게"), MenuOption("차갑게"))),)}
)


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
        default_factory=lambda: _DEFAULT_REQUIRED
    )
    demo_phone: str = "010-0000-0000"  # 전화번호는 묻지 않고 미리 채운다 (spec §2)
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
        return cls(store_name=raw["store"]["name"])
