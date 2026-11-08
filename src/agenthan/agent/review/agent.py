"""사장님 한마디 → HCX 도구 선택 → 도구 실행 → HCX 최종 답변 (E2E 루프).

LLM은 사장님 말에서 인자만 채우고, 가게·날짜 같은 값은 여기서 넣는다 (도구 명세 '시스템이 넣는 값').
공용 대화 루프에 붙일 때는 run_turn()과 ReviewToolbox.dispatch()만 가져가면 된다.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from agenthan.core.llm import tool_call_options

from .analyze import analyze_reviews
from .replies import (
    CONFIRM,
    MockReplyStore,
    ReplyAwareRepo,
    draft_review_reply,
    get_unanswered_reviews,
    post_review_reply,
)
from .report import ReportSettingsStore, run_report, set_review_report
from .report_parse import parse_report_request
from .specs import (
    ANALYZE_REVIEWS,
    DRAFT_REVIEW_REPLY,
    GET_UNANSWERED_REVIEWS,
    POST_REVIEW_REPLY,
    SET_REVIEW_REPORT,
)
from .topic import resolve_from_utterance

# 사장님 문장에 이런 표현이 있을 때만 LLM이 채운 기간을 믿는다. '요즘', '최근'은 애매해서 넣지
# 않는다
# (HCX가 '요즘'을 7d·30d·90d로 매번 다르게 채움, 2026-11-07 실측) → 없으면 자동 확장
# 규칙(7일→30일→…)을 쓴다
TIME_HINTS = re.compile(
    r"어제|오늘|이번\s?주|지난\s?주|일주일|한\s?주|이번\s?달|지난\s?달|한\s?달|올해|작년|전체|지금까지|"
    r"\d+\s?(일|주|개월|달)|석\s?달|분기|월\s|\d+월|"
    r"today|yesterday|week|month|year|all\s?time|"
    r"сегодня|вчера|недел|месяц|год",
    re.I,
)

# 도구 없이 답하면 안 되는 질문의 단서 (언어별). 메뉴 별칭은 ReviewToolbox가 가게 메뉴에서 더한다
REVIEW_HINTS = [
    "리뷰",
    "후기",
    "손님",
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
REPLY_HINTS = re.compile(r"답글|답장|reply|respond|ответ", re.I)
# 초안이 있을 때 '고쳐 달라'로 볼 표현 (HCX가 도구 없이 직접 고쳐 쓴 사례, 2026-11-07)
EDIT_HINTS = re.compile(
    r"해\s?줘|바꿔|고쳐|추가|빼\s?줘|더\s|덜\s|짧게|길게|정중|친근|말해|언급|넣어|"
    r"make|change|shorter|longer|add|remove|polite|сделай|измени|добав|короче|вежлив",
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
    r"(haven'?t|have not|didn'?t|did not|not yet|not) (yet )?(been )?repl|"
    r"без ответа|не ответил|не ответила",
    re.I,
)
WRITE_INTENT = re.compile(
    r"(답글|답장).{0,6}(써|작성|달아|만들)|reply to|write (a )?repl|draft|ответь|напиши ответ|"
    r"ответ на",
    re.I,
)
POST_WORDS = re.compile(r"올려|등록|게시|post|publish|опубликуй|размести", re.I)
TARGET_NEG = re.compile(
    r"안\s?좋은|나쁜|불만|부정|악플|급한|negative|bad|complain|плох|негатив|жалоб", re.I
)
TARGET_ALL = re.compile(r"전부|다\s?(써|달아|작성)|모두|all|все", re.I)

LANG_NAMES = {
    "ko": "한국어",
    "en": "English",
    "ru": "русский",
    "vi": "Tiếng Việt",
    "zh": "中文",
}


def system_prompt(store_name: str, today: date, owner_lang: str) -> str:
    lang = LANG_NAMES.get(owner_lang, owner_lang)
    return (
        f"당신은 '{store_name}' 사장님을 돕는 비서입니다. 오늘은 {today.isoformat()}입니다.\n"
        f"사장님과는 반드시 {lang}로 대화하세요. 대화 상대는 손님이 아니라 가게 사장님입니다. "
        "사장님은 '사장님'으로 부르고, 리뷰를 쓴 사람은 '손님'이라고 하세요 ('고객님' 금지). "
        "메뉴를 먹어 보라고 권하는 등 손님에게 하듯 말하지 마세요.\n"
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
        "도구 결과를 받은 뒤 답변 형식 (사장님 언어로):\n"
        "1) 핵심 1~2문장: 몇 건 중 몇 건인지, 가장 많은 칭찬·불만.\n"
        "2) 불만이 있으면 사장님이 할 수 있는 개선 행동 1가지. 없으면 생략.\n"
        f"마지막으로: 최종 답변은 반드시 {lang} ({LANG_EN.get(owner_lang, owner_lang)})로 쓰세요. "
        "도구 결과가 한국어여도 답변 언어를 바꾸지 마세요."
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
    ):
        self.settings_store = settings_store or ReportSettingsStore()
        self.now = now or datetime.combine(today, datetime.min.time())
        self.pending_suggestion: dict | None = None  # 제안한 주기 (사장님이 '응' 하면 설정)
        self.reply_store = reply_store or MockReplyStore(today=today)
        repo = ReplyAwareRepo(repo, self.reply_store)
        self.ctx = dict(
            store_id=store_id, today=today, repo=repo, catalog=catalog, sentiment=sentiment, llm=llm
        )
        self.writer, self.owner_lang = writer, owner_lang
        self.listed: list[dict] = []  # 직전 미답글 목록 (draft의 대상)
        self.listed_for: str | None = None  # 그 목록을 만든 사장님 발화
        self.drafts: dict[str, dict] = {}  # review_id → 직전 초안 (수정·등록의 대상)
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
        self.hints = [h.lower() for h in REVIEW_HINTS] + sorted(words)

    def route(self, utterance: str) -> tuple[str, dict] | None:
        """답글 흐름 요청이면 (도구, 인자)를 규칙으로 정한다. 아니면 None → LLM이 고른다."""
        u = utterance
        rep = parse_report_request(u, None if self.drafts else self.pending_suggestion)
        if rep:
            kind, args = rep
            if kind == "now":
                return "run_review_report", {}
            return "set_review_report", ({"action": kind} if kind in ("off", "suggest") else args)
        if self.drafts and CONFIRM.search(u):
            return "post_review_reply", {}
        if POST_WORDS.search(u) and not WRITE_INTENT.search(u):
            return "post_review_reply", {}  # 초안이 없으면 '등록할 초안이 없어요'로 끝난다
        if (
            self.drafts
            and EDIT_HINTS.search(u)
            and not LIST_INTENT.search(u)
            and not WRITE_INTENT.search(u)
        ):
            return "draft_review_reply", {"edit_request": u}
        if WRITE_INTENT.search(u):
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
        return any(h in t for h in self.hints)

    def dispatch(self, name: str, args: dict, utterance: str | None = None) -> dict:
        if name not in self.allowed:
            return {"error": "unknown_tool", "tool": name}
        # LLM이 명세에 없는 인자(예: store_id)를 넣어도 무시한다
        clean = {
            k: v for k, v in (args or {}).items() if k in self.allowed[name] and v not in (None, "")
        }
        if (
            utterance and "topic" not in clean and self.ctx["catalog"]
        ):  # LLM이 메뉴를 빠뜨림 (2026-11-07 실측, DASH)
            menus = resolve_from_utterance(utterance, self.ctx["store_id"], self.ctx["catalog"])[
                "menus"
            ]
            if menus:
                clean["topic"] = menus[0]
        self.last_dropped = None
        self.last_args = clean  # 실제로 실행한 인자 (대화 기록을 이 값으로 고친다)
        if utterance is not None and not TIME_HINTS.search(utterance):
            dropped = {k: clean.pop(k) for k in ["period", "start_date", "end_date"] if k in clean}
            if dropped:
                self.last_dropped = dropped  # 로그·디버깅용
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
            if name == "get_unanswered_reviews":
                res = get_unanswered_reviews(**self.ctx, **clean)
                self.listed = res["reviews"]
                self.listed_for = utterance
                return res
            if name == "draft_review_reply":
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
        instruction = None if edit else utterance
        owner_promise = any(self.drafts[r].get("owner_promise") for r in ids if r in self.drafts)
        res = draft_review_reply(
            **self.ctx,
            review_ids=ids,
            writer=self.writer,
            owner_lang=self.owner_lang,
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
                self.drafts[d["review_id"]] = d
        return res


class _SyntheticCall:
    """시스템이 직접 실행하는 도구 호출을 HCX 응답의 tool_call과 같은 모양으로 만든다."""

    def __init__(self, id_, name, arguments):
        self.id = id_
        self.function = type("F", (), {"name": name, "arguments": arguments})()


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
        r = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=512,
            messages=[
                {
                    "role": "system",
                    "content": f"Translate each Korean customer review into {lang}, literally and "
                    "briefly. "
                    f"{gloss}Do not add or guess anything that is not in the Korean text. "
                    'Answer only JSON: {"t": ["translation 0", "translation 1", ...]}',
                },
                {"role": "user", "content": listing},
            ],
        )
        raw = r.choices[0].message.content or ""
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
            "답글 안 단 리뷰는 {total}건이에요{neg}.{risk} 어느 리뷰에 답글을 쓸까요? (예: '안 "
            "좋은 리뷰에 답글 써줘', '전부 써줘')"
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
        "posted": "답글 {n}건을 등록했어요. (데모라 실제 네이버에는 올라가지 않아요)",
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


def _mostly_korean(text: str) -> bool:
    """따옴표 안 인용(한국어 원문)을 뺀 본문에서 한글 비율이 높으면 True."""
    body = re.sub(r'"[^"]*"|“[^”]*”|«[^»]*»', " ", text)
    letters = re.findall(r"[A-Za-zА-Яа-яЁё가-힣]", body)
    return bool(letters) and sum("가" <= ch <= "힣" for ch in letters) / len(letters) > 0.3


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
) -> str:
    """messages에 사장님 발화가 들어 있는 상태로 호출. 도구 호출을 처리하고 최종 답변 문자열을
    돌려준다.
    messages는 제자리에서 갱신되므로 다음 턴에 그대로 이어 쓰면 된다."""
    # 도구 호출은 최대 토큰 1024 이상 (CLOVA Studio 문서), JSON 스키마는 함께 쓰지 않는다
    opts = tool_call_options(model)
    # CLOVA Studio는 도구를 지정하는 tool_choice 객체를 400(Unsupported function)으로 거절한다
    # (2026-11-07 실측).
    # 그래서 리뷰 질문에 도구 없이 답하면 (1) 한 번 더 요청하고, (2) 그래도 안 부르면 시스템이 직접
    # 도구를 실행한다.
    nudged = False
    last_result, last_tool = None, None
    gloss = menu_glossary(toolbox.ctx["catalog"], toolbox.ctx["store_id"], owner_lang)
    utter = messages[_last_user(messages)]["content"]  # 사장님 원래 질문 (재요청 메시지와 구분)
    start = _last_user(messages)
    routed = toolbox.route(utter)
    if routed:
        name, args = routed
        result = toolbox.dispatch(name, args, utterance=utter)
        if log:
            log(f"(규칙) {name}", args, result)
        text = (
            reply_message(name, result, owner_lang) or json.dumps(result, ensure_ascii=False)[:300]
        )
        messages.append({"role": "assistant", "content": text})
        return text + render_block(client, model, name, result, owner_lang, gloss)
    for _ in range(max_rounds):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=toolbox.specs, **opts
        )
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        used_tool = any(m.get("role") == "tool" for m in messages[start:])
        if not calls and not used_tool and toolbox.drafts:
            # 초안이 있는데 도구 없이 답함 → 확정이면 등록, 수정 요청이면 초안 수정을 시스템이 실행
            # (HCX가 '등록하겠습니다'라고만 하고 실제로는 등록하지 않은 사례가 있었음)
            if CONFIRM.search(utter):
                calls = [_SyntheticCall("system_call_post", "post_review_reply", "{}")]
            elif EDIT_HINTS.search(utter):
                calls = [
                    _SyntheticCall(
                        "system_call_edit",
                        "draft_review_reply",
                        json.dumps({"edit_request": utter}, ensure_ascii=False),
                    )
                ]
            if calls and log:
                log(
                    "(직접 실행)",
                    {"reason": "초안 대기 중 도구 호출 없음", "tool": calls[0].function.name},
                    {},
                )
        if not calls and not used_tool and toolbox.looks_like_review_question(utter):
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
                calls = [_SyntheticCall("system_call_1", "get_unanswered_reviews", "{}")]
            else:
                menus = (
                    resolve_from_utterance(utter, ctx["store_id"], ctx["catalog"])["menus"]
                    if ctx["catalog"]
                    else []
                )
                args = {"topic": menus[0]} if menus else {}
                calls = [
                    _SyntheticCall(
                        "system_call_1", "analyze_reviews", json.dumps(args, ensure_ascii=False)
                    )
                ]
            if log:
                log("(직접 실행)", {"reason": "재요청 후에도 도구 호출 없음"}, {})
        if not calls:
            text = msg.content or ""
            fixed = reply_message(last_tool, last_result, owner_lang)
            if (
                fixed is not None
            ):  # 답글 흐름은 정해진 문장으로 (초안 베껴 쓰기·거짓 '등록했습니다' 방지)
                messages.append({"role": "assistant", "content": fixed})
                return fixed + render_block(
                    client, model, last_tool, last_result, owner_lang, gloss
                )
            if owner_lang != "ko" and _mostly_korean(text):
                # 지시문·도구 결과가 한국어라 답변이 한국어로 끌려감 (2026-11-07 실측, DASH·005
                # 모두) → 한 번 다시 쓰게 한다
                if log:
                    log("(언어 재작성)", {"reason": f"답변이 한국어 → {owner_lang}"}, {})
                lang = (
                    f"{LANG_NAMES.get(owner_lang, owner_lang)} "
                    f"({LANG_EN.get(owner_lang, owner_lang)})"
                )
                names = (
                    (
                        "메뉴 이름은 이렇게 쓰세요: "
                        + ", ".join(f"{k} = {v}" for k, v in gloss.items())
                        + ". "
                    )
                    if gloss
                    else ""
                )
                retry = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"(시스템 안내) 위 답변을 {lang}로 다시 쓰세요. {names}"
                        "리뷰 인용은 넣지 마세요. 짧고 쉬운 문장으로 쓰세요.",
                    },
                ]
                r2 = client.chat.completions.create(model=model, messages=retry, **opts)
                text = r2.choices[0].message.content or text
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
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.function.name,
                        "arguments": json.dumps(_args(c.function.arguments), ensure_ascii=False),
                    },
                }
                for c in calls
            ],
        }
        messages.append(asst)
        for i, c in enumerate(calls):
            args = _args(c.function.arguments)
            result = toolbox.dispatch(c.function.name, args, utterance=utter)
            if getattr(toolbox, "last_dropped", None):
                # 버린 기간 인자가 대화 기록에 남으면 모델이 그 값(7d)을 기간으로 말한다
                kept = {k: v for k, v in args.items() if k not in toolbox.last_dropped}
                asst["tool_calls"][i]["function"]["arguments"] = json.dumps(
                    kept, ensure_ascii=False
                )
            if result.get("error") != "not_requested":  # 막힌 초안 호출은 직전 목록 안내를 유지
                last_result, last_tool = result, c.function.name
            if log:
                log(c.function.name, args, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": json.dumps(model_view(c.function.name, result), ensure_ascii=False),
                }
            )
    return "(도구 호출이 반복되어 중단했습니다)"
