import pytest


@pytest.fixture(autouse=True)
def _legacy_unless_asked(monkeypatch):
    """The product default is planner (ADR-0005). Tests written for the keyword router pin
    legacy here; planner and shadow tests pass agent_mode or set AGENT_MODE themselves."""
    monkeypatch.setenv("AGENT_MODE", "legacy")
