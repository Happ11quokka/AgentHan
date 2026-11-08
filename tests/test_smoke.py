import importlib

import pytest


@pytest.mark.parametrize("module", ["core", "agent", "tools", "rag", "voice", "app"])
def test_module_imports(module: str) -> None:
    importlib.import_module(f"agenthan.{module}")
