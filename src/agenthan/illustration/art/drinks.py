"""음료 그림(티켓 03): 잔 모양 × 음료 색 × 고명을 조합한다.

style.py의 도형(cylinder, citrus_wheel, plate …)을 조합하고 ILLUSTRATIONS에 Illustration을
올리면 build가 SVG·카탈로그(catalog/drinks.json)·갤러리에 넣는다.
"""

from agenthan.illustration.art.style import Illustration

FAMILY = "drinks"

ILLUSTRATIONS: list[Illustration] = []
