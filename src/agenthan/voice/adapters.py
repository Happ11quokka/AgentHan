"""설정 이름(`fake`·`clova`) → STT·TTS 어댑터를 만드는 함수 (티켓 04).

진짜 어댑터(티켓 02 STT, 03 TTS)는 여기서만 늦게 import한다. 그래서 그 모듈이 아직 없어도
가짜 모드는 뜨고, `clova`를 골랐는데 없으면 서버가 뜰 때 `AdapterMissing`으로 바로 멈춘다.

02·03이 들어오면 아래 `CLOVA_STT`·`CLOVA_TTS`의 모듈 이름과 `_clova_stt`·`_clova_tts`
두 함수만 실제 이름에 맞춘다. 어댑터는 `env`(환경 변수 + `.env`)에서 키를 읽는다.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable, Mapping
from typing import Any

from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import SttStream, Tts
from agenthan.voice.settings import SettingsError, VoiceSettings

SttFactory = Callable[[], SttStream]
TtsFactory = Callable[[], Tts]

# (모듈, 티켓, 필요한 키)
CLOVA_STT = (
    "agenthan.voice.stt.clova", "02", "CLOVA_SPEECH_SECRET_KEY · CLOVA_SPEECH_GRPC_ENDPOINT"
)
CLOVA_TTS = ("agenthan.voice.tts.clova", "03", "CLOVA_VOICE_CLIENT_ID · CLOVA_VOICE_CLIENT_SECRET")


class AdapterMissing(SettingsError):
    """`clova`를 골랐는데 어댑터 모듈이 없다."""


def stt_factory(settings: VoiceSettings) -> SttFactory:
    """통화마다 새 STT 스트림을 만드는 함수. 연결 하나 = 스트림 하나."""
    if settings.stt == "fake":
        return lambda: FakeSttStream(replies=settings.fake_replies)
    module = _require("PHONE_STT", CLOVA_STT)
    return lambda: _clova_stt(module, settings.env)


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
    return module.ClovaSttStream.from_env(env)


def _clova_tts(module: Any, env: Mapping[str, str]) -> Tts:
    return module.ClovaTts.from_env(env)
