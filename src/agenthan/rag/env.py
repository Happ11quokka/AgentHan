"""레포 루트의 .env를 읽어 환경변수에 넣는다 (이미 있는 값은 덮어쓰지 않는다).

스크립트(eval_rag.py, build_embeddings.py)에서만 부른다. 라이브러리 코드는 os.environ만 본다
(테스트가 실수로 CLOVA를 부르지 않게). 값은 절대 출력하지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path

from agenthan.rag.repository import REPO_ROOT


def load_env_file(path: Path | None = None) -> list[str]:
    """→ 새로 넣은 변수 이름 목록(값은 돌려주지 않는다)."""
    f = path or REPO_ROOT / ".env"
    if not f.exists():
        return []
    added = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and value and not os.environ.get(name):
            os.environ[name] = value
            added.append(name)
    return added
