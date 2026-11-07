"""LLM에게 주는 도구 명세 (OpenAI 호환 function 스키마).

도구 동작을 바꾸면 이 명세도 함께 고친다.
"""

ANALYZE_REVIEWS = {
    "type": "function",
    "function": {
        "name": "analyze_reviews",
        "description": (
            "가게 리뷰를 기간·주제별로 분석하고 요약한다. 사장님이 손님 반응, 불만, 칭찬, 특정 "
            "메뉴에 대한 평가, "
            "맛·가격·웨이팅·친절·청결 같은 항목에 대한 리뷰, 최근 리뷰 요약을 물을 때 사용한다. "
            "'손님들이 OO 뭐래', 'OO 반응 어때'처럼 가게나 메뉴에 대한 손님 의견을 묻는 질문은 "
            "영어·러시아어 등 어떤 언어로 묻더라도 모두 이 도구로 답한다. "
            "매출, 방문자 수, 예약 건수 같은 통계에는 사용하지 않는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["1d", "7d", "30d", "90d", "all"],
                    "description": "분석 기간. '어제'는 1d, '최근'·'이번 주'는 7d, '요즘'·'이번 "
                    "달'은 30d, "
                    "'최근 3개월'은 90d, '전체'·'지금까지'는 all. 사장님이 기간을 말하지 않았으면 "
                    "넣지 말 것",
                },
                "start_date": {
                    "type": "string",
                    "description": (
                        "사장님이 특정 날짜를 말했을 때만. YYYY-MM-DD. 주어지면 period보다 우선"
                    ),
                },
                "end_date": {
                    "type": "string",
                    "description": "start_date와 함께 쓰는 종료일. 생략하면 오늘",
                },
                "focus": {
                    "type": "string",
                    "enum": ["all", "complaint", "praise"],
                    "description": "불만·문제점·안 좋은 리뷰·고칠 점을 물으면 complaint, 좋아하는 "
                    "점·강점·칭찬을 물으면 praise. "
                    "'뭐래', '반응 어때', 'what do they say'처럼 좋고 나쁨을 정하지 않은 질문이면 "
                    "넣지 말 것",
                },
                "topic": {
                    "type": "string",
                    "description": "사장님이 언급한 메뉴나 항목(맛, 가격, 웨이팅, 직원, 포장 등)을 "
                    "사장님이 쓴 글자 그대로 "
                    "(한글·키릴 문자 등). 번역하거나 로마자로 바꾸지 말 것. 없으면 넣지 말 것",
                },
                "format": {
                    "type": "string",
                    "enum": ["chat", "report"],
                    "description": "'보고서'를 요청하면 report. 없으면 생략(기본 chat)",
                },
            },
            "required": [],
        },
    },
}

GET_UNANSWERED_REVIEWS = {
    "type": "function",
    "function": {
        "name": "get_unanswered_reviews",
        "description": (
            "사장님 답글이 아직 없는 리뷰 목록을 보여준다. '답글 안 단 리뷰', '답장해야 할 리뷰', "
            "'급하게 대응할 리뷰'를 물을 때 사용한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "priority": {
                    "type": "string",
                    "enum": ["recent", "negative_first"],
                    "description": (
                        "'급하게', '안 좋은 것부터', '불만부터'면 negative_first. 없으면 넣지 말 것"
                    ),
                }
            },
            "required": [],
        },
    },
}

DRAFT_REVIEW_REPLY = {
    "type": "function",
    "function": {
        "name": "draft_review_reply",
        "description": (
            "리뷰에 달 한국어 답글 초안을 만들거나 고친다. 사장님이 답글을 써 달라고 하거나, 방금 "
            "만든 초안을 "
            "고쳐 달라고 할 때 사용한다. 어느 리뷰인지는 시스템이 알고 있으므로 리뷰 번호를 넣지 "
            "않는다. 등록은 하지 않는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "edit_request": {
                    "type": "string",
                    "description": (
                        "초안을 고쳐 달라는 사장님 요청을 쓴 그대로 (번역하지 말 것). 처음 쓰는 "
                        "것이면 넣지 말 것"
                    ),
                },
                "target": {
                    "type": "string",
                    "enum": ["all_listed", "first", "negative"],
                    "description": (
                        "미답글 목록 중 어느 리뷰에 쓸지. '전부', '다'면 all_listed, '안 좋은 "
                        "것'이면 negative. 없으면 넣지 말 것"
                    ),
                },
            },
            "required": [],
        },
    },
}

POST_REVIEW_REPLY = {
    "type": "function",
    "function": {
        "name": "post_review_reply",
        "description": (
            "방금 만든 답글 초안을 네이버에 등록한다. 사장님이 '이대로 올려줘', '등록해줘'처럼 "
            "명시적으로 확정했을 때만 사용한다."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

SET_REVIEW_REPORT = {
    "type": "function",
    "function": {
        "name": "set_review_report",
        "description": (
            "정기 리뷰 보고와 즉시 알림을 설정·변경·해제하거나, 리뷰 현황에 맞는 보고 주기를 "
            "제안한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set", "suggest", "off"],
                    "description": (
                        "'얼마나 자주', '알아서'면 suggest, '그만'이면 off. 없으면 넣지 말 것(기본 "
                        "set)"
                    ),
                },
                "frequency": {"type": "string", "enum": ["daily", "weekly", "biweekly", "monthly"]},
                "weekday": {
                    "type": "string",
                    "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                },
                "time": {"type": "string", "description": "HH:MM. 아침은 09:00, 저녁은 20:00"},
                "focus": {
                    "type": "string",
                    "enum": ["all", "complaint"],
                    "description": "'불만만'이면 complaint",
                },
                "alert": {
                    "type": "string",
                    "enum": ["negative", "every_new", "none"],
                    "description": "'나쁜 리뷰 바로 알려줘'면 negative",
                },
            },
            "required": [],
        },
    },
}

# 매칭 테스트용 미끼 도구: 리뷰 도구와 헷갈리면 안 되는 통계 도구
GET_SALES_STATS = {
    "type": "function",
    "function": {
        "name": "get_sales_stats",
        "description": "매출, 판매량, 방문자 수, 예약 건수 같은 가게 운영 통계를 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {"period": {"type": "string", "enum": ["1d", "7d", "30d"]}},
            "required": [],
        },
    },
}
