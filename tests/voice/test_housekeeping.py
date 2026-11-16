"""보관 기한·디스크 경고 (6단계): 손님 말이 든 JSONL과 녹음을 기한이 지나면 지운다."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agenthan.voice.housekeeping import Housekeeper, disk_used_pct, sweep
from agenthan.voice.server import create_app
from agenthan.voice.settings import SettingsError, VoiceSettings

DAY = 86_400.0
NOW = 1_800_000_000.0


def _file(path: Path, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_call_logs_and_recordings_past_the_keep_days_are_deleted(tmp_path) -> None:
    logs, rec = tmp_path / "logs", tmp_path / "logs" / "recordings"
    old = [_file(logs / "phone-20261020.jsonl", 20), _file(logs / "stt-20261020.jsonl", 20),
           _file(logs / "hcx-20261020.jsonl", 20), _file(rec / "20261020" / "c1.wav", 20)]
    fresh = [_file(logs / "phone-20261110.jsonl", 2), _file(rec / "20261110" / "c2.wav", 2)]
    removed = sweep([logs, rec], keep_days=14, now=NOW)
    assert removed == 4
    assert not any(p.exists() for p in old) and all(p.exists() for p in fresh)
    assert not (rec / "20261020").exists()  # 빈 날짜 폴더도 지운다
    assert (rec / "20261110").is_dir()


def test_files_the_server_did_not_write_are_left_alone(tmp_path) -> None:
    logs = tmp_path / "logs"
    kept = [_file(logs / "phone-budget.json", 99), _file(logs / "DRAIN", 99),
            _file(logs / "notes.txt", 99), _file(logs / "measure" / "run.jsonl", 99),
            _file(logs / "other.jsonl", 99)]
    assert sweep([logs], keep_days=14, now=NOW) == 0
    assert all(p.exists() for p in kept)


def test_zero_keep_days_keeps_everything_and_a_missing_folder_is_fine(tmp_path) -> None:
    old = _file(tmp_path / "logs" / "phone-20200101.jsonl", 999)
    assert sweep([tmp_path / "logs"], keep_days=0, now=NOW) == 0
    assert old.exists()
    assert sweep([tmp_path / "nowhere"], keep_days=14, now=NOW) == 0


def test_a_full_disk_is_logged_as_a_warning(tmp_path, caplog) -> None:
    keeper = Housekeeper(log_dir=tmp_path, record_dir=None, keep_days=14, disk_warn_pct=80,
                         disk_pct=lambda path: 91.4)
    with caplog.at_level(logging.WARNING):
        keeper.run_once()
    assert "디스크 사용률 91%" in caplog.text
    caplog.clear()
    quiet = Housekeeper(log_dir=tmp_path, record_dir=None, keep_days=14, disk_warn_pct=80,
                        disk_pct=lambda path: 42.0)
    quiet.run_once()
    assert "디스크" not in caplog.text
    assert quiet.status()["disk_pct"] == 42


def test_disk_used_pct_reads_the_real_disk(tmp_path) -> None:
    pct = disk_used_pct(tmp_path)
    assert pct is not None and 0 <= pct <= 100
    assert disk_used_pct(tmp_path / "missing" / "deeper") is not None  # 있는 윗폴더로 잰다


def test_the_keeper_runs_at_start_and_then_on_its_interval(tmp_path) -> None:
    async def scenario() -> int:
        runs = 0

        def count(path: Path) -> float:
            nonlocal runs
            runs += 1
            return 10.0

        keeper = Housekeeper(log_dir=tmp_path, record_dir=None, keep_days=14,
                             disk_warn_pct=80, disk_pct=count, interval_s=0.01)
        task = asyncio.create_task(keeper.run_forever())
        await asyncio.sleep(0.08)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return runs

    assert asyncio.run(scenario()) >= 3


def test_the_server_sweeps_old_logs_when_it_starts(tmp_path) -> None:
    logs = tmp_path / "logs"
    old = logs / "stt-20200101.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("손님 말")
    stamp = time.time() - 30 * DAY
    os.utime(old, (stamp, stamp))
    fresh = logs / "stt-today.jsonl"
    fresh.write_text("x")
    settings = VoiceSettings(log_dir=logs, web_dist=tmp_path / "none", turn_model="off")
    with TestClient(create_app(settings)):
        pass
    assert not old.exists() and fresh.exists()


def test_keep_days_and_disk_warning_come_from_the_environment() -> None:
    assert VoiceSettings.from_env({}, env_file=None).log_keep_days == 14
    assert VoiceSettings.from_env({}, env_file=None).disk_warn_pct == 80
    settings = VoiceSettings.from_env(
        {"PHONE_LOG_KEEP_DAYS": "0", "PHONE_DISK_WARN_PCT": "90"}, env_file=None)
    assert (settings.log_keep_days, settings.disk_warn_pct) == (0, 90)
    with pytest.raises(SettingsError, match="PHONE_LOG_KEEP_DAYS"):
        VoiceSettings.from_env({"PHONE_LOG_KEEP_DAYS": "two weeks"}, env_file=None)
