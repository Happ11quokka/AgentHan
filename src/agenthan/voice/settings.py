"""통화 서버 설정 (티켓 04). 환경 변수 → (없으면) 지금 폴더의 `.env`에서 읽는다.

| 변수 | 값 | 기본 |
|---|---|---|
| `PHONE_NLU` | `fake`(규칙) · `hcx`(HCX, `CLOVASTUDIO_API_KEY` 필요) | `fake` |
| `PHONE_STT` | `fake` · `clova`(티켓 02, `CLOVA_SPEECH_*` 필요) | `fake` |
| `PHONE_TTS` | `fake` · `clova`(티켓 03, `CLOVA_VOICE_*` 필요) | `fake` |
| `PHONE_TZ` | 가게 시간대 (IANA 이름) | `Asia/Seoul` |
| `PHONE_LOG_DIR` | 턴별 지연 JSONL 폴더 (gitignore) | `logs` |
| `PHONE_NLU_TASK_ID` | HCX 튜닝 모델 작업 id(`PHONE_NLU=hcx`일 때). 비면 기본 프롬프트 | 없음 |
| `PHONE_TTS_SPEAKER` | CLOVA Voice 화자 (예: `vdaeseong`, `nara_call`) | `vdaeseong` |
| `PHONE_STT_BOOST_WEIGHT` | CLOVA 키워드 부스팅 가중치. 0이면 끔(3에서 문장 잘림) | `0` |
| `PHONE_RECORD_DIR` | 통화 녹음 폴더(디버그용). 통화마다 WAV 하나. 발표 때는 비운다 | 없음(꺼짐) |
| `PHONE_NOW` | 가게 시작 시각(ISO 8601, 시간대 포함). 그 뒤 실제 시간만큼 감. 시연용 | 없음(지금) |
| `PHONE_FAKE_STT_REPLIES` | 가짜 STT가 `epFlag`마다 낼 손님 말. `\\|`로 나눈다 | 메도빅 주문 대본 |
| `PHONE_STORE_FIXTURE` | 개발 서버가 읽는 더미 가게 JSON | `fixtures/sample_store.json` |
| `PHONE_WEB_DIST` | 개발 서버가 같이 낼 React 빌드 폴더 (있을 때만) | `web/dist` |
| `PHONE_VAD_THRESHOLD` | 무음 감지 RMS 임계값 (PCM16) | `VadSettings` 기본값 |
| `PHONE_VAD_START_MS` | 말 시작으로 볼 말소리 길이(ms) | `VadSettings` 기본값 |
| `PHONE_VAD_END_SILENCE_MS` | 말 끝(턴 판정)으로 볼 무음 길이(ms, 100 이상) | `VadSettings` 값 |
| `PHONE_VAD_EP_SILENCE_MS` | STT `epFlag`를 먼저 보낼 무음 길이(ms, 말 끝 무음 이하) | `250` |
| `PHONE_MAX_CALLS` | 동시 통화 상한. 넘으면 `error{code: "busy"}` (CLOVA 도메인당 15) | `10` |
| `PHONE_MAX_CALL_S` | 통화 하나의 최대 길이(초). 넘으면 `call_too_long` | `600` |
| `PHONE_CONT_WAIT_S` | 이어질 모양("-하고", "음")이면 마지막 말 끝부터 기다리는 초 | `2.0` |
| `PHONE_MORE_NO_WAIT_S` | "더 필요하신 거?" 뒤 맨 "아니요"를 말 끝부터 기다림(초). 0은 끔 | `1.2` |
| `PHONE_MIC_CLOSE` | 반이중 마이크 닫기: `playback`(실제 재생 시작) · `state`(전처럼) | 앞의 것 |
| `PHONE_TURN_REWIND` | `on`이면 첫 소리 전에 이어 한 말을 앞 턴에 합쳐 다시 처리(되감기) | `on` |
| `PHONE_GATE_MAX_S` | 되감기 문지기가 글 없는 소리를 기다리는 상한(초) | `3.0` |
| `PHONE_ACK_FIRST` | `on`이면 받아들인 대답 앞에 고정 문장 "네."를 따로 낸다 | `off` |
| `PHONE_SMOKE_KEY` | 스모크 통화 열쇠. 같은 값을 보낸 통화는 사장님 화면·주문에 안 간다 | 없음 |
| `PHONE_NO_INPUT_S` | 봇 재생 끝부터 이만큼 말이 없으면 "여보세요?" + 직전 질문 (초) | `6.0` |
| `PHONE_WAIT_REQUEST_S` | "잠깐만요" 뒤 첫 무응답 대기 (초) | `15.0` |
| `PHONE_TURN_MODEL` | `smart_turn`(오디오 턴 모델) · `off`(끝말 규칙만) | `smart_turn` |
| `PHONE_TURN_MODEL_PATH` | Smart Turn ONNX. 없거나 체크섬이 틀리면 규칙만 | `/app/models/…` |
| `PHONE_TURN_LOW` | 끝남 확률이 이 값 이하면 이어짐 쪽 | `0.3` |
| `PHONE_TURN_HIGH` | 끝남 확률이 이 값 이상이면 끝남 쪽 | `0.7` |
| `PHONE_VOICE_BARGE_IN` | 음성 끼어들기 off · shadow(기록만) · on. 안드로이드 크롬만 | `off` |
| `PHONE_BARGE_THRESHOLD` | 봇 말 중 손님 소리로 볼 RMS (PCM16) | `1500` |
| `PHONE_BARGE_DUCK_MS` | 이만큼 소리가 이어지면 봇 목소리를 줄인다(ms) | `250` |
| `PHONE_BARGE_STOP_MS` | 이만큼 이어지면 봇 말을 멈춘다(ms, 1000 이상) | `1000` |
| `PHONE_OWNER_KEY` | 사장님 화면 비밀 토큰. `/owner?key=…`. 없으면 사장님 경로가 모두 401 | 없음 |
| `PHONE_TRANSLATE_TIMEOUT_S` | Papago 요청 하나의 시간 제한(초). 넘으면 카드는 원문만 | `3.0` |
| `PHONE_TRANSLATE_KEEPALIVE_S` | Papago 유휴 연결을 두는 시간(초). 쉬었다 불러도 안 끊게 | `60.0` |
| `PHONE_ASK_OWNER` | `on`이면 통화 중 사장님께 바로 묻기. 끄면 사과 + 보충 카드 | `off` |
| `PHONE_ANSWER` | `on`이면 손님 질문에 가게 자료로 답하는 답변기(HCX). NLU가 `hcx`일 때만 | `on` |
| `PHONE_ANSWER_MODEL` | 답변기 모델 (`nlu/hcx_answer.py`가 읽는다) | `HCX-005` |
| `PHONE_OWNER_WAIT_S` | 사장님 질문 답 대기(초, 카드 보낸 때부터). 넘으면 문자 약속 | `20.0` |

6단계 운영 보강 (문지기 `gate.py`). 숫자 상한은 `0`이면 끈다.

| 변수 | 값 | 기본 |
|---|---|---|
| `PHONE_ALLOWED_ORIGINS` | 통화를 걸 수 있는 화면 오리진(쉼표). 비거나 `*`면 검사 안 함 | 없음 |
| `PHONE_TRUSTED_PROXIES` | 믿는 프록시 CIDR(쉼표)·`private`. `X-Forwarded-For`를 읽는다 | 없음 |
| `PHONE_IP_MAX_CALLS` | 주소 하나의 동시 통화 상한 (사설·루프백 주소는 안 셈) | `4` |
| `PHONE_IP_CALLS_PER_HOUR` | 주소 하나가 한 시간에 걸 수 있는 통화 수 | `30` |
| `PHONE_DAY_MAX_CALLS` | 하루 통화 수 상한. 넘으면 `closed_today` | `0`(끔) |
| `PHONE_DAY_MAX_MINUTES` | 하루 통화 분 합계 상한 | `0`(끔) |
| `PHONE_WS_MAX_BYTES` | WS 메시지 하나의 최대 크기(바이트) | `65536` |
| `PHONE_NLU_MAX_TOKENS` | HCX NLU 요청의 `maxTokens`. 0이면 코드 기본(튜닝 512·기본 1024) | `0` |
| `PHONE_NLU_TIMEOUT_S` | HCX NLU 요청 하나의 시간 제한(초) | `4.0` |
| `PHONE_NLU_FALLBACK` | `rules`면 HCX 실패·한도 초과 턴을 규칙 NLU가. `off`면 되묻기 | `rules` |
| `PHONE_HCX_RPM` | 프로세스 전체 HCX NLU 분당 요청 상한. 반을 넘기면 미리 해석을 건너뜀 | `0`(끔) |
| `PHONE_HCX_TPM` | 분당 토큰 상한(입력 + `maxTokens`로 센다) | `0`(끔) |
| `PHONE_TTS_RETRIES` | CLOVA Voice 일시 오류(429·5xx·시간 초과)에 다시 부르는 횟수 | `1` |
| `PHONE_TTS_FALLBACK` | `caption`이면 실패한 문장 하나는 무음 + 자막. `off`면 종료 | `caption` |
| `PHONE_TTS_TIMEOUT_S` | CLOVA Voice 요청 하나의 시간 제한(초) | `10.0` |
| `PHONE_OWNER_MAX_CALL_CARDS` | 사장님 허브가 두는 끝난 통화 카드 수 | `50` |
| `PHONE_OWNER_CALL_CARD_HOURS` | 끝난 통화 카드 보관 시간 | `24` |
| `PHONE_OWNER_MAX_ORDER_CARDS` | 확정 주문 카드 수 | `200` |
| `PHONE_OWNER_ORDER_CARD_HOURS` | 확정 주문 카드 보관 시간 | `72` |
| `PHONE_MAX_ORDER_QTY` | 한 통화의 총 수량 상한. 비면 가게 JSON `phone` 값(기본 100) | 없음 |
| `PHONE_MAX_ORDER_TOTAL` | 한 통화의 총액 상한(원). 비면 가게 JSON(기본 1,000,000) | 없음 |
| `PHONE_LOG_KEEP_DAYS` | 손님 말이 든 JSONL·녹음 보관 일수. 지나면 지운다. 0이면 안 지움 | `14` |
| `PHONE_DISK_WARN_PCT` | 로그 디스크 사용률이 이 값(%)을 넘으면 경고 로그. 0이면 끔 | `80` |

키(`CLOVA_SPEECH_SECRET_KEY` 등)는 여기서 해석하지 않고 `env`에 담아 어댑터에 넘긴다.
`repr`에 값이 나오지 않는다.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agenthan.voice.call.barge import BargeSettings
from agenthan.voice.call.defaults import (
    CONT_WAIT_S,
    MODEL_HIGH,
    MODEL_LOW,
    MORE_NO_WAIT_S,
    NO_INPUT_S,
    OWNER_WAIT_S,
    TRANSLATE_KEEPALIVE_S,
    TRANSLATE_TIMEOUT_S,
    WAIT_REQUEST_S,
)
from agenthan.voice.call.vad import VadSettings
from agenthan.voice.gate import parse_networks
from agenthan.voice.store_config import KST, StoreConfig

if TYPE_CHECKING:
    from agenthan.voice.call.session import SessionSettings
    from agenthan.voice.owner.hub import HubLimits

NluName = Literal["fake", "hcx"]
AdapterName = Literal["fake", "clova"]
TurnModelName = Literal["smart_turn", "off"]
BargeName = Literal["off", "shadow", "on"]
MicCloseName = Literal["playback", "state"]
DEFAULT_TURN_MODEL_PATH = Path("/app/models/smart-turn-v3.2-cpu.onnx")  # Dockerfile이 받는 자리

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
    record_dir: Path | None = None  # 통화 녹음 (5단계 티켓 01). None이면 꺼짐
    stt_boost_weight: float = 0.0  # CLOVA 키워드 부스팅 (5단계 티켓 07). 0이면 끔
    tts_speaker: str = ""  # CLOVA Voice 화자. 비면 어댑터 기본(대성 Pro, 5단계 티켓 10)
    now: datetime | None = None
    fake_replies: tuple[str, ...] = DEFAULT_FAKE_REPLIES
    store_fixture: Path | None = None  # None이면 샘플 가게
    web_dist: Path = Path("web/dist")
    vad: VadSettings = field(default_factory=VadSettings)
    max_calls: int = 10  # 동시 통화 상한 (리뷰 17번)
    max_call_s: float = 600.0  # 통화 하나의 최대 길이
    # 아래 턴 관리 값은 통화 세션 설정과 이름이 같다(`session_settings`, 리뷰 S12)
    cont_wait_s: float = CONT_WAIT_S  # 이어짐 대기 상한 (3단계 티켓 02)
    # 추가 주문 질문 뒤 맨 "아니요"의 짧은 이어짐 대기 (6단계 티켓 02). 0이면 끔
    more_no_wait_s: float = MORE_NO_WAIT_S
    # 반이중에서 마이크를 닫는 때 (6단계 티켓 02). playback: 실제 재생 시작, state: 전처럼
    mic_close: MicCloseName = "playback"
    # 첫 소리 문지기 + 앞 턴 되감기 (6단계 티켓 02 추천안). 11/16 실기기 통화 2건에서 쪼개진 턴
    # 7개가 모두 앞 턴 첫 소리 전에 시작한 말이라 서버(`from_env`)는 기본으로 켠다. 이 필드 기본은
    # 세션 기본(`SessionSettings`)과 같이 끔: 세션 테스트들이 꺼진 동작(다음 턴으로 쌓기)을 본다
    turn_rewind: bool = False
    gate_max_s: float = 3.0  # 문지기 상한: 글 없는 소리는 여기까지만 기다린다
    # 받아들인 대답 앞에 고정 문장 "네." (6단계 품질). 억양을 들어 보기 전이라 기본 끔
    ack_first: bool = False
    # 스모크 통화 표시 열쇠 (6단계 품질). 비면 표시를 받지 않는다
    smoke_key: str = field(default="", repr=False)
    no_input_s: float = NO_INPUT_S  # 무응답 되묻기 대기 (3단계 티켓 04)
    wait_request_s: float = WAIT_REQUEST_S  # "잠깐만요" 뒤 첫 대기
    # 오디오 턴 모델 (3단계 티켓 03). 기본 켬(사용자 결정 2026-11-08), 끄면 끝말 규칙만
    turn_model: TurnModelName = "smart_turn"
    turn_model_path: Path = DEFAULT_TURN_MODEL_PATH
    turn_model_low: float = MODEL_LOW
    turn_model_high: float = MODEL_HIGH
    # 음성 끼어들기 (3단계 티켓 06). None이면 꺼짐(기본, 반이중 + 말 끊기 버튼)
    barge: BargeSettings | None = None
    # 사장님 화면 비밀 토큰 (4단계 티켓 01). 비면 사장님 경로가 모두 잠긴다(401)
    owner_key: str = field(default="", repr=False)
    # Papago 요청 시간 제한 (4단계 티켓 03). 키(`PAPAGO_CLIENT_ID`·`_SECRET`)는 `env`에서 꺼낸다
    translate_timeout_s: float = TRANSLATE_TIMEOUT_S
    # Papago 유휴 연결 유지 (티켓 12). httpx 기본 5초면 턴 사이마다 다시 연결한다
    # (실측 쉬었다 부르기 중앙값 0.82초 → 60초 유지 0.60초)
    translate_keepalive_s: float = TRANSLATE_KEEPALIVE_S
    # 사장님 질문 답 대기 (4단계 티켓 06). 허브 카드 마감과 세션 시간 초과가 같이 쓴다
    owner_wait_s: float = OWNER_WAIT_S
    ask_owner: bool = False  # 통화 중 사장님께 바로 묻기 (5단계 티켓 09). 기본 끔
    answer: bool = True  # 손님 질문에 가게 자료로 답하는 답변기 (5단계 티켓 11)
    # ---- 6단계 운영 보강: 문지기 (`gate.py`). 숫자 상한은 0이면 끈다
    allowed_origins: tuple[str, ...] = ()  # 비면 Origin을 검사하지 않는다
    trusted_proxies: tuple[str, ...] = ()  # CIDR 또는 "private". 비면 소켓 상대 주소를 쓴다
    ip_max_calls: int = 4  # 주소 하나의 동시 통화 (발표장 NAT를 생각해 느슨하게)
    ip_calls_per_hour: int = 30
    day_max_calls: int = 0  # 하루 예산. 0이면 세지 않는다
    day_max_minutes: float = 0.0
    ws_max_bytes: int = 65_536  # WS 메시지 하나 (OWASP 권고 64KB. 오디오 프레임은 3,200바이트)
    # ---- 6단계: HCX 한도 대응 (`runtime.py`, `nlu/budget.py`)
    nlu_max_tokens: int = 0  # 0이면 코드 기본값(정답셋 녹화본과 같은 요청 본문)
    nlu_timeout_s: float = 4.0  # 1회 재시도까지 해도 엔진 시간 제한(20초) 안에 규칙 NLU로 넘어간다
    nlu_fallback: bool = True  # HCX 실패·한도 초과 턴은 규칙 NLU가 맡는다
    hcx_rpm: int = 0  # 분당 요청 상한. 0이면 세지 않는다
    hcx_tpm: int = 0  # 분당 토큰 상한(입력 + maxTokens)
    # ---- 6단계: TTS 일시 오류 (`tts_clova.py`, `adapters.py`)
    tts_retries: int = 1
    tts_fallback: bool = True  # 실패한 문장 하나는 무음 + 자막으로 넘겨 통화를 잇는다
    tts_timeout_s: float = 10.0
    # ---- 6단계: 사장님 허브 카드 상한 (`owner/hub.py` `HubLimits`). 0이면 제한 없음
    owner_max_call_cards: int = 50
    owner_call_card_hours: float = 24.0
    owner_max_order_cards: int = 200
    owner_order_card_hours: float = 72.0
    # ---- 6단계: 주문 가드. None이면 가게 설정(`StoreConfig.max_order_*`)을 그대로 쓴다
    max_order_qty: int | None = None
    max_order_total: int | None = None
    # ---- 6단계: 집안일 (`housekeeping.py`)
    log_keep_days: float = 14.0  # 손님 말이 든 JSONL·녹음 보관 기한. 0이면 지우지 않는다
    disk_warn_pct: float = 80.0  # 디스크 사용률 경고 기준(%). 0이면 끈다
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _check("PHONE_NLU", self.nlu, get_args(NluName))
        _check("PHONE_STT", self.stt, get_args(AdapterName))
        _check("PHONE_TTS", self.tts, get_args(AdapterName))
        _check("PHONE_TURN_MODEL", self.turn_model, get_args(TurnModelName))
        _check("PHONE_MIC_CLOSE", self.mic_close, get_args(MicCloseName))
        if not 0 < self.turn_model_low <= self.turn_model_high < 1:
            raise SettingsError(
                "PHONE_TURN_LOW ≤ PHONE_TURN_HIGH이고 둘 다 0과 1 사이여야 해요: "
                f"{self.turn_model_low!r} / {self.turn_model_high!r}"
            )
        if self.now is not None and self.now.tzinfo is None:
            raise SettingsError("PHONE_NOW에 시간대를 붙여 주세요. 예: 2026-11-08T16:00:00+09:00")
        for origin in self.allowed_origins:
            if not origin.lower().startswith(("http://", "https://")):
                raise SettingsError(
                    "PHONE_ALLOWED_ORIGINS는 https://도메인 모양이어야 해요 (쉼표로 여러 개)")
        try:
            parse_networks(self.trusted_proxies)
        except ValueError as err:
            raise SettingsError(
                "PHONE_TRUSTED_PROXIES는 CIDR(예: 172.16.0.0/12) 목록이나 private이어야 해요"
            ) from err

    def session_settings(self) -> SessionSettings:
        """통화 세션 설정. 같은 이름의 값만 옮기고 나머지는 세션 기본값이다 (리뷰 S12)."""
        from agenthan.voice.call.session import SessionSettings  # 엔진까지 끌어오므로 쓸 때만

        return SessionSettings(**{name: getattr(self, name) for name in _SESSION_FIELDS})

    def with_order_limits(self, config: StoreConfig) -> StoreConfig:
        """서버 설정에 주문 상한이 있으면 가게 설정 위에 덮는다 (6단계 주문 가드)."""
        changes = {name: value for name in ("max_order_qty", "max_order_total")
                   if (value := getattr(self, name)) is not None}
        return replace(config, **changes) if changes else config

    def hub_limits(self) -> HubLimits:
        """사장님 허브 카드 상한 (`OwnerHub(limits=...)`)."""
        from agenthan.voice.owner.hub import HubLimits

        return HubLimits(max_call_cards=self.owner_max_call_cards,
                         call_card_hours=self.owner_call_card_hours,
                         max_order_cards=self.owner_max_order_cards,
                         order_card_hours=self.owner_order_card_hours)

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
            ep_silence_ms=int(_number("PHONE_VAD_EP_SILENCE_MS",
                                      get("PHONE_VAD_EP_SILENCE_MS", ""), base.ep_silence_ms)),
        )
        if vad.ep_silence_ms > vad.end_silence_ms:
            raise SettingsError(
                "PHONE_VAD_EP_SILENCE_MS는 PHONE_VAD_END_SILENCE_MS 이하여야 해요 "
                f"(epFlag 먼저, 턴 판정은 그 뒤): {vad.ep_silence_ms} > {vad.end_silence_ms}"
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
            record_dir=Path(rec) if (rec := get("PHONE_RECORD_DIR", "")) else None,
            tts_speaker=get("PHONE_TTS_SPEAKER", ""),
            stt_boost_weight=_number("PHONE_STT_BOOST_WEIGHT", get("PHONE_STT_BOOST_WEIGHT", ""),
                                     0.0),
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
            cont_wait_s=_number("PHONE_CONT_WAIT_S", get("PHONE_CONT_WAIT_S", ""), CONT_WAIT_S),
            more_no_wait_s=_number("PHONE_MORE_NO_WAIT_S", get("PHONE_MORE_NO_WAIT_S", ""),
                                   MORE_NO_WAIT_S, allow_zero=True),
            mic_close=get("PHONE_MIC_CLOSE", "playback"),  # type: ignore[arg-type]
            turn_rewind=_switch("PHONE_TURN_REWIND", get("PHONE_TURN_REWIND", "on")),
            gate_max_s=_number("PHONE_GATE_MAX_S", get("PHONE_GATE_MAX_S", ""), 3.0),
            ack_first=_switch("PHONE_ACK_FIRST", get("PHONE_ACK_FIRST", "off")),
            smoke_key=get("PHONE_SMOKE_KEY", ""),
            no_input_s=_number("PHONE_NO_INPUT_S", get("PHONE_NO_INPUT_S", ""), NO_INPUT_S),
            wait_request_s=_number("PHONE_WAIT_REQUEST_S", get("PHONE_WAIT_REQUEST_S", ""),
                                   WAIT_REQUEST_S),
            turn_model=get("PHONE_TURN_MODEL", "smart_turn"),  # type: ignore[arg-type]
            turn_model_path=Path(get("PHONE_TURN_MODEL_PATH", str(DEFAULT_TURN_MODEL_PATH))),
            turn_model_low=_fraction("PHONE_TURN_LOW", get("PHONE_TURN_LOW", ""), MODEL_LOW),
            turn_model_high=_fraction("PHONE_TURN_HIGH", get("PHONE_TURN_HIGH", ""), MODEL_HIGH),
            barge=_barge(get),
            owner_key=get("PHONE_OWNER_KEY", ""),
            translate_timeout_s=_number("PHONE_TRANSLATE_TIMEOUT_S",
                                        get("PHONE_TRANSLATE_TIMEOUT_S", ""),
                                        TRANSLATE_TIMEOUT_S),
            translate_keepalive_s=_number("PHONE_TRANSLATE_KEEPALIVE_S",
                                          get("PHONE_TRANSLATE_KEEPALIVE_S", ""),
                                          TRANSLATE_KEEPALIVE_S),
            owner_wait_s=_number("PHONE_OWNER_WAIT_S", get("PHONE_OWNER_WAIT_S", ""),
                                 OWNER_WAIT_S),
            ask_owner=get("PHONE_ASK_OWNER", "off") == "on",
            answer=get("PHONE_ANSWER", "on") == "on",
            # `*`는 "검사 안 함": compose가 기본값(배포 도메인)을 넣어서 빈 값으로는 못 끈다
            allowed_origins=tuple(o for o in _names(get("PHONE_ALLOWED_ORIGINS", ""))
                                  if o != "*"),
            trusted_proxies=_names(get("PHONE_TRUSTED_PROXIES", "")),
            ip_max_calls=_count("PHONE_IP_MAX_CALLS", get("PHONE_IP_MAX_CALLS", ""), 4),
            ip_calls_per_hour=_count("PHONE_IP_CALLS_PER_HOUR",
                                     get("PHONE_IP_CALLS_PER_HOUR", ""), 30),
            day_max_calls=_count("PHONE_DAY_MAX_CALLS", get("PHONE_DAY_MAX_CALLS", ""), 0),
            day_max_minutes=_limit("PHONE_DAY_MAX_MINUTES", get("PHONE_DAY_MAX_MINUTES", ""),
                                   0.0),
            ws_max_bytes=int(_number("PHONE_WS_MAX_BYTES", get("PHONE_WS_MAX_BYTES", ""),
                                     65_536, minimum=4_096)),
            nlu_max_tokens=_count("PHONE_NLU_MAX_TOKENS", get("PHONE_NLU_MAX_TOKENS", ""), 0),
            nlu_timeout_s=_number("PHONE_NLU_TIMEOUT_S", get("PHONE_NLU_TIMEOUT_S", ""), 4.0),
            nlu_fallback=_choice("PHONE_NLU_FALLBACK", get("PHONE_NLU_FALLBACK", "rules"),
                                 ("rules", "off")) == "rules",
            hcx_rpm=_count("PHONE_HCX_RPM", get("PHONE_HCX_RPM", ""), 0),
            hcx_tpm=_count("PHONE_HCX_TPM", get("PHONE_HCX_TPM", ""), 0),
            tts_retries=_count("PHONE_TTS_RETRIES", get("PHONE_TTS_RETRIES", ""), 1),
            tts_fallback=_choice("PHONE_TTS_FALLBACK", get("PHONE_TTS_FALLBACK", "caption"),
                                 ("caption", "off")) == "caption",
            tts_timeout_s=_number("PHONE_TTS_TIMEOUT_S", get("PHONE_TTS_TIMEOUT_S", ""), 10.0),
            owner_max_call_cards=_count("PHONE_OWNER_MAX_CALL_CARDS",
                                        get("PHONE_OWNER_MAX_CALL_CARDS", ""), 50),
            owner_call_card_hours=_limit("PHONE_OWNER_CALL_CARD_HOURS",
                                         get("PHONE_OWNER_CALL_CARD_HOURS", ""), 24.0),
            owner_max_order_cards=_count("PHONE_OWNER_MAX_ORDER_CARDS",
                                         get("PHONE_OWNER_MAX_ORDER_CARDS", ""), 200),
            owner_order_card_hours=_limit("PHONE_OWNER_ORDER_CARD_HOURS",
                                          get("PHONE_OWNER_ORDER_CARD_HOURS", ""), 72.0),
            max_order_qty=(_count("PHONE_MAX_ORDER_QTY", qty, 0)
                           if (qty := get("PHONE_MAX_ORDER_QTY", "")) else None),
            max_order_total=(_count("PHONE_MAX_ORDER_TOTAL", total, 0)
                             if (total := get("PHONE_MAX_ORDER_TOTAL", "")) else None),
            log_keep_days=_limit("PHONE_LOG_KEEP_DAYS", get("PHONE_LOG_KEEP_DAYS", ""), 14.0),
            disk_warn_pct=_limit("PHONE_DISK_WARN_PCT", get("PHONE_DISK_WARN_PCT", ""), 80.0),
            env=env,
        )


# 서버 설정에서 통화 세션 설정으로 그대로 옮기는 값 (`SessionSettings`와 이름이 같다)
_SESSION_FIELDS = ("vad", "max_call_s", "cont_wait_s", "more_no_wait_s", "mic_close",
                   "turn_rewind", "gate_max_s", "ack_first", "smoke_key",
                   "no_input_s", "wait_request_s",
                   "turn_model_low", "turn_model_high", "barge", "owner_wait_s", "ask_owner",
                   "answer")


def _barge(get: Callable[[str, str], str]) -> BargeSettings | None:
    """`PHONE_VOICE_BARGE_IN`과 문턱들. 꺼져 있으면 문턱 변수는 보지 않는다."""
    mode = get("PHONE_VOICE_BARGE_IN", "off")
    _check("PHONE_VOICE_BARGE_IN", mode, get_args(BargeName))
    if mode == "off":
        return None
    base = BargeSettings()
    duck = int(_number("PHONE_BARGE_DUCK_MS", get("PHONE_BARGE_DUCK_MS", ""), base.duck_ms,
                       minimum=100))
    # 한국어 맞장구는 최대 2.4초다. 소리만으로 멈추는 문턱은 1초 아래로 두지 않는다 (s3-papers 3)
    stop = int(_number("PHONE_BARGE_STOP_MS", get("PHONE_BARGE_STOP_MS", ""), base.stop_ms,
                       minimum=max(1_000, duck)))
    return BargeSettings(
        mode=mode,  # type: ignore[arg-type]  # 위에서 검사
        threshold=_number("PHONE_BARGE_THRESHOLD", get("PHONE_BARGE_THRESHOLD", ""),
                          base.threshold),
        duck_ms=duck,
        stop_ms=stop,
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


def _switch(name: str, value: str) -> bool:
    """`on`·`off`만 받는다. 오타("true"·"1")가 조용히 꺼짐이 되지 않게 한다."""
    _check(name, value, ("off", "on"))
    return value == "on"


def _check(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise SettingsError(f"{name}은(는) {' · '.join(allowed)} 중 하나여야 해요: {value!r}")


def _choice(name: str, value: str, allowed: tuple[str, ...]) -> str:
    _check(name, value, allowed)
    return value


def _number(name: str, value: str, default: float, *, minimum: float = 0.0,
            allow_zero: bool = False) -> float:
    """양수(`minimum`이 있으면 그 이상)만 받는다. nan·inf·0은 틀린 값이다 (리뷰 13번).

    `allow_zero`면 0도 받는다(0이 "끔"인 값).
    """
    if not value:
        return float(default)
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    positive = number > 0 or (allow_zero and number == 0)
    if not (math.isfinite(number) and positive and number >= minimum):
        least = "0 이상의" if allow_zero else (f"{minimum:g} 이상" if minimum else "0보다 큰")
        raise SettingsError(f"{name}는 {least} 숫자여야 해요: {value!r}")
    return number


def _limit(name: str, value: str, default: float) -> float:
    """0 이상인 수. 0은 "상한 없음(끔)"이다."""
    if not value:
        return float(default)
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not (math.isfinite(number) and number >= 0):
        raise SettingsError(f"{name}는 0 이상 숫자여야 해요 (0이면 끔): {value!r}")
    return number


def _count(name: str, value: str, default: int) -> int:
    """0 이상인 정수. 0은 "상한 없음(끔)"이다."""
    number = _limit(name, value, default)
    if number != int(number):
        raise SettingsError(f"{name}는 0 이상 정수여야 해요 (0이면 끔): {value!r}")
    return int(number)


def _names(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _fraction(name: str, value: str, default: float) -> float:
    """0과 1 사이(둘 다 빼고)의 수."""
    number = _number(name, value, default)
    if number >= 1:
        raise SettingsError(f"{name}는 0과 1 사이 숫자여야 해요: {value!r}")
    return number


def _zone(name: str) -> tzinfo:
    if name in ("Asia/Seoul", "KST"):
        return KST  # tzdata 없는 서버에서도 되게 (KST는 서머타임이 없다)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise SettingsError(f"PHONE_TZ를 모르겠어요: {name!r}. 예: Asia/Seoul") from err
