"""가짜 휴대폰 끝-끝 측정 (티켓 07). opt-in 측정 도구이고 자동 테스트 통과 기준이 아니다.

1. 서버를 띄운다 (저장소 루트, `.env`에 키). 예: 실제 CLOVA STT·TTS + 가짜 NLU

       PHONE_STT=clova PHONE_TTS=clova PHONE_NLU=fake PHONE_NOW=2026-11-08T16:30:00+09:00 \\
       PHONE_LOG_DIR=private/fake-phone/logs uv run python -m agenthan.voice.server --port 8765

2. 녹음을 흘려 넣는다 (실제 속도). 결과는 `--out` 아래(기본 `private/fake-phone/<시각>-<이름>/`)

       uv run python -m agenthan.voice.fake_phone --url ws://127.0.0.1:8765/api/phone/ws \\
           --recordings private/recordings/donghyeon --label clova-fake --convert

- 통화: A(A1~A4)·B(B1~B5)·C(C1~C4)는 한 통화씩 이어서, D(D1·D2·D3·D5)는 녹음마다 따로
- 쓰는 파일: `calls.jsonl`(통화 원기록: 받은 이벤트·보낸 제어 이벤트·서버 metrics,
  받아 적은 글 포함), `rows.jsonl`(녹음별 줄), `summary.md`(요약 표, 받아 적은 글 원문 없음)
- 녹음·결과는 `private/`에만 둔다(목소리·받아 적은 글은 개인정보, 커밋 금지)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from agenthan.voice.fake_phone.audio import convert_all, read_pcm, speech_end_s
from agenthan.voice.fake_phone.client import CallRun, Clip, run_call
from agenthan.voice.fake_phone.summary import call_outcome, clip_rows, markdown_table, overview

# 대본 `.scratch/phone-order/s2-recording-script.md`의 손님 말 (D4는 녹음 안 함)
SCRIPT = {
    "A1": "메도빅 두 개랑 아이스 아메리카노 하나요",
    "A2": "아니요, 그게 다예요",
    "A3": "여섯 시 반쯤 갈게요",
    "A4": "네, 맞아요",
    "B1": "블린 하나요, 사워크림은 빼 주세요",
    "B2": "그게 다예요",
    "B3": "일곱 시요",
    "B4": "아니요, 블린 두 개요",
    "B5": "네",
    "C1": "메도빅 많이 달아요?",
    "C2": "그럼 메도빅 하나 주세요",
    "C3": "나폴레옹도 하나요",
    "C4": "아니요, 지금 바로 갈게요",
    "D1": "짜장면 있어요?",
    "D2": "사장님 좀 바꿔 주세요",
    "D3": "음… 그러니까… 저기요",
    "D5": "아메리카노 따뜻하게 세 잔이요, 하나는 디카페인으로요",
}
CALLS = {
    "A": ["A1", "A2", "A3", "A4"],
    "B": ["B1", "B2", "B3", "B4", "B5"],
    "C": ["C1", "C2", "C3", "C4"],
    "D1": ["D1"], "D2": ["D2"], "D3": ["D3"], "D5": ["D5"],
}


def load_clips(wav_dir: Path, ids: Sequence[str]) -> list[Clip]:
    clips = []
    for clip_id in ids:
        pcm = read_pcm(wav_dir / f"{clip_id}.wav")
        clips.append(Clip(clip_id, pcm, speech_end_s=speech_end_s(pcm)))
    return clips


def _jsonable(run: CallRun) -> dict[str, Any]:
    return {
        "clips": [vars(c) for c in run.clips],
        "events": run.events,
        "sent": run.sent,
        "metrics": run.metrics,
        "closed_by_server": run.closed_by_server,
    }


async def measure(url: str, wav_dir: Path, calls: Sequence[str], out: Path, label: str,
                  pace: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, Any]] = {}
    with (out / "calls.jsonl").open("a", encoding="utf-8") as raw:
        for name in calls:
            run = await run_call(url, load_clips(wav_dir, CALLS[name]), pace=pace)
            outcome = call_outcome(run)
            outcomes[name] = outcome
            call_rows = clip_rows(run, SCRIPT)
            rows += call_rows
            raw.write(json.dumps({"label": label, "call": name, "outcome": outcome,
                                  **_jsonable(run)}, ensure_ascii=False) + "\n")
            raw.flush()
            matches = ", ".join(f"{r['clip']}={r['match']}" for r in call_rows)
            print(f"[{label}] 통화 {name}: {outcome['status']} {outcome['items']} ({matches})",
                  flush=True)
    with (out / "rows.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({"label": label, **row}, ensure_ascii=False) + "\n")
    summary = overview(rows)
    lines = [f"# fake_phone 측정 · {label}", "", markdown_table(rows), "",
             "## 요약", "", "```", json.dumps(summary, ensure_ascii=False, indent=1), "```", "",
             "## 통화별 결과", "", "| 통화 | 결과 | 주문 | 총액 | 픽업 |", "|---|---|---|---|---|"]
    for name, o in outcomes.items():
        lines.append(f"| {name} | {o['status']} | {o['items'] or '-'} | {o['total'] or '-'} "
                     f"| {o['pickup_time'] or '-'} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="가짜 휴대폰 끝-끝 측정 (티켓 07)")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/api/phone/ws")
    parser.add_argument("--recordings", type=Path, required=True,
                        help="녹음 폴더 (private/recordings/<이름>). 변환본은 그 아래 wav/")
    parser.add_argument("--convert", action="store_true", help="m4a → wav/ 변환부터")
    parser.add_argument("--calls", default=",".join(CALLS), help="예: A,B,C,D1")
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--pace", type=float, default=1.0, help="1 = 실제 속도")
    args = parser.parse_args(argv)
    wav_dir = args.recordings / "wav"
    if args.convert:
        convert_all(args.recordings, wav_dir)
    out = args.out or (args.recordings.parents[1] / "fake-phone"
                       / f"{datetime.now():%Y%m%d-%H%M%S}-{args.label}")
    asyncio.run(measure(args.url, wav_dir, args.calls.split(","), out, args.label, args.pace))


if __name__ == "__main__":
    main()
