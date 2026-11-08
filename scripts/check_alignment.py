"""분절기가 네이버 하이라이트 경계를 얼마나 재현하는지 측정 (끝 문장부호는 무시)."""

import re

from agenthan.tools.review_clf import dataset, segment

P = re.compile(r"[\s.!?~,^ㅎㅋㅠㅜ]+$")


def core_end(t, s, e):
    m = P.search(t[s:e])
    return s + (m.start() if m else e - s)


rows = dataset.load("data/reviews.jsonl")
n = a = b = ab = 0
for r in rows:
    t = r["text"]
    cl = segment.split_clauses(t)
    starts = {c.start for c in cl}
    ends = {core_end(t, c.start, c.end) for c in cl}
    for sp in r["labels"].values():
        for s, e in sp:
            while s < e and t[s].isspace():
                s += 1
            ce = core_end(t, s, e)
            n += 1
            x, y = s in starts, ce in ends
            a += x
            b += y
            ab += x and y
print(f"gold spans {n}  start {a / n:.1%}  end {b / n:.1%}  both {ab / n:.1%}")
