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
    pilot._knowledge_folder = None
