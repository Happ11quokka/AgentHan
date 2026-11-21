"""러시아 요리·과자 그림(티켓 04): 블린·펠메니·보르시처럼 그림마다 함수 하나로 그린다.

style.py의 도형을 조합하고 ILLUSTRATIONS에 Illustration을 올리면 build가
SVG·카탈로그(catalog/dishes.json)·갤러리에 넣는다.
"""

from agenthan.illustration.art.style import Illustration

FAMILY = "dishes"

ILLUSTRATIONS: list[Illustration] = []
