"""[Part B 가게 지식] 정제·인덱싱과 사장님 RAG. core.contracts.StoreKnowledge를 구현한다.

쓰는 법:
    from agenthan.rag import LiveShopKnowledge, RagStoreKnowledge
    kb = RagStoreKnowledge.from_dir()      # AGENTHAN_DATA_DIR(없으면 fixtures/dummy_store)
    kb = RagStoreKnowledge.from_fixture()  # 테스트·데모: 더미, 벡터 끔
    kb = RagStoreKnowledge.from_db("sample")  # PostgreSQL 가게, 벡터는 pgvector (docs/db.md)
    kb = LiveShopKnowledge(store.read)     # 비서 가게 파일(DummyStore), 바뀌면 다시 색인
"""

from agenthan.rag.knowledge import RagStoreKnowledge
from agenthan.rag.shop import LiveShopKnowledge

__all__ = ["LiveShopKnowledge", "RagStoreKnowledge"]
