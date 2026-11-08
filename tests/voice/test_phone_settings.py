"""통화 서버 설정: 환경 변수·`.env` → `VoiceSettings`, 이름 → 어댑터 (티켓 04)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from agenthan.voice import adapters
from agenthan.voice.adapters import AdapterMissing, stt_factory, tts_factory
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import PortError
from agenthan.voice.call.vad import VadSettings
from agenthan.voice.settings import SettingsError, VoiceSettings


@pytest.fixture(autouse=True)
def _no_grpc_dns_leak(monkeypatch):
    """`stt_factory`가 `os.environ`에 넣는 `GRPC_DNS_RESOLVER`를 테스트 뒤에 되돌린다."""
    monkeypatch.setenv("GRPC_DNS_RESOLVER", "placeholder")
    monkeypatch.delenv("GRPC_DNS_RESOLVER")


def test_without_any_variable_everything_is_fake_and_kst(tmp_path) -> None:
    s = VoiceSettings.from_env({}, env_file=tmp_path / "없는.env")
    assert (s.nlu, s.stt, s.tts) == ("fake", "fake", "fake")
    assert s.log_dir == Path("logs")
    assert s.now is None
    assert datetime(2026, 11, 8, 7, 0, tzinfo=s.tz).utcoffset().total_seconds() == 9 * 3600


def test_env_file_is_read_and_real_environment_wins(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 주석\nPHONE_NLU=hcx\nPHONE_LOG_DIR='/var/log/phone'\nCLOVA_VOICE_CLIENT_ID=abc\n"
        "PHONE_FAKE_STT_REPLIES=아메리카노 하나요 | 네\n",
        encoding="utf-8",
    )
    s = VoiceSettings.from_env({"PHONE_NLU": "fake", "PHONE_NOW": "2026-11-08T16:00:00+09:00"},
                               env_file=env_file)
    assert s.nlu == "fake"
    assert s.log_dir == Path("/var/log/phone")
    assert s.fake_replies == ("아메리카노 하나요", "네")
    assert s.now == datetime.fromisoformat("2026-11-08T16:00:00+09:00")
    assert s.env["CLOVA_VOICE_CLIENT_ID"] == "abc"


def test_vad_values_come_from_the_environment_and_default_to_the_tuned_values() -> None:
    assert VoiceSettings.from_env({}, env_file=None).vad == VadSettings()
    s = VoiceSettings.from_env({"PHONE_VAD_THRESHOLD": "900", "PHONE_VAD_START_MS": "300",
                                "PHONE_VAD_END_SILENCE_MS": "600"}, env_file=None)
    assert s.vad == VadSettings(threshold=900.0, start_ms=300, end_silence_ms=600)


def test_call_limits_have_server_defaults_and_can_be_changed() -> None:
    default = VoiceSettings.from_env({}, env_file=None)
    assert (default.max_calls, default.max_call_s) == (10, 600.0)
    s = VoiceSettings.from_env({"PHONE_MAX_CALLS": "3", "PHONE_MAX_CALL_S": "300"}, env_file=None)
    assert (s.max_calls, s.max_call_s) == (3, 300.0)


def test_keys_do_not_show_in_repr(tmp_path) -> None:
    s = VoiceSettings.from_env({"CLOVA_SPEECH_SECRET_KEY": "s3cr3t"}, env_file=None)
    assert "s3cr3t" not in repr(s)


@pytest.mark.parametrize(
    ("name", "value"),
    [("PHONE_STT", "google"), ("PHONE_TTS", "x"), ("PHONE_NLU", "gpt"),
     ("PHONE_NOW", "2026-11-08T16:00:00"), ("PHONE_NOW", "내일"), ("PHONE_TZ", "Mars/Base"),
     ("PHONE_VAD_THRESHOLD", "크게"), ("PHONE_VAD_END_SILENCE_MS", "-1"),
     # 리뷰 13번: nan은 `< 0` 검사를 지나고, 0은 무음 한 프레임마다 말 끝이 된다
     ("PHONE_VAD_THRESHOLD", "nan"), ("PHONE_VAD_THRESHOLD", "inf"), ("PHONE_VAD_THRESHOLD", "0"),
     ("PHONE_VAD_START_MS", "0"), ("PHONE_VAD_END_SILENCE_MS", "0"),
     ("PHONE_VAD_END_SILENCE_MS", "50"),
     ("PHONE_MAX_CALLS", "0"), ("PHONE_MAX_CALLS", "많이"), ("PHONE_MAX_CALL_S", "nan")],
)
def test_wrong_value_names_the_variable(name, value) -> None:
    with pytest.raises(SettingsError, match=name):
        VoiceSettings.from_env({name: value}, env_file=None)


def test_fake_adapters_make_a_fresh_port_per_call() -> None:
    s = VoiceSettings(fake_replies=("메도빅 하나요",))
    make_stt, make_tts = stt_factory(s), tts_factory(s)
    first, second = make_stt(), make_stt()
    assert isinstance(first, FakeSttStream) and first is not second
    assert isinstance(make_tts(), FakeTts)


def test_clova_without_its_adapter_module_fails_clearly(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "CLOVA_STT", ("agenthan.voice.없는_stt", "02", "KEY"))
    monkeypatch.setattr(adapters, "CLOVA_TTS", ("agenthan.voice.없는_tts", "03", "KEY"))
    with pytest.raises(AdapterMissing, match="PHONE_STT=fake"):
        stt_factory(VoiceSettings(stt="clova"))
    with pytest.raises(AdapterMissing, match="티켓 03"):
        tts_factory(VoiceSettings(tts="clova"))


def test_clova_tts_takes_its_keys_from_the_settings_env() -> None:
    env = {"PHONE_TTS": "clova", "CLOVA_VOICE_CLIENT_ID": "id-1",
           "CLOVA_VOICE_CLIENT_SECRET": "s-1"}
    tts = tts_factory(VoiceSettings.from_env(env, env_file=None))()
    assert type(tts).__name__ == "ClovaVoiceTts"
    assert "s-1" not in repr(tts)


def test_clova_tts_without_keys_fails_per_call_with_tts_config() -> None:
    make_tts = tts_factory(VoiceSettings.from_env({"PHONE_TTS": "clova"}, env_file=None))
    with pytest.raises(PortError) as err:
        make_tts()
    assert (err.value.code, err.value.retry) == ("tts_config", False)


def test_clova_stt_builds_from_the_settings_env_without_network() -> None:
    env = {"PHONE_STT": "clova", "CLOVA_SPEECH_SECRET_KEY": "stt-secret-1",
           "CLOVA_SPEECH_GRPC_ENDPOINT": "https://clovaspeech-gw.ncloud.com"}
    make_stt = stt_factory(VoiceSettings.from_env(env, env_file=None))
    first, second = make_stt(), make_stt()
    assert type(first).__name__ == "ClovaSttStream" and first is not second
    assert first._settings.endpoint == "clovaspeech-gw.ncloud.com:50051"
    assert "stt-secret-1" not in repr(first._settings)
    assert first._conn is None  # 접속은 세션의 start()에서
    assert os.environ["GRPC_DNS_RESOLVER"] == "native"


def test_clova_stt_keeps_a_grpc_dns_resolver_that_is_already_set(monkeypatch) -> None:
    monkeypatch.setenv("GRPC_DNS_RESOLVER", "ares")
    stt_factory(VoiceSettings.from_env({"PHONE_STT": "clova"}, env_file=None))
    assert os.environ["GRPC_DNS_RESOLVER"] == "ares"


def test_clova_stt_takes_grpc_dns_resolver_from_the_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("PHONE_STT=clova\nGRPC_DNS_RESOLVER=ares\n", encoding="utf-8")
    stt_factory(VoiceSettings.from_env({}, env_file=env_file))
    assert os.environ["GRPC_DNS_RESOLVER"] == "ares"


def test_clova_stt_without_its_key_fails_per_call_with_stt_config() -> None:
    env = {"PHONE_STT": "clova", "CLOVA_SPEECH_SECRET_KEY": "  "}
    make_stt = stt_factory(VoiceSettings.from_env(env, env_file=None))
    with pytest.raises(PortError, match="CLOVA_SPEECH_SECRET_KEY") as err:
        make_stt()
    assert (err.value.code, err.value.retry) == ("stt_config", False)
