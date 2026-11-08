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


def test_only_dev_entry_points_import_core_fakes_at_module_level() -> None:
    """1단계 리뷰 S6: 운영 코드가 가짜 가게를 기본값으로 끌고 오지 않는다.

    가게 지식·설정은 라우터를 만들 때 넣는다. `StoreConfig.from_fixture()`만 함수 안에서 쓴다.
    개발용 진입점(텍스트 CLI, 개발 서버)은 더미 가게로 띄우므로 예외다.
    """
    dev_entry_points = {"cli.py", "server.py"}
    bad = []
    for path in VOICE_DIR.rglob("*.py"):
        if str(path.relative_to(VOICE_DIR)) in dev_entry_points:
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ImportFrom) and node.module == "agenthan.core.fakes":
                bad.append(str(path.relative_to(VOICE_DIR)))
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
