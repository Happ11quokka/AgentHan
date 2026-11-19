"""메뉴 임베딩 캐시를 미리 만든다 (데모 전에 한 번). 내용이 안 바뀐 메뉴는 다시 부르지 않는다.

    uv run python scripts/build_embeddings.py                                  # 더미
    uv run python scripts/build_embeddings.py --data data/clean fixtures/dummy_store

캐시: data/cache/<폴더 이름>_embeddings.json (data/는 커밋 안 됨). 키는 .env의 CLOVASTUDIO_API_KEY.
"""

from __future__ import annotations

import argparse
import sys

from agenthan.rag import RagStoreKnowledge
from agenthan.rag.env import load_env_file
from agenthan.rag.repository import resolve_data_dir
from agenthan.rag.vector import default_cache_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", nargs="*", default=[None], help="데이터 폴더 (기본: 더미)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_env_file()
    code = 0
    for d in args.data:
        data_dir = resolve_data_dir(d)
        kb = RagStoreKnowledge.from_dir(data_dir)
        if not kb.vector.enabled:
            print("CLOVASTUDIO_API_KEY가 없어 벡터 캐시를 만들 수 없습니다 (.env 확인)")
            return 2
        try:
            calls = kb.vector.warm()
        except Exception as error:  # 오류 내용에 키가 섞이지 않게 종류만
            print(f"{data_dir.name}: 실패 ({type(error).__name__})")
            code = 1
            continue
        print(
            f"{data_dir.name}: 메뉴 {len(kb.records)}개, 이번 호출 {calls}번 "
            f"(나머지는 캐시) → {default_cache_path(data_dir)}"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
