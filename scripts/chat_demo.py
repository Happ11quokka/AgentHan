"""대화 데모: 사장님 한마디가 도구 호출을 거쳐 답변이 되는 E2E 흐름.

CLOVA Studio에 접속할 수 있는 환경에서 실행한다.

    python scripts/chat_demo.py --store 홍만당 --ask "요즘 손님들 불만 뭐야?"
    python scripts/chat_demo.py --store 홍만당 --lang ru            # 대화형 (빈 줄 입력 시 종료)
    python scripts/chat_demo.py --store 홍만당 --analysis hcx       # 도구 안 감정 판단·요약도 HCX로
    # 답글 흐름: 같은 대화에서 차례로 묻기 (--ask 여러 번)
    python scripts/chat_demo.py --store 홍만당 --reset-replies \
        --ask "답글 안 단 리뷰 있어?" \
        --ask "안 좋은 리뷰에 답글 써줘" \
        --ask "사과를 더 정중하게, 다음엔 맛을 꼭 확인하겠다고 해줘" \
        --ask "이대로 올려줘"
    # 정기 보고
    python scripts/chat_demo.py --store 홍만당 --reset-replies \
        --ask "보고 얼마나 자주 받는 게 좋아?" \
        --ask "응 그렇게 해줘" \
        --ask "나쁜 리뷰 올라오면 바로 알려줘" \
        --ask "지금 리뷰 보고서 보여줘"
"""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from agenthan.agent.review import MenuCatalog, SnapshotRepo
from agenthan.agent.review.agent import ReviewToolbox, run_turn, system_prompt
from agenthan.agent.review.hcx_llm import HCXReviewLLM, HCXSentiment
from agenthan.agent.review.replies import MockReplyStore
from agenthan.agent.review.report import ReportSettingsStore
from agenthan.core.llm import make_client

ROOT = Path(__file__).resolve().parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--store", default="홍만당")
ap.add_argument("--today", default="2026-11-07")
ap.add_argument("--lang", default="ko", help="사장님 언어 (ko, en, ru …)")
ap.add_argument("--model", default="HCX-DASH-002", help="도구 안 감정 판단·요약 모델")
ap.add_argument(
    "--chat-model",
    default="HCX-005",
    help="도구 선택·최종 답변 모델 (2026-11-07 비교: 005가 지시를 더 잘 따름)",
)
ap.add_argument(
    "--analysis", choices=["rule", "hcx"], default="rule", help="도구 안 감정 판단·요약 방식"
)
ap.add_argument(
    "--ask",
    action="append",
    default=None,
    help="차례로 물을 질문 (여러 번 쓰면 같은 대화로 이어짐)",
)
ap.add_argument(
    "--reset-replies", action="store_true", help="데모용 답글 등록 기록·보고 설정 지우기"
)
ap.add_argument(
    "--seed-days",
    type=int,
    default=14,
    help="방문일이 이 일수보다 최근인 리뷰를 '답글 없음'으로 가정 (데모용)",
)
ap.add_argument("--quiet", action="store_true", help="도구 호출 로그 숨기기")
ap.add_argument(
    "--snapshot",
    type=Path,
    default=ROOT / "data" / "snapshots" / "test_baseline.jsonl",
    help="리뷰 스냅샷 (실제 데이터 없이: tests/fixtures/review_snapshot.jsonl)",
)
ap.add_argument(
    "--menus",
    type=Path,
    default=ROOT / "data" / "menus_sample.json",
    help="메뉴 별칭 (실제 데이터 없이: tests/fixtures/menus.json)",
)
a = ap.parse_args()
chat_model = a.chat_model or a.model

client = make_client()
repo = SnapshotRepo(a.snapshot)
store_name = repo._match_store(a.store)
today = date.fromisoformat(a.today)
extra = {}
if a.analysis == "hcx":
    llm = HCXReviewLLM(
        a.model, client=client, cache_path=ROOT / "models" / f"hcx_review_cache_{a.model}.jsonl"
    )
    extra = {"llm": llm, "sentiment": HCXSentiment(llm)}
reply_path = ROOT / "models" / "replies_mock.json"
if a.reset_replies and reply_path.exists():
    reply_path.write_text("{}", encoding="utf-8")
writer = HCXReviewLLM(
    chat_model, client=client, cache_path=ROOT / "models" / f"hcx_review_cache_{chat_model}.jsonl"
)
settings_path = ROOT / "models" / "report_settings.json"
if a.reset_replies and settings_path.exists():
    settings_path.write_text("{}", encoding="utf-8")
toolbox = ReviewToolbox(
    a.store,
    today,
    repo,
    MenuCatalog.load(a.menus),
    **extra,
    writer=writer,
    reply_store=MockReplyStore(reply_path, today=today, seed_days=a.seed_days),
    owner_lang=a.lang,
    settings_store=ReportSettingsStore(settings_path),
    now=datetime.combine(today, datetime.now().time()),
)  # 스냅샷 날짜 + 현재 시각


def log(name, args, result):
    if a.quiet:
        return
    if getattr(toolbox, "last_dropped", None):
        print(
            f"  ↳ (기간 무시) 사장님 문장에 기간 표현이 없어 {toolbox.last_dropped} 대신 자동 확장"
        )
        toolbox.last_dropped = None
    brief = {
        k: result.get(k)
        for k in [
            "error",
            "candidates",
            "period_expanded",
            "total_reviews",
            "review_count",
            "sentiment",
            "insights",
            "total",
            "negative_count",
            "posted_count",
            "action",
            "next_run",
            "suggested",
        ]
        if k in result
    }
    print(
        f"  ↳ {name}({json.dumps(args, ensure_ascii=False)}) → "
        f"{json.dumps(brief, ensure_ascii=False)}"
    )


messages = [{"role": "system", "content": system_prompt(store_name, today, a.lang)}]
print(
    f"[{store_name} · 오늘 {today} · 언어 {a.lang} · 대화 {chat_model} · 분석 "
    f"{a.analysis}({a.model})]"
)
queue = list(a.ask or [])
while True:
    if a.ask is not None:
        if not queue:
            break
        q = queue.pop(0)
        print(f"\n사장님> {q}")
    else:
        q = input("\n사장님> ").strip()
        if not q:
            break
    messages.append({"role": "user", "content": q})
    print("비서>", run_turn(client, chat_model, messages, toolbox, log, owner_lang=a.lang))
