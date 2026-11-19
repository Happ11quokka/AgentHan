"""저장소: 메뉴·묶음·리뷰를 어디서 읽을지. 검색 코드는 이 Protocol만 본다.

지금은 JSON 폴더(fixtures/dummy_store 또는 data/clean)를 메모리에 올린다.
나중에 NCP PostgreSQL + pgvector로 옮길 때는 같은 메서드를 가진 클래스를 하나 더 만들면 된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agenthan.core.contracts import MenuItem, MenuOption, Review

REPO_ROOT = Path(__file__).resolve().parents[3]
DUMMY_DIR = REPO_ROOT / "fixtures" / "dummy_store"
DATA_DIR_ENV = "AGENTHAN_DATA_DIR"  # 예: AGENTHAN_DATA_DIR=data/clean → 실제 데이터


@dataclass(frozen=True)
class MenuRecord:
    """계약의 MenuItem + 검색에 쓰는 extra 값."""

    item: MenuItem
    family: str = ""
    tags: tuple[str, ...] = ()
    popularity: dict | None = None
    gift: dict | None = None
    taste: dict | None = None
    name_en: str = ""


@dataclass(frozen=True)
class Family:
    id: str
    name_ko: str
    representative: str
    members: tuple[str, ...]
    aliases: tuple[str, ...]
    popularity: dict | None = None
    gift: dict | None = None


@dataclass
class StoreData:
    menus: list[MenuRecord]
    families: list[Family]
    reviews: list[Review]
    store: dict = field(default_factory=dict)


class MenuRepository(Protocol):
    def load(self) -> StoreData: ...


def resolve_data_dir(path: str | Path | None = None) -> Path:
    """인자 → 환경변수 AGENTHAN_DATA_DIR → 더미 순서로 폴더를 고른다. 상대 경로는 레포 기준."""
    chosen = path or os.environ.get(DATA_DIR_ENV) or DUMMY_DIR
    p = Path(chosen)
    return p if p.is_absolute() else REPO_ROOT / p


class JsonDirRepository:
    """menus.json, families.json(선택), reviews.json(선택), store.json(선택)이 있는 폴더."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = resolve_data_dir(path)

    def _read(self, name: str, default):
        f = self.path / name
        if not f.exists():
            return default
        return json.loads(f.read_text(encoding="utf-8"))

    def load(self) -> StoreData:
        raw_menus = self._read("menus.json", None)
        if raw_menus is None:
            raise FileNotFoundError(f"menus.json이 없습니다: {self.path}")
        menus = [_menu(m) for m in raw_menus]
        families = [_family(f) for f in self._read("families.json", [])]
        reviews = [_review(r) for r in self._read("reviews.json", [])]
        return StoreData(menus, families, reviews, self._read("store.json", {}))


def _menu(m: dict) -> MenuRecord:
    x = m.get("extra") or {}
    item = MenuItem(
        id=m["id"],
        name_ko=m["name_ko"],
        price=m["price"],
        description=m.get("description", "") or "",
        category=m.get("category", "") or "",
        name_native=m.get("name_native", "") or "",
        aliases=tuple(m.get("aliases") or ()),
        options=tuple(MenuOption(**o) for o in m.get("options") or ()),
        allergens=tuple(m.get("allergens") or ()),
        sold_out=bool(m.get("sold_out", False)),
    )
    return MenuRecord(
        item=item,
        family=x.get("family", "") or "",
        tags=tuple(x.get("tags") or ()),
        popularity=x.get("popularity"),
        gift=x.get("gift"),
        taste=x.get("taste"),
        name_en=x.get("name_en", "") or "",
    )


def _family(f: dict) -> Family:
    return Family(
        id=f["id"],
        name_ko=f.get("name_ko", ""),
        representative=f["representative"],
        members=tuple(f.get("members") or ()),
        aliases=tuple(f.get("aliases") or ()),
        popularity=f.get("popularity"),
        gift=f.get("gift"),
    )


def _review(r: dict) -> Review:
    return Review(
        id=r["id"],
        text=r.get("text", ""),
        created_at=datetime.fromisoformat(r["created_at"]),
        rating=r.get("rating"),
        author=r.get("author", ""),
        menu_ids=tuple(r.get("menu_ids") or ()),
    )
