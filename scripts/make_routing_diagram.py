"""docs/diagrams/review-routing.drawio 생성: 규칙 / 의도 분류 / 혼합 라우터 호출 흐름 비교.

uv run python scripts/make_routing_diagram.py
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "review-routing.drawio"

S = {
    "start": "ellipse;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;fontStyle=1;",
    "proc": "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
    "rule": "rounded=1;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;",
    "hcx": "rounded=1;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;fontStyle=1;",
    "dec": "rhombus;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;",
    "decI": "rhombus;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;",
    "tool": "rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;",
    "safe": "rounded=1;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;",
    "note": "shape=note;whiteSpace=wrap;html=1;size=14;fillColor=#ffffff;strokeColor=#999999;"
    "align=left;verticalAlign=top;spacingLeft=6;spacingTop=4;fontSize=11;",
    "lane": "swimlane;whiteSpace=wrap;html=1;startSize=40;fontSize=15;fontStyle=1;rounded=1;",
    "title": "text;html=1;fontSize=20;fontStyle=1;align=left;verticalAlign=middle;",
    "text": "text;html=1;fontSize=12;align=left;verticalAlign=middle;",
}

cells: list[str] = []
_n = [0]


def _id() -> str:
    _n[0] += 1
    return f"c{_n[0]}"


def v(label: str, x, y, w, h, style: str) -> str:
    i = _id()
    val = escape(label.replace("\n", "<br>"), {'"': "&quot;"})
    cells.append(
        f'<mxCell id="{i}" value="{val}" style="{S[style]}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return i


def e(a, b, label="", dashed=False, exit=None, entry=None, points=None):
    st = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;"
    st += "dashed=1;" if dashed else ""
    if exit:
        st += f"exitX={exit[0]};exitY={exit[1]};"
    if entry:
        st += f"entryX={entry[0]};entryY={entry[1]};"
    pts = ""
    if points:
        pts = (
            '<Array as="points">'
            + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points)
            + "</Array>"
        )
    cells.append(
        f'<mxCell id="{_id()}" value="{escape(label)}" style="{st}" edge="1" parent="1" '
        f'source="{a}" target="{b}"><mxGeometry relative="1" as="geometry">{pts}'
        f"</mxGeometry></mxCell>"
    )


def tail(x0, top, w, cmp_note):
    """세 방식 공통 뒷부분: 인자 검증 → 도구 실행 → 답변 → 출력 안전장치 → 번역."""
    cx = x0 + (w - 300) / 2
    d = v(
        "dispatch: 인자 정리·검증 (세 방식 공통)\n기간 보정 · 메뉴·항목 보충 · 등록 확인",
        cx,
        top,
        300,
        56,
        "safe",
    )
    t = v("도구 실행", cx, top + 80, 300, 40, "tool")
    a = v("HCX-005 답변 (답글 흐름은 정해진 문장)", cx, top + 144, 300, 40, "hcx")
    s = v("출력 안전장치: 거짓 '올렸습니다' 막기 등", cx, top + 208, 300, 40, "safe")
    o = v("출력 번역 (사장님 언어)", cx + 50, top + 272, 200, 40, "start")
    for p, q in [(d, t), (t, a), (a, s), (s, o)]:
        e(p, q)
    v(cmp_note, x0 + 20, top + 336, w - 40, 150, "note")
    return d, a, o


LW, GAP, TOP = 560, 30, 70
X = [40, 40 + LW + GAP, 40 + 2 * (LW + GAP)]
H = 1110

v("리뷰 도구 호출 구조 비교: 규칙 · 의도 분류 · 혼합", 40, 14, 1000, 40, "title")
v('A. 규칙 라우터 (기본, router="rules")', X[0], TOP, LW, H, "lane")
v('B. 의도 분류 라우터 (실험, router="intent")', X[1], TOP, LW, H, "lane")
v('C. 혼합 라우터 (실험, router="hybrid")', X[2], TOP, LW, H, "lane")

# ---------------- A. 규칙 ----------------
x0 = X[0]
c = x0 + LW / 2
a0 = v("사장님 발화 → 입력 번역", c - 130, 130, 260, 44, "start")
a1 = v(
    "정규식 규칙 route()\n보고 → 확정 → 등록 → 수정 → 쓰기 → 목록",
    c - 170,
    200,
    340,
    56,
    "rule",
)
a2 = v("규칙에\n걸렸나?", c - 60, 280, 120, 76, "dec")
a3 = v("도구 바로 실행\n(HCX 0회)", x0 + LW - 170, 288, 150, 60, "tool")
a4 = v("초안 대기 + 미루는 말 → 안내문", c - 150, 380, 300, 40, "rule")
a5 = v("HCX-005가 도구 5개 중 선택", c - 150, 444, 300, 44, "hcx")
a6 = v(
    "도구를 안 불렀으면 보정 규칙\n· 리뷰 단서(REVIEW_HINTS) → 재요청 → 시스템 직접 실행\n"
    "· 초안 대기 + 확정/수정 표현 → 시스템 실행",
    c - 220,
    512,
    440,
    76,
    "rule",
)
e(a0, a1)
e(a1, a2)
e(a2, a3, "예", exit=(1, 0.5))
e(a2, a4, "아니오", exit=(0.5, 1))
e(a4, a5)
e(a5, a6)
d, ans, _ = tail(
    x0,
    640,
    LW,
    "특징\n· 표현 이해 = 정규식, 도구 선택 = 규칙 + HCX\n"
    "· 답글·보고 턴은 HCX 0회로 빠르고 일정함\n"
    "· 약점: 새 표현마다 단어 추가, 규칙 충돌,\n  리뷰 단서가 넓으면 관계없는 질문에도 도구 호출\n"
    "· 측정 holdout5 93.8% · dev 95.4% · 도구 없음(dev) 90.4%\n  · 턴당 4.9초 · HCX 1.50회",
)
e(a6, d)
e(a3, ans, dashed=True, exit=(0.5, 1), entry=(1, 0.5))

# ---------------- B. 의도 분류 ----------------
x0 = X[1]
c = x0 + LW / 2
b0 = v("사장님 발화 → 입력 번역", c - 130, 130, 260, 44, "start")
b1 = v(
    "HCX-007 의도 분류 (JSON 스키마)\n입력: 발화 + 직전 답변 + 상태\n출력: 의도 1개 + 답글 대상",
    c - 170,
    200,
    340,
    64,
    "hcx",
)
b2 = v("분류\n성공?", c - 60, 288, 120, 76, "decI")
b3 = v("규칙 라우터로\n(A와 같음)", x0 + LW - 160, 296, 140, 60, "rule")
b4 = v("의도 → 실행 계획 (시스템이 정함)", c - 150, 388, 300, 40, "proc")
b5 = v("목록·작성·수정\n등록·지금 보고서\n→ 도구 바로 실행", x0 + 15, 452, 130, 76, "tool")
b6 = v("미루기\n→ 안내문", x0 + 155, 452, 100, 76, "tool")
b7 = v("분석·보고 설정\n→ 그 도구 하나만\nHCX-005에 지정", x0 + 265, 452, 140, 76, "hcx")
b8 = v("관계없음\n→ 도구 없이 답변", x0 + 415, 452, 130, 76, "hcx")
e(b0, b1)
e(b1, b2)
e(b2, b3, "아니오", exit=(1, 0.5))
e(b2, b4, "예", exit=(0.5, 1))
for t in (b5, b6, b7, b8):
    e(b4, t, exit=(0.5, 1), entry=(0.5, 0))
d, ans, out = tail(
    x0,
    640,
    LW,
    "특징\n· 표현 이해 = HCX 분류, 의도 → 도구 = 시스템\n"
    "· 규칙은 안전장치로만 (등록 확인·기간 보정)\n"
    "· 강점: 관계없는 질문·보고 의도 100%\n"
    "· 약점: 리뷰 세부 의도 혼동 ('별로라는 리뷰' → 미답글)\n"
    "· 측정 holdout5 95.3% · dev 90.3% · 도구 없음(dev) 100%\n  · 턴당 4.9초 · HCX 2.45회",
)
e(b5, d, exit=(0.5, 1), entry=(0, 0.5))
e(b7, d, exit=(0.5, 1), entry=(1, 0.5))
e(b8, ans, "도구 없이", dashed=True, exit=(0.5, 1), entry=(1, 0.5))
e(
    b6,
    out,
    "안내문",
    dashed=True,
    exit=(0.5, 1),
    entry=(0, 0.5),
    points=[(x0 + 205, 600), (x0 + 110, 600), (x0 + 110, 932)],
)

# ---------------- C. 혼합 ----------------
x0 = X[2]
c = x0 + LW / 2
h0 = v("사장님 발화 → 입력 번역", c - 130, 130, 260, 44, "start")
h1 = v(
    "① 정규식 규칙 route() 먼저 (A와 같음)",
    c - 170,
    200,
    340,
    44,
    "rule",
)
h2 = v("규칙에\n걸렸나?", c - 60, 268, 120, 76, "dec")
h3 = v("도구 바로 실행\n(HCX 0회)", x0 + LW - 170, 276, 150, 60, "tool")
h4 = v(
    "② 못 잡은 턴만 HCX-007 의도 분류",
    c - 150,
    368,
    300,
    44,
    "hcx",
)
h5 = v("관계없음\n+ 리뷰 단서 없음\n→ 도구 없이 답변", x0 + 15, 440, 140, 80, "hcx")
h6 = v("보고 · 미루기\n→ 보고 도구 / 안내문", x0 + 165, 440, 150, 80, "tool")
h7 = v(
    "그 밖 (분석·목록·작성·수정·등록)\n또는 분류 실패\n→ A의 HCX-005 흐름\n(도구 5개 + 재요청)",
    x0 + 325,
    440,
    220,
    80,
    "hcx",
)
e(h0, h1)
e(h1, h2)
e(h2, h3, "예", exit=(1, 0.5))
e(h2, h4, "아니오", exit=(0.5, 1))
for t in (h5, h6, h7):
    e(h4, t, exit=(0.5, 1), entry=(0.5, 0))
d, ans, _ = tail(
    x0,
    640,
    LW,
    "특징\n· 규칙이 강한 곳(답글·보고 표현)은 규칙 그대로 → 빠르고 일정\n"
    "· 분류는 규칙이 약한 곳만: 관계없는 질문 걸러내기, 보고, 미루기\n"
    "· 분류가 '관계없음'이라도 리뷰 단서·메뉴 단어가 있으면 A 흐름으로\n"
    "· 분류는 규칙이 못 잡은 턴에만 → HCX 호출 증가가 B보다 적음\n"
    "· 측정 holdout5 93.8% · dev 95.1% · 도구 없음(dev) 100%\n  · 턴당 5.4초 · HCX 2.19회",
)
e(h6, d, exit=(0.5, 1), entry=(0, 0.5))
e(h7, d, exit=(0.5, 1), entry=(1, 0.5))
e(
    h5,
    ans,
    "도구 없이",
    dashed=True,
    exit=(0, 0.5),
    entry=(0, 0.5),
    points=[(x0 + 10, 480), (x0 + 10, 804)],
)
e(
    h3,
    ans,
    dashed=True,
    exit=(0.5, 1),
    entry=(1, 0.5),
    points=[(x0 + LW - 95, 600), (x0 + LW - 30, 600), (x0 + LW - 30, 804)],
)

# ---------------- 범례 ----------------
ly = TOP + H + 20
v("범례", 40, ly, 60, 30, "title")
for k, (st, lab) in enumerate(
    [
        ("rule", "정규식 규칙"),
        ("hcx", "HCX 호출"),
        ("tool", "도구 실행"),
        ("safe", "안전장치·검증"),
        ("start", "입출력"),
    ]
):
    v(lab, 120 + k * 150, ly, 130, 30, st)

xml = (
    '<mxfile host="drawio"><diagram id="routing" name="리뷰 도구 호출 비교">'
    '<mxGraphModel dx="1800" dy="1000" grid="1" gridSize="10" guides="1" tooltips="1" '
    'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1820" '
    f'pageHeight="{ly + 60}" math="0" shadow="0"><root><mxCell id="0"/>'
    f'<mxCell id="1" parent="0"/>{"".join(cells)}</root></mxGraphModel></diagram></mxfile>'
)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(xml, encoding="utf-8")
print(f"saved {OUT.relative_to(OUT.parents[2])} ({len(cells)} cells)")
