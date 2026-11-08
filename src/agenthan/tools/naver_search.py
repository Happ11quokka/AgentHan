"""NAVER API HUB search and an interchangeable, fictional offline client."""

import html
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1"
FIXTURE_PATH = Path(__file__).with_name("search_fixture.json")
MAX_RETRIES = 2
REQUEST_TIMEOUT = 15
RETRY_DELAY = 0.25


class NaverSearchError(Exception):
    """A Korean error that contains no credentials or server response body."""


def clean_text(text: str) -> str:
    return re.sub(r"</?b\s*>", "", html.unescape(text), flags=re.IGNORECASE)


@dataclass(frozen=True)
class LocalItem:
    title: str
    category: str = ""
    address: str = ""
    roadAddress: str = ""
    mapx: str = ""
    mapy: str = ""
    link: str = ""
    telephone: str = ""


@dataclass(frozen=True)
class BlogItem:
    title: str
    description: str = ""
    bloggername: str = ""
    postdate: str = ""
    link: str = ""


@dataclass
class BlogResult:
    total: int
    items: list[BlogItem] = field(default_factory=list)


class SearchClient(Protocol):
    def local(self, query: str, display: int = 5) -> list[LocalItem]: ...

    def blog(self, query: str, display: int = 10) -> BlogResult: ...


def _items(payload: dict, kind: type[LocalItem] | type[BlogItem]) -> list:
    rows = payload["items"]
    if not isinstance(rows, list):
        raise ValueError
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("title"), str):
            raise ValueError
        values = {key: row.get(key, "") for key in kind.__dataclass_fields__}
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError
        values["title"] = clean_text(values["title"])
        if kind is BlogItem:
            values["description"] = clean_text(values["description"])
        result.append(kind(**values))
    return result


def _blog_result(payload: dict, display: int) -> BlogResult:
    total = payload["total"]
    if type(total) is not int or total < 0:
        raise ValueError
    return BlogResult(total, _items(payload, BlogItem)[:display])


def _validate(query: str, display: int, maximum: int) -> str:
    if not isinstance(query, str) or not query.strip():
        raise NaverSearchError("검색어를 입력해 주세요.")
    if type(display) is not int or not 1 <= display <= maximum:
        raise NaverSearchError("검색 결과 개수가 허용 범위를 벗어났습니다.")
    return " ".join(query.split())


class ApiHubSearch:
    """Read keys only from the environment; cache successful requests per instance."""

    def __init__(self):
        self._cache: dict[tuple[str, str, int], dict] = {}

    def _request(self, kind: str, query: str, display: int) -> dict:
        key = (kind, query, display)
        if key in self._cache:
            return self._cache[key]
        client_id = os.environ.get("NAVER_API_HUB_CLIENT_ID")
        client_secret = os.environ.get("NAVER_API_HUB_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise NaverSearchError("네이버 검색 API 환경 변수에 인증 키 두 개를 설정해 주세요.")
        for attempt in range(MAX_RETRIES + 1):
            retry = False
            failure = "네이버 검색 응답 또는 연결을 확인할 수 없습니다."
            try:
                params = urllib.parse.urlencode(
                    {
                        "query": query,
                        "display": display,
                        "start": 1,
                        "sort": "sim" if kind == "blog" else "random",
                    }
                )
                request = urllib.request.Request(
                    f"{ENDPOINT}/{kind}?{params}",
                    headers={
                        "X-NCP-APIGW-API-KEY-ID": client_id,
                        "X-NCP-APIGW-API-KEY": client_secret,
                    },
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ValueError
                # Validate before caching; an error response must never become a result.
                if kind == "local":
                    _items(payload, LocalItem)
                else:
                    _blog_result(payload, display)
                self._cache[key] = payload
                return payload
            except urllib.error.HTTPError as error:
                retry = error.code == 429 or 500 <= error.code <= 599
                error.close()
                failure = "네이버 검색 API 요청에 실패했습니다."
            except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
                pass
            if retry and attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            # Outside except: traceback context cannot expose urllib errors or bodies.
            raise NaverSearchError(failure)
        raise NaverSearchError("네이버 검색 API 요청에 실패했습니다.")  # pragma: no cover

    def local(self, query: str, display: int = 5) -> list[LocalItem]:
        query = _validate(query, display, 5)
        return _items(self._request("local", query, display), LocalItem)[:display]

    def blog(self, query: str, display: int = 10) -> BlogResult:
        query = _validate(query, display, 100)
        return _blog_result(self._request("blog", query, display), display)


class FixtureSearch:
    """Search only the fictional data shipped alongside this module."""

    def __init__(self, path: str | Path = FIXTURE_PATH):
        self._data = json.loads(Path(path).read_text(encoding="utf-8"))

    def local(self, query: str, display: int = 5) -> list[LocalItem]:
        query = _validate(query, display, 5)
        return _items({"items": self._data["local"].get(query, [])}, LocalItem)[:display]

    def blog(self, query: str, display: int = 10) -> BlogResult:
        query = _validate(query, display, 100)
        payload = self._data["blog"].get(query, {"total": 0, "items": []})
        return _blog_result(payload, display)
