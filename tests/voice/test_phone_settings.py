"""통화 서버 설정: 환경 변수·`.env` → `VoiceSettings`, 이름 → 어댑터 (티켓 04)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from agenthan.voice import adapters
from agenthan.voice.adapters import AdapterMissing, stt_factory, tts_factory
from agenthan.voice.call.fakes import FakeSttStream, FakeTts
from agenthan.voice.call.ports import PortError
from agenthan.voice.settings import SettingsError, VoiceSettings


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


def test_keys_do_not_show_in_repr(tmp_path) -> None:
    s = VoiceSettings.from_env({"CLOVA_SPEECH_SECRET_KEY": "s3cr3t"}, env_file=None)
    assert "s3cr3t" not in repr(s)


@pytest.mark.parametrize(
    ("name", "value"),
    [("PHONE_STT", "google"), ("PHONE_TTS", "x"), ("PHONE_NLU", "gpt"),
     ("PHONE_NOW", "2026-11-08T16:00:00"), ("PHONE_NOW", "내일"), ("PHONE_TZ", "Mars/Base")],
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
