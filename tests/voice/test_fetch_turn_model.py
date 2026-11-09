"""Smart Turn 모델 받기 도우미 (3단계 감사 1번): 고정 URL·체크섬, 틀리면 파일을 남기지 않는다.

네트워크 없이 `file://` 주소로 시험한다. 기대 체크섬은 시험이 만든 바이트에서 따로 구한다.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from agenthan.voice import fetch_turn_model as fetch
from agenthan.voice.smart_turn import MODEL_FILE, MODEL_SHA256, MODEL_URL

DOCKERFILE = Path(__file__).resolve().parents[2] / "deploy" / "phone" / "Dockerfile"


def source(tmp_path: Path, data: bytes) -> tuple[str, str]:
    src = tmp_path / "src" / MODEL_FILE
    src.parent.mkdir()
    src.write_bytes(data)
    return src.as_uri(), hashlib.sha256(data).hexdigest()


def test_the_default_url_and_checksum_are_the_ones_the_image_pins() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert f"--checksum=sha256:{MODEL_SHA256}" in text
    assert MODEL_URL in text
    assert fetch.DEFAULT_DEST == Path("models") / MODEL_FILE  # 저장소 루트의 gitignore된 `/models/`


def test_a_file_with_the_pinned_checksum_is_installed(tmp_path) -> None:
    url, digest = source(tmp_path, b"model bytes")
    dest = tmp_path / "models" / MODEL_FILE

    assert fetch.fetch(dest, url=url, sha256=digest) == "fetched"
    assert dest.read_bytes() == b"model bytes"


def test_a_wrong_checksum_leaves_no_file_behind(tmp_path) -> None:
    url, _ = source(tmp_path, b"something else")
    dest = tmp_path / "models" / MODEL_FILE

    with pytest.raises(fetch.ChecksumError):
        fetch.fetch(dest, url=url, sha256="0" * 64)
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []  # 받다 만 임시 파일도 없다


def test_a_partial_download_left_by_a_killed_run_is_cleaned_up(tmp_path) -> None:
    url, digest = source(tmp_path, b"model bytes")
    dest = tmp_path / "models" / MODEL_FILE
    dest.parent.mkdir()
    (dest.parent / f".{MODEL_FILE}.abc123.part").write_bytes(b"half")

    assert fetch.fetch(dest, url=url, sha256=digest) == "fetched"
    assert sorted(p.name for p in dest.parent.iterdir()) == [MODEL_FILE]


def test_a_good_file_already_in_place_is_kept(tmp_path) -> None:
    url, digest = source(tmp_path, b"model bytes")
    dest = tmp_path / "models" / MODEL_FILE
    dest.parent.mkdir()
    dest.write_bytes(b"model bytes")

    assert fetch.fetch(dest, url="file:///nowhere", sha256=digest) == "present"


def test_a_bad_file_in_place_is_replaced(tmp_path) -> None:
    url, digest = source(tmp_path, b"model bytes")
    dest = tmp_path / "models" / MODEL_FILE
    dest.parent.mkdir()
    dest.write_bytes(b"truncated")

    assert fetch.fetch(dest, url=url, sha256=digest) == "fetched"
    assert dest.read_bytes() == b"model bytes"


def test_the_command_prints_the_server_setting_to_use(tmp_path, capsys) -> None:
    url, digest = source(tmp_path, b"model bytes")
    dest = tmp_path / "models" / MODEL_FILE

    assert fetch.main(["--dest", str(dest), "--url", url, "--sha256", digest]) == 0
    out = capsys.readouterr().out
    assert re.search(rf"PHONE_TURN_MODEL_PATH={re.escape(str(dest))}", out)


def test_the_command_fails_on_a_wrong_checksum(tmp_path, capsys) -> None:
    url, _ = source(tmp_path, b"model bytes")
    dest = tmp_path / "models" / MODEL_FILE

    assert fetch.main(["--dest", str(dest), "--url", url, "--sha256", "0" * 64]) == 1
    assert "SHA-256" in capsys.readouterr().err
