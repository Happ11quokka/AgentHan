"""텍스트로 주문 대화를 해 보는 CLI.

    uv run python -m agenthan.voice.cli [--now 2026-11-08T15:00:00+09:00] [--nlu fake|hcx]

손님 말을 한 줄씩 넣는다. 주문이 확정되면 `Order`를 찍고 끝난다.
`--nlu hcx`는 HCX-DASH-002를 부른다. 키는 `CLOVASTUDIO_API_KEY` 환경 변수나 `.env`에서 읽는다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import TextIO

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import TERMINAL
from agenthan.voice.nlu.base import Nlu
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import StoreConfig


def _print_turn(result: TurnResult, out: TextIO) -> None:
    for text in result.utterances:
        print(f"AI: {text}", file=out)


def _make_nlu(kind: str) -> Nlu:
    if kind == "hcx":
        from agenthan.voice.nlu.hcx import HcxNlu

        return HcxNlu.from_env()
    return FakeNlu()


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="텍스트 주문 대화")
    parser.add_argument("--now", help="고정 시각 ISO 8601. 없으면 지금 시각(KST)")
    parser.add_argument(
        "--nlu", choices=("fake", "hcx"), default="fake", help="fake: 규칙 기반(기본), hcx: HCX"
    )
    args = parser.parse_args(argv)
    inp = stdin or sys.stdin
    out = stdout or sys.stdout

    try:
        nlu = _make_nlu(args.nlu)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 2
    config = StoreConfig.from_fixture()
    fixed_now = datetime.fromisoformat(args.now) if args.now else None

    def now() -> datetime:
        return fixed_now or datetime.now(config.tz)

    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(),
        nlu,
        config,
        order_id=f"cli-{now():%Y%m%d%H%M%S}",
    )
    _print_turn(engine.start(now()), out)

    while True:
        print("손님> ", end="", file=out, flush=True)
        line = inp.readline()
        if not line:
            print("\n주문이 끝나지 않았어요.", file=out)
            return 1
        text = line.strip()
        if not text:
            continue
        result = engine.handle(text, now())
        _print_turn(result, out)
        if result.order is not None:
            print(f"\n주문서: {result.order!r}", file=out)
            print(f"총액: {result.cart.total():,}원", file=out)
            return 0
        if result.state in TERMINAL:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
