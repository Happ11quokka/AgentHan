"""데이터 넣기: 레포의 가게 데이터 전부(+ 있으면 data/clean 실제 데이터)를 DB에 올린다.

    uv run --env-file .env python -m agenthan.db.seed            # 스키마 올리기 + 전부 넣기
    uv run --env-file .env python -m agenthan.db.seed --no-real  # 실제 데이터는 빼고

가게마다 지우고 다시 넣는다(여러 번 돌려도 같은 결과). 임베딩 캐시 JSON(data/cache/*.json)도
embedding_cache 표로 옮겨서, 벡터를 만들 때 CLOVA를 다시 부르지 않게 한다.

| 가게 id | 원본 | 형식 |
|---|---|---|
| dummy_store | fixtures/dummy_store/ | rag (Part B 더미) |
| real | data/clean/ (있을 때만, 커밋 금지) | rag (실제 가게) |
| sample, sample-pho, t-* | fixtures/sample_store.json, fixtures/phone/*.json | phone |
| pilot, owner_seed, demo | app/pilot_store.json, tools/dummy_store_seed.json, |
|  | fixtures/demo_store.json | owner |
| snap-* | fixtures/review_snapshot.jsonl + review_menus.json | snapshot |
| place_report | tools/place_report_data.json | report |
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from agenthan.db.conn import connect
from agenthan.db.migrate import migrate

REPO_ROOT = Path(__file__).resolve().parents[3]
KST = timezone(timedelta(hours=9))
REAL_STORE_ID = "real"


# ------------------------------------------------------------------ 한 가게 = 표에 넣을 행 묶음
@dataclass
class StoreRows:
    id: str
    name: str
    kind: str
    source_format: str
    source_path: str
    info: dict = field(default_factory=dict)
    raw: dict | None = None
    menus: list[dict] = field(default_factory=list)
    families: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    docs: list[dict] = field(default_factory=list)
    owner_records: list[dict] = field(default_factory=list)
    place_stats: list[dict] = field(default_factory=list)


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=KST)


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _doc(doc_id: str, kind: str, title: str, body: str, data=None) -> dict | None:
    body = body.strip()
    if not body:
        return None
    return {"id": doc_id, "kind": kind, "title": title, "body": body, "data": data}


def _menu_row(raw: dict, position: int, **over) -> dict:
    extra = raw.get("extra") or {}
    row = {
        "id": raw["id"],
        "position": position,
        "name_ko": raw.get("name_ko") or raw.get("name") or "",
        "name_native": raw.get("name_native") or "",
        "name_en": extra.get("name_en") or "",
        "price": raw.get("price"),
        "category": raw.get("category") or "",
        "description": raw.get("description") or "",
        "aliases": list(raw.get("aliases") or []),
        "options": raw.get("options") or [],
        "allergens": list(raw.get("allergens") or []),
        "sold_out": bool(raw.get("sold_out", False)),
        "family_id": extra.get("family") or "",
        "tags": list(extra.get("tags") or []),
        "popularity": extra.get("popularity"),
        "extra": extra,
        "raw": raw,
    }
    row.update(over)
    return row


def _review_row(raw: dict, position: int) -> dict:
    extra = raw.get("extra") or {}
    reply = raw.get("owner_reply")
    if isinstance(reply, dict):
        reply = reply.get("text") or json.dumps(reply, ensure_ascii=False)
    return {
        "id": str(raw.get("id") or raw.get("review_id")),
        "position": position,
        "text": raw.get("text") or "",
        "created_at": _ts(raw.get("created_at") or raw.get("visit_date")),
        "visit_date": _date(raw.get("visit_date") or extra.get("visit_datetime")),
        "rating": raw.get("rating"),
        "author": raw.get("author") or "",
        "menu_ids": list(raw.get("menu_ids") or []),
        "features": raw.get("features"),
        "owner_reply": reply if isinstance(reply, str) else None,
        "language": extra.get("language") or "",
        "extra": extra,
        "raw": raw,
    }


# ------------------------------------------------------------------ 가게 정보 → 검색 조각
def _rag_info_docs(info: dict) -> list[dict]:
    docs = []
    hours = []
    for h in info.get("business_hours") or []:
        line = f"{h.get('day', '')} {h.get('start', '')}~{h.get('end', '')}"
        if h.get("break"):
            line += f" (쉬는 시간 {h['break']})"
        if h.get("last_order"):
            line += f" (주문 마감 {h['last_order']})"
        hours.append(line.strip())
    closed = ", ".join(info.get("regular_closed_days") or []) or "정기 휴무 없음"
    docs.append(_doc("hours", "hours", "영업시간", f"영업시간: {'; '.join(hours)}. 휴무: {closed}"))
    place = [info.get("road_address") or info.get("address") or ""]
    if info.get("subway"):
        place.append("가까운 역: " + ", ".join(info["subway"]))
    docs.append(_doc("location", "location", "위치", " / ".join(p for p in place if p)))
    facility = []
    for key, label in (("conveniences", "편의"), ("payment", "결제"), ("keywords", "대표 키워드")):
        if info.get(key):
            facility.append(f"{label}: {', '.join(info[key])}")
    docs.append(_doc("facility", "facility", "편의·결제", ". ".join(facility)))
    if info.get("phone"):
        docs.append(_doc("contact", "contact", "전화", f"가게 전화번호 {info['phone']}"))
    if info.get("description"):
        docs.append(_doc("description", "description", "가게 소개", info["description"]))
    summary = info.get("review_summary") or {}
    if summary:
        parts = []
        if summary.get("total"):
            parts.append(f"리뷰 {summary['total']}개, 평균 별점 {summary.get('avg_rating', '-')}")
        if summary.get("themes"):
            parts.append(
                "자주 나온 주제: "
                + ", ".join(f"{t['label']}({t['count']})" for t in summary["themes"][:10])
            )
        if summary.get("menus_mentioned"):
            parts.append(
                "자주 언급된 메뉴: "
                + ", ".join(f"{t['label']}({t['count']})" for t in summary["menus_mentioned"][:10])
            )
        if summary.get("micro_reviews"):
            parts.append("한 줄 평: " + " / ".join(summary["micro_reviews"][:5]))
        docs.append(
            _doc("review_summary", "review_summary", "리뷰 요약", ". ".join(parts), summary)
        )
    return [d for d in docs if d]


def _owner_shop_docs(shop: dict) -> list[dict]:
    docs = []
    if shop.get("introduction"):
        docs.append(_doc("introduction", "description", "가게 소개", shop["introduction"]))
    hours = []
    for day, h in (shop.get("hours") or {}).items():
        if not isinstance(h, dict):
            continue
        hours.append(
            f"{day} 휴무"
            if h.get("closed")
            else f"{day} {h.get('open_time', '')}~{h.get('close_time', '')}"
        )
    if hours:
        docs.append(_doc("hours", "hours", "영업시간", "영업시간: " + "; ".join(hours)))
    if shop.get("address"):
        docs.append(_doc("location", "location", "위치", shop["address"]))
    if shop.get("phone"):
        docs.append(_doc("contact", "contact", "전화", f"가게 전화번호 {shop['phone']}"))
    return [d for d in docs if d]


# ------------------------------------------------------------------ 형식별 읽기
def load_rag_dir(path: Path, store_id: str, kind: str) -> StoreRows:
    menus = _read(path / "menus.json")
    families = _read(path / "families.json") if (path / "families.json").exists() else []
    reviews = _read(path / "reviews.json") if (path / "reviews.json").exists() else []
    info = _read(path / "store.json") if (path / "store.json").exists() else {}
    rows = StoreRows(store_id, info.get("name") or store_id, kind, "rag", _rel(path), info=info)
    rows.menus = [_menu_row(m, i) for i, m in enumerate(menus)]
    rows.families = [
        {
            "id": f["id"],
            "position": i,
            "name_ko": f.get("name_ko") or "",
            "representative": f.get("representative") or "",
            "members": list(f.get("members") or []),
            "aliases": list(f.get("aliases") or []),
            "popularity": f.get("popularity"),
            "gift": f.get("gift"),
            "raw": f,
        }
        for i, f in enumerate(families)
    ]
    rows.reviews = [_review_row(r, i) for i, r in enumerate(reviews)]
    rows.docs = _rag_info_docs(info)
    return rows


def _phone_rows(raw: dict, source: Path, store_id: str | None = None) -> StoreRows:
    store = raw.get("store") or {}
    sid = store_id or store.get("id") or source.stem
    rows = StoreRows(
        sid, store.get("name") or sid, "dummy", "phone", _rel(source), info=store, raw=raw
    )
    rows.menus = [_menu_row(m, i) for i, m in enumerate(raw.get("menu") or [])]
    rows.reviews = [_review_row(r, i) for i, r in enumerate(raw.get("reviews") or [])]
    phone = raw.get("phone") or {}
    if phone:
        body = f"영업시간 {phone.get('open', '')}~{phone.get('close', '')}"
        if phone.get("prep_minutes"):
            body += f", 주문 뒤 준비 시간 약 {phone['prep_minutes']}분"
        rows.docs.append(_doc("hours", "hours", "영업시간·준비 시간", body, phone))
    return rows


def load_phone_file(path: Path) -> list[StoreRows]:
    raw = _read(path)
    if "stores" in raw:  # 튜닝용 가상 가게 여러 곳
        return [_phone_rows(s, path) for s in raw["stores"]]
    return [_phone_rows(raw, path)]


OWNER_COLLECTIONS = ("talktalk", "orders", "reservations", "calls", "coupons")


def load_owner_file(path: Path, store_id: str) -> StoreRows:
    raw = _read(path)
    shop = raw.get("shop") or {}
    rows = StoreRows(
        store_id, shop.get("name") or store_id, "dummy", "owner", _rel(path), info=shop, raw=raw
    )
    rows.menus = [_menu_row(m, i) for i, m in enumerate(raw.get("menus") or [])]
    rows.reviews = [_review_row(r, i) for i, r in enumerate(raw.get("reviews") or [])]
    rows.docs = _owner_shop_docs(shop)
    for name in OWNER_COLLECTIONS:
        for i, rec in enumerate(raw.get(name) or []):
            rows.owner_records.append(
                {"collection": name, "id": str(rec.get("id") or i), "position": i, "data": rec}
            )
    for name in ("settlements", "statistics"):
        for key, value in (raw.get(name) or {}).items():
            rows.owner_records.append({"collection": name, "id": key, "position": 0, "data": value})
    for i, talk in enumerate(raw.get("talktalk") or []):  # 손님 문의도 검색 조각으로
        doc = _doc(
            f"talk-{i}",
            "inquiry",
            "손님 문의",
            f"문의: {talk.get('message', '')} 답: {talk.get('reply_text', '')}",
            talk,
        )
        if doc:
            rows.docs.append(doc)
    return rows


def load_snapshot(snapshot: Path, menus_file: Path) -> list[StoreRows]:
    menus = {k: v for k, v in _read(menus_file).items() if not k.startswith("_")}
    by_store: dict[str, list[dict]] = {}
    category: dict[str, str] = {}
    for line in snapshot.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            by_store.setdefault(rec["store"], []).append(rec)
            category[rec["store"]] = rec.get("category") or ""
    out = []
    for n, (name, recs) in enumerate(sorted(by_store.items())):
        rows = StoreRows(
            f"snap-{n + 1}",
            name,
            "dummy",
            "snapshot",
            _rel(snapshot),
            info={"name": name, "category": category[name]},
        )
        short = next((k for k in menus if k in name), "")
        rows.menus = [
            _menu_row(
                {
                    "id": f"m{i + 1:02d}",
                    "name_ko": m["name"],
                    "aliases": m.get("aliases"),
                    "price": None,
                },
                i,
            )
            for i, m in enumerate(menus.get(short, []))
        ]
        rows.reviews = [_review_row(r, i) for i, r in enumerate(recs)]
        out.append(rows)
    return out


def load_place_report(path: Path) -> StoreRows:
    raw = _read(path)
    store = raw.get("store") or {}
    rows = StoreRows(
        "place_report",
        store.get("name") or "place_report",
        "dummy",
        "report",
        _rel(path),
        info=store,
        raw=raw,
    )
    for period in ("week", "day"):
        for rec in raw.get(period) or []:
            rows.place_stats.append(
                {"period": period, "label": rec.get("end") or rec.get("date"), "data": rec}
            )
    if store.get("representative_keywords"):
        rows.docs.append(
            _doc(
                "keywords",
                "facility",
                "대표 키워드",
                "대표 키워드: " + ", ".join(store["representative_keywords"]),
            )
        )
    return rows


def all_stores(include_real: bool = True, real_dir: Path | None = None) -> list[StoreRows]:
    f = REPO_ROOT / "fixtures"
    out = [load_rag_dir(f / "dummy_store", "dummy_store", "dummy")]
    real = real_dir or REPO_ROOT / "data" / "clean"
    if include_real and (real / "menus.json").exists():
        out.append(load_rag_dir(real, REAL_STORE_ID, "real"))
    out += load_phone_file(f / "sample_store.json")
    out += load_phone_file(f / "phone" / "sample_pho_store.json")
    out += load_phone_file(f / "phone" / "tuning_stores.json")
    src = REPO_ROOT / "src" / "agenthan"
    out.append(load_owner_file(src / "app" / "pilot_store.json", "pilot"))
    out.append(load_owner_file(src / "tools" / "dummy_store_seed.json", "owner_seed"))
    out.append(load_owner_file(f / "demo_store.json", "demo"))
    out += load_snapshot(f / "review_snapshot.jsonl", f / "review_menus.json")
    out.append(load_place_report(src / "tools" / "place_report_data.json"))
    return out


def eval_files() -> list[tuple[str, str, Path]]:
    d = REPO_ROOT / "fixtures" / "dummy_store"
    return [(f"dummy_store/{p.stem}", "dummy_store", p) for p in sorted(d.glob("*.jsonl"))]


# ------------------------------------------------------------------ 쓰기
def _j(value):
    return None if value is None else Jsonb(value)


def write_store(conn, rows: StoreRows) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM stores WHERE id = %s", (rows.id,))
        conn.execute(
            "INSERT INTO stores (id, name, kind, source_format, source_path, info, raw)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                rows.id,
                rows.name,
                rows.kind,
                rows.source_format,
                rows.source_path,
                Jsonb(rows.info),
                _j(rows.raw),
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO menus (store_id, id, position, name_ko, name_native, name_en, price,"
                " category, description, aliases, options, allergens, sold_out, family_id, tags,"
                " popularity, extra, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)",
                [
                    (
                        rows.id,
                        m["id"],
                        m["position"],
                        m["name_ko"],
                        m["name_native"],
                        m["name_en"],
                        m["price"],
                        m["category"],
                        m["description"],
                        m["aliases"],
                        Jsonb(m["options"]),
                        m["allergens"],
                        m["sold_out"],
                        m["family_id"],
                        m["tags"],
                        _j(m["popularity"]),
                        Jsonb(m["extra"]),
                        Jsonb(m["raw"]),
                    )
                    for m in rows.menus
                ],
            )
            cur.executemany(
                "INSERT INTO menu_families (store_id, id, position, name_ko, representative,"
                " members, aliases, popularity, gift, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        rows.id,
                        f["id"],
                        f["position"],
                        f["name_ko"],
                        f["representative"],
                        f["members"],
                        f["aliases"],
                        _j(f["popularity"]),
                        _j(f["gift"]),
                        Jsonb(f["raw"]),
                    )
                    for f in rows.families
                ],
            )
            cur.executemany(
                "INSERT INTO reviews (store_id, id, position, text, created_at, visit_date, rating,"
                " author, menu_ids, features, owner_reply, language, extra, raw)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        rows.id,
                        r["id"],
                        r["position"],
                        r["text"],
                        r["created_at"],
                        r["visit_date"],
                        r["rating"],
                        r["author"],
                        r["menu_ids"],
                        _j(r["features"]),
                        r["owner_reply"],
                        r["language"],
                        Jsonb(r["extra"]),
                        Jsonb(r["raw"]),
                    )
                    for r in rows.reviews
                ],
            )
            cur.executemany(
                "INSERT INTO store_docs (store_id, id, kind, title, body, data)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                [
                    (rows.id, d["id"], d["kind"], d["title"], d["body"], _j(d["data"]))
                    for d in rows.docs
                ],
            )
            cur.executemany(
                "INSERT INTO owner_records (store_id, collection, id, position, data)"
                " VALUES (%s,%s,%s,%s,%s)",
                [
                    (rows.id, o["collection"], o["id"], o["position"], Jsonb(o["data"]))
                    for o in rows.owner_records
                ],
            )
            cur.executemany(
                "INSERT INTO place_stats (store_id, period, label, data) VALUES (%s,%s,%s,%s)",
                [(rows.id, s["period"], s["label"], Jsonb(s["data"])) for s in rows.place_stats],
            )


def write_eval_cases(conn, files: Iterable[tuple[str, str, Path]]) -> int:
    n = 0
    with conn.transaction():
        for dataset, store_id, path in files:
            conn.execute("DELETE FROM eval_cases WHERE dataset = %s", (dataset,))
            cases = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO eval_cases (dataset, idx, store_id, query, expected_ids,"
                    " primary_id, type, channel, data) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (
                            dataset,
                            i,
                            store_id,
                            c.get("query", ""),
                            c.get("expected_ids") or [],
                            c.get("primary_id") or "",
                            c.get("type") or "",
                            c.get("channel") or "",
                            Jsonb(c),
                        )
                        for i, c in enumerate(cases)
                    ],
                )
            n += len(cases)
    return n


def vector_literal(vector: Iterable[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


def import_embedding_caches(conn, cache_dir: Path | None = None) -> int:
    """data/cache/*_embeddings.json → embedding_cache. 이미 있는 해시는 건너뛴다."""
    folder = cache_dir or REPO_ROOT / "data" / "cache"
    n = 0
    for path in sorted(folder.glob("*_embeddings.json")):
        raw = _read(path)
        model = raw.get("model", "")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO embedding_cache (content_hash, model, embedding) VALUES (%s,%s,%s)"
                " ON CONFLICT (content_hash) DO NOTHING",
                [(h, model, vector_literal(v)) for h, v in (raw.get("vectors") or {}).items()],
            )
            n += len(raw.get("vectors") or {})
    return n


_SAFE = re.compile(r"[^0-9A-Za-z_\-]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-real", action="store_true", help="data/clean 실제 데이터는 넣지 않음")
    parser.add_argument("--real-dir", type=Path, default=None, help="실제 데이터 폴더")
    args = parser.parse_args(argv)
    stores = all_stores(include_real=not args.no_real, real_dir=args.real_dir)
    with connect() as conn:
        applied = migrate(conn)
        if applied:
            print("스키마:", ", ".join(applied))
        for rows in stores:
            write_store(conn, rows)
            print(
                f"  {_SAFE.sub('', rows.id):14s} {rows.kind:5s} {rows.source_format:8s}"
                f" 메뉴 {len(rows.menus):3d} · 리뷰 {len(rows.reviews):3d}"
                f" · 조각 {len(rows.docs):2d}"
                f" · 기타 {len(rows.owner_records) + len(rows.place_stats):3d}"
            )
        cases = write_eval_cases(conn, eval_files())
        cached = import_embedding_caches(conn)
    print(f"가게 {len(stores)}곳, 채점 문항 {cases}개, 임베딩 캐시 {cached}개 확인")
    return 0


if __name__ == "__main__":
    sys.exit(main())
