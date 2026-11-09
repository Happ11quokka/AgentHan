"""Smart Turn 모델을 로컬에 받는다 (실기기 시험·로컬 서버용, 3단계 감사 1번).

    uv run python -m agenthan.voice.fetch_turn_model           # → models/smart-turn-v3.2-cpu.onnx

이미지(`deploy/phone/Dockerfile`)와 같은 HF 고정 리비전·SHA-256(`smart_turn.MODEL_URL`·
`MODEL_SHA256`)으로 받는다. 저장소 루트에서 돌리면 gitignore된 `/models/`에 둔다.
- 임시 파일로 받아 체크섬이 맞을 때만 제자리로 옮긴다. 틀리면 아무 파일도 남기지 않고 실패한다.
  강제로 끊겨 남은 임시 파일(`.….part`)은 다음에 돌릴 때 지운다
- 이미 맞는 파일이 있으면 받지 않는다. 틀린 파일이 있으면 새로 받아 바꾼다
- 끝나면 서버에 줄 `PHONE_TURN_MODEL_PATH=…`를 출력한다
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Literal

from agenthan.voice.smart_turn import MODEL_FILE, MODEL_SHA256, MODEL_URL

DEFAULT_DEST = Path("models") / MODEL_FILE
_BLOCK = 1 << 20


class ChecksumError(Exception):
    pass


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(
    dest: Path, *, url: str = MODEL_URL, sha256: str = MODEL_SHA256, timeout_s: float = 60.0
) -> Literal["present", "fetched"]:
    """`dest`에 체크섬이 맞는 모델을 둔다. 받지 않았으면 `present`, 받았으면 `fetched`."""
    if dest.is_file() and _digest(dest) == sha256:
        return "present"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in dest.parent.glob(f".{dest.name}.*.part"):  # 강제로 끊긴 지난 받기
        stale.unlink(missing_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
    tmp = Path(name)
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=timeout_s) as src:
            for block in iter(lambda: src.read(_BLOCK), b""):
                digest.update(block)
                out.write(block)
        got = digest.hexdigest()
        if got != sha256:
            raise ChecksumError(f"SHA-256이 고정값과 다름: {got[:12]}… (기대 {sha256[:12]}…)")
        tmp.chmod(0o644)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return "fetched"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smart Turn 모델을 고정 리비전·체크섬으로 받는다")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"기본 {DEFAULT_DEST}")
    parser.add_argument("--url", default=MODEL_URL, help=argparse.SUPPRESS)
    parser.add_argument("--sha256", default=MODEL_SHA256, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        status = fetch(args.dest, url=args.url, sha256=args.sha256)
    except ChecksumError as err:
        print(f"모델 받기 실패: {err}", file=sys.stderr)
        return 1
    except OSError as err:
        print(f"모델 받기 실패: {err}", file=sys.stderr)
        return 1
    print("이미 있음(체크섬 맞음)" if status == "present" else "받음(체크섬 맞음)")
    print(f"PHONE_TURN_MODEL_PATH={args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
