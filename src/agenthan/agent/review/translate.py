"""사장님 언어 ↔ 한국어 번역. 비서는 안에서 한국어로만 생각하고, 입·출력 경계에서만 번역한다.

- 1순위 Papago(NCP Papago Translation, PRD §0 '네이버 클라우드 모델만'). 한국어 ↔ 12개 언어
- Papago 키가 없거나 지원하지 않는 언어(몽골어 등)면 HCX로 번역한다 (PRD §2.2)
- Papago는 ko→vi 등에서 용어집을 쓸 수 없어서, 메뉴 이름은 번역 전에 사장님 언어 표기로 바꿔 넣는다
- 출력 번역에서 리뷰 원문("…")과 게시할 답글(> …)은 한국어 그대로 두고 번역을 덧붙인다
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

PAPAGO_URL = "https://papago.apigw.ntruss.com/nmt/v1/translation"
# 우리 언어 코드 → Papago 코드 (한국어에서 갈 수 있는 언어만)
PAPAGO_LANGS = {
    "ko": "ko",
    "en": "en",
    "ja": "ja",
    "zh": "zh-CN",
    "zh-CN": "zh-CN",
    "zh-TW": "zh-TW",
    "vi": "vi",
    "th": "th",
    "id": "id",
    "fr": "fr",
    "es": "es",
    "ru": "ru",
    "de": "de",
    "it": "it",
}
LANG_EN = {
    "ko": "Korean",
    "en": "English",
    "ru": "Russian",
    "vi": "Vietnamese",
    "zh": "Chinese",
    "ja": "Japanese",
    "mn": "Mongolian",
}
HANGUL = re.compile(r"[가-힣]")
MAX_CHARS = 4500  # Papago 요청당 5,000자 제한에 여유
PER_ITEM_MAX = 8  # 묶음 번역이 어긋났을 때 한 건씩 다시 보낼 최대 개수 (호출 한도 보호)

Transport = Callable[[str, dict, bytes, float], tuple[int, dict, bytes]]


class TranslateError(RuntimeError):
    """화면에 그대로 보여도 되는 오류 (키·서버 본문 없음)."""


def _urllib_transport(url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (고정 호스트)
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


class PapagoTranslator:
    """NCP Papago Translation. 키는 PAPAGO_CLIENT_ID / PAPAGO_CLIENT_SECRET (Application 정보)."""

    name = "papago"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        transport: Transport | None = None,
        timeout: float = 20.0,
        max_retries: int = 2,
    ):
        self._id = client_id if client_id is not None else os.getenv("PAPAGO_CLIENT_ID", "")
        self._secret = (
            client_secret if client_secret is not None else os.getenv("PAPAGO_CLIENT_SECRET", "")
        )
        self._send = transport or _urllib_transport
        self.timeout, self.max_retries = timeout, max_retries

    def __repr__(self) -> str:
        return "PapagoTranslator()"

    @property
    def available(self) -> bool:
        return bool(self._id and self._secret)

    def supports(self, source: str, target: str) -> bool:
        return source in PAPAGO_LANGS and target in PAPAGO_LANGS and "ko" in (source, target)

    def _one(self, text: str, source: str, target: str) -> str:
        headers = {
            "X-NCP-APIGW-API-KEY-ID": self._id,
            "X-NCP-APIGW-API-KEY": self._secret,
            "Content-Type": "application/json",
        }
        body = json.dumps(
            {"source": PAPAGO_LANGS[source], "target": PAPAGO_LANGS[target], "text": text},
            ensure_ascii=False,
        ).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            try:
                status, hdrs, raw = self._send(PAPAGO_URL, headers, body, self.timeout)
            except OSError:
                raise TranslateError("Papago 연결 오류") from None
            if (status == 429 or 500 <= status < 600) and attempt < self.max_retries:
                time.sleep(2**attempt)
                continue
            if not 200 <= status < 300:
                raise TranslateError(f"Papago HTTP {status}")
            try:
                return json.loads(raw.decode("utf-8"))["message"]["result"]["translatedText"]
            except (ValueError, KeyError, TypeError):
                raise TranslateError("Papago 응답 형식 오류") from None
        raise TranslateError("Papago 재시도 초과")  # pragma: no cover

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        """줄바꿈으로 묶어 한 번에 보내고, 줄 수가 어긋나면 한 건씩 다시 보낸다."""
        out: list[str] = []
        batch: list[str] = []

        def flush():
            if not batch:
                return
            joined = self._one("\n".join(batch), source, target).split("\n")
            if len(joined) == len(batch):
                out.extend(s.strip() for s in joined)
            else:
                out.extend(self._one(t, source, target).strip() for t in batch)
            batch.clear()

        for t in texts:
            if batch and sum(len(x) + 1 for x in batch) + len(t) > MAX_CHARS:
                flush()
            batch.append(t.replace("\n", " "))
        flush()
        return out


class HCXTranslator:
    """Papago를 못 쓸 때의 대체 번역 (HCX 채팅 한 번, JSON 목록으로 받는다)."""

    name = "hcx"

    def __init__(self, client, model: str = "HCX-005"):
        self.client, self.model = client, model

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        src, tgt = LANG_EN.get(source, source), LANG_EN.get(target, target)
        listing = "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
        try:
            msg = self._chat(src, tgt, listing)
        except RuntimeError as e:  # ClovaError(429 등)도 번역 실패로 다룬다
            raise TranslateError(f"HCX 번역 호출 실패: {e}") from None
        raw = msg["content"] or ""
        return self._parse(raw, texts)

    def _chat(self, src: str, tgt: str, listing: str) -> dict:
        return self.client.chat(
            self.model,
            [
                {
                    "role": "system",
                    "content": f"Translate each {src} line into {tgt}, literally and briefly. "
                    "Keep names, numbers and dates. Do not add anything. "
                    'Answer only JSON: {"t": ["translation 0", "translation 1", ...]}',
                },
                {"role": "user", "content": listing},
            ],
            temperature=0,
            maxTokens=2048,
        )

    @staticmethod
    def _parse(raw: str, texts: list[str]) -> list[str]:
        try:
            out = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])["t"]
        except (ValueError, KeyError, TypeError):
            # 한국어로 옮길 때는 JSON 대신 '[0] 번역' 줄로 답하는 경우가 있다 (2026-11-08 실측)
            out = [m.group(1).strip() for m in re.finditer(r"^\s*\[\d+\]\s*(.+)$", raw, re.M)]
            if not out and len(texts) == 1 and raw.strip():
                out = [re.sub(r"^\s*\[[^\]]{1,3}\]\s*", "", raw.strip())]
        if isinstance(out, list) and len(texts) == 1 and len(out) > 1:
            out = [" ".join(map(str, out))]  # 한 문장을 여러 줄로 나눠 답한 경우
        if not isinstance(out, list) or len(out) != len(texts):
            raise TranslateError("HCX 번역 응답 형식 오류")
        # '[0] …', '[t] …'처럼 번호를 붙여 돌려주는 경우가 있어 떼어 낸다
        return [re.sub(r"^\s*\[[^\]]{1,3}\]\s*", "", str(x)) for x in out]


class Translator:
    """Papago를 먼저 쓰고, 키가 없거나 지원하지 않는 언어·오류면 HCX로 번역한다."""

    def __init__(self, papago: PapagoTranslator | None = None, fallback=None):
        self.papago, self.fallback = papago, fallback
        self.last_engine: str | None = None
        self._cache: dict[tuple[str, str, str], str] = {}

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if source == target or not texts:
            return list(texts)
        todo = [t for t in dict.fromkeys(texts) if (t, source, target) not in self._cache]
        if todo:
            engines = []
            if self.papago and self.papago.available and self.papago.supports(source, target):
                engines.append(self.papago)
            if self.fallback:
                engines.append(self.fallback)
            err = None
            for eng in engines:
                try:
                    done = eng.translate(todo, source, target)
                except TranslateError as e:
                    err = e
                    if len(todo) == 1 or len(todo) > PER_ITEM_MAX:
                        continue
                    done = []  # 묶음이 어긋나면 같은 번역기로 한 건씩
                    for x in todo:
                        try:
                            done.append(eng.translate([x], source, target)[0])
                        except TranslateError:
                            done.append(None)
                    if all(d is None for d in done):
                        continue
                    # 끝내 못 옮긴 조각만 원문으로 둔다 (출력 전체를 한국어로 되돌리지 않는다)
                    done = [x if d is None else d for x, d in zip(todo, done, strict=True)]
                self.last_engine = eng.name
                break
            else:
                raise err or TranslateError("쓸 수 있는 번역기가 없습니다")
            for t, d in zip(todo, done, strict=True):
                self._cache[(t, source, target)] = d
        return [self._cache[(t, source, target)] for t in texts]


def make_translator(clova_client=None, model: str = "HCX-005") -> Translator:
    fallback = HCXTranslator(clova_client, model) if clova_client is not None else None
    return Translator(PapagoTranslator(), fallback)


# ---------------------------------------------------------------- 메뉴 이름 보호
def _swap_names(text: str, names: dict[str, str]) -> str:
    """names의 키를 값으로 바꾼다 (긴 이름 먼저, 대소문자 무시)."""
    for k in sorted(names, key=len, reverse=True):
        text = re.sub(re.escape(k), lambda _m, v=names[k]: v, text, flags=re.I)
    return text


def to_korean(text: str, source: str, tr: Translator, names: dict[str, str] | None = None):
    """사장님 입력 → 한국어. 이미 한국어면 그대로. names: {사장님 언어 메뉴 표기: 한국어 이름}."""
    foreign = re.search(r"[A-Za-zА-Яа-яЁёÀ-ỹ\u4e00-\u9fff]{2,}", text)
    if source == "ko" or not text.strip() or (HANGUL.search(text) and not foreign):
        return text
    # 'strawberry mochi'처럼 사장님 표기를 먼저 한국어 메뉴명으로 바꿔 두면 메뉴를 놓치지 않는다
    prepared = _swap_names(text, names or {})
    return tr.translate([prepared], source, "ko")[0]


# ---------------------------------------------------------------- 출력 번역
QUOTE = re.compile(r'"([^"\n]*[가-힣][^"\n]*)"')
# 번역기로 보내지 않는 기호: 마크다운 굵게, 괄호, 가운뎃점, 콜론 → 그 사이의 한국어 구절만 보낸다
_MARK = re.compile(r"(\*\*|·|:)")  # 괄호는 문장 안에 있을 때가 많아 자르지 않는다
_LEAD = re.compile(r"^([^\w]*)(.*?)(\s*)$", re.S)


def localize(text: str, target: str, tr: Translator, glossary: dict[str, str] | None = None) -> str:
    """한국어 답변 → 사장님 언어. 줄 단위로 번역해 마크다운 모양을 지킨다.
    - 따옴표 안 한국어(리뷰 원문)는 그대로 두고 뒤에 (번역)을 붙인다
    - '> '로 시작하는 줄(게시할 한국어 답글)은 그대로 두고 아래에 '> (번역)' 줄을 붙인다
    - 한글이 없는 조각(날짜·이모지·기호)은 보내지 않는다
    """
    if target == "ko" or not text:
        return text
    gloss = glossary or {}
    lines = text.split("\n")
    pieces: list[str] = []  # 번역할 한국어 조각

    def ask(s: str) -> int:
        pieces.append(_swap_names(s, gloss))
        return len(pieces) - 1

    plan = []  # 줄마다: ("reply", 원문, 번호) 또는 ("mixed", [(종류, 값)])
    for line in lines:
        if line.startswith("> ") and HANGUL.search(line):
            plan.append(("reply", line, ask(line[2:])))
            continue
        parts, pos = [], 0
        for m in QUOTE.finditer(line):
            parts.append(("text", line[pos : m.start()]))
            parts.append(("quote", m.group(1)))
            pos = m.end()
        parts.append(("text", line[pos:]))
        items = []
        for kind, val in parts:
            if not HANGUL.search(val):
                items.append(("keep", val))
            elif kind == "quote":
                items.append(("quote", val, ask(val)))
            else:
                for chunk in _MARK.split(val):
                    if not HANGUL.search(chunk):
                        items.append(("keep", chunk))
                        continue
                    pre, core, post = _LEAD.match(chunk).groups()  # 앞 기호·이모지, 뒤 공백
                    items.append(("text", pre, ask(core), post))
        plan.append(("mixed", items))

    done = tr.translate(pieces, "ko", target) if pieces else []
    out = []
    for p in plan:
        if p[0] == "reply":
            out.append(p[1])
            out.append(f"> ({done[p[2]]})")
            continue
        s = ""
        for it in p[1]:
            if it[0] == "keep":
                s += it[1]
            elif it[0] == "quote":
                s += f'"{it[1]}" ({done[it[2]]})'
            else:
                s += it[1] + done[it[2]] + it[3]
        out.append(s)
    return "\n".join(out)
