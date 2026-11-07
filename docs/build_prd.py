"""docs/prd.html 을 만든다.

prd.src.html(본문) + prd.css(양식) + diagrams/*.svg(도식)를 한 파일로 합친다.
도식을 draw.io에서 고쳤으면 같은 이름의 .svg로 다시 내보낸 뒤 이 스크립트를 돌린다.

    uv run python docs/build_prd.py
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent
PRINT_BAR = (
    '<div class="print-bar"><button type="button" onclick="window.print()">PDF로 저장</button>'
    "<small>인쇄 창에서 「PDF로 저장」 · 배경 그래픽 켬</small></div>"
)
NOTE = "<!-- 생성 파일: docs/build_prd.py. prd.src.html · prd.css · diagrams/*.svg 를 고친다 -->"
FONT = "Pretendard Variable, Pretendard, Apple SD Gothic Neo, Noto Sans KR, sans-serif"


def inline_svg(name: str) -> str:
    svg = (DOCS / "diagrams" / f"{name}.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<\?xml[^>]*>\s*|<!DOCTYPE[^>]*>\s*", "", svg)
    m = re.search(r'<svg[^>]*?width="([\d.]+)px"[^>]*?height="([\d.]+)px"', svg)
    w = m.group(1) if m else "900"
    svg = re.sub(r'(<svg[^>]*?)\swidth="[\d.]+px"', r"\1", svg, count=1)
    svg = re.sub(r'(<svg[^>]*?)\sheight="[\d.]+px"', r"\1", svg, count=1)
    svg = svg.replace("color-scheme: light dark;", "color-scheme: light;", 1)
    svg = svg.replace("<svg ", f'<svg role="img" aria-label="{name}" data-w="{w}" ', 1)
    # 설치 안 된 PC에서도 한글이 비슷한 폭으로 나오게 대체 글꼴을 붙인다
    return svg.replace("font-family: Pretendard;", f"font-family: {FONT};")


def main() -> None:
    body = (DOCS / "prd.src.html").read_text(encoding="utf-8")
    body = re.sub(r"\{\{svg:([\w-]+)\}\}", lambda m: inline_svg(m.group(1)), body)
    css = (DOCS / "prd.css").read_text(encoding="utf-8")
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>AgentHan PRD</title>
<meta name="doc-id" content="AGH-PRD">
<meta name="version" content="v0.1">
{NOTE}
<style>
{css}
</style>
</head>
<body class="compact-doc">
{PRINT_BAR}
<article class="sheet">
{body}
</article>
</body>
</html>
"""
    out = DOCS / "prd.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
