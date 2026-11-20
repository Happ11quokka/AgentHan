"""스키마 올리기: migrations/NNN_*.sql을 번호 순서로, 아직 안 한 것만 한 번씩.

uv run --env-file .env python -m agenthan.db.migrate
"""

from __future__ import annotations

import sys
from pathlib import Path

from agenthan.db.conn import connect

MIGRATIONS = Path(__file__).with_name("migrations")


def pending(conn) -> list[Path]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    return [p for p in sorted(MIGRATIONS.glob("*.sql")) if p.stem not in done]


def migrate(conn) -> list[str]:
    """→ 이번에 올린 버전 이름들. 파일 하나 = 트랜잭션 하나(실패하면 그 파일은 통째로 취소)."""
    applied = []
    for path in pending(conn):
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        applied.append(path.stem)
    return applied


def main() -> int:
    with connect() as conn:
        applied = migrate(conn)
        version = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    print("올린 스키마:", ", ".join(applied) if applied else "없음(최신)")
    print("pgvector:", version[0] if version else "없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
