"""데이터베이스 (PostgreSQL + pgvector). 가게 지식·벡터·전화 주문·비서 가게 파일을 담는다.

- 켜기: 환경변수 `AGENTHAN_DB_URL` (예: postgresql://agenthan:...@db:5432/agenthan).
  비어 있으면 DB를 쓰지 않고 지금처럼 JSON 파일로만 동작한다(테스트·로컬 기본).
- 연결이 안 되면 경고만 남기고 JSON으로 돌아간다(`conn.available()`), 잠깐 쉬었다 다시 시도한다.
- 스키마: `migrations/*.sql`을 번호 순서로 한 번씩 (`python -m agenthan.db.migrate`).
- 데이터 넣기: `python -m agenthan.db.seed` (레포 더미 전부 + data/clean 실제 데이터가 있으면 같이).
- 벡터 만들기: `python -m agenthan.db.embed` (CLOVA 임베딩 v2, 캐시가 있으면 다시 부르지 않음).

voice(전화)는 core 말고 다른 파트를 import하지 않는 규칙이라, DB를 전화에 붙이는 일은
`agenthan.serve`가 한다.
"""

from agenthan.db.conn import DB_URL_ENV, STORE_ID_ENV, available, connect, db_url, enabled

__all__ = ["DB_URL_ENV", "STORE_ID_ENV", "available", "connect", "db_url", "enabled"]
