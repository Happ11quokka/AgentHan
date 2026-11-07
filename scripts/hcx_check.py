"""CLOVA Studio 연결·모델 점검. CLOVA Studio에 접속할 수 있는 환경에서 실행한다.

    export CLOVASTUDIO_API_KEY=nv-...      # 키는 터미널에만. 파일·채팅에 붙여넣지 말 것
    python scripts/hcx_check.py

확인하는 것
1. 사용 가능한 모델 목록
2. 모델별 기본 응답과 지연 시간
3. JSON 스키마 출력이 지켜지는지 (문서상 Structured Outputs는 HCX-007 전용 → HCX-005에서도 되는지
확인)
4. analyze_reviews 도구 호출(function calling)이 기대대로 되는지
결과: models/hcx_check.json (키는 저장하지 않음)
"""

import json
import os
import time
from pathlib import Path

from openai import OpenAI

from review_tools.specs import ANALYZE_REVIEWS, GET_SALES_STATS

ROOT = Path(__file__).resolve().parents[1]

client = OpenAI(
    api_key=os.environ["CLOVASTUDIO_API_KEY"],
    base_url=os.getenv("CLOVASTUDIO_BASE_URL", "https://clovastudio.stream.ntruss.com/v1/openai"),
)
out = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def no_think(model):  # HCX-007은 추론 모델: JSON 스키마·도구 호출과 추론을 동시에 쓸 수 없어 끈다
    return {"reasoning_effort": "none"} if model.startswith("HCX-007") else {}


def call(model, **kw):
    if (
        model.startswith("HCX-007") and "max_tokens" in kw
    ):  # 추론 모델은 max_completion_tokens만 받는다
        kw["max_completion_tokens"] = max(kw.pop("max_tokens"), 1024)
    if not model.startswith(
        "HCX-007"
    ):  # 005·DASH는 response_format을 거절(400) → 프롬프트만으로 JSON 요청
        kw.pop("response_format", None)
    t = time.time()
    try:
        r = client.chat.completions.create(model=model, extra_body=no_think(model), **kw)
        return r, round(time.time() - t, 2), None
    except Exception as e:
        return None, round(time.time() - t, 2), f"{type(e).__name__}: {str(e)[:300]}"


# 1. 모델 목록
try:
    out["models"] = sorted(m.id for m in client.models.list().data)
except Exception as e:
    out["models_error"] = str(e)[:300]
    out["models"] = []
print("models:", out["models"] or out.get("models_error"))
cands = [
    m for m in ["HCX-005", "HCX-007", "HCX-DASH-002"] if not out["models"] or m in out["models"]
]

# 2~3. 기본 응답 + JSON 스키마
schema = {
    "name": "s",
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clause": {"type": "integer"},
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                    },
                    "required": ["clause", "sentiment"],
                },
            }
        },
        "required": ["items"],
    },
}
clauses = "[0] 떡피는 최곤데\n[1] 딸기모찌 개 노맛...ㅜㅜ\n[2] 식탁 위에서 바퀴벌레 기어다녀요"
expect = {0: "positive", 1: "negative", 2: "negative"}
out["chat"], out["json_schema"] = {}, {}
for m in cands:
    r, sec, err = call(
        m, messages=[{"role": "user", "content": "한 단어로만 답하세요: 안녕"}], max_tokens=20
    )
    out["chat"][m] = {
        "ok": err is None,
        "sec": sec,
        "text": r.choices[0].message.content if r else None,
        "error": err,
    }
    r, sec, err = call(
        m,
        max_tokens=300,
        temperature=0,
        response_format={"type": "json_schema", "json_schema": schema},
        messages=[
            {
                "role": "system",
                "content": "각 절의 감정을 positive/negative/neutral 로 판단해 다음 JSON으로만 "
                "답하세요: "
                '{"items": [{"clause": 번호, "sentiment": "positive|negative|neutral"}]}',
            },
            {"role": "user", "content": clauses},
        ],
    )
    raw = r.choices[0].message.content if r else None
    rec = {"sec": sec, "raw": raw, "error": err}
    try:
        items = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])["items"]
        rec["valid_json"] = True
        rec["correct"] = sum(expect.get(i["clause"]) == i["sentiment"] for i in items)
    except Exception:
        rec["valid_json"] = False
    out["json_schema"][m] = rec
    print(
        m,
        "chat",
        out["chat"][m]["ok"],
        "| json",
        rec.get("valid_json"),
        rec.get("correct"),
        "/3",
        f"{sec}s",
        err or "",
    )

# 4. 도구 호출
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
out["function_calling"] = {}
for m in cands:
    rows = []
    for text, want_tool, want_args in cases:
        r, sec, err = call(
            m,
            max_tokens=1024,
            tools=[ANALYZE_REVIEWS, GET_SALES_STATS],
            messages=[
                {
                    "role": "system",
                    "content": "당신은 가게 사장님을 돕는 비서입니다. 필요하면 도구를 호출하세요.",
                },
                {"role": "user", "content": text},
            ],
        )
        tc = (r.choices[0].message.tool_calls or []) if r else []
        got_tool = tc[0].function.name if tc else None
        try:
            got_args = json.loads(tc[0].function.arguments) if tc else None
        except Exception:
            got_args = tc[0].function.arguments if tc else None
        ok_tool = err is None and got_tool == want_tool  # 호출 실패는 통과로 치지 않는다
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

(ROOT / "models").mkdir(exist_ok=True)
(ROOT / "models" / "hcx_check.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
)
print("saved models/hcx_check.json")
