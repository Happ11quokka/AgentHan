"""`live` 마커: 실제 CLOVA를 부르는 테스트(키·과금). `-m live`로 고를 때만 돈다."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: 실제 CLOVA 호출(키 필요). `-m live`일 때만 돈다")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if "live" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="실제 CLOVA 호출. `-m live`로 켠다")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
