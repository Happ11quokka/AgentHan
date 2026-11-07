"""리뷰를 절(clause) 단위로 분절한다.

조사 데이터 분석 결과, 네이버 하이라이트는 어절 경계에서 시작·끝나고
끝은 대부분 문장 종결(~요. ~니다)이나 연결어미(~하고 ~해서 ~는데 ~지만)였다.
그래서 같은 규칙으로 잘라 절마다 특징을 붙이고, 이웃한 같은 특징 절은 하나의 구간으로 합친다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 어절이 이 어미로 끝나면 그 뒤에서 절을 끊는다
_CONNECTIVE = re.compile(
    r"(고|구|서|는데|은데|ㄴ데|던데|인데|지만|며|면서|니까|으니|는지|아서|어서|해서|셔서|라서|"
    r"하고|하며|든지|거나|도록)$"
)
# 문장 종결로 보는 꼬리 (문장부호·이모티콘·자모 반복 포함)
_SENT_END_PUNCT = re.compile(r"[.!?~^♡♥❤️👍🙌😊😍🥰ㅎㅋㅠㅜ]+$")
_SENT_END_WORD = re.compile(
    r"(요|다|죠|네|음|임|함|굿|용|당|욤|여|최고|강추|추천|구요|고요|니당|어용|아용)$"
)
# 어절: 띄어쓰기 없이 붙은 '좋아요~아름다운', '말았아요,,,너무'도 부호 뒤에서 나눈다
_EOJEOL = re.compile(r"[^\s.!?~,]+[.!?~,^]*|[.!?~,^]+")


@dataclass
class Clause:
    start: int
    end: int
    text: str


def _strip_tail(word: str) -> str:
    return re.sub(r"[^\w가-힣]+$", "", word)


def split_clauses(text: str) -> list[Clause]:
    clauses: list[Clause] = []
    cur_start: int | None = None
    cur_end = 0
    prev_end = 0
    for m in _EOJEOL.finditer(text):
        s, e, w = m.start(), m.end(), m.group()
        # 줄바꿈이 끼어 있으면 앞 절을 닫는다
        if cur_start is not None and "\n" in text[prev_end:s]:
            clauses.append(Clause(cur_start, cur_end, text[cur_start:cur_end]))
            cur_start = None
        if cur_start is None:
            cur_start = s
        cur_end = e
        prev_end = e
        core = _strip_tail(w)
        boundary = (
            bool(_SENT_END_PUNCT.search(w))
            or w.endswith(",")
            or bool(_CONNECTIVE.search(core))
            or bool(_SENT_END_WORD.search(core))
            and len(core) >= 2  # 부호 없이 끝나는 '좋아요 빈 재료…'
        )
        if boundary:
            clauses.append(Clause(cur_start, cur_end, text[cur_start:cur_end]))
            cur_start = None
    if cur_start is not None:
        clauses.append(Clause(cur_start, cur_end, text[cur_start:cur_end]))
    return clauses


def merge_adjacent(clauses: list[Clause], flags: list[bool], text: str) -> list[tuple[int, int]]:
    """flag가 켜진 이웃 절들을 하나의 (start, end) 구간으로 합친다."""
    spans: list[tuple[int, int]] = []
    cur: list[int] | None = None
    for c, f in zip(clauses, flags, strict=False):
        if f:
            if cur is None:
                cur = [c.start, c.end]
            else:
                cur[1] = c.end
        elif cur is not None:
            spans.append((cur[0], cur[1]))
            cur = None
    if cur is not None:
        spans.append((cur[0], cur[1]))
    return spans
