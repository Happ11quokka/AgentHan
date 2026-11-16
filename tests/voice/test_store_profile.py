"""가게 데이터만 바꾸면 돌아가게 (5단계 티켓 05): 전화 주문 설정은 가게 JSON의 `phone` 묶음."""

from __future__ import annotations

from datetime import time
from pathlib import Path

from agenthan.core.contracts import MenuOption
from agenthan.voice.store_config import RequiredOption, StoreConfig

PHO = Path(__file__).parents[2] / "fixtures" / "phone" / "sample_pho_store.json"


def test_sample_store_reads_its_required_temperature_from_the_store_file() -> None:
    config = StoreConfig.from_fixture()
    assert config.required_options["m03"] == (
        RequiredOption("온도", (MenuOption("따뜻하게"), MenuOption("차갑게"))),
    )
    assert (config.prep_minutes, config.open_time, config.close_time) == (15, time(10), time(21))


def test_another_store_brings_its_own_hours_prep_and_required_choices() -> None:
    config = StoreConfig.from_fixture(PHO)
    assert config.store_name == "샘플 쌀국수집"
    assert (config.prep_minutes, config.open_time, config.close_time) == (20, time(11), time(22))
    (size,) = config.required_options["p01"]
    assert size.name == "양" and size.default == "보통"
    assert size.choices == (MenuOption("보통"), MenuOption("곱빼기", extra_price=2000))
    assert config.required_options["p04"][0].choices[1].name == "차갑게"
    assert "m03" not in config.required_options


def test_a_store_without_a_phone_block_asks_no_required_choices(tmp_path) -> None:
    path = tmp_path / "store.json"
    path.write_text('{"store": {"name": "빈 가게"}, "menu": []}', encoding="utf-8")
    config = StoreConfig.from_fixture(path)
    assert config.required_options == {}
    assert config.store_name == "빈 가게"
