import importlib

import pytest


@pytest.mark.parametrize("module", ["agent", "rag", "tools", "voice"])
def test_module_imports(module: str) -> None:
    importlib.import_module(f"agenthan.{module}")
