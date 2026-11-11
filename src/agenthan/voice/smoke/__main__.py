"""배포 직후 스모크 평가 (6단계 품질). 서버와 같은 환경(컨테이너 안)에서 돌린다.

    python -m agenthan.voice.smoke                      # S1 + S2 (서버 설정 그대로)
    python -m agenthan.voice.smoke --skip-s2            # 엔진·NLU만
    python -m agenthan.voice.smoke --nlu fake --skip-s2 # 키 없이 (테스트·로컬 확인용)
    python -m agenthan.voice.smoke --calls 2 --json /app/logs/smoke.json

서버와 같은 설정을 읽는다(`PHONE_*` 환경 변수 → 지금 폴더의 `.env`, 가게는 `PHONE_STORE_FIXTURE`).

- **S1** (`scripts.py`): 가게 데이터로 만든 대본 9개를 엔진에 넣고 최종 Order를 본다. NLU는
  `PHONE_NLU` 그대로(`hcx`면 실제 HCX 약 20번, 호출 사이 0.7초 이상). `--nlu fake`면 가짜 NLU
- **S2** (`calls.py`): 가짜 휴대폰으로 `--url`(기본 `ws://127.0.0.1:8000/api/phone/ws`)에 통화
  `--calls`건(기본 1, 최대 3)을 건다. 서버가 실제 STT면 손님 말을 CLOVA Voice로 그때그때 합성하고
  (`PHONE_SMOKE_SPEAKER`, 기본 `nara`), 가짜 STT면 사인파를 보낸다. 주문 확정 여부와 말 끝 → 첫
  소리를 낸다. `--max-voice-p50-ms`를 주면 그 중앙값을 넘을 때 실패로 본다(기본은 숫자만 냄)
- 실패한 대본·통화는 한 번 다시 돌린다. 그때 통과하면 flaky로 표시하고 통과로 센다
- `PHONE_SMOKE_KEY`가 서버와 같으면 스모크 통화는 사장님 화면·주문 받는 곳에 안 가고 턴 기록에
  `smoke: true`가 남는다(`turn_summary`가 뺀다). 없으면 보통 통화처럼 사장님 화면에 뜬다

출력: 표준 출력에 결과 JSON, 마지막 줄에 한 줄 요약(`SMOKE PASS …` / `SMOKE FAIL …`).
종료 코드: 0 통과 · 1 실패 · 2 설정 오류(키 없음 등).

실제 모드의 호출 수(기본값): HCX 약 22번(S1 20 + S2 2), CLOVA Voice 손님 말 4문장 + 봇 말 약
8문장, CLOVA Speech 통화 1건(약 40초). 약 1분 30초 걸린다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agenthan.core.contracts import StoreKnowledge
from agenthan.voice.nlu.base import Nlu
from agenthan.voice.settings import SettingsError, VoiceSettings
from agenthan.voice.smoke import calls, scripts
from agenthan.voice.store_config import StoreConfig

DEFAULT_URL = "ws://127.0.0.1:8000/api/phone/ws"
SPEAKER_ENV = "PHONE_SMOKE_SPEAKER"
DEFAULT_SPEAKER = "nara"  # 손님 목소리. 봇(대성 Pro)과 다른 화자
MAX_CALLS = 3


class ConfigError(Exception):
    """스모크를 돌릴 수 없는 설정(키 없음 등). 종료 코드 2."""


def _nlu(name: str, env_file: Path | None) -> Nlu:
    if name == "fake":
        from agenthan.voice.nlu.fake import FakeNlu

        return FakeNlu()
    from agenthan.voice.nlu.hcx import HcxNlu

    try:
        return HcxNlu.from_env(env_file)
    except ValueError as err:
        raise ConfigError(str(err)) from err


def _voice(settings: VoiceSettings) -> tuple[calls.Voice | None, Any]:
    """손님 목소리 합성기와 닫을 것. 서버가 가짜 STT면 (None, None): 사인파를 쓴다."""
    if settings.stt == "fake":
        return None, None
    from agenthan.voice import tts_clova

    client_id, secret = (settings.env.get(k, "").strip() for k in tts_clova.KEY_ENVS)
    if not (client_id and secret):
        raise ConfigError(f"손님 말을 합성할 CLOVA Voice 키가 없어요 "
                          f"({', '.join(tts_clova.KEY_ENVS)})")
    speaker = settings.env.get(SPEAKER_ENV, "").strip() or DEFAULT_SPEAKER
    tts = tts_clova.ClovaVoiceTts(client_id, secret, speaker=speaker)
    return tts.synth, tts


async def _s2(url: str, settings: VoiceSettings, store: StoreKnowledge, config: StoreConfig,
              n: int, max_p50_ms: int | None) -> calls.S2Report:
    from agenthan.voice.ws import store_clock

    voice, tts = _voice(settings)
    if voice is None:
        plans = [calls.fake_stt_plan(settings.fake_replies)]
    else:
        plans = calls.plan_calls(store.list_menu(), config, store_clock(settings).now(), n)
    try:
        # 서버가 Origin을 검사하면(`PHONE_ALLOWED_ORIGINS`) 허용된 화면 오리진으로 건다.
        # `ws://127.0.0.1`의 오리진은 목록에 없어 403이 난다 (6단계 첫 배포에서 확인)
        allowed = [o for o in settings.allowed_origins if o != "*"]
        return await calls.run_s2(url, plans, voice=voice, smoke_key=settings.smoke_key,
                                  max_p50_ms=max_p50_ms, origin=allowed[0] if allowed else "")
    finally:
        if tts is not None:
            await tts.aclose()


def summary_line(report: Mapping[str, Any]) -> str:
    parts = ["SMOKE PASS" if report["ok"] else "SMOKE FAIL"]
    s1, s2 = report["s1"], report["s2"]
    if s1.get("skipped"):
        parts.append("s1 skipped")
    else:
        parts.append(f"s1 {s1['passed']}/{s1['total']} (flaky {s1['flaky']}, nlu={s1['nlu']}, "
                     f"calls {s1['nlu_calls']})")
    if s2.get("skipped"):
        parts.append("s2 skipped")
    else:
        voice = s2["voice"]
        parts.append(f"s2 {s2['passed']}/{s2['total']} (flaky {s2['flaky']}, voice p50 "
                     f"{voice['p50_ms']}ms p90 {voice['p90_ms']}ms n={voice['n']})")
    parts.append(f"{report['seconds']}s")
    return " ".join(parts)


def run(args: argparse.Namespace, environ: Mapping[str, str] | None,
        env_file: Path | None) -> dict[str, Any]:
    # 개발 서버(`server.py`)와 같은 더미 가게를 같은 방법으로 읽는다. 진입점 안에서만 쓴다
    from agenthan.core.fakes import FakeStoreKnowledge

    started = time.monotonic()
    settings = VoiceSettings.from_env(environ, env_file)
    fixture = settings.store_fixture
    store = (FakeStoreKnowledge.from_fixture(fixture) if fixture
             else FakeStoreKnowledge.from_fixture())
    config = replace(StoreConfig.from_fixture(fixture), tz=settings.tz)
    report: dict[str, Any] = {"ok": True, "at": datetime.now(settings.tz).isoformat("T", "seconds"),
                              "s1": {"skipped": True}, "s2": {"skipped": True}}
    if not args.skip_s1:
        name = args.nlu or settings.nlu
        s1 = scripts.run_s1(store, config, _nlu(name, env_file), nlu_name=name,
                            today=datetime.now(settings.tz), min_gap_s=args.min_gap_s,
                            limit=args.scripts)
        report["s1"] = s1.as_json()
        report["ok"] = report["ok"] and s1.ok
    if not args.skip_s2:
        s2 = asyncio.run(_s2(args.url, settings, store, config, args.calls,
                             args.max_voice_p50_ms))
        report["s2"] = {"url": args.url, "stt": settings.stt, **s2.as_json()}
        report["ok"] = report["ok"] and s2.ok
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         env_file: Path | None = Path(".env")) -> int:
    parser = argparse.ArgumentParser(prog="python -m agenthan.voice.smoke",
                                     description="배포 직후 스모크 평가 (S1 대본 + S2 통화)")
    parser.add_argument("--url", default=DEFAULT_URL, help="S2가 걸 통화 서버 WS")
    parser.add_argument("--nlu", choices=("fake", "hcx"), default=None,
                        help="S1의 NLU. 기본은 서버 설정(PHONE_NLU)")
    parser.add_argument("--calls", type=int, default=1, choices=range(1, MAX_CALLS + 1),
                        help="S2 통화 수 (1: 주문 · 2: + 사장님 연결 · 3: + 두 품목)")
    parser.add_argument("--scripts", type=int, default=None, help="S1 대본을 앞에서 N개만")
    parser.add_argument("--min-gap-s", type=float, default=scripts.MIN_GAP_S,
                        help="S1 NLU 호출 사이 최소 간격(초). 분당 한도용")
    parser.add_argument("--max-voice-p50-ms", type=int, default=None,
                        help="S2 말 끝 → 첫 소리 중앙값이 이 값을 넘으면 실패")
    parser.add_argument("--skip-s1", action="store_true")
    parser.add_argument("--skip-s2", action="store_true")
    parser.add_argument("--json", type=Path, default=None, help="결과 JSON을 이 파일에도 쓴다")
    args = parser.parse_args(argv)
    try:
        report = run(args, environ, env_file)
    except (ConfigError, SettingsError) as err:
        print(f"SMOKE CONFIG {err}", file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(summary_line(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
