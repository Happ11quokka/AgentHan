def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "live_clova: 실제 CLOVA를 부르는 측정 (키 필요). AGENTHAN_LIVE_CLOVA=1 일 때만 돈다",
    )
