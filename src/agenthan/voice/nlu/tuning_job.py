"""HCX 튜닝 작업 도구 (5단계 티켓 08): 학습 데이터 올리기 → 학습 시작 → 상태 보기.

키는 `.env`(저장소 루트)에서 읽는다. 값은 출력하지 않는다.
- `CLOVASTUDIO_API_KEY`: 학습·상태 API
- `NCP_ACCESS_KEY`·`NCP_SECRET_KEY`·`BUCKET_NAME`: Object Storage(S3 호환, kr-standard)

    uv run python -m agenthan.voice.nlu.tuning_job upload private/tuning/train.jsonl
    uv run python -m agenthan.voice.nlu.tuning_job create --epochs 4    # 돈이 든다(학습 토큰)
    uv run python -m agenthan.voice.nlu.tuning_job status <taskId>

API: `POST /tuning/v2/tasks`(https://api.ncloud-docs.com/docs/clovastudio-posttask). 상태는
`GET /tuning/v2/tasks/{taskId}`. 학습 데이터는 Object Storage 경로와 접근 키로 넘긴다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

CLOVA = "https://clovastudio.stream.ntruss.com"
OBJECT_HOST = "kr.object.ncloudstorage.com"
REGION = "kr-standard"
MODEL = "HCX-DASH-002"
DATASET_KEY = "tuning/phone-nlu/train.jsonl"


def read_env(path: Path = Path(".env")) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.strip().startswith("#"):
                env[name.strip()] = value.strip().strip("'\"")
    return env


def _need(env: Mapping[str, str], *names: str) -> list[str]:
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise SystemExit(f".env에 {', '.join(missing)}가 없어요")
    return [env[n] for n in names]


# ---------------------------------------------------------------- Object Storage (SigV4)


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(
    method: str,
    host: str,
    path: str,
    body: bytes,
    access: str,
    secret: str,
    *,
    now: dt.datetime | None = None,
    region: str = REGION,
) -> dict[str, str]:
    """S3 SigV4 서명 헤더. 경로는 이미 `/bucket/key` 꼴."""
    now = now or dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical = "\n".join(
        (
            method,
            quote(path, safe="/-_.~"),
            "",
            "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
            signed,
            payload_hash,
        )
    )
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest())
    )
    key = _sign(_sign(_sign(_sign(f"AWS4{secret}".encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, "
        f"Signature={signature}"
    )
    return {"Authorization": auth, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}


def upload(file: Path, env: Mapping[str, str], key: str = DATASET_KEY) -> str:
    access, secret, bucket = _need(env, "NCP_ACCESS_KEY", "NCP_SECRET_KEY", "BUCKET_NAME")
    body = file.read_bytes()
    path = f"/{bucket}/{key}"
    headers = sigv4_headers("PUT", OBJECT_HOST, path, body, access, secret)
    r = httpx.put(f"https://{OBJECT_HOST}{path}", content=body, headers=headers, timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"올리기 실패 HTTP {r.status_code}: {r.text[:300]}")
    return f"{bucket}/{key}"


# ---------------------------------------------------------------- 학습 API


def _clova(env: Mapping[str, str]) -> dict[str, str]:
    (key,) = _need(env, "CLOVASTUDIO_API_KEY")
    return {
        "Authorization": f"Bearer {key}",
        "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def create_body(
    env: Mapping[str, str],
    *,
    epochs: int,
    learning_rate: float,
    lora_r: int,
    name: str,
    key: str = DATASET_KEY,
) -> dict[str, Any]:
    access, secret, bucket = _need(env, "NCP_ACCESS_KEY", "NCP_SECRET_KEY", "BUCKET_NAME")
    return {
        "name": name,
        "model": MODEL,
        "tuningType": "PEFT",
        "trainEpochs": epochs,
        "learningRate": learning_rate,
        "loraR": lora_r,
        "validationSplitRatio": 0.05,
        "trainingDatasetBucket": bucket,
        "trainingDatasetFilePath": key,
        "trainingDatasetAccessKey": access,
        "trainingDatasetSecretKey": secret,
    }


def create(env: Mapping[str, str], **kw: Any) -> dict[str, Any]:
    r = httpx.post(
        f"{CLOVA}/tuning/v2/tasks", headers=_clova(env), json=create_body(env, **kw), timeout=30
    )
    return _result(r)


def status(env: Mapping[str, str], task_id: str) -> dict[str, Any]:
    r = httpx.get(f"{CLOVA}/tuning/v2/tasks/{task_id}", headers=_clova(env), timeout=30)
    return _result(r)


def _result(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        raise SystemExit(f"CLOVA HTTP {r.status_code}: {r.text[:300]}") from None
    if r.status_code != 200 or body.get("status", {}).get("code") != "20000":
        raise SystemExit(
            f"CLOVA HTTP {r.status_code}: {json.dumps(body, ensure_ascii=False)[:500]}"
        )
    return body["result"]


def _show(result: Mapping[str, Any]) -> str:
    keep = {k: result.get(k) for k in ("id", "name", "model", "status")}
    info = result.get("statusInfo") or {}
    keep["progress"] = {
        k: info.get(k)
        for k in (
            "currEpoch",
            "totalTrainEpochs",
            "currStep",
            "totalTrainSteps",
            "trainLoss",
            "estimatedTime",
            "failureReason",
            "message",
        )
        if info.get(k)
    }
    return json.dumps(keep, ensure_ascii=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HCX 튜닝 작업")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload")
    up.add_argument("file", type=Path)
    cr = sub.add_parser("create")
    cr.add_argument("--epochs", type=int, default=4)
    cr.add_argument("--lr", type=float, default=1e-4)
    cr.add_argument("--lora-r", type=int, default=8)
    cr.add_argument("--name", default="phone-nlu-" + dt.datetime.now().strftime("%m%d-%H%M"))
    st = sub.add_parser("status")
    st.add_argument("task_id")
    args = parser.parse_args(argv)
    env = read_env()
    if args.cmd == "upload":
        print("올림:", upload(args.file, env))
    elif args.cmd == "create":
        print(
            _show(
                create(
                    env,
                    epochs=args.epochs,
                    learning_rate=args.lr,
                    lora_r=args.lora_r,
                    name=args.name,
                )
            )
        )
    else:
        print(_show(status(env, args.task_id)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
