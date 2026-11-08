"""voice/는 agenthan.core.* 외의 파트 모듈을 import하지 않는다 (PRD §7)."""

import ast
from pathlib import Path

import agenthan.voice

VOICE_DIR = Path(agenthan.voice.__file__).parent


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_voice_imports_only_core_from_other_parts() -> None:
    bad = [
        f"{path.relative_to(VOICE_DIR)}: {name}"
        for path in VOICE_DIR.rglob("*.py")
        for name in _imported_modules(path)
        if name.startswith("agenthan.")
        and not name.startswith(("agenthan.core", "agenthan.voice"))
    ]
    assert bad == []


def test_call_session_knows_no_web_or_vendor_library() -> None:
    """통화 세션(`voice/call/`)은 FastAPI·gRPC·httpx를 모른다 (spec 모듈).

    바깥과는 포트로만 만난다.
    """
    banned = ("fastapi", "starlette", "grpc", "httpx", "uvicorn")
    bad = [
        f"{path.relative_to(VOICE_DIR)}: {name}"
        for path in (VOICE_DIR / "call").rglob("*.py")
        for name in _imported_modules(path)
        if name.split(".")[0] in banned
    ]
    assert bad == []
