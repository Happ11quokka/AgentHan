"""`nest.proto` → `nest_pb2.py`·`nest_pb2_grpc.py` 다시 만들기 (생성 코드는 커밋해 둔다).

저장소 루트에서: `uv run python src/agenthan/voice/stt/gen_proto.py`
`grpcio-tools`(dev 의존성)가 필요하다. 실행에는 `grpcio`·`protobuf`만 있으면 된다.
`-I src`로 돌려서 생성 코드가 `from agenthan.voice.stt import nest_pb2`로 서로를 찾는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[2]  # .../src
HEADER = "# ruff: noqa\n# 생성 코드. 고치지 말고 gen_proto.py로 다시 만든다.\n"


def main() -> int:
    from grpc_tools import protoc  # dev 의존성

    proto = HERE / "nest.proto"
    code = protoc.main([
        "grpc_tools.protoc",
        f"-I{SRC}",
        f"--python_out={SRC}",
        f"--grpc_python_out={SRC}",
        str(proto.relative_to(SRC)),
    ])
    if code != 0:
        return code
    for name in ("nest_pb2.py", "nest_pb2_grpc.py"):
        path = HERE / name
        path.write_text(HEADER + path.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
