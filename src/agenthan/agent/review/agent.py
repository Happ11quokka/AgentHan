"""사장님 한마디 → HCX 도구 선택 → 도구 실행 → HCX 최종 답변 (E2E 루프).

LLM은 사장님 말에서 인자만 채우고, 가게·날짜 같은 값은 여기서 넣는다 (도구 명세 '시스템이 넣는 값').
공용 대화 루프에 붙일 때는 run_turn()과 ReviewToolbox.dispatch()만 가져가면 된다.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from .analyze import analyze_reviews
from .clova import ClovaError, model_options
from .harsh import softened_ids
from .replies import (
    CONFIRM,
    MockReplyStore,
    ReplyAwareRepo,
    draft_review_reply,
    get_unanswered_reviews,
    post_review_reply,
)
from .report import ReportSettingsStore, run_report, set_review_report
from .report_parse import CONFIRM_YES, parse_report_request
from .specs import (
    ANALYZE_REVIEWS,
    DRAFT_REVIEW_REPLY,
    GET_UNANSWERED_REVIEWS,
    POST_REVIEW_REPLY,
    SET_REVIEW_REPORT,
)
from .topic import ASPECT_KEYWORDS, resolve_from_utterance

# 사장님 문장에 이런 표현이 있을 때만 LLM이 채운 기간을 믿는다. '요즘', '최근'은 애매해서 넣지
# 않는다
# (HCX가 '요즘'을 7d·30d·90d로 매번 다르게 채움, 2026-11-07 실측) → 없으면 자동 확장
# 규칙(7일→30일→…)을 쓴다
TIME_HINTS = re.compile(
    r"어제|그제|그저께|오늘|이번\s?주|지난\s?주|일주일|한\s?주|이번\s?달|지난\s?달|한\s?달|올해|작년|전체|지금까지|"
    r"\d+\s?(일|주|개월|달)|석\s?달|분기|월\s|\d+월|"
    r"today|yesterday|week|month|year|all\s?time|"
    r"сегодня|вчера|недел|месяц|год",
    re.I,
)

# 사장님이 기간 길이를 직접 말하면 HCX가 채운 기간 대신 시스템이 계산한다
# ('지난 2주'에 HCX가 7d를 넣고 답변은 '지난 2주간'이라고 함,
# '오늘'에 내일 날짜를 넣음. holdout1 A24·A15)
_NUM_WORDS = {"한": 1, "두": 2, "세": 3, "석": 3, "네": 4, "넉": 4, "다섯": 5, "여섯": 6, "열": 10}
DURATION = re.compile(r"(\d+|한|두|세|석|네|넉|다섯|여섯|열)\s?(일|주|개월|달)|일주일|열흘|보름")
_UNIT_DAYS = {"일": 1, "주": 7, "개월": 30, "달": 30}
# 기간 전체를 뜻하는 말 ('가게 연 뒤로 모든 리뷰', holdout1 A20)
ALL_TIME = re.compile(
    r"모든\s?리뷰|전체\s?기간|전\s?기간|처음부터|통틀어|지금까지|"
    r"(가게|매장)?\s?(연|오픈한|문\s?연)\s?(뒤|후|이후)|all\s?time|since (we )?opened",
    re.I,
)


MONTH_NAME = re.compile(
    r"(?<!\d)(1[0-2]|[1-9])\s?월(?!\s?\d)"
)  # '10월'(달 이름), '10월 25일'은 날짜
RELATIVE_DAYS = {"어제": 1, "그제": 2, "그저께": 2, "엊그제": 3}
RELATIVE_DAY = re.compile(r"엊그제|그저께|그제|어제")


def span_days(utterance: str) -> int | None:
    """'지난 2주' → 14, '석 달' → 90. '10월 25일'처럼 날짜인 '일'은 기간이 아니다."""
    for m in DURATION.finditer(utterance):
        word = m.group(0)
        if word == "일주일":
            return 7
        if word in ("열흘", "보름"):
            return 10 if word == "열흘" else 15
        before = utterance[: m.start()].rstrip()
        if m.group(2) == "일" and (before.endswith("월") or m.group(1) in _NUM_WORDS):
            continue  # '11월 1일', '한 일' 같은 말은 날짜·다른 뜻
        after = utterance[m.end() : m.end() + 3]
        if m.group(2) == "일" and re.match(r"\s?(부터|이후|까지)", after):
            continue  # '25일부터'는 날짜
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        return n * _UNIT_DAYS[m.group(2)]
    return None


# 도구 없이 답하면 안 되는 질문의 단서 (언어별). 메뉴 별칭은 ReviewToolbox가 가게 메뉴에서 더한다
REVIEW_HINTS = [
    "리뷰",
    "후기",
    "손님들",  # '손님'만으로는 넓다: '손님한테 쿠폰 주고 싶어'에 미답글을 부름 (벤치마크 J06)
    "반응",
    "평가",
    "불만",
    "칭찬",
    "별점",
    "답글",
    "답장",
    "review",
    "customer",
    "feedback",
    "complain",
    "reply",
    "отзыв",
    "клиент",
    "говорят",
    "жалоб",
    "ответ",
]
# 단어 목록 밖의 리뷰 질문 표현. '웨이팅 길다는 얘기 있어?'에 도구 없이 '100건 중 5건'을 지어냄
# (벤치마크 D03, 2026-11-08) → 항목 단어(아래 ASPECT_HINTS)와 함께 본다
REVIEW_HINT_RE = re.compile(
    r"손님\s?(반응|평|후기|말|얘기|이\s)|(얘기|말|소리|평)\s?(있|나와|나오|많)|"
    r"(다는|하는|라는|단)\s?(얘기|말(?!기)|소리|평|리뷰)|뭐래|뭐라고\s?(해|하)|어떻다고|"
    r"고쳐야|개선할|강점|약점|장점|단점|아쉬운\s?점|"
    r"(괜찮|어떻|좋|별로|맛있|맛없)대\b|평\s?(어때|좋|나빠|안\s?좋)"
)
# 항목 키워드 중 리뷰 밖에서도 흔한 말은 단서로 쓰지 않는다
# ('메뉴 등록', '예약 받고 싶어', '매장 영업시간')
_HINT_SKIP_ASPECTS = {"목적", "예약", "메뉴", "방역"}
_HINT_SKIP_WORDS = {"매장", "크기", "사이즈", "주문", "결제"}
ASPECT_HINTS = sorted(
    {
        kw
        for a, kws in ASPECT_KEYWORDS.items()
        if a not in _HINT_SKIP_ASPECTS
        for kw in kws
        if len(kw) >= 2 and re.search(r"[가-힣]", kw) and kw not in _HINT_SKIP_WORDS
    }
)
REPLY_HINTS = re.compile(r"답글|답장|댓글|대답|답변|응답|reply|respond|ответ", re.I)
# 목록 질문인데 쓰기 표현이 섞인 경우 ('답글 달아야 할 리뷰')
LIST_Q = re.compile(r"(달아|써|작성해|남겨)야\s?(할|하는|될|되는|한)")
# 초안이 있을 때 '고쳐 달라'로 볼 표현 (HCX가 도구 없이 직접 고쳐 쓴 사례, 2026-11-07)
EDIT_HINTS = re.compile(
    r"해\s?줘|바꿔|고쳐|추가|빼\s?줘|더\s|덜\s|짧게|길게|정중|친근|말해|언급|넣어|다시|"
    r"make|change|shorter|longer|add|remove|polite|say|tell|mention|"
    r"сделай|измени|добав|короче|вежлив|скажи|напиши|упомян|убери",
    re.I,
)
REPLY_TOOLS = {"get_unanswered_reviews", "draft_review_reply", "post_review_reply"}
REPORT_TOOLS = {"set_review_report", "run_review_report"}
# 답글 흐름은 규칙으로 고른다: HCX-005가 도구 4개 중 답글 도구를 안정적으로 고르지 못함 (2026-11-07
# 실측:
# '안 좋은 리뷰에 답글 써줘'에 분석 도구를 부르고 초안을 직접 지어냄, '이대로 올려줘'에 등록 없이
# '게시하겠습니다')
LIST_INTENT = re.compile(
    r"답글\s?(안|없|못)|답장\s?(안|없|못)|미답글|without (a )?repl|no repl|unanswered|"
    # 'haven't replied to'가 규칙에 안 걸려 LLM이 목록과 초안을 한 번에 부름 (2026-11-07 실측)
    # '대답 안 한', '답 안 달린', '댓글 안 단' (holdout1 G11·G13)
    r"(대답|답변|응답|댓글)\s?(안|없|못)|답\s?안\s?(달|한|단)|"
    # '답글 달아야 할 리뷰 몇 개 남았어?'는 쓰라는 말이 아니라 목록 질문 (holdout1 G12)
    r"(답글|답장|댓글)\s?(을|를)?\s?(달아|써|작성해|남겨)야\s?(할|하는|될|되는|한)|"
    r"(haven'?t|have not|didn'?t|did not|not yet|not) (yet )?(been )?repl|"
    r"без ответа|не ответил|не ответила",
    re.I,
)
WRITE_INTENT = re.compile(
    r"(답글|답장).{0,6}(써|작성|달아|만들|부탁)|reply to|write (a )?repl|draft|ответь|напиши ответ|"
    r"ответ на",
    re.I,
)
# 리뷰를 인용하며 묻는 말: 인용 안의 지시('보고를 끄세요')는 사장님 요청이 아니다 (holdout6 V53)
QUOTE_FRAME = re.compile(
    r"(써|적어|남겨|달아)\s?(놨|놓|뒀|있)|라고\s?(써|적|했|하더|되어|돼)|무슨\s?뜻|이게\s?뭐|"
    r"wrote|written|says?\b|написал|пишет",
    re.I,
)
QUOTED = re.compile(r"'[^']{4,}'|\"[^\"]{4,}\"|‘[^’]{4,}’|“[^”]{4,}”|「[^」]{4,}」")


def drop_quoted(u: str) -> str:
    """인용하며 묻는 말이면 따옴표 안을 지운다. 그 밖의 따옴표('딸기모찌' 리뷰)는 그대로 둔다."""
    return QUOTED.sub(" ", u) if QUOTE_FRAME.search(u) else u


# 고친 뒤 올리라는 말 ('짧게 해서 올려줘'): 먼저 고쳐서 다시 보여 준다 (holdout6 V44).
# EDIT_HINTS의 '해 줘'는 '등록해 줘'에도 있으므로 여기서는 고치는 말만 본다
EDIT_THEN_POST = re.compile(
    r"바꿔|고쳐|추가|빼\s?(고|서|줘)|더\s|덜\s|짧게|길게|정중|친근|언급|넣어|다시\s?써|"
    r"shorter|longer|polite|change|add|remove|короче|вежлив|добав|измени|убери",
    re.I,
)
# 보고 설정을 바꾸는 쓰기에는 사장님 말에 보고·알림 단어와 바꿀 값이 있어야 한다
# ('매일 해줘'(무엇을?), '알림 설정 바꿔줘'(무엇으로?) → 되묻기, holdout6 V21·V34)
REPORT_NOUN = re.compile(
    r"보고|리포트|알림|알려|정리해\s?(서\s?)?보내|요약해\s?(서\s?)?보내|report|summary|alert|notif|"
    r"отч[её]т|уведомл|сводк|тайлан|мэдэгдэл",
    re.I,
)
REPORT_VALUE = re.compile(
    r"매일|날마다|하루|매주|주마다|일주일|격주|2주|두\s?주|매달|한\s?달|월요일|화요일|수요일|목요일|금요일|"
    r"토요일|일요일|[월화수목금토일]요일|\d+\s?시|아침|오전|오후|저녁|점심|밤|새벽|부정|불만|나쁜|"
    r"새\s?리뷰|모든|전부|올라올\s?때|바로|끄|꺼|그만|중단|해제|안\s?받|필요\s?없|멈춰|"
    r"daily|every|weekly|monthly|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"morning|evening|\d+\s?(am|pm)|off|stop|disable|negative|new|"
    r"ежедневно|кажд|недел|месяц|утр|вечер|выключ|отключ|не\s?нужн",
    re.I,
)
POST_WORDS = re.compile(r"올려|등록|게시|post|publish|опубликуй|размести", re.I)
# 답글이 아닌 것을 올리거나 등록하려는 말 ('메뉴판 사진 올려서 메뉴 등록하고 싶어', 벤치마크 J04)
OTHER_POST_OBJECT = re.compile(
    r"메뉴|사진|공지|이벤트|쿠폰|가격|영업\s?시간|photo|picture|menu|notice|coupon", re.I
)
AS_IS = re.compile(r"이대로|그대로|as is|как есть", re.I)
# 분석 대상이 리뷰임을 말하는 표현 (미답글 → 분석 바꾸기는 이 말이 있을 때만)
REVIEW_NOUN = re.compile(r"리뷰|후기|평가|review|отзыв", re.I)
# 초안이 남아 있는데 도구 없이 '올리겠습니다'처럼 답하면 거짓 안내다
# (벤치마크 H06 '음 잠깐만', 2026-11-08)
POST_CLAIM = re.compile(r"(올리|올려|등록하|게시하|올렸|등록했|게시했)(겠|드리|습니다|어요|을게)")
# '원문 보여줘': 가린 리뷰가 있을 때만 시스템이 원문을 보여 준다 (번역문·원문 둘 다 본다)
ORIGINAL_ASK = re.compile(
    r"(원문|가린|가려진|가려\s?놓은|숨긴|전체\s?(내용|문장)|원래\s?(내용|문장|리뷰))\S*\s?"
    r"(\S+\s)?(보여|볼래|볼게|보자|봐|알려|뭐라|읽어|확인)|"
    r"original|full\s?(text|review)|оригинал|полн\S* текст",
    re.I,
)
ORIGINAL_MSG = "가렸던 리뷰 원문이에요. 거친 표현이 그대로 있어요."
HIDDEN_HINT = (
    "거친 표현이 있는 리뷰는 표현을 가렸어요. 원문이 필요하면 '원문 보여줘'라고 해 주세요."
)
HOLD_MSG = "초안은 그대로 두었어요. 올리려면 '이대로 올려줘', 고치려면 고칠 내용을 말씀해 주세요."
# 초안을 보고 잠시 미루는 말. 수정 요청이 아니다 ('음 잠깐만'에 HCX가 초안을 고침, 벤치마크 H06)
HOLD_WORDS = re.compile(
    r"잠깐|잠시만|기다려|생각\s?(해|좀)|나중에|이따|보류|wait|hold on|later|подожди|позже", re.I
)
TARGET_NEG = re.compile(
    r"안\s?좋은|나쁜|불만|부정|악플|급한|negative|bad|complain|плох|негатив|жалоб", re.I
)
TARGET_ALL = re.compile(
    r"전부|다\s?(써|달아|작성|답글|답장)|모두|all|все", re.I
)  # '다 답글 달아줘' (holdout1 H12)


def system_prompt(store_name: str, today: date, owner_lang: str = "ko") -> str:
    """비서는 늘 한국어로 생각하고 답한다. 사장님 언어 번역은 run_turn이 입·출력에서 한다
    (owner_lang은 호환용 인자, 프롬프트에는 쓰지 않는다)."""
    return (
        f"당신은 '{store_name}' 사장님을 돕는 비서입니다. 오늘은 {today.isoformat()}입니다.\n"
        "답변은 반드시 한국어로 쓰세요. 사장님 언어로 옮기는 일은 시스템이 합니다. "
        "대화 상대는 손님이 아니라 가게 사장님입니다. "
        "사장님은 '사장님'으로 부르고, 리뷰를 쓴 사람은 '손님'이라고 하세요 ('고객님' 금지). "
        "메뉴를 먹어 보라고 권하는 등 손님에게 하듯 말하지 마세요.\n"
        "당신이 할 수 있는 일은 리뷰 분석, 답글 작성·등록, 리뷰 보고 설정뿐입니다. "
        "메뉴 등록, 쿠폰, 매출, 영업시간 같은 다른 일은 직접 할 수 없다고 짧게 말하고, "
        "할 수 있는 척하지 마세요.\n"
        "가게·메뉴·손님 반응·리뷰·답글에 관한 질문은 어떤 언어로 오든 반드시 알맞은 도구를 "
        "호출하세요 "
        "(분석은 analyze_reviews, 답글 안 단 리뷰는 get_unanswered_reviews, 답글 작성·수정은 "
        "draft_review_reply, "
        "등록은 post_review_reply). 도구 결과 없이 메뉴를 설명하거나 리뷰를 인용·창작하면 안 "
        "됩니다.\n"
        "답글: 미답글 목록·답글 초안 원문과 확인 질문은 시스템이 붙입니다. 답변에서 초안 문장을 "
        "다시 쓰거나 번역하지 마세요. 사장님이 분명히 확정하기 전에는 post_review_reply를 부르지 "
        "마세요.\n"
        "분석 답변 끝에 '이대로 올릴까요', '수정할 부분이 있을까요' 같은 확인 질문을 "
        "붙이지 마세요.\n"
        "도구가 돌려준 숫자와 리뷰만 사용하고, 없는 내용을 지어내지 마세요.\n"
        "few_reviews가 true이면 경향을 일반화하지 말고 'total_reviews건 중 review_count건'처럼 "
        "사실과 리뷰만 전하세요.\n"
        "리뷰 원문은 시스템이 답변 아래에 따로 붙입니다. 답변 안에서 리뷰를 인용하거나 번역하지 "
        "마세요.\n"
        # '7일' 같은 금지 예시를 적으면 모델이 오히려 그 기간을 말함 (2026-11-07 실측)
        "기간은 도구 결과의 period(start~end)만 쓰고, 기간을 스스로 만들지 마세요. 기간을 넓힌 "
        "경우의 안내는 시스템이 답변 앞에 붙이니 답변에서는 기간을 말하지 마세요.\n"
        "결과에 review_count가 0이면 last_review와 next_actions를 보고 다음에 할 일을 제안하세요. "
        "리뷰가 없는 것을 '인기가 없다'처럼 해석하지 마세요.\n"
        "error가 topic_unresolved이면 candidates를 보여주며 어느 것인지 되물으세요.\n"
        "항목 이름은 aspect 대신 display 값을 쓰세요 (예: '배달'이 아니라 '포장·배달').\n"
        "도구 결과를 받은 뒤 답변 형식:\n"
        "1) 핵심 1~2문장: 몇 건 중 몇 건인지, 가장 많은 칭찬·불만.\n"
        "2) 불만이 있으면 사장님이 할 수 있는 개선 행동 1가지. 없으면 생략."
    )


class ReviewToolbox:
    """도구 이름 → 실행 함수. 시스템 값(store_id, today, repo, 방금 보여준 리뷰, 직전 초안 등)은
    여기서 주입한다."""

    def __init__(
        self,
        store_id: str,
        today: date,
        repo,
        catalog=None,
        sentiment=None,
        llm=None,
        writer=None,
        reply_store: MockReplyStore | None = None,
        owner_lang: str = "ko",
        settings_store: ReportSettingsStore | None = None,
        now: datetime | None = None,
        router: str = "rules",
    ):
        # router: "rules"(기본, 정규식 규칙 라우터) | "intent"(실험: HCX 의도 분류, intent.py)
        # "hybrid"(실험: 규칙 먼저, 못 잡은 턴만 분류. intent.hybrid_plan)
        if router not in ("rules", "intent", "hybrid"):
            raise ValueError(f"router는 rules, intent, hybrid 중 하나: {router!r}")
        self.router = router
        self.last_intent: dict | None = None
        self.settings_store = settings_store or ReportSettingsStore()
        self.now = now or datetime.combine(today, datetime.min.time())
        self.pending_suggestion: dict | None = None  # 제안한 주기 (사장님이 '응' 하면 설정)
        self.reply_store = reply_store or MockReplyStore(today=today)
        repo = ReplyAwareRepo(repo, self.reply_store)
        self.ctx = dict(
            store_id=store_id, today=today, repo=repo, catalog=catalog, sentiment=sentiment, llm=llm
        )
        self.writer, self.owner_lang = writer, owner_lang
        # 사장님이 실제로 쓴 문장 (번역 전). 답글 지시는 원어 그대로 생성 프롬프트에 넣는다
        self.original_utterance: str | None = None
        self.listed: list[dict] = []  # 직전 미답글 목록 (draft의 대상)
        self.listed_for: str | None = None  # 그 목록을 만든 사장님 발화
        self.drafts: dict[str, dict] = {}  # review_id → 직전 초안 (수정·등록의 대상)
        # review_id → 마지막 초안 버전. 고칠 때마다 1씩 늘고 등록 뒤에도 줄지 않는다.
        # 비서가 '사장님이 본 그 초안'일 때만 승인을 받도록 밖에서 읽는다 (drafts[id]["version"])
        self.draft_versions: dict[str, int] = {}
        self.routed_call = False  # 지금 실행하는 호출을 규칙·의도 분류가 정했는지
        self.hidden_reviews: list[str] = []  # 직전에 거친 표현을 가린 리뷰 ('원문 보여줘' 대상)
        self.specs = [
            ANALYZE_REVIEWS,
            GET_UNANSWERED_REVIEWS,
            DRAFT_REVIEW_REPLY,
            POST_REVIEW_REPLY,
            SET_REVIEW_REPORT,
        ]
        self.allowed = {
            "analyze_reviews": {"period", "start_date", "end_date", "focus", "topic", "format"},
            "get_unanswered_reviews": {"priority"},
            "draft_review_reply": {"edit_request", "target"},
            "post_review_reply": set(),
            "set_review_report": {"action", "frequency", "weekday", "time", "focus", "alert"},
            "run_review_report": set(),
        }
        words = set()
        for m in catalog.menus(store_id) if catalog else []:
            for alias in [m["name"], *m.get("aliases", [])]:
                words |= {w.lower() for w in re.findall(r"[\w가-힣]+", alias) if len(w) >= 2}
        # 항목 단어(배달·직원·포장)만으로는 리뷰 질문으로 보지 않는다: '배달앱 수수료', '직원 월급',
        # '포장 용기'에 분석을 실행함 (holdout1 J11·J12·J17·J20). 항목 질문은 REVIEW_HINT_RE의
        # '~다는 얘기', '~대?' 같은 표현으로 잡는다
        self.hints = [h.lower() for h in REVIEW_HINTS] + sorted(words)

    def route(self, utterance: str) -> tuple[str, dict] | None:
        """답글 흐름 요청이면 (도구, 인자)를 규칙으로 정한다. 아니면 None → LLM이 고른다."""
        u = drop_quoted(utterance)
        # '불만 리뷰에 바로 답장 달아줘'는 답글 쓰기다.
        # 알림 규칙('불만 … 바로')보다 먼저 본다 (holdout2 H21)
        writes = WRITE_INTENT.search(u) and not LIST_Q.search(u)
        rep = (
            None
            if writes
            else parse_report_request(u, None if self.drafts else self.pending_suggestion)
        )
        if rep:
            kind, args = rep
            if kind == "now":
                return "run_review_report", {}
            return "set_review_report", ({"action": kind} if kind in ("off", "suggest") else args)
        if self.drafts and EDIT_THEN_POST.search(u) and (POST_WORDS.search(u) or CONFIRM.search(u)):
            return "draft_review_reply", {"edit_request": u}  # 고친 초안은 다시 확인받는다
        if self.drafts and CONFIRM.search(u):
            return "post_review_reply", {}
        if (
            POST_WORDS.search(u)
            and not WRITE_INTENT.search(u)
            and not OTHER_POST_OBJECT.search(u)
            and (self.drafts or REPLY_HINTS.search(u) or AS_IS.search(u))
        ):
            return "post_review_reply", {}  # 초안이 없으면 '등록할 초안이 없어요'로 끝난다
        if (
            self.drafts
            and self._asks_edit(u)
            and not LIST_INTENT.search(u)
            and not WRITE_INTENT.search(u)
        ):
            return "draft_review_reply", {"edit_request": u}
        if WRITE_INTENT.search(u) and not LIST_Q.search(u):
            target = (
                "negative"
                if TARGET_NEG.search(u)
                else "all_listed"
                if TARGET_ALL.search(u)
                else "first"
            )
            return "draft_review_reply", {"target": target}
        if LIST_INTENT.search(u):
            return "get_unanswered_reviews", (
                {"priority": "negative_first"} if TARGET_NEG.search(u) else {}
            )
        return None

    def _fix_period(self, clean: dict, utterance: str) -> None:
        """사장님이 말한 기간을 HCX 값보다 우선한다.
        고친 값은 last_dropped에 남겨 대화 기록도 맞춘다."""
        today = self.ctx["today"]
        before = {k: clean.get(k) for k in ("period", "start_date", "end_date")}
        rel = RELATIVE_DAY.search(utterance)
        if ALL_TIME.search(utterance):
            for k in ("start_date", "end_date"):
                clean.pop(k, None)
            clean["period"] = "all"
        elif MONTH_NAME.search(utterance):
            pass  # '10월 한 달'은 달 이름이 기간이다. HCX가 채운 날짜를 쓴다 (holdout2 A28)
        elif (days := span_days(utterance)) is not None and not clean.get("end_date"):
            clean.pop("period", None)
            clean["start_date"] = (today - timedelta(days=days)).isoformat()
        elif rel and not clean.get(
            "start_date"
        ):  # '그저께부터' (시스템 직접 실행일 때 인자가 없다)
            clean.pop("period", None)
            clean["start_date"] = (today - timedelta(days=RELATIVE_DAYS[rel.group(0)])).isoformat()
        for k in ("start_date", "end_date"):  # 기준일보다 뒤 날짜는 지어낸 값 (holdout1 A15 '오늘')
            if clean.get(k) and clean[k] > today.isoformat():
                clean.pop(k)
                if k == "start_date":
                    clean.pop("end_date", None)
                    clean["period"] = "1d"
        after = {k: clean.get(k) for k in ("period", "start_date", "end_date")}
        if after != before:
            self.last_dropped = {
                **(self.last_dropped or {}),
                **{k: v for k, v in before.items() if v},
            }

    @staticmethod
    def _asks_replies(u: str) -> bool:
        return bool(REPLY_HINTS.search(u) or LIST_INTENT.search(u) or WRITE_INTENT.search(u))

    def _wants_analysis(self, u: str) -> bool:
        """리뷰를 보자고 했지만 답글 얘기는 없고, 답글 흐름 중도 아니다."""
        return bool(
            REVIEW_NOUN.search(u)
            and not self._asks_replies(u)
            and not self.listed
            and not self.drafts
        )

    @staticmethod
    def _asks_edit(u: str) -> bool:
        return bool(EDIT_HINTS.search(u)) and not (HOLD_WORDS.search(u) and len(u) <= 12)

    def profile(self) -> dict:
        """답글 프롬프트용 가게 정보: 업체명·업종(리뷰 데이터) + 대표 메뉴(메뉴 목록)."""
        sid = self.ctx["store_id"]
        rows = self.ctx["repo"].get_reviews(sid)
        name = rows[0]["store"] if rows else sid
        cat = rows[0].get("category", "") if rows else ""
        menus = [m["name"] for m in (self.ctx["catalog"].menus(sid) if self.ctx["catalog"] else [])]
        return {"name": name, "category": cat, "menus": menus}

    def looks_like_review_question(self, text: str) -> bool:
        t = text.lower()
        return any(h in t for h in self.hints) or bool(REVIEW_HINT_RE.search(t))

    def dispatch(self, name: str, args: dict, utterance: str | None = None) -> dict:
        # routed_call: 규칙·의도 분류가 사장님 말에서 인자를 꺼낸 호출이면 근거 검사를 생략한다
        # (키워드 인자로 받지 않는 이유: dispatch를 감싸는 쪽(벤치마크·팀 비서)이 그대로 쓰도록)
        if not self.routed_call and name == "set_review_report" and utterance is not None:
            why = self._report_write_unfounded(args or {}, utterance)
            if why:
                self.last_tool, self.last_args = name, dict(args or {})
                self.hidden_reviews = []
                return {"error": "not_requested", "message": why}
        res = self._dispatch(name, args, utterance)
        # 직전 결과에서 거른 리뷰만 '원문 보여줘'로 펼쳐 볼 수 있다 (PRD A-2)
        self.hidden_reviews = softened_ids(res)
        return res

    def _report_write_unfounded(self, args: dict, utterance: str) -> str | None:
        """HCX가 고른 보고 설정 변경이 사장님 말에 근거가 없으면 이유를 돌려준다 (실행하지 않음)."""
        if args.get("action") == "suggest":
            return None  # 제안만은 바꾸는 것이 없다
        u = drop_quoted(utterance)
        if self.pending_suggestion and CONFIRM_YES.search(u):
            return None  # 방금 제안한 주기를 받아들임
        if not REPORT_NOUN.search(u):
            return "사장님 말에 리뷰 보고·알림 얘기가 없습니다. 무엇을 바꿀지 되물으세요."
        if not REPORT_VALUE.search(u):
            return "어떻게 바꿀지(주기·요일·시간·알림 종류) 사장님 말에 없습니다. 되물으세요."
        return None

    def wants_original(self, utterance: str) -> bool:
        return bool(
            self.hidden_reviews
            and ORIGINAL_ASK.search(utterance)
            and not re.search(r"올려|등록|게시|답글", utterance)
        )

    def original_message(self) -> str:
        """가렸던 리뷰 원문. 모델을 거치지 않고 시스템이 그대로 보여 준다."""
        by_id = {
            r["review_id"]: r
            for r in self.ctx["repo"].get_reviews(self.ctx["store_id"], None, self.ctx["today"])
        }
        rows = [by_id[i] for i in self.hidden_reviews if i in by_id]
        lines = [ORIGINAL_MSG]
        lines += [f'- "{" ".join(r["text"].split())}" · {r["visit_date"]}' for r in rows]
        return "\n".join(lines)

    def _dispatch(self, name: str, args: dict, utterance: str | None = None) -> dict:
        if name not in self.allowed:
            return {"error": "unknown_tool", "tool": name}
        # LLM이 명세에 없는 인자(예: store_id)를 넣어도 무시한다
        clean = {
            k: v for k, v in (args or {}).items() if k in self.allowed[name] and v not in (None, "")
        }
        if isinstance(clean.get("topic"), list):  # 메뉴 두 개를 목록으로 보냄 (holdout1 C19)
            clean["topic"] = " ".join(str(t) for t in clean["topic"]) or None
            if not clean["topic"]:
                clean.pop("topic")
        if name == "analyze_reviews" and utterance and "topic" not in clean and self.ctx["catalog"]:
            # LLM이 메뉴·항목을 빠뜨림 (2026-11-07 DASH, holdout1 D22 '친절하다는 리뷰')
            menus = resolve_from_utterance(utterance, self.ctx["store_id"], self.ctx["catalog"])[
                "menus"
            ]
            aspect = next((w for w in ASPECT_HINTS if w in utterance), None)
            if menus:
                clean["topic"] = " ".join(menus)
            elif aspect:
                clean["topic"] = aspect
        self.last_dropped = None
        self.last_tool = name  # 실제로 실행한 도구 (시스템이 바꿀 수 있다)
        self.last_args = clean  # 실제로 실행한 인자 (대화 기록을 이 값으로 고친다)
        if utterance is not None and not TIME_HINTS.search(utterance):
            dropped = {k: clean.pop(k) for k in ["period", "start_date", "end_date"] if k in clean}
            if dropped:
                self.last_dropped = dropped  # 로그·디버깅용
        if name == "analyze_reviews" and utterance:
            self._fix_period(clean, utterance)
        try:
            if name == "set_review_report":
                res = set_review_report(
                    store_id=self.ctx["store_id"],
                    today=self.ctx["today"],
                    repo=self.ctx["repo"],
                    settings_store=self.settings_store,
                    now=self.now,
                    **clean,
                )
                self.pending_suggestion = (
                    res.get("suggested") if res.get("action") == "suggest" else None
                )
                return res
            if name == "run_review_report":
                st = self.settings_store.get(self.ctx["store_id"])
                return run_report(**self.ctx, settings=st)
            if name == "get_unanswered_reviews" and utterance and self._wants_analysis(utterance):
                # 답글 얘기가 없는데 미답글 목록을 부름
                # ('부정적인 리뷰만 모아서 보여줘', 벤치마크 B05)
                # → 분석으로 바꾼다. 실제로 실행한 도구는 last_tool에 남긴다
                name = "analyze_reviews"
                clean = {"focus": "complaint"} if TARGET_NEG.search(utterance) else {}
                self.last_tool = name
                self.last_args = clean
                return analyze_reviews(**self.ctx, **clean, utterance=utterance)
            if name == "get_unanswered_reviews":
                res = get_unanswered_reviews(**self.ctx, **clean)
                self.listed = res["reviews"]
                self.listed_for = utterance
                return res
            if name == "draft_review_reply":
                if (
                    self.drafts
                    and clean.get("edit_request")
                    and utterance is not None
                    and not self._asks_edit(utterance)
                ):
                    # 수정 요청이 아닌 말로 초안을 고치지 않는다 ('음 잠깐만', 벤치마크 H06)
                    return {
                        "error": "not_requested",
                        "detail": "사장님은 수정을 요청하지 않았습니다. 초안은 그대로 두세요.",
                    }
                if (
                    utterance is not None
                    and utterance == self.listed_for
                    and not WRITE_INTENT.search(utterance)
                    and not clean.get("edit_request")
                ):
                    # 목록만 물었는데 같은 턴에 초안까지 쓰지 않는다. 어느 리뷰에 쓸지 먼저 묻는다
                    return {
                        "error": "not_requested",
                        "detail": (
                            "사장님은 목록만 물었습니다. 어느 리뷰에 답글을 쓸지 먼저 물으세요."
                        ),
                    }
                return self._draft(clean, utterance)
            if name == "post_review_reply":
                res = post_review_reply(
                    drafts=self.drafts, store=self.reply_store, utterance=utterance
                )
                for r in res.get("results", []):
                    if r.get("posted"):
                        self.drafts.pop(r["review_id"], None)
                        self.listed = [x for x in self.listed if x["review_id"] != r["review_id"]]
                return res
            return analyze_reviews(**self.ctx, **clean, utterance=utterance)
        except (KeyError, ValueError) as e:  # 잘못된 기간 값 등
            return {"error": "bad_arguments", "detail": str(e)[:200], "arguments": clean}

    def _draft(self, args: dict, utterance: str | None = None) -> dict:
        edit = args.get("edit_request")
        if edit and self.drafts:  # 고치기: 직전 초안들이 대상
            ids = list(self.drafts)
        else:  # 새로 쓰기: 직전 미답글 목록에서 고른다
            target = args.get("target") or "first"
            if not self.listed:  # 목록을 안 본 상태면 먼저 만든다
                self.listed = get_unanswered_reviews(**self.ctx, priority="negative_first")[
                    "reviews"
                ]
            pool = self.listed
            if target == "negative":
                pool = [x for x in pool if x["sentiment"] in ("negative", "mixed")] or pool
            ids = [x["review_id"] for x in (pool if target == "all_listed" else pool[:1])]
        prev = {rid: d["reply_ko"] for rid, d in self.drafts.items()}
        # 첫 초안에도 사장님 지시를 원어 그대로 넣는다 ('답글 써줘, 다음에 음료 서비스 준다고')
        instruction = None if edit else (self.original_utterance or utterance)
        owner_promise = any(self.drafts[r].get("owner_promise") for r in ids if r in self.drafts)
        res = draft_review_reply(
            **self.ctx,
            review_ids=ids,
            writer=self.writer,
            owner_lang="ko",  # 초안 번역은 run_turn 출력 단계(Papago)에서 한다
            glossary=menu_glossary(self.ctx["catalog"], self.ctx["store_id"], self.owner_lang),
            edit_request=edit,
            previous=prev,
            profile=self.profile(),
            recent=self.reply_store.recent_replies(),
            instruction=instruction,
            owner_promise=owner_promise,
        )
        for d in res.get("drafts", []):
            if "reply_ko" in d:
                rid = d["review_id"]
                self.draft_versions[rid] = self.draft_versions.get(rid, 0) + 1
                # 사본에만 붙인다: res는 그대로 모델에 돌아가므로 도구 결과 모양을 바꾸지 않는다
                self.drafts[rid] = {**d, "version": self.draft_versions[rid]}
        return res


def _call(id_: str, name: str, arguments) -> dict:
    """도구 호출 한 건 (CLOVA v3 toolCalls[] 모양). 시스템이 직접 실행하는 호출도 같은 모양으로."""
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


LANG_EN = {
    "ru": "Russian",
    "en": "English",
    "ko": "Korean",
    "vi": "Vietnamese",
    "zh": "Chinese",
}
QUOTE_HEADER = {"ko": "대표 리뷰", "en": "Sample reviews", "ru": "Примеры отзывов"}


SCRIPT = {
    "ru": re.compile(r"[А-Яа-яЁё]"),
    "en": re.compile(r"^[A-Za-z][A-Za-z\s'-]*$"),
    # 베트남어: 성조·특수 모음이 하나라도 있는 라틴 표기 (Bún chả)
    "vi": re.compile(
        r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I
    ),
    "zh": re.compile(r"[\u4e00-\u9fff]"),
}


def menu_glossary(catalog, store_id: str, owner_lang: str) -> dict[str, str]:
    """메뉴 정식명 → 사장님 언어 표기 (사장님 RAG의 다국어 별칭에서). HCX가 메뉴명을 엉뚱하게
    번역하는 것 방지."""
    pat = SCRIPT.get(owner_lang)
    out = {}
    for m in catalog.menus(store_id) if catalog and pat else []:
        hit = next((a for a in m.get("aliases", []) if pat.search(a)), None)
        if hit:
            out[m["name"]] = hit
    return out


def menu_swaps(catalog, store_id: str, owner_lang: str) -> tuple[dict, dict]:
    """번역 전에 메뉴 이름을 바꿔 넣을 표 두 개.
    지금은 MenuCatalog(fixtures 더미)에서 만든다. 나중에 Part B StoreKnowledge.list_menu()의
    MenuItem(name_ko = 한국어 메뉴명, name_native = 사장님 원문 메뉴명, aliases)로 바꾼다.
    - 출력용 {한국어 이름·한국어 별칭: 사장님 언어 표기} ('딸기 앙찌'처럼 띄어 쓴 별칭도)
    - 입력용 {사장님 언어 별칭: 한국어 정식명}"""
    pat = SCRIPT.get(owner_lang)
    out_map, in_map = {}, {}
    for m in catalog.menus(store_id) if catalog and pat else []:
        foreign = [a for a in m.get("aliases", []) if pat.search(a)]
        if not foreign:
            continue
        for k in [m["name"], *(a for a in m.get("aliases", []) if re.search(r"[가-힣]", a))]:
            out_map[k] = foreign[0]
        for a in foreign:
            in_map[a] = m["name"]
    return out_map, in_map


def translate_quotes(
    client, model: str, quotes: list[str], owner_lang: str, glossary: dict[str, str] | None = None
) -> list[str | None]:
    """리뷰 인용만 번역하는 전용 호출. 답변 모델에게 맡기면 번역을 지어냄 (2026-11-07 실측).
    실패하면 None."""
    lang = LANG_EN.get(owner_lang, owner_lang)
    listing = "\n".join(f"[{i}] {q}" for i, q in enumerate(quotes))
    gloss = (
        ("Use these menu names: " + "; ".join(f"{k} = {v}" for k, v in glossary.items()) + ". ")
        if glossary
        else ""
    )
    try:
        raw = client.chat(
            model,
            [
                {
                    "role": "system",
                    "content": f"Translate each Korean customer review into {lang}, literally and "
                    "briefly. "
                    f"{gloss}Do not add or guess anything that is not in the Korean text. "
                    'Answer only JSON: {"t": ["translation 0", "translation 1", ...]}',
                },
                {"role": "user", "content": listing},
            ],
            temperature=0,
            **model_options(model, 512),
        )["content"]
        data = json.loads(
            re.sub(
                r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', raw[raw.find("{") : raw.rfind("}") + 1]
            )
        )
        out = data.get("t") or next(
            (v for v in data.values() if isinstance(v, list)), []
        )  # 키 이름이 달라도 읽는다
        out = [str(x).strip() or None for x in out]
        return out[: len(quotes)] + [None] * (len(quotes) - len(out))
    except Exception:
        return [None] * len(quotes)


REPLY_MSG = {
    "ko": {
        "none": "답글 안 단 리뷰가 없어요.",
        "list": (
            "답글 안 단 리뷰는 {total}건이에요{neg}.{risk} 어느 리뷰에 답글을 쓸까요? "
            "(예를 들어 '안 좋은 리뷰에 답글 써줘', '전부 써줘')"
        ),
        "neg": ", 그중 불만이 담긴 리뷰가 {n}건이에요",
        "risk": " 🚨 위생·안전 관련 리뷰가 {n}건 있어 먼저 확인하시는 게 좋아요.",
        "escalate": (
            "위생·안전과 관련된 리뷰(감지: {words})라서, 원인을 단정하지 않고 사실 확인 중이라고 "
            "안내하는 짧은 답글로 썼어요. 사장님이 내용을 직접 확인하신 뒤 올려 주세요. 이대로 "
            "올릴까요?"
        ),
        "draft": "초안을 만들었어요. 이대로 올릴까요, 고칠 부분이 있을까요?",
        "revised": "말씀하신 대로 고쳤어요. 이대로 올릴까요?",
        "issues": "초안에 확인이 필요한 부분이 있어요: {issues}. 고쳐 드릴까요?",
        "no_target": (
            "어느 리뷰에 답글을 쓸지 모르겠어요. 먼저 '답글 안 단 리뷰 보여줘'라고 해 주세요."
        ),
        "posted": "답글 {n}건을 등록했어요. (데모 화면이라서 실제 네이버에는 올라가지 않아요)",
        "post_blocked": "확인이 필요한 부분이 있어 등록하지 않았어요: {issues}",
        "not_confirmed": "등록할까요? '이대로 올려줘'라고 하시면 등록할게요.",
        "no_draft": "등록할 초안이 없어요. 먼저 답글을 써 볼까요?",
    },
    "en": {
        "none": "There are no reviews without a reply.",
        "list": (
            "There are {total} reviews without a reply{neg}.{risk} Which one should I reply to? (e."
            "g. 'reply to the negative one', 'reply to all')"
        ),
        "neg": ", including {n} with complaints",
        "risk": " 🚨 {n} mention hygiene or safety issues — please check those first.",
        "escalate": (
            "This review mentions a hygiene or safety issue ({words}), so I wrote a short reply "
            "that apologizes and says you're checking the facts, without admitting a cause. Please "
            "check it yourself before posting. Post as is?"
        ),
        "draft": "Here is a draft. Shall I post it as is, or would you like changes?",
        "revised": "I've revised it as you asked. Shall I post it?",
        "issues": "The draft needs a check: {issues}. Shall I fix it?",
        "no_target": (
            "I'm not sure which review to reply to. Please ask me to show reviews without a reply "
            "first."
        ),
        "posted": "Posted {n} reply(ies). (Demo: nothing is actually posted to Naver)",
        "post_blocked": "Not posted because the draft needs a check: {issues}",
        "not_confirmed": "Shall I post it? Say 'post it as is' and I will.",
        "no_draft": "There is no draft to post yet. Shall I write one?",
    },
    "ru": {
        "none": "Отзывов без ответа нет.",
        "list": (
            "Отзывов без ответа: {total}{neg}.{risk} На какой ответить? (например: «ответь на "
            "плохой отзыв», «ответь на все»)"
        ),
        "neg": ", из них с жалобами: {n}",
        "risk": " 🚨 Касаются гигиены или безопасности: {n} — проверьте их в первую очередь.",
        "escalate": (
            "Отзыв касается гигиены или безопасности ({words}), поэтому ответ короткий: извинение "
            "и сообщение, что факты проверяются, без признания причины. Проверьте сами перед "
            "публикацией. Опубликовать?"
        ),
        "draft": "Черновик готов. Опубликовать как есть или что-то изменить?",
        "revised": "Исправил, как вы просили. Опубликовать?",
        "issues": "В черновике нужно кое-что проверить: {issues}. Исправить?",
        "no_target": (
            "Не понимаю, на какой отзыв отвечать. Сначала попросите показать отзывы без ответа."
        ),
        "posted": "Опубликовано ответов: {n}. (Демо: на Naver ничего не публикуется)",
        "post_blocked": "Не опубликовано, нужно проверить: {issues}",
        "not_confirmed": "Опубликовать? Скажите «опубликуй как есть».",
        "no_draft": "Черновика пока нет. Написать ответ?",
    },
}


REPORT_MSG = {
    "ko": {
        "freq": {
            "daily": "매일",
            "weekly": "매주 {wd}",
            "biweekly": "격주 {wd}",
            "monthly": "매달 1일",
        },
        "wd": {
            "mon": "월요일",
            "tue": "화요일",
            "wed": "수요일",
            "thu": "목요일",
            "fri": "금요일",
            "sat": "토요일",
            "sun": "일요일",
        },
        "focus": {"all": "", "complaint": " (불만 위주)"},
        "alert": {
            "negative": " 부정 리뷰는 올라오는 대로 바로 알려드려요.",
            "every_new": " 새 리뷰는 올라올 때마다 알려드려요.",
            "none": "",
        },
        "set": "{when} {time}에 리뷰 보고를 보내드릴게요{focus}.{alert} 다음 보고: {next}",
        "from_suggestion": " (최근 리뷰 수를 보고 정한 주기예요)",
        "suggest": (
            "최근 4주 동안 리뷰가 {n}건(주 평균 {avg}건)이라 {freq} 보고가 적당해 보여요. {reason}."
            " 이렇게 설정할까요?"
        ),
        "suggest_more": " 리뷰가 뜸한 편이라 손님께 리뷰를 부탁하는 문구도 만들어 드릴 수 있어요.",
        "off": "정기 보고를 껐어요. 다시 받고 싶으시면 언제든 말씀해 주세요.",
        "now": "리뷰 보고서예요.",
    },
    "en": {
        "freq": {
            "daily": "every day",
            "weekly": "every {wd}",
            "biweekly": "every other {wd}",
            "monthly": "on the 1st of each month",
        },
        "wd": {
            "mon": "Monday",
            "tue": "Tuesday",
            "wed": "Wednesday",
            "thu": "Thursday",
            "fri": "Friday",
            "sat": "Saturday",
            "sun": "Sunday",
        },
        "focus": {"all": "", "complaint": " (focused on complaints)"},
        "alert": {
            "negative": " I'll alert you right away about negative reviews.",
            "every_new": " I'll alert you about every new review.",
            "none": "",
        },
        "set": "I'll send a review report {when} at {time}{focus}.{alert} Next report: {next}",
        "from_suggestion": " (frequency chosen from your recent review volume)",
        "suggest": (
            "You had {n} reviews in the last 4 weeks ({avg} per week), so a {freq} report seems "
            "right. Shall I set it up?"
        ),
        "suggest_more": (
            " Reviews are sparse, so I can also write a message asking customers for reviews."
        ),
        "off": "Regular reports are turned off. Just tell me if you want them again.",
        "now": "Here is your review report.",
    },
    "ru": {
        "freq": {
            "daily": "каждый день",
            "weekly": "каждый {wd}",
            "biweekly": "раз в две недели ({wd})",
            "monthly": "1-го числа каждого месяца",
        },
        "wd": {
            "mon": "понедельник",
            "tue": "вторник",
            "wed": "среда",
            "thu": "четверг",
            "fri": "пятница",
            "sat": "суббота",
            "sun": "воскресенье",
        },
        "focus": {"all": "", "complaint": " (только жалобы)"},
        "alert": {
            "negative": " О негативных отзывах сообщу сразу.",
            "every_new": " О каждом новом отзыве сообщу сразу.",
            "none": "",
        },
        "set": (
            "Буду присылать отчёт по отзывам {when} в {time}{focus}.{alert} Следующий отчёт: {next}"
        ),
        "from_suggestion": " (частота выбрана по количеству отзывов)",
        "suggest": (
            "За последние 4 недели было {n} отзыва(ов) ({avg} в неделю), поэтому подойдёт отчёт с "
            "частотой «{freq}». Настроить?"
        ),
        "suggest_more": " Отзывов мало — могу подготовить текст с просьбой оставить отзыв.",
        "off": "Регулярные отчёты отключены.",
        "now": "Вот отчёт по отзывам.",
    },
}


def report_message(tool, result, owner_lang) -> str | None:
    m = REPORT_MSG.get(owner_lang) or REPORT_MSG["en"]
    if tool == "run_review_report":
        return m["now"]
    if result.get("action") == "off":
        return m["off"]
    if result.get("action") == "suggest":
        sg = result["suggested"]
        freq = m["freq"][sg["frequency"]].format(wd=m["wd"]["mon"])
        msg = m["suggest"].format(
            n=sg["reviews_28d"], avg=sg["weekly_avg"], freq=freq, reason=sg["reason"]
        )
        return msg + (m["suggest_more"] if sg.get("suggest_increase_reviews") else "")
    st = result["settings"]
    when = m["freq"][st["frequency"]].format(wd=m["wd"][st["weekday"]])
    nxt = result.get("next_run", "").replace("T", " ")
    msg = m["set"].format(
        when=when,
        time=st["time"],
        focus=m["focus"].get(st.get("focus", "all"), ""),
        alert=m["alert"].get(st.get("alert", "negative"), ""),
        next=nxt,
    )
    return msg + (m["from_suggestion"] if result.get("changed", {}).get("from_suggestion") else "")


def reply_message(tool, result, owner_lang) -> str | None:
    """답글·보고 도구 결과 → 정해진 안내 문장. 해당 도구가 아니거나 지원하지 않는 언어면 None (모델
    답변 사용)."""
    if tool in REPORT_TOOLS and result and not result.get("error"):
        return report_message(tool, result, owner_lang)
    if tool not in REPLY_TOOLS or not result or owner_lang not in REPLY_MSG:
        return None
    m = REPLY_MSG[owner_lang]
    err = result.get("error")
    if tool == "get_unanswered_reviews":
        if not result.get("total"):
            return m["none"]
        neg = m["neg"].format(n=result["negative_count"]) if result.get("negative_count") else ""
        risk = (
            m["risk"].format(n=result["high_risk_count"]) if result.get("high_risk_count") else ""
        )
        return m["list"].format(total=result["total"], neg=neg, risk=risk)
    if tool == "draft_review_reply":
        if err == "no_target_review":
            return m["no_target"]
        ds = [d for d in result.get("drafts", []) if "reply_ko" in d]
        bad = sorted({i for d in ds for i in d["checks"]["issues"]})
        if bad:
            return m["issues"].format(issues=", ".join(bad))
        esc = [d for d in ds if d.get("risk")]
        if esc and not any(d.get("revised") for d in ds):
            return m["escalate"].format(words=", ".join(esc[0]["risk"]["words"]))
        return m["revised"] if any(d.get("revised") for d in ds) else m["draft"]
    if tool == "post_review_reply":
        if err in ("not_confirmed", "no_draft"):
            return m[err]
        n = result.get("posted_count", 0)
        if n:
            return m["posted"].format(n=n)
        bad = sorted({i for r in result.get("results", []) for i in (r.get("issues") or [])})
        return m["post_blocked"].format(issues=", ".join(bad))
    return None


DRAFT_HEADER = {"ko": "답글 초안", "en": "Reply draft", "ru": "Черновик ответа"}
# 의도 체크리스트 (제안 구조 D1-b): 사장님이 책임질 사실만 사장님 언어로. 전체 번역은 그 아래
CHECK_LABEL = {
    "ko": {
        "title": "확인할 점",
        "apology": "사과함",
        "no_apology": "사과 없음",
        "promise": "약속",
        "menu": "메뉴",
        "full": "전체 번역",
    },
    "en": {
        "title": "Check before posting",
        "apology": "Apologizes",
        "no_apology": "No apology",
        "promise": "Promise",
        "menu": "Menu",
        "full": "Full translation",
    },
    "ru": {
        "title": "Проверьте перед публикацией",
        "apology": "Есть извинение",
        "no_apology": "Без извинения",
        "promise": "Обещание",
        "menu": "Меню",
        "full": "Полный перевод",
    },
    "vi": {
        "title": "Kiểm tra ý định",
        "apology": "Đã xin lỗi",
        "no_apology": "Chưa xin lỗi",
        "promise": "Hứa",
        "menu": "Món",
        "full": "Bản dịch đầy đủ",
    },
    "zh": {
        "title": "发布前确认",
        "apology": "已道歉",
        "no_apology": "未道歉",
        "promise": "承诺",
        "menu": "菜单",
        "full": "完整翻译",
    },
}


def checklist_lines(d: dict, owner_lang: str) -> list[str]:
    """⚠ 약속을 맨 위에, 그다음 사과 여부·메뉴. 한국어 사장님은 약속만 표시한다."""
    c = d.get("checklist") or {}
    lab = CHECK_LABEL.get(owner_lang, CHECK_LABEL["en"])
    promises = c.get("promises") or []
    shown = c.get("promises_translated") or promises
    lines = [f"⚠️ {lab['promise']}: {p}" for p in shown]
    if owner_lang == "ko":
        return lines
    lines.append(("✔ " + lab["apology"]) if c.get("apology") else ("✖ " + lab["no_apology"]))
    for ko, native in c.get("menus") or []:
        lines.append(f"{lab['menu']}: {native} ({ko})")
    return [f"**{lab['title']}**", *[f"- {x}" for x in lines]]


LIST_HEADER = {"ko": "답글 안 단 리뷰", "en": "Reviews without a reply", "ru": "Отзывы без ответа"}
SENT_MARK = {"negative": "🔴", "mixed": "🟠", "positive": "🟢", "neutral": "⚪"}


def render_block(client, model, tool, result, owner_lang, glossary=None) -> str:
    """도구 결과의 원문(리뷰·초안)은 모델이 아니라 시스템이 그대로 붙인다. 번역은 전용 호출."""
    if not result or result.get("error"):
        return ""
    if tool == "analyze_reviews":
        return quote_block(client, model, result, owner_lang, glossary)
    if tool == "get_unanswered_reviews" and result.get("reviews"):
        rv = result["reviews"]
        trans = (
            translate_quotes(client, model, [r["quote"] for r in rv], owner_lang, glossary)
            if owner_lang != "ko"
            else [None] * len(rv)
        )
        lines = [f"\n\n**{LIST_HEADER.get(owner_lang, LIST_HEADER['en'])}** ({result['total']})"]
        for i, (r, t) in enumerate(zip(rv, trans, strict=False), 1):
            mark = "🚨" if r.get("risk") else SENT_MARK.get(r["sentiment"], "")
            lines.append(
                f'{i}. {mark} "{r["quote"]}"' + (f" ({t})" if t else "") + f" · {r['visit_date']}"
            )
        if any(r.get("softened") for r in rv):
            lines.append(f"_{HIDDEN_HINT}_")
        return "\n".join(lines)
    if tool == "run_review_report":
        return report_block(client, model, result, owner_lang, glossary)
    if tool == "draft_review_reply" and result.get("drafts"):
        lines = []
        for d in result["drafts"]:
            if "reply_ko" not in d:
                continue
            lines.append(
                f"\n\n**{DRAFT_HEADER.get(owner_lang, DRAFT_HEADER['en'])}** · 리뷰: "
                f'"{d["review_quote"]}"'
            )
            lines.extend(checklist_lines(d, owner_lang))
            lines.append(f"> {d['reply_ko']}")
            if d.get("reply_translated"):
                lab = CHECK_LABEL.get(owner_lang, CHECK_LABEL["en"])
                lines.append(f"> ({lab['full']}: {d['reply_translated']})")
            if not d["checks"]["passed"]:
                lines.append(f"⛔ {', '.join(d['checks']['issues'])}")
            warns = [  # 약속은 체크리스트 맨 위에 이미 보여 준다
                w for w in d["checks"].get("warnings", []) if not w.startswith("약속 포함")
            ]
            if warns:
                lines.append(f"⚠️ {', '.join(warns)}")
            if d.get("owner_check"):
                lines.append(f"🚨 사장님 확인 필요: {', '.join(d['risk']['words'])}")
        return "\n".join(lines)
    return ""


REPORT_LABEL = {
    "ko": [
        "기간",
        "리뷰",
        "지난 기간",
        "칭찬과 불만",
        "많이 나온 불만",
        "많이 나온 칭찬",
        "답글 안 단 리뷰",
        "불만",
        "요약",
        "리뷰가 뜸해요. 손님께 리뷰를 부탁하는 문구를 만들어 드릴까요?",
        "건",
        "긍정",
        "부정",
        "혼합",
        "중립",
        "항목별 만족도",
        "개선 제안",
        "홍보 포인트",
    ],
    "en": [
        "Period",
        "Reviews",
        "previous period",
        "Sentiment",
        "Top complaints",
        "Top praise",
        "Reviews without a reply",
        "complaints",
        "Summary",
        "Reviews are sparse. Shall I write a message asking customers for reviews?",
        "",
        "positive",
        "negative",
        "mixed",
        "neutral",
        "Satisfaction by aspect",
        "Suggested actions",
        "Marketing points",
    ],
    "ru": [
        "Период",
        "Отзывы",
        "прошлый период",
        "Тональность",
        "Частые жалобы",
        "Частые похвалы",
        "Отзывы без ответа",
        "жалобы",
        "Итоги",
        "Отзывов мало. Подготовить текст с просьбой оставить отзыв?",
        "",
        "позитив",
        "негатив",
        "смешанные",
        "нейтральные",
        "Удовлетворённость по аспектам",
        "Что улучшить",
        "Что продвигать",
    ],
}


def report_block(client, model, result, owner_lang, glossary=None) -> str:
    """보고서 본문: 숫자는 도구 결과 그대로, 요약·인용 번역은 전용 호출."""
    L = REPORT_LABEL.get(owner_lang) or REPORT_LABEL["en"]
    a = result["analysis"]
    p = a.get("period", {})
    lines = [f"\n\n**{L[0]}**: {p.get('start') or '…'} ~ {p.get('end')}"]
    prev = a.get("previous_period", {}).get("review_count")
    lines.append(
        f"- {L[1]}: {a.get('review_count', 0)}{L[10]}"
        + (f" ({L[2]} {prev}{L[10]})" if prev is not None else "")
    )
    if a.get("sentiment"):
        names = {"positive": L[11], "negative": L[12], "mixed": L[13], "neutral": L[14]}
        lines.append(
            f"- {L[3]}: " + ", ".join(f"{names.get(k, k)} {v}" for k, v in a["sentiment"].items())
        )
    asp = a.get("aspects", [])
    ideas = a.get("report_ideas") or {}
    neg = [f"{x['display']} {x['count']}" for x in ideas.get("complaints_top", [])] or [
        f"{x['display']} {x['negative']}"
        for x in sorted(asp, key=lambda x: -x["negative"])
        if x["negative"]
    ][:3]
    pos = [f"{x['display']} {x['count']}" for x in ideas.get("praise_top", [])] or [
        f"{x['display']} {x['positive']}"
        for x in sorted(asp, key=lambda x: -x["positive"])
        if x["positive"]
    ][:3]
    if neg:
        lines.append(f"- {L[4]}: " + ", ".join(neg))
    if pos:
        lines.append(f"- {L[5]}: " + ", ".join(pos))
    sat = [
        x for x in asp if x.get("satisfaction") is not None and x["positive"] + x["negative"] >= 2
    ]
    if sat:  # 판단된 언급이 2개 이상인 항목만 (1건짜리 100%·0%는 오해를 부름)
        lines.append(
            f"- {L[15]}: "
            + ", ".join(
                f"{x['display']} {round(x['satisfaction'] * 100)}%"
                for x in sorted(sat, key=lambda x: -(x["positive"] + x["negative"]))[:5]
            )
        )
    u = result.get("unanswered", {})
    lines.append(
        f"- {L[6]}: {u.get('total', 0)}{L[10]}"
        + (f" ({L[7]} {u['negative_count']})" if u.get("negative_count") else "")
    )
    ins = a.get("insights") or []
    if ins and not a.get("few_reviews"):
        if owner_lang != "ko":
            ins = [
                t or o
                for t, o in zip(
                    translate_quotes(client, model, ins, owner_lang, glossary), ins, strict=False
                )
            ]
        lines.append(f"\n**{L[8]}**\n" + "\n".join(f"- {x}" for x in ins))
    acts, mkt = ideas.get("improvements") or [], ideas.get("marketing") or []
    if (acts or mkt) and not a.get("few_reviews"):
        if owner_lang != "ko":
            tr = translate_quotes(client, model, acts + mkt, owner_lang, glossary)
            acts = [t or o for t, o in zip(tr[: len(acts)], acts, strict=False)]
            mkt = [t or o for t, o in zip(tr[len(acts) :], mkt, strict=False)]
        if acts:
            lines.append(f"\n**{L[16]}**\n" + "\n".join(f"- {x}" for x in acts))
        if mkt:
            lines.append(f"\n**{L[17]}**\n" + "\n".join(f"- {x}" for x in mkt))
    if a.get("review_count"):
        lines.append(quote_block(client, model, a, owner_lang, glossary))
    if result.get("suggest_increase_reviews"):
        lines.append(f"\n💡 {L[9]}")
    return "\n".join(lines)


def quote_block(
    client, model: str, result: dict | None, owner_lang: str, glossary: dict[str, str] | None = None
) -> str:
    ex = (result or {}).get("examples") or []
    quotes = [e["quote"] for e in ex[:3]]
    if not quotes:
        return ""
    trans = (
        translate_quotes(client, model, quotes, owner_lang, glossary)
        if owner_lang != "ko"
        else [None] * len(quotes)
    )
    lines = [f"\n\n**{QUOTE_HEADER.get(owner_lang, QUOTE_HEADER['en'])}**"]
    for e, q, t in zip(ex, quotes, trans, strict=False):
        lines.append(f'- "{q}"' + (f" ({t})" if t else "") + f" · {e['visit_date']}")
    if any(e.get("softened") for e in ex[:3]):
        lines.append(f"_{HIDDEN_HINT}_")
    return "\n".join(lines)


# 기간을 넓혔다는 안내는 모델이 아니라 시스템이 붙인다: 모델이 자기가 넣은 period(7d)와 섞어
# '최근 일주일', '7일로'라고 말함 (2026-11-07 실측, 실제로는 30일로 넓힘)
EXPAND_MSG = {
    "ko": "기간을 정하지 않으셔서, 최근 {from_n}일에 리뷰가 없어 {span}({start} ~ {end})로 "
    "넓혀 봤어요.",
    "en": "You didn't set a period and there were no reviews in the last {from_n} days, "
    "so I looked at {span} ({start} ~ {end}).",
    "ru": "Период не указан, а за последние {from_n} дн. отзывов нет, поэтому я посмотрел "
    "{span} ({start} ~ {end}).",
    "vi": "Bạn chưa chọn khoảng thời gian và {from_n} ngày gần đây không có review, "
    "nên tôi đã xem {span} ({start} ~ {end}).",
    "zh": "您没有指定时间，最近{from_n}天没有评论，所以我查看了{span}（{start} ~ {end}）。",
}
SPAN = {
    "ko": ("최근 {n}일", "전체 기간"),
    "en": ("the last {n} days", "all reviews"),
    "ru": ("последние {n} дн.", "весь период"),
    "vi": ("{n} ngày gần đây", "toàn bộ thời gian"),
    "zh": ("最近{n}天", "全部时间"),
}
# 분석 답변 끝에 붙는 답글용 확인 질문 (모델이 답글 지시를 분석 답변에도 따라 씀)
CONFIRM_Q = re.compile(
    r"이대로.{0,20}까요|(고칠|수정할)\s?(부분|내용|사항|점).{0,10}(있을까요|있으신가요)|"
    r"shall i post|post it as is|would you like (any )?(changes|edits)|"
    r"опубликовать\?|внести изменения",
    re.I,
)


# 기간 안내를 붙인 답변에서 모델이 따로 말한 기간 표현 ('지난 7일 동안', 'in the last week')
PERIOD_PHRASE = re.compile(
    r"(지난|최근|요즘)\s?(\d+\s?일|일주일|한\s?주|이번\s?주|한\s?달|1개월|\d+개월)\s?(동안|간)?\s?|"
    r"\b(in|over|during) the (last|past) (\d+ days|week|month|\d+ months),?\s?|"
    r"\bthis (week|month),?\s?",
    re.I,
)


def expand_notice(result: dict | None, owner_lang: str) -> str:
    pe = (result or {}).get("period_expanded")
    p = (result or {}).get("period") or {}
    if not pe:
        return ""
    lang = owner_lang if owner_lang in EXPAND_MSG else "en"
    days, whole = SPAN[lang]
    to = str(pe.get("to", ""))
    span = whole if to == "all" else days.format(n=to.rstrip("d"))
    start = p.get("start") or "…"
    return EXPAND_MSG[lang].format(
        from_n=str(pe.get("from", "7d")).rstrip("d"), span=span, start=start, end=p.get("end", "")
    )


def strip_confirm(text: str) -> str:
    """분석 답변에서 답글용 확인 질문 문장만 지운다 (줄 구조는 유지)."""
    if not CONFIRM_Q.search(text):
        return text
    lines = []
    for line in text.split("\n"):
        sents = re.split(r"(?<=[.!?。])\s+", line)
        kept = [x for x in sents if not CONFIRM_Q.search(x)]
        if line.strip() and not kept:
            continue  # 확인 질문만 있던 줄은 통째로 뺀다
        lines.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def model_view(tool: str, result: dict) -> dict:
    """모델에게 보여 줄 도구 결과.

    기간 확장 안내는 시스템이 붙이므로 '7d→30d' 숫자를 빼고 알린다.
    """
    if tool == "analyze_reviews" and result.get("period_expanded"):
        result = dict(result)
        result["period_expanded"] = (
            "시스템이 기간 안내 문장을 답변 앞에 붙입니다. 기간을 말하지 마세요."
        )
    return result


def _last_user(messages: list[dict]) -> int:
    return max(i for i, m in enumerate(messages) if m.get("role") == "user")


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


def run_turn(
    client,
    model: str,
    messages: list[dict],
    toolbox: ReviewToolbox,
    log=None,
    max_rounds: int = 4,
    owner_lang: str = "ko",
    translator=None,
) -> str:
    """사장님 한 턴. 안에서는 전부 한국어로 처리하고, 사장님 언어가 한국어가 아니면
    입력을 한국어로, 최종 출력을 사장님 언어로 번역한다 (Papago 우선, 안 되면 HCX).
    messages에는 한국어 문장이 쌓인다 (HCX는 늘 한국어 대화만 본다)."""
    if owner_lang == "ko":
        return _run_turn_ko(client, model, messages, toolbox, log, max_rounds)
    from .translate import HCXTranslator, TranslateError, Translator, localize, to_korean

    tr = translator or Translator(None, HCXTranslator(client, model))
    out_names, in_names = menu_swaps(toolbox.ctx["catalog"], toolbox.ctx["store_id"], owner_lang)
    i = _last_user(messages)
    original = messages[i]["content"]
    try:
        korean = to_korean(original, owner_lang, tr, in_names)
    except TranslateError as e:  # 입력 번역이 안 되면 원문 그대로 (HCX가 직접 이해)
        korean = original
        if log:
            log("(입력 번역 실패)", {"error": str(e)}, {})
    messages[i] = {**messages[i], "content": korean}
    if log and korean != original:
        log("(입력 번역)", {"engine": tr.last_engine, "to": "ko"}, {"text": korean})
    toolbox.original_utterance = original
    try:
        out_ko = _run_turn_ko(client, model, messages, toolbox, log, max_rounds)
    finally:
        toolbox.original_utterance = None
    try:
        out = localize(out_ko, owner_lang, tr, out_names)
    except TranslateError as e:  # 번역이 안 되면 한국어로라도 보여 준다
        if log:
            log("(출력 번역 실패)", {"error": str(e)}, {})
        return out_ko
    if log:
        log("(출력 번역)", {"engine": tr.last_engine, "to": owner_lang}, {})
    return out


def _run_turn_ko(
    client,
    model: str,
    messages: list[dict],
    toolbox: ReviewToolbox,
    log=None,
    max_rounds: int = 4,
    owner_lang: str = "ko",
) -> str:
    """messages에 사장님 발화가 들어 있는 상태로 호출. 도구 호출을 처리하고 최종 답변 문자열을
    돌려준다.
    messages는 제자리에서 갱신되므로 다음 턴에 그대로 이어 쓰면 된다."""
    opts = model_options(model, 1024)  # 도구 호출은 최대 토큰 1024 이상 (CLOVA Studio 문서)
    # 리뷰 질문에 도구 없이 답하면 (1) 한 번 더 요청하고, (2) 그래도 안 부르면 시스템이 직접
    # 도구를 실행한다. (OpenAI 호환 API에서는 도구 지정 toolChoice가 400이었고, 네이티브 v3에서는
    # 된다(2026-11-08 실측). 강제 지정으로 바꿀지는 시나리오로 비교한 뒤 정한다.)
    nudged = False
    last_result, last_tool = None, None
    gloss = menu_glossary(toolbox.ctx["catalog"], toolbox.ctx["store_id"], owner_lang)
    utter = messages[_last_user(messages)]["content"]  # 사장님 원래 질문 (재요청 메시지와 구분)
    if toolbox.original_utterance and toolbox.original_utterance != utter:
        # 규칙(확정·수정·기간 표현)은 번역문과 원문을 함께 본다 ('Post it'이 엉뚱하게 번역돼도 잡힘)
        utter = f"{utter}\n{toolbox.original_utterance}"
    start = _last_user(messages)
    if toolbox.wants_original(utter):  # 가린 리뷰 원문 (도구·모델 없이)
        text = toolbox.original_message()
        messages.append({"role": "assistant", "content": text})
        if log:
            log("(원문 보기)", {}, {"review_ids": toolbox.hidden_reviews})
        return text
    # 실행 설정: 규칙 라우터는 도구 5개 + 재요청·시스템 직접 실행, 의도 라우터는 plan()이 정한다
    specs, tool_choice, rule_fallbacks = toolbox.specs, None, True
    plan = None
    hybrid_routed = None
    if toolbox.router == "hybrid":
        hybrid_routed = toolbox.route(utter)
        held = (
            not hybrid_routed
            and toolbox.drafts
            and HOLD_WORDS.search(utter)
            and not toolbox._asks_edit(utter)
        )
        if not hybrid_routed and not held:
            from .intent import classify, hybrid_plan

            toolbox.last_intent = classify(client, utter, toolbox, messages[:start])
            if toolbox.last_intent.get("intent"):
                plan = hybrid_plan(toolbox.last_intent, utter, toolbox)
            if log:
                log("(의도)", toolbox.last_intent, plan or {"fallback": "rules"})
    elif toolbox.router == "intent":
        from .intent import classify
        from .intent import plan as intent_plan

        toolbox.last_intent = classify(client, utter, toolbox, messages[:start])
        plan = intent_plan(toolbox.last_intent, utter, toolbox)
        if log:
            log("(의도)", toolbox.last_intent, plan or {"fallback": "rules"})
    if plan is not None:
        routed = plan.get("routed")
        if plan.get("hold"):
            messages.append({"role": "assistant", "content": HOLD_MSG})
            return HOLD_MSG
        if not routed:
            specs, tool_choice, rule_fallbacks = plan["specs"], plan["tool_choice"], False
    else:  # 규칙 라우터 (의도 분류가 실패했을 때도 여기로)
        routed = hybrid_routed if toolbox.router == "hybrid" else toolbox.route(utter)
        if (
            not routed
            and toolbox.drafts
            and HOLD_WORDS.search(utter)
            and not toolbox._asks_edit(utter)
        ):
            messages.append({"role": "assistant", "content": HOLD_MSG})
            return HOLD_MSG
    if routed:
        name, args = routed
        toolbox.routed_call = True
        try:
            result = toolbox.dispatch(name, args, utterance=utter)
        finally:
            toolbox.routed_call = False
        if log:
            log(f"(규칙) {name}" if plan is None else f"(의도) {name}", args, result)
        text = (
            reply_message(name, result, owner_lang) or json.dumps(result, ensure_ascii=False)[:300]
        )
        messages.append({"role": "assistant", "content": text})
        return text + render_block(client, model, name, result, owner_lang, gloss)
    unsupported = False
    for _ in range(max_rounds):
        used_tool = any(m.get("role") == "tool" for m in messages[start:])
        force = tool_choice if not used_tool else None  # 도구 결과를 받은 뒤에는 지정하지 않는다
        try:
            extra = {"tool_choice": force} if force is not None else {}  # 규칙 버전 호출은 그대로
            msg = client.chat(model, messages, tools=specs or None, **extra, **opts)
        except ClovaError as e:
            if force is not None:  # 도구 지정이 400이면 지정 없이 한 번 더 (2026-11-08 실측 제약)
                tool_choice = None
                if log:
                    log("(도구 지정 실패)", {"error": str(e)[:80]}, {})
                continue
            # '그저께부터 리뷰 어때?'에 첫 요청부터 400 40009 Unsupported function (2026-11-08 실측,
            # 매번 재현). 도구 결과가 붙은 다음 요청은 정상 → 시스템이 도구를 직접 실행하고 이어간다
            if e.code != "40009" or used_tool or unsupported:
                raise
            unsupported, nudged = True, True  # 재요청 없이 바로 시스템 직접 실행으로
            if log:
                log("(CLOVA 40009)", {"reason": "지원하지 않는 함수 호출 → 시스템 직접 실행"}, {})
            msg = {"role": "assistant", "content": ""}
        calls = msg.get("toolCalls") or []
        if not calls and not used_tool and toolbox.drafts and rule_fallbacks:
            # 초안이 있는데 도구 없이 답함 → 확정이면 등록, 수정 요청이면 초안 수정을 시스템이 실행
            # (HCX가 '등록하겠습니다'라고만 하고 실제로는 등록하지 않은 사례가 있었음)
            if CONFIRM.search(utter):
                calls = [_call("system_call_post", "post_review_reply", "{}")]
            elif toolbox._asks_edit(utter):
                calls = [
                    _call(
                        "system_call_edit",
                        "draft_review_reply",
                        json.dumps({"edit_request": utter}, ensure_ascii=False),
                    )
                ]
            if calls and log:
                log(
                    "(직접 실행)",
                    {"reason": "초안 대기 중 도구 호출 없음", "tool": calls[0]["function"]["name"]},
                    {},
                )
        if (
            not calls
            and not used_tool
            and rule_fallbacks
            and toolbox.looks_like_review_question(utter)
        ):
            if not nudged:
                nudged = True
                if log:
                    log("(재요청)", {"reason": "리뷰 질문인데 도구 호출 없음"}, {})
                # CLOVA는 요청당 system 메시지를 1개만 받으므로 user 메시지로 붙인다 (2026-11-07
                # 실측: 400 Invalid messages)
                messages.append(
                    {
                        "role": "user",
                        "content": "(시스템 안내) 방금 질문은 리뷰에 관한 질문입니다. "
                        "답하지 말고 먼저 analyze_reviews를 호출하세요.",
                    }
                )
                continue
            # 두 번째도 안 부르면 시스템이 인자 없이 직접 실행 (topic은 사장님 원문에서 찾음)
            ctx = toolbox.ctx
            if REPLY_HINTS.search(utter):
                calls = [_call("system_call_1", "get_unanswered_reviews", "{}")]
            else:
                menus = (
                    resolve_from_utterance(utter, ctx["store_id"], ctx["catalog"])["menus"]
                    if ctx["catalog"]
                    else []
                )
                # 메뉴가 없으면 항목 단어를 topic으로 ('웨이팅 길다는 얘기' → 대기시간)
                aspect = next((w for w in ASPECT_HINTS if w in utter), None)
                args = {"topic": menus[0]} if menus else {"topic": aspect} if aspect else {}
                calls = [
                    _call("system_call_1", "analyze_reviews", json.dumps(args, ensure_ascii=False))
                ]
            if log:
                log("(직접 실행)", {"reason": "재요청 후에도 도구 호출 없음"}, {})
        if not calls:
            text = msg["content"] or ""
            if unsupported and not used_tool and not text:
                text = "이 질문은 처리하지 못했어요. 조금 다르게 말씀해 주시겠어요?"
            if toolbox.drafts and not used_tool and POST_CLAIM.search(text):
                text = HOLD_MSG  # 등록하지 않았는데 '올리겠습니다'라고 말하지 않게
            fixed = reply_message(last_tool, last_result, owner_lang)
            if (
                fixed is not None
            ):  # 답글 흐름은 정해진 문장으로 (초안 베껴 쓰기·거짓 '등록했습니다' 방지)
                messages.append({"role": "assistant", "content": fixed})
                return fixed + render_block(
                    client, model, last_tool, last_result, owner_lang, gloss
                )
            if last_tool == "analyze_reviews":  # 확인 질문은 지우고, 기간 안내는 시스템이 붙인다
                notice = expand_notice(last_result, owner_lang)
                text = strip_confirm(text)
                if notice:  # 본문의 기간 표현은 지우고 시스템 안내만 남긴다
                    text = PERIOD_PHRASE.sub("", text)
                    text = f"{notice}\n\n{text[:1].upper()}{text[1:]}"
            messages.append({"role": "assistant", "content": text})
            return text + render_block(client, model, last_tool, last_result, owner_lang, gloss)
        asst = {
            "role": "assistant",
            "content": msg["content"] or "",
            "toolCalls": [
                _call(c["id"], c["function"]["name"], _args(c["function"]["arguments"]))
                for c in calls
            ],
        }
        messages.append(asst)
        for i, c in enumerate(calls):
            name = c["function"]["name"]
            args = _args(c["function"]["arguments"])
            result = toolbox.dispatch(name, args, utterance=utter)
            ran = getattr(toolbox, "last_tool", None) or name
            if ran != name:  # 시스템이 도구를 바꿨으면 대화 기록도 실제 실행대로 고친다
                name = ran
                asst["toolCalls"][i]["function"]["name"] = ran
                asst["toolCalls"][i]["function"]["arguments"] = dict(toolbox.last_args)
                args = dict(toolbox.last_args)
            if getattr(toolbox, "last_dropped", None):
                # 버린 기간 인자가 대화 기록에 남으면 모델이 그 값(7d)을 기간으로 말한다
                # 시스템이 고친 기간(예: '2주' → start_date)도 실제 실행한 값으로 남긴다
                asst["toolCalls"][i]["function"]["arguments"] = dict(toolbox.last_args)
            if result.get("error") != "not_requested":  # 막힌 초안 호출은 직전 목록 안내를 유지
                last_result, last_tool = result, name
            if log:
                log(name, args, result)
            messages.append(
                {
                    "role": "tool",
                    "toolCallId": c["id"],
                    "content": json.dumps(model_view(name, result), ensure_ascii=False),
                }
            )
    return "(도구 호출이 반복되어 중단했습니다)"
