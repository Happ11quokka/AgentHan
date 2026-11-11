"""보고 설정 요청을 규칙으로 해석한다 (한·영·러). LLM이 도구·인자를 안정적으로 고르지 못해 답글
흐름처럼 규칙으로 처리.

parse_report_request("매주 월요일 아침에 리뷰 정리해서 보내줘")
  → ("set", {"frequency": "weekly", "weekday": "mon", "time": "09:00"})
"""

from __future__ import annotations

import re


def R(p):
    return re.compile(p, re.I)


REPORT_WORD = R(
    r"보고|리포트|report|отч[её]т|정리해서\s?(보내|알려)|요약해서\s?(보내|알려)|모아서\s?(보내|알려)|"
    r"보내\s?줘|send|присылай|отправляй|알림|알려\s?줘|notify|alert|уведом|сообщ"
)
NOW = R(
    r"(지금|당장|바로).{0,8}(보고서|리포트)|"
    r"(보고서|리포트)\s?(를|좀)?\s?(지금|당장|바로)\s?(받|보|줘|보내)|"
    r"(보고서|리포트)\s?(를|좀)?\s?받아\s?(볼|보고)|"
    r"(보고서|리포트).{0,6}(지금|당장|바로)\s?\S{0,4}\s?(뽑|만들|받|보|줘|보내)|"  # holdout2 F07
    r"보고서\s?(보여|줘|만들)|리포트\s?(보여|줘)|report now|"
    r"show (me )?(the )?report|отч[её]т сейчас|покажи отч"
)
OFF = R(
    r"그만|끄|꺼|중지|중단|안\s?보내|안\s?받|필요\s?없|"
    r"stop|turn off|disable|cancel|отключ|не присылай|"
    r"хватит|стоп"
)
SUGGEST = R(
    r"얼마나\s?자주|알아서|적당|추천|주기\s?(는\s?)?어떻게\s?(하는\s?게|해야|할까|하면)|어느\s?주기|"
    r"how often|recommend|suggest|как часто|посоветуй"
)
FREQ = [
    ("daily", R(r"매일|날마다|하루\s?한\s?번|daily|every\s?day|каждый день|ежедневн")),
    (
        "biweekly",
        R(r"격주|2주|두\s?주|biweekly|every (two|2) weeks|раз в две недели|каждые две недели"),
    ),
    (
        "weekly",
        R(
            r"매주|일주일에\s?한\s?번|주\s?1회|weekly|every\s?week|еженедельн|каждую неделю|раз в "
            r"неделю"
        ),
    ),
    (
        "monthly",
        R(
            r"매달|매월|한\s?달에\s?한\s?번|monthly|every\s?month|ежемесячн|каждый месяц|раз в "
            r"месяц"
        ),
    ),
]
WEEKDAY = [
    ("mon", R(r"월요일|월욜|monday|понедельник")),
    ("tue", R(r"화요일|화욜|tuesday|вторник")),
    ("wed", R(r"수요일|수욜|wednesday|сред[ау]")),
    ("thu", R(r"목요일|목욜|thursday|четверг")),
    ("fri", R(r"금요일|금욜|friday|пятниц")),
    ("sat", R(r"토요일|토욜|saturday|суббот")),
    ("sun", R(r"일요일|일욜|sunday|воскресень")),
]
PART = [
    (R(r"아침|morning|утр"), 9),
    (R(r"점심|noon|lunch|обед"), 12),
    (R(r"저녁|evening|вечер"), 20),
    (R(r"밤|night|ночь"), 21),
]
FOCUS_COMPLAINT = R(
    r"불만만|안\s?좋은\s?(것|거|리뷰)만|나쁜\s?(것|거|리뷰)만|complaints? only|only (the )"
    r"?complaints|только жалоб|только плох"
)
ALERT_NEG = R(
    r"(나쁜|부정|안\s?좋은|불만|악플).{0,12}(바로|즉시|올라오면|생기면|달리면)|"
    r"(bad|negative).{0,20}(immediately|right away|as soon)|"
    r"(плох|негатив).{0,25}(сразу|немедленно)|сразу.{0,25}(плох|негатив)"
)
ALERT_ALL = R(
    r"새\s?리뷰.{0,8}(마다|올라오면\s?다|생길\s?때마다)|every new review|каждом новом отзыве"
)
# 주기와 함께 오면 보고 설정으로 보는 말 ('매일 저녁 8시에 리뷰 요약해줘', 벤치마크 I04).
# 주기 없이 '리뷰 정리해줘'만 오면 지금 분석해 달라는 뜻이라 보고 설정이 아니다
SUMMARY_WORD = R(r"요약해\s?(줘|주세요)|정리해\s?(줘|주세요)|summar")
CONFIRM_YES = R(
    r"^\s*(응|어|네|예|좋아|그래|그렇게\s?(해|하자|해줘)|오케이|ok|okay|yes|sure|да|хорошо|давай)\b"
)


def _time(u: str) -> str | None:
    m = re.search(r"(\d{1,2}):(\d{2})", u)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"(오전|오후|아침|저녁|밤)?\s?(\d{1,2})\s?시(\s?(\d{1,2})\s?분|\s?반)?", u)
    if m:
        h = int(m.group(2))
        mm = 30 if m.group(3) and "반" in m.group(3) else int(m.group(4) or 0)
        if m.group(1) in ("오후", "저녁", "밤") and h < 12:
            h += 12
        return f"{h % 24:02d}:{mm:02d}"
    m = re.search(r"(\d{1,2})\s?(am|pm)", u, re.I)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(2).lower() == "pm" else 0)
        return f"{h:02d}:00"
    m = re.search(r"в\s?(\d{1,2})(\s?час|\s?утра|\s?вечера)?", u, re.I)
    if m and m.group(2):
        h = int(m.group(1)) + (12 if "вечера" in m.group(2) and int(m.group(1)) < 12 else 0)
        return f"{h:02d}:00"
    for pat, h in PART:
        if pat.search(u):
            return f"{h:02d}:00"
    return None


def parse_report_request(u: str, pending_suggestion: dict | None = None):
    """→ (kind, args) | None. kind: now / off / suggest / set."""
    if pending_suggestion and CONFIRM_YES.search(u):
        return "set", {"frequency": pending_suggestion["frequency"]}
    if NOW.search(u):
        return "now", {}
    freq = next((f for f, p in FREQ if p.search(u)), None)
    wd = next((d for d, p in WEEKDAY if p.search(u)), None)
    t = _time(u) if (freq or wd or REPORT_WORD.search(u)) else None
    if freq and SUMMARY_WORD.search(u) and not REPORT_WORD.search(u):
        return "set", {k: v for k, v in {"frequency": freq, "weekday": wd, "time": t}.items() if v}
    alert = "negative" if ALERT_NEG.search(u) else "every_new" if ALERT_ALL.search(u) else None
    focus = "complaint" if FOCUS_COMPLAINT.search(u) else None
    has_report = REPORT_WORD.search(u)
    if has_report and OFF.search(u) and not (freq or wd or alert):
        return "off", {}
    if SUGGEST.search(u) and (has_report or "주기" in u) and not freq:
        return "suggest", {}
    if alert or ((freq or wd) and has_report) or (focus and has_report):
        args = {
            "frequency": freq or ("weekly" if wd else None),
            "weekday": wd,
            "time": t,
            "focus": focus,
            "alert": alert,
        }
        return "set", {k: v for k, v in args.items() if v}
    if has_report and t and "보고" in u:  # '보고 시간 오전 8시로 바꿔줘'
        return "set", {"time": t}
    return None
