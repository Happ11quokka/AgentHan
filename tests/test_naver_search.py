import http.client
import io
import json
import os
import traceback
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from agenthan.tools import naver_search as search
from agenthan.tools.search_visibility import Query, StoreProfile, diagnose


@pytest.fixture(autouse=True)
def offline_by_default(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        return
    for key in ("NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)

    def no_network(*args, **kwargs):
        pytest.fail("Offline tests must not use the network")

    monkeypatch.setattr(search.urllib.request, "urlopen", no_network)


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_SECRET", "fake-client-secret")


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def test_headers_parameters_cleaning_and_cache(monkeypatch, credentials):
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        assert timeout == search.REQUEST_TIMEOUT
        return response(
            {
                "total": 17,
                "items": [
                    {
                        "title": "<b>가상</b> 빵집 &amp; 카페",
                        "roadAddress": "가상시 연습로 3",
                        "description": "<b>가상</b> 후기 &quot;빵&quot;",
                        "bloggername": "가상 손님",
                        "postdate": "20261108",
                    }
                ],
            }
        )

    monkeypatch.setattr(search.urllib.request, "urlopen", urlopen)
    client = search.ApiHubSearch()
    local = client.local("연습동 빵집")
    assert local[0].title == "가상 빵집 & 카페"
    assert local[0].roadAddress == "가상시 연습로 3"
    assert local[0].telephone == local[0].link == ""
    local.clear()
    assert len(client.local("연습동  빵집")) == 1
    blog = client.blog("가상 빵집")
    assert blog.total == 17
    assert blog.items[0].description == '가상 후기 "빵"'
    assert blog.items[0].bloggername == "가상 손님"
    assert blog.items[0].postdate == "20261108"
    assert client.blog("가상 빵집") == blog
    assert len(calls) == 2
    for call, kind, query, display in zip(
        calls, ("local", "blog"), ("연습동 빵집", "가상 빵집"), (5, 10), strict=True
    ):
        assert call.get_method() == "GET"
        headers = {k.lower(): v for k, v in call.header_items()}
        assert headers["x-ncp-apigw-api-key-id"] == "fake-client-id"
        assert headers["x-ncp-apigw-api-key"] == "fake-client-secret"
        url = urlsplit(call.full_url)
        assert f"{url.scheme}://{url.netloc}{url.path}" == f"{search.ENDPOINT}/{kind}"
        params = parse_qs(url.query)
        assert params["query"] == [query]
        assert params["display"] == [str(display)]
        assert params["start"] == ["1"]


@pytest.mark.parametrize("status", [429, 500, 503, 599, 400, 401, 403, 404])
def test_retries_and_sanitized_http_errors(monkeypatch, credentials, capsys, status):
    calls, sleeps = [], []

    def urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(
            "fake-client-id", status, "fake-client-secret", {}, io.BytesIO(b"private-server-body")
        )

    monkeypatch.setattr(search.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(search.time, "sleep", sleeps.append)
    with pytest.raises(search.NaverSearchError) as error:
        search.ApiHubSearch().local("가상 빵집")
    retryable = status == 429 or status >= 500
    assert len(calls) == (3 if retryable else 1)
    assert sleeps == ([0.25, 0.5] if retryable else [])
    trace = "".join(traceback.format_exception(error.value)) + repr(capsys.readouterr())
    for secret in ("fake-client-id", "fake-client-secret", "private-server-body"):
        assert secret not in trace
    assert error.value.__context__ is None


@pytest.mark.parametrize("status", [429, 500])
def test_retry_success(monkeypatch, credentials, status):
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        if len(calls) < 3:
            raise urllib.error.HTTPError("hidden", status, "hidden", {}, io.BytesIO())
        # total can be positive while items is empty; never infer rank from total.
        return response({"total": 5, "items": []})

    monkeypatch.setattr(search.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(search.time, "sleep", lambda _: None)
    assert search.ApiHubSearch().local("긴 가상 검색어") == []
    assert len(calls) == 3


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"items": None}, {"items": [None]}, {"items": [{}]}, {"items": [{"title": 1}]}],
)
def test_malformed_response(monkeypatch, credentials, payload):
    monkeypatch.setattr(search.urllib.request, "urlopen", lambda *a, **k: response(payload))
    with pytest.raises(search.NaverSearchError):
        search.ApiHubSearch().local("가상 빵집")


@pytest.mark.parametrize("failure", ["transport", "protocol", "json", "blog"])
def test_non_http_errors_are_sanitized(monkeypatch, credentials, failure):
    def urlopen(*args, **kwargs):
        if failure == "transport":
            raise urllib.error.URLError("fake-client-secret")
        if failure == "protocol":
            raise http.client.IncompleteRead(b"fake-client-secret")
        if failure == "json":
            return io.BytesIO(b"fake-client-secret")
        return response({"total": "fake-client-secret", "items": []})

    monkeypatch.setattr(search.urllib.request, "urlopen", urlopen)
    with pytest.raises(search.NaverSearchError) as error:
        search.ApiHubSearch().blog("가상 빵집")
    assert "fake-client-secret" not in "".join(traceback.format_exception(error.value))
    assert error.value.__context__ is None


@pytest.mark.parametrize("missing", ["NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET"])
def test_missing_key(monkeypatch, credentials, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(search.NaverSearchError, match="인증 키"):
        search.ApiHubSearch().local("가상 빵집")


@pytest.mark.parametrize("query,display", [("", 5), (" ", 5), ("가상", 6), ("가상", 0)])
def test_invalid_local_parameters(query, display):
    with pytest.raises(search.NaverSearchError):
        search.ApiHubSearch().local(query, display)


@pytest.mark.network
def test_live_naver_search():
    if not all(
        os.environ.get(key) for key in ("NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET")
    ):
        pytest.skip("네이버 검색 API 환경 키가 없습니다.")
    client = search.ApiHubSearch()
    items = client.local("스타벅스 강남")
    assert items
    assert all(item.title and item.roadAddress for item in items)
    first = items[0]
    profile = StoreProfile(first.title, "", "", [], [], first.roadAddress)
    report = diagnose(profile, client, [Query("스타벅스 강남", "store_name")])
    assert report.local_results[0].rank == 1
    assert client.blog("메도빅 케이크").total > 0
