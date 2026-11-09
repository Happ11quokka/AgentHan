"""Transport-free chat/embed recording. Files contain hashes and response payloads only."""

import copy
import hashlib
import json
from pathlib import Path

from agenthan.agent.clova_chat import (
    CHAT_MODEL,
    TEMPERATURE,
    ClovaChatError,
    ClovaRequestRejected,
    is_rejected,
    make_client,
    parse_message,
)
from agenthan.core.embedding import ClovaEmbeddingError, embed_text, validate_embedding
from agenthan.core.llm import tool_call_options

EMBED_MODEL = "embedding-v2"
# Recorded in place of a response when HCX answered HTTP 400 (no body is stored).
REJECTED = {"rejected": True}


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def request_hash(operation: str, payload: dict) -> str:
    return hashlib.sha256(canonical({"operation": operation, **payload}).encode()).hexdigest()


def chat_request(messages: list[dict], tools: list[dict], model: str) -> dict:
    return {
        "model": model,
        "messages": messages,
        "tools": tools,
        "temperature": TEMPERATURE,
        **tool_call_options(model),
    }


class ReplayMiss(ClovaChatError, ClovaEmbeddingError):
    """A missing recording must count as an API error, including matcher fallback."""


class Live:
    """Same HCX chat settings as clova_chat, retaining usage discarded by chat()."""

    def __init__(self, model: str = CHAT_MODEL):
        self.model = model
        self.client = None

    def chat(self, messages: list[dict], *, tools: list[dict]) -> dict:
        failure, rejected = "CLOVA evaluation chat failed", False
        try:
            if self.client is None:
                self.client = make_client()
            request = chat_request(messages, tools, self.model)
            if not tools:
                request.pop("tools")
            response = self.client.chat.completions.create(**request)
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise ClovaChatError("CLOVA evaluation response was truncated")
            message = parse_message(choice.message)
            usage = getattr(response, "usage", None)
            if usage is not None:
                if hasattr(usage, "model_dump"):
                    usage = usage.model_dump()
                message["usage"] = {
                    key: usage[key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage, dict) and isinstance(usage.get(key), int)
                }
            return message
        except Exception as error:  # SDK errors may include headers, keys, and server bodies.
            rejected = is_rejected(error)
        if rejected:
            raise ClovaRequestRejected("CLOVA evaluation chat rejected (HTTP 400)")
        raise ClovaChatError(failure)

    def embed(self, text: str) -> list[float]:
        return embed_text(text)


class Recorder:
    def __init__(self, path: Path, backend, *, provenance: str = "live"):
        self.path, self.backend = path, backend
        self.model = backend.model
        self.data = {"version": 1, "provenance": provenance, "entries": {}}

    def _save(self, key: str, response):
        # Requests, clients, headers, and exception messages are never serialized.
        self.data["entries"][key] = copy.deepcopy(response)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(canonical(self.data) + "\n", encoding="utf-8")
        return response

    def chat(self, messages: list[dict], *, tools: list[dict]) -> dict:
        key = request_hash("chat", chat_request(messages, tools, self.model))
        try:
            response = self.backend.chat(messages, tools=tools)
        except ClovaRequestRejected:
            self._save(key, REJECTED)
            raise
        safe = parse_message(response)
        if isinstance(response.get("usage"), dict):
            safe["usage"] = {
                key: response["usage"][key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(response["usage"].get(key), int)
            }
        return self._save(key, safe)

    def embed(self, text: str) -> list[float]:
        response = validate_embedding(self.backend.embed(text))
        return self._save(request_hash("embed", {"model": EMBED_MODEL, "text": text}), response)


class Replay:
    def __init__(self, path: Path, model: str = CHAT_MODEL):
        self.model = model
        self.data = json.loads(path.read_text(encoding="utf-8"))
        if self.data.get("version") != 1 or not isinstance(self.data.get("entries"), dict):
            raise ValueError("Invalid replay file")

    def _read(self, operation: str, payload: dict):
        key = request_hash(operation, payload)
        if key not in self.data["entries"]:
            raise ReplayMiss(f"Missing replay {operation}: {key}")
        return copy.deepcopy(self.data["entries"][key])

    def chat(self, messages: list[dict], *, tools: list[dict]) -> dict:
        response = self._read("chat", chat_request(messages, tools, self.model))
        if response == REJECTED:
            raise ClovaRequestRejected("Recorded CLOVA rejection (HTTP 400)")
        return response

    def embed(self, text: str) -> list[float]:
        return validate_embedding(self._read("embed", {"model": EMBED_MODEL, "text": text}))
