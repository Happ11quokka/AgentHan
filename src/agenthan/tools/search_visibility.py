"""Deterministic search visibility diagnosis, independent of the orchestrator."""

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from itertools import zip_longest
from pathlib import Path

from agenthan.tools.naver_search import (
    ApiHubSearch,
    BlogItem,
    NaverSearchError,
    SearchClient,
    clean_text,
)


@dataclass
class StoreProfile:
    name: str
    area: str
    category: str
    menus: list[str]
    native_menus: list[str]
    road_address: str = ""


@dataclass(frozen=True)
class Query:
    text: str
    kind: str


@dataclass(frozen=True)
class Competitor:
    name: str
    category: str


@dataclass
class LocalVisibility:
    query: Query
    rank: int | None
    competitors: list[Competitor]


@dataclass
class BlogVisibility:
    query: str
    total: int
    mention_count: int
    items: list[BlogItem]


@dataclass
class VisibilityReport:
    profile: StoreProfile
    local_results: list[LocalVisibility] = field(default_factory=list)
    blog_results: list[BlogVisibility] = field(default_factory=list)


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _normalize(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKC", clean_text(text)).casefold() if c.isalnum()
    )


def _normalize_address(address: str) -> str:
    # Keep building-number separators: 1-23 and 12-3 are different addresses.
    return "".join(unicodedata.normalize("NFKC", clean_text(address)).casefold().split())


def profile_from_store(data: dict) -> StoreProfile:
    shop = data.get("shop", {})
    address = _text(shop.get("address"))
    area = next(
        (
            token
            for token in re.split(r"[\s(),]+", address)
            if re.fullmatch(r"[가-힣][가-힣0-9·]*(?:동(?:\d+가)?|읍|면|리)", token)
        ),
        "",
    )
    menus = data.get("menus", [])
    return StoreProfile(
        name=_text(shop.get("name")),
        area=area,
        category=_text(shop.get("category")),
        menus=[_text(m.get("name")) for m in menus if _text(m.get("name"))],
        native_menus=[_text(m.get("name_native")) for m in menus if _text(m.get("name_native"))],
        road_address=_text(shop.get("road_address") or shop.get("roadAddress")),
    )


def candidate_queries(profile: StoreProfile, limit: int = 12) -> list[Query]:
    if limit <= 0:
        return []
    area, category, name = (_text(s) for s in (profile.area, profile.category, profile.name))
    category = _text(re.split(r"[·,]", category, maxsplit=1)[0])
    menus = [_text(m) for m in profile.menus if _text(m)]
    groups = [
        [Query(f"{area} {category}", "area_category")] if area and category else [],
        [Query(f"{area} {m}", "area_menu") for m in menus] if area else [],
        [Query(m, "menu") for m in menus],
        [Query(_text(m), "native_menu") for m in profile.native_menus if _text(m)],
        [Query(name, "store_name")] if name else [],
        [Query(f"{name} {area}", "store_area")] if name and area else [],
    ]
    # Round-robin preserves query kinds even for a large menu.
    seen, result = set(), []
    for row in zip_longest(*groups):
        for query in row:
            if query is not None and (key := _normalize(query.text)) not in seen:
                seen.add(key)
                result.append(query)
                if len(result) == limit:
                    return result
    return result


def diagnose(
    profile: StoreProfile, client: SearchClient, queries: list[Query] | None = None
) -> VisibilityReport:
    report = VisibilityReport(profile)
    local_cache, blog_cache = {}, {}
    name, address = _normalize(profile.name), _normalize_address(profile.road_address)
    if not name:
        raise NaverSearchError("진단할 가게 이름을 입력해 주세요.")
    for query in candidate_queries(profile) if queries is None else queries:
        text = _text(query.text)
        key = unicodedata.normalize("NFKC", text).casefold()
        if key not in local_cache:
            local_cache[key] = client.local(text, display=5)[:5]
        items = local_cache[key]
        rank = next((i for i, item in enumerate(items, 1) if _normalize(item.title) == name), None)
        if rank is None and address:
            rank = next(
                (
                    i
                    for i, item in enumerate(items, 1)
                    if _normalize_address(item.roadAddress) == address
                ),
                None,
            )
        competitors = [
            Competitor(item.title, item.category) for i, item in enumerate(items, 1) if i != rank
        ][:4]
        report.local_results.append(LocalVisibility(query, rank, competitors))
    blog_queries = [profile.name]
    if profile.menus and _text(profile.menus[0]):
        blog_queries.append(f"{profile.name} {profile.menus[0]}")
    for query in blog_queries:
        text = _text(query)
        if text in blog_cache:
            continue
        result = client.blog(text, display=10)
        blog_cache[text] = result
        items = result.items[:10]
        mentions = sum(
            name in _normalize(item.title) or name in _normalize(item.description) for item in items
        )
        report.blog_results.append(BlogVisibility(text, result.total, mentions, items))
    return report


def summarize(report: VisibilityReport) -> str:
    found = [r for r in report.local_results if r.rank is not None]
    missing = [r for r in report.local_results if r.rank is None]
    lines = [f"{report.profile.name} 검색 노출 진단", "나오는 검색어와 순위:"]
    lines.extend(f"- {r.query.text}: {r.rank}위" for r in found)
    if not found:
        lines.append("- 확인된 검색어 없음")
    lines.append("안 나오는 검색어 (5위 밖 또는 없음):")
    lines.extend(f"- {r.query.text}" for r in missing)
    if not missing:
        lines.append("- 없음")
    lines.append("같이 나온 가게:")
    for row in report.local_results:
        if row.competitors:
            shops = ", ".join(f"{c.name} ({c.category})" for c in row.competitors)
            lines.append(f"- {row.query.text}: {shops}")
    lines.append(f"손님이 들어올 수 있는 경로: 지역 검색 상위 5개에서 {len(found)}개 검색어 노출.")
    lines.append("블로그 검색 경로 (전체 글 수는 검색 결과 수이며 실제 방문·유입 수가 아닙니다):")
    for blog in report.blog_results:
        lines.append(
            f"- {blog.query}: 전체 {blog.total}개 글, "
            f"상위 {len(blog.items)}개 중 가게명 언급 {blog.mention_count}개"
        )
    lines.append("개인화 없는 검색 기준이며 실제 화면과 다를 수 있음")
    return "\n".join(lines)


def _save_path(value: str) -> Path:
    # Resolve the destination, but keep data/ anchored to this working directory:
    # resolving the root itself would accept a data symlink to an outside folder.
    try:
        path = Path(value).resolve()
        root = Path.cwd().resolve() / "data"
    except (OSError, RuntimeError, ValueError):
        # A symlink loop or invalid path cannot be verified as staying in data/.
        pass
    else:
        if path.is_relative_to(root) and path != root:
            return path
    raise NaverSearchError("검색 결과는 data/ 아래 파일에만 저장할 수 있습니다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개인화 없는 네이버 검색 노출 진단")
    parser.add_argument("--name", required=True)
    parser.add_argument("--area", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--menu", action="append", default=[])
    parser.add_argument("--native-menu", action="append", default=[])
    parser.add_argument("--road-address", default="")
    parser.add_argument("--save")
    args = parser.parse_args(argv)
    try:
        path = _save_path(args.save) if args.save is not None else None
        profile = StoreProfile(
            args.name, args.area, args.category, args.menu, args.native_menu, args.road_address
        )
        report = diagnose(profile, ApiHubSearch())
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(summarize(report))
        return 0
    except NaverSearchError as error:
        print(str(error), file=sys.stderr)
    except OSError:
        print("검색 결과 파일을 저장할 수 없습니다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
