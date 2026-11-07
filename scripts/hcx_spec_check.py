"""도구 명세 중 무엇이 CLOVA Studio에서 '400 Unsupported function'을 일으키는지 찾는다 (본인
컴퓨터에서, 호출 4회).

python scripts/hcx_spec_check.py
"""

import copy
import os
from pathlib import Path

from openai import OpenAI

from review_tools.specs import ANALYZE_REVIEWS

ROOT = Path(__file__).resolve().parents[1]

client = OpenAI(
    api_key=os.environ["CLOVASTUDIO_API_KEY"],
    base_url="https://clovastudio.stream.ntruss.com/v1/openai",
)


def variant(extra: str):
    v = copy.deepcopy(ANALYZE_REVIEWS)
    v["function"]["description"] += extra
    return v


cases = {
    "현재 명세": ANALYZE_REVIEWS,
    "+ 키릴 문자": variant(" 예: 'Что говорят'"),
    "+ 물결표(~)": variant(" 예: '손님들이 ~ 뭐래'"),
    "+ 물음표(?)": variant(" 예: '반응 어때?'"),
}
for name, spec in cases.items():
    try:
        r = client.chat.completions.create(
            model=os.getenv("HCX_MODEL", "HCX-DASH-002"),
            max_tokens=1024,
            tools=[spec],
            messages=[{"role": "user", "content": "최근 리뷰 어때?"}],
        )
        tc = r.choices[0].message.tool_calls
        print(f"OK    {name:<10} → {tc[0].function.name if tc else '도구 호출 없음'}")
    except Exception as e:
        print(f"FAIL  {name:<10} → {str(e)[:120]}")
