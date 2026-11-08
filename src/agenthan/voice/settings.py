"""통화 서버 설정 (티켓 04). 환경 변수 → (없으면) 지금 폴더의 `.env`에서 읽는다.

| 변수 | 값 | 기본 |
|---|---|---|
| `PHONE_NLU` | `fake`(규칙) · `hcx`(HCX, `CLOVASTUDIO_API_KEY` 필요) | `fake` |
| `PHONE_STT` | `fake` · `clova`(티켓 02, `CLOVA_SPEECH_*` 필요) | `fake` |
| `PHONE_TTS` | `fake` · `clova`(티켓 03, `CLOVA_VOICE_*` 필요) | `fake` |
| `PHONE_TZ` | 가게 시간대 (IANA 이름) | `Asia/Seoul` |
| `PHONE_LOG_DIR` | 턴별 지연 JSONL 폴더 (gitignore) | `logs` |
| `PHONE_NOW` | 가게 시각 고정 (ISO 8601, 시간대 포함). 시연·테스트용 | 없음(지금 시각) |
| `PHONE_FAKE_STT_REPLIES` | 가짜 STT가 `epFlag`마다 낼 손님 말. `\\|`로 나눈다 | 메도빅 주문 대본 |
| `PHONE_STORE_FIXTURE` | 개발 서버가 읽는 더미 가게 JSON | `fixtures/sample_store.json` |
| `PHONE_WEB_DIST` | 개발 서버가 같이 낼 React 빌드 폴더 (있을 때만) | `web/dist` |
| `PHONE_VAD_THRESHOLD` | 무음 감지 RMS 임계값 (PCM16) | `VadSettings` 기본값 |
| `PHONE_VAD_START_MS` | 말 시작으로 볼 말소리 길이(ms) | `VadSettings` 기본값 |
| `PHONE_VAD_END_SILENCE_MS` | 말 끝으로 볼 무음 길이(ms, 100 이상) | `VadSettings` 기본값 |
| `PHONE_MAX_CALLS` | 동시 통화 상한. 넘으면 `error{code: "busy"}` (CLOVA 도메인당 15) | `10` |
| `PHONE_MAX_CALL_S` | 통화 하나의 최대 길이(초). 넘으면 `call_too_long` | `600` |
| `PHONE_CONT_WAIT_S` | 이어질 모양("-하고", "음")이면 마지막 말 끝부터 기다리는 초 | `2.0` |

키(`CLOVA_SPEECH_SECRET_KEY` 등)는 여기서 해석하지 않고 `env`에 담아 어댑터에 넘긴다.
`repr`에 값이 나오지 않는다.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agenthan.voice.call.vad import VadSettings
from agenthan.voice.store_config import KST

NluName = Literal["fake", "hcx"]
AdapterName = Literal["fake", "clova"]

DEFAULT_FAKE_REPLIES = ("메도빅 하나요", "그게 다예요", "6시 반이요", "네")


class SettingsError(ValueError):
    """설정값이 틀렸다. 메시지에 변수 이름과 고칠 방법을 담는다 (값이 키일 수 있어 값은 안 담음)."""


@dataclass(frozen=True)
class VoiceSettings:
    nlu: NluName = "fake"
    stt: AdapterName = "fake"
    tts: AdapterName = "fake"
    tz: tzinfo = KST
    log_dir: Path | None = Path("logs")
    now: datetime | None = None
    fake_replies: tuple[str, ...] = DEFAULT_FAKE_REPLIES
    store_fixture: Path | None = None  # None이면 샘플 가게
    web_dist: Path = Path("web/dist")
    vad: VadSettings = field(default_factory=VadSettings)
    max_calls: int = 10  # 동시 통화 상한 (리뷰 17번)
    max_call_s: float = 600.0  # 통화 하나의 최대 길이
    cont_wait_s: float = 2.0  # 이어짐 대기 상한 (3단계 티켓 02, `SessionSettings.cont_wait_s`)
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _check("PHONE_NLU", self.nlu, get_args(NluName))
        _check("PHONE_STT", self.stt, get_args(AdapterName))
        _check("PHONE_TTS", self.tts, get_args(AdapterName))
        if self.now is not None and self.now.tzinfo is None:
            raise SettingsError("PHONE_NOW에 시간대를 붙여 주세요. 예: 2026-11-08T16:00:00+09:00")

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")
    ) -> VoiceSettings:
        """환경 변수가 `.env`보다 먼저다. `.env`는 지금 폴더 기준 (저장소 루트에서 실행)."""
        env = {**read_env_file(env_file), **(os.environ if environ is None else environ)}

        def get(name: str, default: str) -> str:
            return env.get(name, "").strip() or default

        now = get("PHONE_NOW", "")
        replies = get("PHONE_FAKE_STT_REPLIES", "")
        try:
            fixed_now = datetime.fromisoformat(now) if now else None
        except ValueError as err:
            raise SettingsError(
                "PHONE_NOW는 ISO 8601이어야 해요. 예: 2026-11-08T16:00:00+09:00"
            ) from err
        log_dir = get("PHONE_LOG_DIR", "logs")
        base = VadSettings()
        vad = VadSettings(
            threshold=_number("PHONE_VAD_THRESHOLD", get("PHONE_VAD_THRESHOLD", ""),
                              base.threshold),
            start_ms=int(_number("PHONE_VAD_START_MS", get("PHONE_VAD_START_MS", ""),
                                 base.start_ms)),
            # 무음 프레임 하나(100ms)보다 짧으면 프레임마다 말 끝이 된다 (리뷰 13번)
            end_silence_ms=int(_number("PHONE_VAD_END_SILENCE_MS",
                                       get("PHONE_VAD_END_SILENCE_MS", ""),
                                       base.end_silence_ms, minimum=100)),
        )
        max_calls = _number("PHONE_MAX_CALLS", get("PHONE_MAX_CALLS", ""), 10)
        if max_calls != int(max_calls):
            raise SettingsError(f"PHONE_MAX_CALLS는 정수여야 해요: {max_calls!r}")
        return cls(
            nlu=get("PHONE_NLU", "fake"),  # type: ignore[arg-type]  # __post_init__가 검사
            stt=get("PHONE_STT", "fake"),  # type: ignore[arg-type]
            tts=get("PHONE_TTS", "fake"),  # type: ignore[arg-type]
            tz=_zone(get("PHONE_TZ", "Asia/Seoul")),
            log_dir=Path(log_dir),
            now=fixed_now,
            fake_replies=(
                tuple(r.strip() for r in replies.split("|") if r.strip())
                if replies
                else DEFAULT_FAKE_REPLIES
            ),
            store_fixture=Path(fixture) if (fixture := get("PHONE_STORE_FIXTURE", "")) else None,
            web_dist=Path(get("PHONE_WEB_DIST", "web/dist")),
            vad=vad,
            max_calls=int(max_calls),
            max_call_s=_number("PHONE_MAX_CALL_S", get("PHONE_MAX_CALL_S", ""), 600.0),
            cont_wait_s=_number("PHONE_CONT_WAIT_S", get("PHONE_CONT_WAIT_S", ""), 2.0),
            env=env,
        )


def read_env_file(path: Path | None) -> dict[str, str]:
    """`이름=값` 줄만 읽는다. `#` 주석·빈 줄은 건너뛰고 값의 따옴표는 뗀다. 파일이 없으면 {}."""
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep:
            values[name.strip()] = value.strip().strip("'\"")
    return values


def _check(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise SettingsError(f"{name}은(는) {' · '.join(allowed)} 중 하나여야 해요: {value!r}")


def _number(name: str, value: str, default: float, *, minimum: float = 0.0) -> float:
    """양수(`minimum`이 있으면 그 이상)만 받는다. nan·inf·0은 틀린 값이다 (리뷰 13번)."""
    if not value:
        return float(default)
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not (math.isfinite(number) and number > 0 and number >= minimum):
        least = f"{minimum:g} 이상" if minimum else "0보다 큰"
        raise SettingsError(f"{name}는 {least} 숫자여야 해요: {value!r}")
    return number


def _zone(name: str) -> tzinfo:
    if name in ("Asia/Seoul", "KST"):
        return KST  # tzdata 없는 서버에서도 되게 (KST는 서머타임이 없다)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise SettingsError(f"PHONE_TZ를 모르겠어요: {name!r}. 예: Asia/Seoul") from err
