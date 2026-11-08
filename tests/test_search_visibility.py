import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from agenthan.tools import search_visibility as visibility
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.naver_search import (
    BlogItem,
    BlogResult,
    FixtureSearch,
    LocalItem,
    NaverSearchError,
)
from agenthan.tools.search_visibility import (
    Query,
    StoreProfile,
    candidate_queries,
    diagnose,
    profile_from_store,
    summarize,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Offline tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    for key in ("NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def profile():
    return StoreProfile(
        "가상 연습 베이커리",
        "연습동",
        "베이커리",
        ["꿀케이크", "겹케이크"],
        ["Медовик"],
        "가상시 연습로 3",
    )


def test_store_adapter_and_missing_neighborhood(tmp_path):
    data = DummyStore(tmp_path / "store.json").read()
    data["shop"]["address"] = "가상시 연습구 연습로 3"
    profile = profile_from_store(data)
    assert profile.name == data["shop"]["name"]
    assert profile.menus == [m["name"] for m in data["menus"]]
    assert profile.area == ""  # A district or street does not imply a neighborhood.
    assert not {q.kind for q in candidate_queries(profile)} & {
        "area_category",
        "area_menu",
        "store_area",
    }
    data["shop"]["address"] = "가상시 연습구 연습로 3 (연습동)"
    data["shop"]["roadAddress"] = "가상시 연습로 3"
    data["menus"][0]["name_native"] = "Медовик"
    profile = profile_from_store(data)
    assert profile.area == "연습동"
    assert profile.road_address == "가상시 연습로 3"
    assert profile.native_menus == [m["name_native"] for m in data["menus"] if m.get("name_native")]
    assert profile.native_menus[0] == "Медовик"
    assert profile_from_store({"shop": {"name": "가상"}}).area == ""


@pytest.mark.parametrize("area", ["성수동1가", "대치1동", "가상읍", "가상면", "가상리"])
def test_neighborhood_formats(area):
    assert profile_from_store({"shop": {"address": f"가상시 {area} 1"}}).area == area


def test_neighborhood_skips_building_number():
    data = {"shop": {"address": "서울 강남구 테헤란로 1, 101동 (역삼동)"}}
    assert profile_from_store(data).area == "역삼동"


def test_query_kinds_deduplication_and_limit(profile):
    queries = candidate_queries(profile)
    assert {q.kind for q in queries} == {
        "area_category",
        "area_menu",
        "menu",
        "native_menu",
        "store_name",
        "store_area",
    }
    assert Query("연습동 베이커리", "area_category") in queries
    assert Query("연습동 꿀케이크", "area_menu") in queries
    assert Query("꿀케이크", "menu") in queries
    assert Query("Медовик", "native_menu") in queries
    assert Query("가상 연습 베이커리 연습동", "store_area") in queries
    repeated = replace(
        profile, menus=["꿀케이크", "꿀케이크", " 꿀케이크 "], native_menus=["꿀케이크"]
    )
    texts = [q.text for q in candidate_queries(repeated)]
    assert texts.count("꿀케이크") == texts.count("연습동 꿀케이크") == 1
    many = replace(profile, menus=[f"메뉴{i}" for i in range(20)])
    assert len(candidate_queries(many)) == 12
    assert {q.kind for q in candidate_queries(many)} == {q.kind for q in queries}
    assert len(candidate_queries(profile, limit=2)) == 2
    assert candidate_queries(profile, limit=0) == []


def test_fixture_rank_competitors_blog_and_summary(profile):
    report = diagnose(profile, FixtureSearch())
    rows = {r.query.text: r for r in report.local_results}
    assert rows["연습동 베이커리"].rank == 3
    assert rows["연습동 꿀케이크"].rank is None
    assert rows["꿀케이크"].rank is None  # Different branch name must not match.
    assert rows[profile.name].rank == 1
    assert len(rows["연습동 베이커리"].competitors) == 4
    assert all(c.name != profile.name for c in rows["연습동 베이커리"].competitors)
    assert rows["연습동 베이커리"].competitors[0].category == "베이커리"
    assert [(b.total, b.mention_count) for b in report.blog_results] == [(42, 2), (7, 1)]
    text = summarize(report)
    assert "연습동 베이커리: 3위" in text
    assert "5위 밖 또는 없음" in text
    assert "블로그 검색 경로" in text
    assert "전체 42개 글, 상위 3개 중 가게명 언급 2개" in text
    assert text.endswith("개인화 없는 검색 기준이며 실제 화면과 다를 수 있음")


def test_normalized_names_address_fallback_and_name_priority(profile):
    class Client:
        def local(self, query, display=5):
            return [
                LocalItem("다른 표시명", roadAddress="가상시  연습로 3"),
                LocalItem("가상 연습 베이커리 별빛점"),
                LocalItem("<b>가상연습</b> 베이커리"),
            ]

        def blog(self, query, display=10):
            return BlogResult(0)

    query = [Query("가상 검색", "menu")]
    assert diagnose(profile, Client(), query).local_results[0].rank == 3
    renamed = replace(profile, name="옛 가게 이름")
    assert diagnose(renamed, Client(), query).local_results[0].rank == 1
    unknown = replace(renamed, road_address="")
    assert diagnose(unknown, Client(), query).local_results[0].rank is None


@pytest.mark.parametrize("road_address", ["가상시 연습로 12-3", "", "가상시 연습로 1-24"])
def test_address_fallback_does_not_confuse_building_numbers(profile, road_address):
    class Client:
        def local(self, query, display=5):
            return [LocalItem("다른 가게", roadAddress=road_address)]

        def blog(self, query, display=10):
            return BlogResult(0)

    profile = replace(profile, road_address="가상시 연습로 1-23")
    report = diagnose(profile, Client(), [Query("가상 검색", "menu")])
    assert report.local_results[0].rank is None


def test_cache_with_protocol_client_and_blog_mentions(profile):
    calls = Counter()

    class Client:
        def local(self, query, display=5):
            calls["local", query] += 1
            return []

        def blog(self, query, display=10):
            calls["blog", query] += 1
            return BlogResult(
                100,
                [
                    BlogItem(profile.name, profile.name),  # Count a post once.
                    BlogItem("후기", "<b>가상연습베이커리</b>의 메뉴"),
                    BlogItem("다른 가게 후기"),
                ],
            )

    queries = [Query(profile.name, "store_name"), Query(profile.name, "menu")]
    report = diagnose(profile, Client(), queries)
    assert all(count == 1 for count in calls.values())
    assert calls["local", profile.name] == calls["blog", profile.name] == 1
    assert [(b.total, b.mention_count) for b in report.blog_results] == [(100, 2), (100, 2)]
    # Caches belong to one diagnosis, rather than leaking results across stores/runs.
    diagnose(profile, Client(), queries)
    assert calls["local", profile.name] == 2


def test_empty_queries_and_no_menus(profile):
    report = diagnose(replace(profile, menus=[]), FixtureSearch(), queries=[])
    assert not report.local_results
    assert len(report.blog_results) == 1
    assert "확인된 검색어 없음" in summarize(report)


@pytest.mark.parametrize("path", ["outside.json", "data/../outside.json", "data", "/tmp/out.json"])
def test_cli_rejects_outside_data(tmp_path, monkeypatch, capsys, path):
    monkeypatch.chdir(tmp_path)
    assert visibility.main(["--name", "가상 가게", "--save", path]) == 1
    assert "data/ 아래" in capsys.readouterr().err
    assert not (tmp_path / "outside.json").exists()


def test_cli_rejects_symlink_escape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(NaverSearchError):
        visibility._save_path("data/escape/out.json")


def test_cli_rejects_symlink_data_root(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.chdir(workspace)
    with pytest.raises(NaverSearchError):
        visibility._save_path("data/out.json")


def test_cli_rejects_unresolvable_save_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir()
    Path("data/loop").symlink_to("loop", target_is_directory=True)
    assert visibility.main(["--name", "가상 가게", "--save", "data/loop/out.json"]) == 1
    assert capsys.readouterr().err == "검색 결과는 data/ 아래 파일에만 저장할 수 있습니다.\n"


def test_cli_save_and_missing_keys(tmp_path, monkeypatch, capsys, profile):
    monkeypatch.chdir(tmp_path)
    args = ["--name", profile.name, "--area", profile.area, "--category", profile.category]
    assert visibility.main(args) == 1
    output = capsys.readouterr()
    assert "인증 키" in output.err
    assert "Traceback" not in output.err
    monkeypatch.setattr(visibility, "ApiHubSearch", FixtureSearch)
    assert visibility.main([*args, "--menu", "꿀케이크", "--save", "data/search/result.json"]) == 0
    saved = json.loads(Path("data/search/result.json").read_text())
    assert saved["profile"]["name"] == profile.name
    assert saved["local_results"][0]["rank"] == 3
    assert "3위" in capsys.readouterr().out


def test_cli_api_and_save_errors_are_clean(tmp_path, monkeypatch, capsys):
    class Client:
        def local(self, *args, **kwargs):
            raise NaverSearchError("네이버 검색 API 요청에 실패했습니다.")

    monkeypatch.setattr(visibility, "ApiHubSearch", Client)
    assert visibility.main(["--name", "가상 가게"]) == 1
    assert capsys.readouterr().err == "네이버 검색 API 요청에 실패했습니다.\n"
    monkeypatch.setattr(visibility, "ApiHubSearch", FixtureSearch)
    monkeypatch.chdir(tmp_path)
    Path("data/existing").mkdir(parents=True)
    assert visibility.main(["--name", "가상 가게", "--save", "data/existing"]) == 1
    assert capsys.readouterr().err == "검색 결과 파일을 저장할 수 없습니다.\n"


def test_seed_store_has_neighborhood(tmp_path):
    profile = profile_from_store(DummyStore(tmp_path / "store.json").read())
    assert profile.area == "이태원동"
    assert {"area_category", "area_menu", "store_area"} <= {
        q.kind for q in candidate_queries(profile)
    }
