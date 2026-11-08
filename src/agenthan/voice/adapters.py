"""설정 이름(`fake`·`clova`) → STT·TTS 어댑터를 만드는 함수 (티켓 04).

진짜 어댑터(티켓 02 STT, 03 TTS)는 여기서만 늦게 import한다. 그래서 그 모듈이 아직 없어도
가짜 모드는 뜨고, `clova`를 골랐는데 없으면 서버가 뜰 때 `AdapterMissing`으로 바로 멈춘다.

키는 설정의 `env`(환경 변수 + `.env`)에서 꺼내 넘긴다. 키가 없으면 통화를 만들 때 `PortError`
(`*_config`)가 나고, 라우터가 그것을 `error` 이벤트로 낸다.

- TTS(티켓 03): `tts_clova.ClovaVoiceTts`. 통화마다 하나 만들고, 통화가 끝나면 라우터가 `aclose()`
- STT(티켓 02): `stt.clova.ClovaSttStream(ClovaSttSettings.from_env(env))`. 통화마다 하나.
  접속은 세션이 `start()`할 때 한다(여기서는 네트워크를 쓰지 않는다)
- gRPC DNS: 맥에서 gRPC 기본 DNS(c-ares)가 "Could not contact DNS servers"로 실패해서,
  STT 모듈(= `grpc`)을 import하기 전에 `GRPC_DNS_RESOLVER`가 비어 있으면 `native`로 둔다.
  환경 변수나 `.env`에 값이 있으면 그 값을 쓴다
"""

from __future__ import annotations

import importlib
import importlib.util
import os
from collections.abc import Callable, Mapping
from typing import Any

from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import PortError, SttStream, Tts
from agenthan.voice.settings import SettingsError, VoiceSettings

SttFactory = Callable[[], SttStream]
TtsFactory = Callable[[], Tts]

# (모듈, 티켓, 필요한 키)
CLOVA_STT = (
    "agenthan.voice.stt.clova", "02", "CLOVA_SPEECH_SECRET_KEY · CLOVA_SPEECH_GRPC_ENDPOINT"
)
STT_KEY_ENV = "CLOVA_SPEECH_SECRET_KEY"
STT_ENDPOINT_ENV = "CLOVA_SPEECH_GRPC_ENDPOINT"
GRPC_DNS_ENV = "GRPC_DNS_RESOLVER"
CLOVA_TTS = ("agenthan.voice.tts_clova", "03", "CLOVA_VOICE_CLIENT_ID · CLOVA_VOICE_CLIENT_SECRET")


class AdapterMissing(SettingsError):
    """`clova`를 골랐는데 어댑터 모듈이 없다."""


def stt_factory(settings: VoiceSettings) -> SttFactory:
    """통화마다 새 STT 스트림을 만드는 함수. 연결 하나 = 스트림 하나."""
    if settings.stt == "fake":
        return lambda: FakeSttStream(replies=settings.fake_replies)
    default_grpc_dns(settings.env)
    module = _require("PHONE_STT", CLOVA_STT)
    return lambda: _clova_stt(module, settings.env)


def default_grpc_dns(env: Mapping[str, str]) -> None:
    """`grpc` import 전에 부른다. 값이 없으면 `native`(getaddrinfo). 있으면 건드리지 않는다."""
    os.environ.setdefault(GRPC_DNS_ENV, env.get(GRPC_DNS_ENV, "").strip() or "native")


def tts_factory(settings: VoiceSettings) -> TtsFactory:
    if settings.tts == "fake":
        return FakeTts
    module = _require("PHONE_TTS", CLOVA_TTS)
    return lambda: _clova_tts(module, settings.env)


def _require(var: str, spec: tuple[str, str, str]) -> Any:
    name, ticket, keys = spec
    try:
        found = importlib.util.find_spec(name)
    except ModuleNotFoundError:
        found = None
    if found is None:
        raise AdapterMissing(
            f"{var}=clova인데 CLOVA 어댑터 `{name}`(티켓 {ticket})가 아직 없어요. "
            f"{var}=fake로 띄우거나 티켓 {ticket} 브랜치를 합쳐 주세요. 필요한 키: {keys}"
        )
    return importlib.import_module(name)


def _clova_stt(module: Any, env: Mapping[str, str]) -> SttStream:
    key = env.get(STT_KEY_ENV, "").strip()
    if not key:
        raise PortError(f"CLOVA Speech 키가 없어요 ({STT_KEY_ENV})", code="stt_config",
                        retry=False)
    endpoint = env.get(STT_ENDPOINT_ENV, "").strip()
    settings = module.ClovaSttSettings.from_env({STT_KEY_ENV: key, STT_ENDPOINT_ENV: endpoint})
    return module.ClovaSttStream(settings)


def _clova_tts(module: Any, env: Mapping[str, str]) -> Tts:
    client_id, secret = (env.get(k, "").strip() for k in module.KEY_ENVS)
    if not (client_id and secret):
        keys = ", ".join(module.KEY_ENVS)
        raise PortError(f"CLOVA Voice 키가 없어요 ({keys})", code="tts_config", retry=False)
    return module.ClovaVoiceTts(client_id, secret)
