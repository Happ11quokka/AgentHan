"""Local-only shop data. No publishing or network operations."""

import json
import os
import tempfile
import unicodedata
import uuid
from pathlib import Path

SEED_PATH = Path(__file__).with_name("dummy_store_seed.json")
DEFAULT_STORE = Path(".agenthan/store.json")


class DummyStoreError(Exception):
    """A sanitized storage or input error."""


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def valid_price(value: object) -> bool:
    return type(value) is int and value > 0


def find_reviews(reviews: list[dict], query: str) -> list[dict]:
    """Find a review by id, exact text, then a unique partial quote of the text."""
    key = normalize(query)
    for exact in (
        [r for r in reviews if normalize(r["id"]) == key],
        [r for r in reviews if normalize(r["review_text"]) == key],
    ):
        if exact:
            return exact
    # Owners and the model often quote only part of a review ("늦게 나왔어요").
    return [r for r in reviews if key in normalize(r["review_text"])]


class DummyStore:
    def __init__(self, path: str | Path = DEFAULT_STORE):
        self.path = Path(path).expanduser()
        if self.path.resolve() == SEED_PATH.resolve():
            raise DummyStoreError("시드 파일은 실행 저장소로 사용할 수 없습니다.")

    def read(self) -> dict:
        """Before the first valid write, read the seed without creating a file."""
        try:
            source = self.path if self.path.exists() else SEED_PATH
            data = json.loads(source.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("shop"), dict)
                or not isinstance(data.get("menus"), list)
                or not isinstance(data.get("reviews"), list)
            ):
                raise ValueError
            for record in data["menus"]:
                if (
                    not isinstance(record["id"], str)
                    or not isinstance(record["name"], str)
                    or not normalize(record["name"])
                    or not valid_price(record["price"])
                ):
                    raise ValueError
            for record in data["reviews"]:
                if any(not isinstance(record[key], str) for key in ("id", "review_text")):
                    raise ValueError
            return data
        except (OSError, ValueError, TypeError, KeyError):
            pass
        raise DummyStoreError("더미 저장소를 읽을 수 없습니다.")

    def _write(self, data: dict) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            return
        except OSError:
            pass
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        raise DummyStoreError("더미 저장소에 저장할 수 없습니다.")

    def upsert_menu(self, name: str, price: int) -> dict:
        if not isinstance(name, str) or not normalize(name) or not valid_price(price):
            raise DummyStoreError("메뉴 이름과 양의 정수 가격(원)이 필요합니다.")
        data = self.read()
        record = next((m for m in data["menus"] if normalize(m["name"]) == normalize(name)), None)
        previous_price = None
        action = "created"
        if record is None:
            record = {"id": str(uuid.uuid4()), "name": " ".join(name.split()), "price": price}
            data["menus"].append(record)
        else:
            action = "updated"
            previous_price = record["price"]
            record["price"] = price
        self._write(data)
        saved = next(m for m in self.read()["menus"] if m["id"] == record["id"])
        result = {"ok": True, "action": action, "record": saved}
        if action == "updated":
            result["previous_price"] = previous_price
        return result

    def save_review_reply(self, review_text: str, reply_text: str) -> dict:
        if any(not isinstance(x, str) or not normalize(x) for x in (review_text, reply_text)):
            raise DummyStoreError("손님 리뷰 내용과 답글 내용이 필요합니다.")
        data = self.read()
        matches = find_reviews(data["reviews"], review_text)
        if len(matches) != 1:
            return {
                "ok": False,
                "error": "review_not_found" if not matches else "review_ambiguous",
                "message": "답글을 달 리뷰를 확인해 주세요.",
                "candidates": [
                    {"id": r["id"], "review_text": r["review_text"]}
                    for r in (matches or data["reviews"])
                ],
            }
        record = matches[0]
        record.update(reply_text=reply_text.strip(), status="saved_not_sent")
        self._write(data)
        saved = next(r for r in self.read()["reviews"] if r["id"] == record["id"])
        return {"ok": True, "action": "saved_not_sent", "record": saved}
