"""상처 되는 표현 거르기 (PRD A-2: "상처 되는 표현은 걸러 내고 … 원문은 펼쳐 볼 수 있다").

사장님에게 보여 주는 리뷰 인용(대표 리뷰·미답글 목록·알림·초안의 리뷰)은 모두 soften_quote()를
거친다. 불만 내용은 남기고 표현만 거른다: 실제 문제(맛·응대·위생)를 숨기면 안 된다.

- 강조 욕설('존나', 'ㅈㄴ'): 그 단어만 지운다 → '존나 짜요' → '짜요'
- 욕설·인신공격·저주('사장 인성 쓰레기', '망해라'): 그 문장을 뺀다
- 남는 문장이 없으면 원문 대신 '거친 표현이 많아 원문을 가린 불만 리뷰'로 보여 준다
원문은 사장님이 '원문 보여줘'라고 할 때만 시스템이 보여 준다 (ReviewToolbox.hidden_reviews).
HCX에는 거른 문장만 넘긴다 (요약·답변에 원문이 새지 않게).

규칙 사전이라 놓치는 표현이 있다. 오탐을 줄이려고 '미친 맛'(칭찬), '씹는 맛', '시발점',
'벌레'(위생 고위험 리뷰는 replies.HIGH_RISK가 따로 본다)는 거르지 않는다.
"""

from __future__ import annotations

import re

# 강조로 붙는 욕설: 단어만 지우고 문장은 남긴다
INTENSIFIER = re.compile(r"(존나|졸라|존내|ㅈㄴ|ㅈㄹ나게|씨발|시발(?!점)|ㅅㅂ|ㅆㅂ)\s?")

# 이 표현이 들어간 문장은 뺀다
SWEAR = re.compile(
    r"씨발|시발(?!점)|씨바|ㅅㅂ|ㅆㅂ|썅|씹(새|놈|년|창|할)|좆|ㅈ같|ㅈ까|개새|새끼(?!손)|"
    r"병신|븅신|ㅂㅅ|지랄|염병|미친\s?(놈|년|새끼|인간|것들|거\s?아냐)|미쳤냐|"
    r"꺼져|닥쳐|엿\s?(먹|이나)"
)
ATTACK = re.compile(
    r"(사장|주인|직원|알바|아줌마|아저씨)\S{0,4}\s?(싸가지|돼지|못생|양아치|사기꾼|쓰레기|"
    r"수준\s?(하고|이|봐)|얼굴|머리\s?나쁘)|"
    r"인성\s?(쓰레기|최악|파탄|글러|문제|봐라|보소)|싸가지|사기꾼|양아치|도둑놈|"
    r"쓰레기\s?(같은|집|가게|들)|거지\s?같"
)
CURSE = re.compile(
    r"망해\s?(라|버려|버리|야\s?돼|야\s?함)|망하(길|세요|기를|라)|망했으면|망하면\s?좋겠|"
    r"폐업\s?(해라|하세요|하길|각)|장사\s?(접어|때려\s?치)|문\s?닫아(라|버려)|"
    r"죽어(라|버려)|뒤져(라)?|뒈져"
)
_KINDS = (("욕설", SWEAR), ("인신공격", ATTACK), ("저주", CURSE))
_ALL = (("욕설", INTENSIFIER), *_KINDS)
_SENT = re.compile(r"[^.!?…~\n]+[.!?…~\n]*")

HIDDEN_NOTE = "거친 표현 가림"
HIDDEN_ALL = "거친 표현이 많아 원문을 가린 불만 리뷰"


def harsh_of(text: str) -> dict | None:
    """→ {"kinds": ["욕설", ...]} 또는 None. 어떤 단어였는지는 돌려주지 않는다."""
    kinds = list(dict.fromkeys(k for k, rx in _ALL if rx.search(text)))
    return {"kinds": kinds} if kinds else None


def _clean_sentence(s: str) -> str | None:
    s = INTENSIFIER.sub("", s)
    if any(rx.search(s) for _, rx in _KINDS):
        return None
    return s.strip() or None


def _cut(text: str, limit: int) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def soften_quote(text: str, limit: int = 60, aspect: str | None = None) -> tuple[str, bool]:
    """→ (보여 줄 인용, 걸렀는지). 거친 표현이 없으면 원문을 limit 글자로 자른 것."""
    if not harsh_of(text):
        return _cut(text, limit), False
    kept = [c for s in _SENT.findall(text) if (c := _clean_sentence(s))]
    if kept:
        return _cut(" ".join(kept), limit) + f" ({HIDDEN_NOTE})", True
    about = f" · {aspect}" if aspect else ""
    return f"[{HIDDEN_ALL}{about}]", True


def softened_ids(result) -> list[str]:
    """도구 결과 안에서 인용을 거른 리뷰 id (중복 없이, 나온 순서대로)."""
    out: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("softened") and x.get("review_id") and x["review_id"] not in out:
                out.append(x["review_id"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(result)
    return out
