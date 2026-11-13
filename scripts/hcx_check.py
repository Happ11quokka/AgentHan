"""CLOVA Studio 네이티브 REST v3 점검. CLOVA Studio에 접속할 수 있는 환경에서 실행한다.

    uv run --env-file .env python scripts/hcx_check.py   # 키는 .env에만. 파일·채팅에 붙여넣지 말 것

확인하는 것
1. 모델별 기본 응답과 지연 시간
2. JSON 스키마(responseFormat): 문서상 HCX-007 전용 → 005·DASH에서도 되는지
3. 도구 호출(function calling), 도구 지정 toolChoice
4. 메시지 제약 (system 2개)
결과: models/hcx_check.json (키는 저장하지 않음)
"""

import json
import time
from pathlib import Path

from agenthan.agent.review.clova import ClovaClient, ClovaError, model_options
from agenthan.agent.review.specs import ANALYZE_REVIEWS, GET_SALES_STATS

ROOT = Path(__file__).resolve().parents[1]
MODELS = ["HCX-005", "HCX-007", "HCX-DASH-002"]
client = ClovaClient()
out = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "api": "native v3", "models": MODELS}


def call(model, messages, **kw):
    t = time.time()
    try:
        return client.chat(model, messages, **kw), round(time.time() - t, 2), None
    except ClovaError as e:
        return None, round(time.time() - t, 2), str(e)


# 1~2. 기본 응답 + JSON 스키마 (구절은 지어낸 예시)
schema = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clause": {"type": "integer"},
                    "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                },
                "required": ["clause", "sentiment"],
            },
        }
    },
    "required": ["items"],
}
clauses = (
    "[0] 떡이 쫀득해서 좋았는데\n[1] 딸기모찌는 완전 노맛\n[2] 테이블 밑에 바퀴벌레가 있었어요"
)
expect = {0: "positive", 1: "negative", 2: "negative"}
system = (
    "각 절의 감정을 positive/negative/neutral 로 판단해 다음 JSON으로만 답하세요: "
    '{"items": [{"clause": 번호, "sentiment": "positive|negative|neutral"}]}'
)
out["chat"], out["json_schema"] = {}, {}
for m in MODELS:
    r, sec, err = call(
        m, [{"role": "user", "content": "한 단어로만 답하세요: 안녕"}], **model_options(m, 200)
    )
    out["chat"][m] = {"ok": err is None, "sec": sec, "text": r and r["content"], "error": err}
    opts = model_options(m, 1024)
    opts["responseFormat"] = {"type": "json", "schema": schema}  # 모든 모델에 붙여 본다
    if m.startswith("HCX-007"):
        opts.pop("maxTokens", None)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": clauses}]
    r, sec, err = call(m, msgs, temperature=0, **opts)
    raw = r and r["content"]
    rec = {"sec": sec, "raw": raw, "error": err}
    try:
        items = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])["items"]
        rec["valid_json"] = True
        rec["correct"] = sum(expect.get(i["clause"]) == i["sentiment"] for i in items)
    except Exception:
        rec["valid_json"] = False
    out["json_schema"][m] = rec
    print(m, "chat", out["chat"][m]["ok"], "| responseFormat", rec.get("valid_json"), err or "")

# 3. 도구 호출
cases = [
    ("최근 리뷰 어때?", "analyze_reviews", {}),
    (
        "이번 달 메도빅 불만 있어?",
        "analyze_reviews",
        {"period": "30d", "focus": "complaint", "topic": "메도빅"},
    ),
    ("웨이팅 길다는 얘기 많아?", "analyze_reviews", {"topic": "웨이팅"}),
    ("Any bad reviews this week?", "analyze_reviews", {"period": "7d", "focus": "complaint"}),
    (
        "Есть плохие отзывы на этой неделе?",
        "analyze_reviews",
        {"period": "7d", "focus": "complaint"},
    ),
    ("이번 주 매출 얼마야?", "get_sales_stats", None),
    ("오늘 날씨 어때?", None, None),
]
tools = [ANALYZE_REVIEWS, GET_SALES_STATS]
sys_msg = {
    "role": "system",
    "content": "당신은 가게 사장님을 돕는 비서입니다. 필요하면 도구를 호출하세요.",
}
out["function_calling"] = {}
for m in ["HCX-005", "HCX-DASH-002"]:
    rows = []
    for text, want_tool, want_args in cases:
        r, sec, err = call(
            m, [sys_msg, {"role": "user", "content": text}], tools=tools, **model_options(m, 1024)
        )
        tc = (r or {}).get("toolCalls") or []
        got_tool = tc[0]["function"]["name"] if tc else None
        got_args = tc[0]["function"]["arguments"] if tc else None
        ok_tool = err is None and got_tool == want_tool
        ok_args = want_args is None or (
            isinstance(got_args, dict) and all(got_args.get(k) == v for k, v in want_args.items())
        )
        rows.append(
            {
                "text": text,
                "want_tool": want_tool,
                "got_tool": got_tool,
                "want_args": want_args,
                "got_args": got_args,
                "tool_ok": ok_tool,
                "args_ok": ok_args,
                "sec": sec,
                "error": err,
            }
        )
        print(
            f"  {m} | {text[:28]:<28} → {got_tool} {got_args} "
            f"{'OK' if ok_tool and ok_args else 'CHECK'}"
        )
    out["function_calling"][m] = rows

# 3-2. 도구 지정 + 4. system 2개
force = {"type": "function", "function": {"name": "analyze_reviews"}}
out["tool_choice_forced"] = {}
for q in ["딸기모찌 리뷰 어때?", "안녕하세요"]:  # 관련 질문 / 도구와 무관한 인사
    r, sec, err = call(
        "HCX-005",
        [{"role": "user", "content": q}],
        tools=tools,
        tool_choice=force,
        **model_options("HCX-005", 1024),
    )
    out["tool_choice_forced"][q] = {"ok": bool(r and r.get("toolCalls")), "error": err}
r, sec, err = call(
    "HCX-005",
    [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "hi"},
    ],
    maxTokens=30,
)
out["two_system_messages"] = {"ok": err is None, "error": err}
print("toolChoice 지정:", out["tool_choice_forced"], "| system 2개:", out["two_system_messages"])

(ROOT / "models").mkdir(exist_ok=True)
(ROOT / "models" / "hcx_check.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
)
print("saved models/hcx_check.json")
