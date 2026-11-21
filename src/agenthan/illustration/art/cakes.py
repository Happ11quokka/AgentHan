"""케이크·디저트 그림(티켓 02): 조각 모양 × 층 색 배열 × 위 장식을 조합한다.

style.py의 도형(cake_slice, cylinder, plate …)을 조합하고 ILLUSTRATIONS에 Illustration을
올리면 build가 SVG·카탈로그(catalog/cakes.json)·갤러리에 넣는다.
"""

from agenthan.illustration.art.style import Illustration

FAMILY = "cakes"

ILLUSTRATIONS: list[Illustration] = []
