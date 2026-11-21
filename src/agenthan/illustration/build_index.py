"""대표 그림 임베딩 인덱스를 다시 만드는 명령(티켓 06). 그림을 더하거나 이름·별칭을 고친 뒤,
가족별 그림 브랜치를 병합한 뒤에 돌린다. 글이 바뀐 그림만 CLOVA로 임베딩하고 그 id를 출력한다.

    uv run --env-file .env python -m agenthan.illustration.build_index

새로 임베딩할 그림이 없으면 키 없이도 돈다(인덱스가 카탈로그와 맞는지 확인용).
"""

import sys

from agenthan.core.embedding import ClovaEmbeddingError
from agenthan.illustration import embedding_index
from agenthan.illustration.library import load_catalog


def main() -> int:
    total = len(load_catalog())
    try:
        embedded = embedding_index.build_index()
    except (ClovaEmbeddingError, OSError) as error:
        # ClovaEmbeddingError 문구는 키·응답 본문을 담지 않는다(core.embedding).
        print(f"임베딩 실패: {error}. 그때까지 만든 벡터는 저장했어요.", file=sys.stderr)
        return 1
    reused = total - len(embedded)
    print(f"그림 {total}개 중 새로 임베딩 {len(embedded)}개, 저장된 벡터 재사용 {reused}개")
    for key in embedded:
        print(f"  임베딩: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
