"""CLOVA Studio embedding v2 using only the Python standard library."""

import http.client
import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid

ENDPOINT = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
EMBEDDING_DIMENSIONS = 1024
MAX_RETRIES = 2
REQUEST_TIMEOUT = 15
RETRY_DELAY = 0.25


class ClovaEmbeddingError(Exception):
    """A sanitized configuration, transport, API, or response error."""


def validate_embedding(value: object) -> list[float]:
    """Reject malformed vectors before they enter an index or a cosine score."""
    if not isinstance(value, (list, tuple)) or len(value) != EMBEDDING_DIMENSIONS:
        raise ClovaEmbeddingError("Expected a 1024-dimensional embedding")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        raise ClovaEmbeddingError("Embedding contains invalid values")
    try:
        vector = [float(x) for x in value]
    except OverflowError:
        raise ClovaEmbeddingError("Embedding contains invalid values") from None
    norm = math.hypot(*vector)
    if not all(math.isfinite(x) for x in vector) or not norm or not math.isfinite(norm):
        raise ClovaEmbeddingError("Embedding has an invalid norm")
    return vector


def _retryable(code: int) -> bool:
    return code == 429 or 500 <= code <= 599


def embed_text(text: str) -> list[float]:
    """Embed text. Retry HTTP/API 429 and 5xx at most twice.

    Server bodies and underlying exception messages are deliberately never exposed:
    they may contain credentials. The key is read only from the environment.
    """
    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if not key:
        raise ClovaEmbeddingError("CLOVASTUDIO_API_KEY is not configured")
    if not isinstance(text, str) or not text.strip():
        raise ClovaEmbeddingError("Embedding text must not be empty")
    for attempt in range(MAX_RETRIES + 1):
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        retry = False
        failure = "CLOVA embedding request failed"
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.load(response)
            code = payload["status"]["code"]
            if code == "20000":
                return validate_embedding(payload["result"]["embedding"])
            retry = isinstance(code, str) and code[:3].isdigit() and _retryable(int(code[:3]))
            failure = "CLOVA embedding API returned an error"
        except urllib.error.HTTPError as error:
            retry = _retryable(error.code)
            error.close()
            failure = "CLOVA embedding HTTP request failed"
        except (
            OSError,
            http.client.HTTPException,
            ValueError,
            KeyError,
            TypeError,
            ClovaEmbeddingError,
        ):
            # Never propagate a body, URL, request, or exception from urllib.
            failure = "CLOVA embedding response or connection is invalid"
        if retry and attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY * (attempt + 1))
            continue
        # Raised outside an except block so sensitive exceptions have no context.
        raise ClovaEmbeddingError(failure)
    raise ClovaEmbeddingError("CLOVA embedding request failed")  # pragma: no cover
