"""번역 경계: Papago 요청 모양, 묶음·대체 규칙, 출력 줄 번역 (키·네트워크 불필요)."""

import json

import pytest

from agenthan.agent.review.translate import (
    PapagoTranslator,
    TranslateError,
    Translator,
    localize,
    to_korean,
)


class Transport:
    def __init__(self, fn):
        self.fn, self.sent = fn, []

    def __call__(self, url, headers, body, timeout):
        req = json.loads(body)
        self.sent.append({"url": url, "headers": headers, "body": req})
        status, text = self.fn(req)
        data = {"message": {"result": {"translatedText": text}}}
        return status, {}, json.dumps(data).encode()


def papago(fn):
    t = Transport(fn)
    return PapagoTranslator("id-x", "secret-x", transport=t), t


def test_papago_request_and_batching():
    p, t = papago(lambda r: (200, r["text"].replace("안녕", "hello")))
    assert p.translate(["안녕 1", "안녕 2"], "ko", "en") == ["hello 1", "hello 2"]
    sent = t.sent[0]
    assert sent["url"].endswith("/nmt/v1/translation") and len(t.sent) == 1  # 줄바꿈으로 묶어 1번
    assert sent["headers"]["X-NCP-APIGW-API-KEY-ID"] == "id-x"
    assert sent["body"] == {"source": "ko", "target": "en", "text": "안녕 1\n안녕 2"}
    assert "secret-x" not in repr(p)


def test_papago_line_mismatch_falls_back_to_one_by_one():
    p, t = papago(lambda r: (200, "합쳐진 한 줄" if "\n" in r["text"] else f"T:{r['text']}"))
    assert p.translate(["a", "b"], "ko", "en") == ["T:a", "T:b"]
    assert len(t.sent) == 3


def test_papago_language_codes():
    p, t = papago(lambda r: (200, "x"))
    p.translate(["딸기"], "ko", "zh")
    assert t.sent[0]["body"]["target"] == "zh-CN"
    assert not p.supports("ko", "mn")  # 몽골어는 Papago에 없다


class Fallback:
    name = "hcx"

    def __init__(self):
        self.calls = 0

    def translate(self, texts, source, target):
        self.calls += 1
        return [f"hcx:{t}" for t in texts]


def test_translator_uses_fallback_when_needed():
    fb = Fallback()
    no_key = Translator(PapagoTranslator("", ""), fb)
    assert no_key.translate(["안녕"], "ko", "en") == ["hcx:안녕"] and no_key.last_engine == "hcx"
    p, _ = papago(lambda r: (200, "hello"))
    tr = Translator(p, fb)
    assert tr.translate(["안녕"], "ko", "mn") == ["hcx:안녕"]  # 지원 안 하는 언어
    assert tr.translate(["안녕"], "ko", "en") == ["hello"] and tr.last_engine == "papago"
    broken, _ = papago(lambda r: (500, ""))
    broken.max_retries = 0
    assert Translator(broken, fb).translate(["안녕"], "ko", "en") == ["hcx:안녕"]  # 오류면 대체
    with pytest.raises(TranslateError):
        Translator(broken, None).translate(["안녕"], "ko", "en")


def test_translator_caches():
    fb = Fallback()
    tr = Translator(None, fb)
    tr.translate(["a", "a"], "ko", "en")
    tr.translate(["a"], "ko", "en")
    assert fb.calls == 1


class Tag:
    last_engine = "fake"

    def translate(self, texts, source, target):
        return [f"<{target}> {t}" for t in texts]


def test_to_korean():
    assert to_korean("딸기모찌 어때?", "en", Tag()) == "딸기모찌 어때?"  # 이미 한국어
    assert to_korean("How is strawberry mochi?", "en", Tag(), {"strawberry mochi": "딸기모찌"}) == (
        "<ko> How is 딸기모찌?"
    )  # 사장님 표기 → 한국어 메뉴명으로 바꿔 보낸다


def test_localize_keeps_quotes_and_replies():
    ko = "\n".join(
        [
            "총 3건 중 1건이 불만이에요.",
            "",
            "**대표 리뷰**",
            '- "맛이 없어요..." · 2026-10-25',
            "> 불편을 드려 죄송합니다. 딸기모찌 맛을 확인하겠습니다.",
            "2026-10-25",
        ]
    )
    out = localize(ko, "en", Tag(), {"딸기모찌": "strawberry mochi"}).splitlines()
    assert out[0] == "<en> 총 3건 중 1건이 불만이에요."
    assert out[1] == "" and out[2] == "**<en> 대표 리뷰**"  # 마크다운 기호는 그대로
    assert out[3] == '- "맛이 없어요..." (<en> 맛이 없어요...) · 2026-10-25'  # 원문 + 번역
    assert out[4] == "> 불편을 드려 죄송합니다. 딸기모찌 맛을 확인하겠습니다."  # 게시할 답글 원문
    assert out[5] == "> (<en> 불편을 드려 죄송합니다. strawberry mochi 맛을 확인하겠습니다.)"
    assert out[6] == "2026-10-25"  # 한글 없는 줄은 보내지 않는다
    assert localize(ko, "ko", Tag()) == ko


def test_hcx_translator_accepts_numbered_lines():
    from agenthan.agent.review.translate import HCXTranslator

    class C:
        def __init__(self, content):
            self.content = content

        def chat(self, model, messages, **kw):
            return {"role": "assistant", "content": self.content}

    assert HCXTranslator(C("[0] 딸기모찌 어때?")).translate(["How?"], "en", "ko") == [
        "딸기모찌 어때?"
    ]
    two = HCXTranslator(C('{"t": ["a", "b"]}')).translate(["가", "나"], "ko", "en")
    assert two == ["a", "b"]
    with pytest.raises(TranslateError):
        HCXTranslator(C('{"t": ["a"]}')).translate(["가", "나"], "ko", "en")


def test_hcx_translator_turns_api_errors_into_translate_errors():
    from agenthan.agent.review.clova import ClovaError
    from agenthan.agent.review.translate import HCXTranslator

    class Busy:
        def chat(self, model, messages, **kw):
            raise ClovaError("CLOVA HTTP 429 (x)")

    with pytest.raises(TranslateError):
        HCXTranslator(Busy()).translate(["가"], "ko", "en")


def test_localize_keeps_parenthesized_sentence_together():
    out = localize("최근 30일(2026-10-08 ~ 2026-11-07)로 넓혀 봤어요.", "en", Tag())
    assert out == "<en> 최근 30일(2026-10-08 ~ 2026-11-07)로 넓혀 봤어요."
