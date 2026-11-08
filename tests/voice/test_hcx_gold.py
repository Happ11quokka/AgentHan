"""L1 NLU 골드셋 (spec §9, 티켓 07). HCX 응답은 녹화본(cassette)을 재생한다.

키·네트워크 없이 돈다. 녹화 다시 하기(실제 호출 30번, 키 필요):

    uv run pytest tests/voice/test_hcx_gold.py --record-mode=rewrite

프롬프트나 도구 스키마를 바꾸면 요청 본문이 달라져 재생이 실패한다. 그때 다시 녹화한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.clova import ClovaClient, load_api_key
from agenthan.voice.nlu.hcx import HcxNlu

GOLD = Path(__file__).with_name("nlu_gold.json")
CASSETTES = Path(__file__).with_name("cassettes")
# 녹화 당시 잰 값보다 조금 낮게 둔다. 프롬프트를 고쳐 다시 녹화하면 함께 본다.
MIN_TYPE_ACCURACY = 0.8


# 녹화하지 않을 요청 헤더. 키, 요청 id, 세션 쿠키와 XSRF 토큰.
FILTERED_REQUEST_HEADERS = (
    "authorization",
    "x-ncp-clovastudio-request-id",
    "cookie",
    "set-cookie",
    "x-xsrf-token",
    "x-csrf-token",
)
# 응답 헤더는 이것만 남긴다 (Set-Cookie 등은 모두 버린다).
KEPT_RESPONSE_HEADERS = frozenset({"content-type"})


def _vcr_config() -> dict[str, Any]:
    return {
        "filter_headers": list(FILTERED_REQUEST_HEADERS),
        "match_on": ["method", "scheme", "host", "path", "body"],
        "decode_compressed_response": True,
        "before_record_response": _drop_response_headers,
    }


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return _vcr_config()


def _drop_response_headers(response: dict[str, Any]) -> dict[str, Any]:
    response["headers"] = {
        k: v for k, v in response["headers"].items() if k.lower() in KEPT_RESPONSE_HEADERS
    }
    return response


def load_gold() -> dict[str, Any]:
    return json.loads(GOLD.read_text(encoding="utf-8"))


def context(item: dict[str, Any], menu_names: list[str]) -> NluContext:
    ctx = item.get("ctx", {})
    return NluContext(
        menu_names=tuple(menu_names),
        cart_summary=ctx.get("cart_summary", ""),
        last_question=ctx.get("last_question", ""),
        state=ctx.get("state", "ORDERING"),
    )


def act_types(acts: list[Act]) -> list[str]:
    """엔진 `_parse`처럼 뒤에 내용이 있으면 맨 앞의 yes/no를 뗀다."""
    types = [a.type for a in acts]
    if any(t not in ("yes", "no") for t in types):
        while types and types[0] in ("yes", "no"):
            types = types[1:]
    return types


def types_ok(item: dict[str, Any], acts: list[Act]) -> bool:
    got = act_types(acts)
    return got == [a["type"] for a in item["expect"]] or got in item.get("also_ok", [])


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def slots_ok(item: dict[str, Any], acts: list[Act]) -> bool:
    """타입이 맞은 항목에서 menu_text(한쪽이 다른 쪽을 포함)와 qty가 맞는지."""
    got = [a for a in acts if a.type not in ("yes", "no")] or acts
    if len(got) != len(item["expect"]):
        return False
    for want, act in zip(item["expect"], got, strict=True):
        if "menu_text" in want:
            w, g = _norm(want["menu_text"]), _norm(act.menu_text)
            if not g or (w not in g and g not in w):
                return False
        if "qty" in want and act.qty != want["qty"]:
            return False
    return True


def score(gold: dict[str, Any], parse) -> list[dict[str, Any]]:
    rows = []
    for item in gold["items"]:
        acts = parse(item["text"], context(item, gold["menu_names"]))
        ok = types_ok(item, acts)
        rows.append({
            "id": item["id"],
            "text": item["text"],
            "got": act_types(acts),
            "acts": acts,
            "types_ok": ok,
            "slots_ok": ok and slots_ok(item, acts),
        })
    return rows


@pytest.mark.vcr
def test_hcx_gold_set_type_accuracy() -> None:
    gold = load_gold()
    nlu = HcxNlu(ClovaClient(load_api_key() or "replay-only-dummy-key"))
    rows = score(gold, nlu.parse)

    n = len(rows)
    type_acc = sum(r["types_ok"] for r in rows) / n
    slot_acc = sum(r["slots_ok"] for r in rows) / n
    print(f"\nHCX gold: act type {type_acc:.0%} ({sum(r['types_ok'] for r in rows)}/{n}), "
          f"type+menu_text+qty {slot_acc:.0%}")
    for r in rows:
        if not r["slots_ok"]:
            print(f"  {r['id']} {r['text']!r} → {r['got']} {r['acts']}")
    assert n == 30
    assert type_acc >= MIN_TYPE_ACCURACY


def test_gold_set_shape() -> None:
    gold = load_gold()
    items = gold["items"]
    assert len(items) == 30
    assert len({i["id"] for i in items}) == 30
    multi = [i for i in items if len(i["expect"]) > 1]
    assert len(multi) >= 5  # 다중 act 확인용


def test_gold_scorer_strips_leading_no_like_engine() -> None:
    item = {"expect": [{"type": "change_qty", "menu_text": "아메리카노", "qty": 2}]}
    acts = [Act("no"), Act("change_qty", menu_text="아메리카노", qty=2)]
    assert types_ok(item, acts)
    assert slots_ok(item, acts)
    assert not slots_ok(item, [Act("change_qty", menu_text="아메리카노", qty=3)])


def test_vcr_filters_cookies_and_session_headers() -> None:
    filtered = {h.lower() for h in _vcr_config()["filter_headers"]}
    assert {"authorization", "cookie", "set-cookie"} <= filtered
    response = {
        "headers": {
            "Content-Type": ["application/json"],
            "Set-Cookie": ["JSESSIONID=abc; Path=/"],
            "X-XSRF-TOKEN": ["t"],
        }
    }
    assert _drop_response_headers(response)["headers"] == {"Content-Type": ["application/json"]}


def test_cassettes_hold_no_secret() -> None:
    files = list(CASSETTES.rglob("*.yaml"))
    assert files, "녹화본이 없어요. --record-mode=rewrite로 녹화하세요"
    for f in files:
        text = f.read_text(encoding="utf-8")
        lower = text.lower()
        assert "Bearer" not in text
        assert not re.search(r"nv-[0-9a-f]{8,}", text)
        assert "authorization" not in lower
        # 세션 쿠키·XSRF 토큰 (httpx가 Set-Cookie를 받아 다음 요청에 돌려보낸다)
        assert "cookie" not in lower  # cookie, set-cookie 둘 다
        assert "jsessionid" not in lower
        assert "xsrf" not in lower
        assert "csrf" not in lower
