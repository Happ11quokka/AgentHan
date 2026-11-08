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

from agenthan.core.fakes import SAMPLE_STORE

KST = timezone(timedelta(hours=9), "KST")


@dataclass(frozen=True)
class RequiredOption:
    """손님이 반드시 골라야 하는 옵션. 예: 아메리카노 온도."""

    name: str  # "온도"
    choices: tuple[str, ...]  # ("따뜻하게", "차갑게")


_DEFAULT_REQUIRED: Mapping[str, tuple[RequiredOption, ...]] = MappingProxyType(
    {"m03": (RequiredOption("온도", ("따뜻하게", "차갑게")),)}
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
    # 메뉴 id → 필수 옵션들. 엔진 검사는 티켓 03.
    required_options: Mapping[str, tuple[RequiredOption, ...]] = field(
        default_factory=lambda: _DEFAULT_REQUIRED
    )
    demo_phone: str = "010-0000-0000"  # 전화번호는 묻지 않고 미리 채운다 (spec §2)
    tz: timezone = KST

    @classmethod
    def from_fixture(cls, path: Path = SAMPLE_STORE) -> StoreConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(store_name=raw["store"]["name"])
