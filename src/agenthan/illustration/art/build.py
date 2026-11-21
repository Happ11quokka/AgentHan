"""대표 그림 전체를 다시 만든다: SVG, 가족별 카탈로그 JSON, 검수용 갤러리 HTML.

    uv run python -m agenthan.illustration.art

- SVG: web/public/illustrations/<id>.svg (웹 정적 파일, 화면 주소 /illustrations/<id>.svg)
- 카탈로그: src/agenthan/illustration/catalog/<가족>.json (백엔드 매처가 읽는다)
- 갤러리: build/illustrations/gallery.html (팀 검수용, git에 올리지 않는다)

그림 가족(defaults·cakes·bakery·drinks·dishes)마다 파일 하나와 ILLUSTRATIONS 목록 하나를 둔다.
가족끼리 같은 줄을 고치지 않도록 카탈로그도 가족마다 따로 쓴다.
"""

import html
import json
import urllib.parse
from pathlib import Path

from agenthan.illustration import library, matcher
from agenthan.illustration.art import bakery, cakes, defaults, dishes, drinks
from agenthan.illustration.art.style import Illustration

FAMILIES = (defaults, cakes, bakery, drinks, dishes)
ROOT = Path(__file__).resolve().parents[4]
SVG_DIR = ROOT / "web" / "public" / "illustrations"
GALLERY_PATH = ROOT / "build" / "illustrations" / "gallery.html"
MAX_SVG_BYTES = 8 * 1024  # 파일 하나 수 KB 이내(spec)

# 갤러리의 "데모 가게 메뉴 이름 → 고른 그림" 표가 읽는 메뉴 벌. 실제 가게 정제 메뉴는 git에
# 없어(메인 저장소 로컬에만 있음) 있을 때만 읽는다.
DEMO_MENUS = (
    ("시드 가게", ROOT / "fixtures" / "dummy_store" / "menus.json"),
    ("파일럿 가게", ROOT / "src" / "agenthan" / "app" / "pilot_store.json"),
    ("실제 가게", ROOT / "data" / "clean" / "menus.json"),
)


def illustrations() -> list[Illustration]:
    return [item for family in FAMILIES for item in family.ILLUSTRATIONS]


def outputs() -> dict[Path, str]:
    """저장소에 커밋하는 생성물 전부(경로 → 내용). 갤러리는 빼고."""
    files: dict[Path, str] = {}
    for family in FAMILIES:
        rows = [item.catalog_row() for item in family.ILLUSTRATIONS]
        text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
        files[library.CATALOG_DIR / f"{family.FAMILY}.json"] = text
        for item in family.ILLUSTRATIONS:
            files[SVG_DIR / f"{item.id}.svg"] = item.render()
    return files


def demo_menus() -> list[tuple[str, str, str | None]]:
    """(출처, 메뉴 이름, 분류) — 데모 가게 세 벌, 있는 파일만."""
    out = []
    for source, path in DEMO_MENUS:
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        menus = data["menus"] if isinstance(data, dict) else data
        for menu in menus:
            name = menu.get("name_ko") or menu.get("name")
            if name:
                out.append((source, name, menu.get("category")))
    return out


def _image(svg_text: str, size: int) -> str:
    uri = "data:image/svg+xml;utf8," + urllib.parse.quote(svg_text)
    return f'<img src="{uri}" width="{size}" height="{size}" alt="">'


def gallery_html() -> str:
    svgs = {item.id: item.render() for item in illustrations()}
    sections = []
    for family in FAMILIES:
        cards = "".join(
            f"<figure>{_image(svgs[item.id], 160)}<figcaption><b>{html.escape(item.name)}</b>"
            f"<code>{item.id}</code><span>{item.category}</span>"
            f"<small>{html.escape(', '.join(item.aliases + item.keywords))}</small>"
            "</figcaption></figure>"
            for item in family.ILLUSTRATIONS
        )
        sections.append(
            f"<h2>{family.FAMILY} <small>{len(family.ILLUSTRATIONS)}장</small></h2>"
            f'<div class="grid">{cards or "<p>아직 없음</p>"}</div>'
        )
    rows = []
    for source, name, category in demo_menus():
        picked = matcher.pick_illustration(name, category)
        fallback = picked in library.DEFAULT_IDS.values()
        rows.append(
            f'<tr class="{"fallback" if fallback else ""}"><td>{html.escape(source)}</td>'
            f"<td>{html.escape(name)}</td><td>{html.escape(category or '')}</td>"
            f"<td>{_image(svgs[picked], 56)}</td><td><code>{picked}</code></td></tr>"
        )
    fallbacks = sum('class="fallback"' in row for row in rows)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>대표 그림 갤러리</title>
<style>
body{{font:14px/1.5 system-ui,sans-serif;margin:24px;background:#fff;color:#222}}
body.dark{{background:#16181c;color:#e8e8e8}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:16px}}
figure{{margin:0}} figcaption{{display:flex;flex-direction:column;font-size:12px}}
code{{font-size:11px;opacity:.75}} small{{opacity:.6}}
table{{border-collapse:collapse}} td{{padding:4px 10px;border-bottom:1px solid #8883}}
tr.fallback td{{background:#f5a62333}}
</style></head><body>
<h1>대표 그림 갤러리 <small>{len(svgs)}장</small></h1>
<label><input type="checkbox" onchange="document.body.classList.toggle('dark',this.checked)">
어두운 화면에서 보기</label>
{"".join(sections)}
<h2>데모 가게 메뉴 → 고른 그림 <small>분류 기본 그림 {fallbacks}개(노란 줄)</small></h2>
<table><tr><th>출처</th><th>메뉴</th><th>분류</th><th></th><th>그림 id</th></tr>
{"".join(rows)}</table>
</body></html>
"""


def write() -> None:
    files = outputs()
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for stale in SVG_DIR.glob("*.svg"):  # 목록에서 빠진 그림
        if stale not in files:
            stale.unlink()
    library.load_catalog.cache_clear()
    GALLERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    GALLERY_PATH.write_text(gallery_html(), encoding="utf-8")


def main() -> int:
    write()
    print(f"대표 그림 {len(illustrations())}장 → {SVG_DIR.relative_to(ROOT)}")
    print(f"갤러리 → {GALLERY_PATH.relative_to(ROOT)}")
    return 0
