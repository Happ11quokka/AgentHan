"""저장소: 메뉴·묶음·리뷰를 어디서 읽을지. 검색 코드는 이 Protocol만 본다.

- JsonDirRepository: JSON 폴더(fixtures/dummy_store 또는 data/clean)를 메모리에 올린다.
- PgRepository: PostgreSQL(`agenthan.db`, AGENTHAN_DB_URL)의 가게 한 곳을 같은 모양으로 올린다.
- ShopDataRepository: 비서 가게 파일(DummyStore) 한 벌(dict)을 같은 모양으로.
  메뉴의 family·popularity 등과 최상위 families가 있으면 같이 읽는다
  (실제 데이터로 만든 파일럿 가게).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
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


class PgRepository:
    """PostgreSQL(`agenthan.db`)의 한 가게. JSON 폴더와 같은 StoreData를 돌려준다.

    표의 열에서 다시 만든다(원본 형식이 rag·phone·owner 어느 것이든). 순서는 원본 순서(position)."""

    def __init__(self, store_id: str, url: str | None = None) -> None:
        self.store_id = store_id
        self.url = url

    def load(self) -> StoreData:
        from agenthan.db.conn import connect

        with connect(self.url) as conn:
            store = conn.execute(
                "SELECT info FROM stores WHERE id = %s", (self.store_id,)
            ).fetchone()
            if store is None:
                raise LookupError(f"DB에 가게가 없습니다: {self.store_id}")
            menus = conn.execute(
                "SELECT id, name_ko, price, description, category, name_native, aliases, options,"
                " allergens, sold_out, family_id, tags, popularity, extra, name_en FROM menus"
                " WHERE store_id = %s ORDER BY position, id",
                (self.store_id,),
            ).fetchall()
            families = conn.execute(
                "SELECT id, name_ko, representative, members, aliases, popularity, gift"
                " FROM menu_families WHERE store_id = %s ORDER BY position, id",
                (self.store_id,),
            ).fetchall()
            reviews = conn.execute(
                "SELECT id, text, created_at, visit_date, rating, author, menu_ids FROM reviews"
                " WHERE store_id = %s ORDER BY position, id",
                (self.store_id,),
            ).fetchall()
        return StoreData(
            menus=[_menu_from_row(r) for r in menus],
            families=[
                Family(f[0], f[1], f[2], tuple(f[3]), tuple(f[4]), f[5], f[6]) for f in families
            ],
            reviews=[_review_from_row(r) for r in reviews],
            store=store[0] or {},
        )


class ShopDataRepository:
    """비서 가게 파일(DummyStore, `shop`·`menus`·`reviews` 형식) 한 벌을 그대로 검색 데이터로.

    메뉴 이름 키는 `name`(없으면 `name_ko`), 리뷰 id는 `review_id`, 시각은 `visit_date`."""

    def __init__(self, data: dict) -> None:
        self.data = data

    def load(self) -> StoreData:
        menus = []
        for m in self.data.get("menus") or []:
            item = MenuItem(
                id=str(m["id"]),
                name_ko=m.get("name") or m.get("name_ko") or "",
                price=m.get("price") or 0,
                description=m.get("description") or "",
                category=m.get("category") or "",
                name_native=m.get("name_native") or "",
                aliases=tuple(m.get("aliases") or ()),
                options=tuple(
                    MenuOption(**{k: v for k, v in o.items() if k in _OPTION_FIELDS})
                    for o in m.get("options") or ()
                    if isinstance(o, dict) and o.get("name")
                ),
                allergens=tuple(m.get("allergens") or ()),
                sold_out=bool(m.get("sold_out", False)),
            )
            menus.append(
                MenuRecord(
                    item=item,
                    family=m.get("family") or "",
                    tags=tuple(m.get("tags") or ()),
                    popularity=m.get("popularity"),
                    gift=m.get("gift"),
                    taste=m.get("taste"),
                )
            )
        kst = timezone(timedelta(hours=9))
        reviews = []
        for r in self.data.get("reviews") or []:
            when = r.get("created_at") or r.get("visit_date") or "1970-01-01"
            try:
                t = datetime.fromisoformat(str(when))
            except ValueError:
                t = datetime(1970, 1, 1)
            reviews.append(
                Review(
                    id=str(r.get("review_id") or r.get("id")),
                    text=r.get("text") or "",
                    created_at=t if t.tzinfo else t.replace(tzinfo=kst),
                    rating=r.get("rating"),
                    author=r.get("author") or "",
                    menu_ids=tuple(r.get("menu_ids") or ()),
                )
            )
        # 묶음(families)이 있으면 같이: '메도빅'이 묶음 전체를 가리키게. 빠진 메뉴는 묶음에서 뺀다
        ids = {m.item.id for m in menus}
        families = []
        for f in self.data.get("families") or []:
            fam = _family(f)
            members = tuple(m for m in fam.members if m in ids)
            if members:
                lead = fam.representative if fam.representative in members else members[0]
                families.append(replace(fam, members=members, representative=lead))
        return StoreData(menus, families, reviews, dict(self.data.get("shop") or {}))


_OPTION_FIELDS = {"name", "available", "extra_price", "note"}


def _menu_from_row(r) -> MenuRecord:
    (mid, name_ko, price, description, category, name_native, aliases, options, allergens,
     sold_out, family, tags, popularity, extra, name_en) = r
    extra = extra or {}
    item = MenuItem(
        id=mid,
        name_ko=name_ko,
        price=price or 0,
        description=description,
        category=category,
        name_native=name_native,
        aliases=tuple(aliases),
        options=tuple(
            MenuOption(**{k: v for k, v in o.items() if k in _OPTION_FIELDS}) for o in options
        ),
        allergens=tuple(allergens),
        sold_out=sold_out,
    )
    return MenuRecord(
        item=item,
        family=family,
        tags=tuple(tags),
        popularity=popularity,
        gift=extra.get("gift"),
        taste=extra.get("taste"),
        name_en=name_en,
    )


def _review_from_row(r) -> Review:
    rid, text, created_at, visit_date, rating, author, menu_ids = r
    if created_at is None:  # 시각이 없는 리뷰: 방문일 → 없으면 아주 옛날(최신순 맨 뒤)
        kst = timezone(timedelta(hours=9))
        created_at = (
            datetime.combine(visit_date, datetime.min.time(), kst)
            if visit_date
            else datetime(1970, 1, 1, tzinfo=kst)
        )
    return Review(rid, text, created_at, rating, author, tuple(menu_ids))


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
