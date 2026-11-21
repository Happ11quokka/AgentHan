import pytest


@pytest.fixture(autouse=True)
def _legacy_unless_asked(monkeypatch):
    """The product default is planner (ADR-0005). Tests written for the keyword router pin
    legacy here; planner and shadow tests pass agent_mode or set AGENT_MODE themselves."""
    monkeypatch.setenv("AGENT_MODE", "legacy")


@pytest.fixture(autouse=True)
def _no_menu_index_leak():
    """pilot.assistant plugs the store knowledge RAG into the tools (ADR-0008). Unplug it
    after each test so other tests keep exact menu matching."""
    yield
    from agenthan.app import pilot
    from agenthan.tools import menu_index

    menu_index.configure(None)
    pilot._knowledge_on = False


@pytest.fixture(autouse=True)
def _no_database_unless_asked(monkeypatch):
    """Tests never touch a real database by accident: AGENTHAN_DB_URL is cleared and the DB
    circuit breaker reset. tests/test_db.py sets its own URL from AGENTHAN_TEST_DB_URL."""
    monkeypatch.delenv("AGENTHAN_DB_URL", raising=False)
    from agenthan.db.conn import reset_pause

    reset_pause()
