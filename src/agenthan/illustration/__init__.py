"""메뉴 대표 그림: 사진이 없는 메뉴 자리에 보여 줄 미리 만든 SVG 일러스트(spec: menu-illustration).

- library: 카탈로그(catalog/*.json)와 화면 주소(/illustrations/<id>.svg)
- matcher: 메뉴 이름(+분류) → 그림 id
- art: 그림·카탈로그·갤러리 생성기(빌드 때만). uv run python -m agenthan.illustration.art
"""

from agenthan.illustration.library import illustration_url
from agenthan.illustration.matcher import assign_missing, pick_illustration

__all__ = ["assign_missing", "illustration_url", "pick_illustration"]
