"""Local-only shop data. No publishing or network operations."""

import json
import os
import re
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


# Collections added after the first seed; older store files get these defaults.
OPTIONAL_COLLECTIONS = {
    "talktalk": list,
    "orders": list,
    "reservations": list,
    "calls": list,
    "coupons": list,
    "settlements": dict,
    "statistics": dict,
}


def find_records(records: list[dict], query: str, text_key: str) -> list[dict]:
    """Find records by id, exact text, then a partial quote of the text."""
    key = normalize(query)
    for exact in (
        [r for r in records if normalize(r["id"]) == key],
        [r for r in records if normalize(str(r.get(text_key, ""))) == key],
    ):
        if exact:
            return exact
    # Owners and the model often quote only part of a text ("늦게 나왔어요").
    return [r for r in records if key and key in normalize(str(r.get(text_key, "")))]


def _migrate_review(record: dict) -> dict:
    """Convert a pre-integration review {id, review_text, reply_text, status} in place."""
    if "review_id" in record or "id" not in record:
        return record
    reply = record.pop("reply_text", "") or ""
    status = record.pop("status", None)
    record["review_id"] = record.pop("id")
    record["text"] = record.pop("review_text", "")
    record.setdefault("visit_date", None)
    record.setdefault("rating", None)
    record.setdefault("features", [])
    record["has_owner_reply"] = bool(reply)
    if reply:
        record["owner_reply"] = {"reply": reply, "status": status or "saved_not_sent"}
    return record


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
                record.setdefault("aliases", [])
                if (
                    not isinstance(record["id"], str)
                    or not isinstance(record["name"], str)
                    or not normalize(record["name"])
                    or not valid_price(record["price"])
                    or not isinstance(record["aliases"], list)
                    or any(not isinstance(a, str) for a in record["aliases"])
                ):
                    raise ValueError
            # Reviews use the agenthan.agent.review row schema:
            # {review_id, visit_date, rating, text, features, has_owner_reply[, owner_reply]}
            for record in data["reviews"]:
                _migrate_review(record)
                if any(not isinstance(record[key], str) for key in ("review_id", "text")):
                    raise ValueError
                if not isinstance(record.setdefault("features", []), list):
                    raise ValueError
            for key, kind in OPTIONAL_COLLECTIONS.items():
                data.setdefault(key, kind())
                if not isinstance(data[key], kind):
                    raise ValueError
                if kind is list and any(
                    not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in data[key]
                ):
                    raise ValueError
            return data
        except (OSError, ValueError, TypeError, KeyError):
            pass
        raise DummyStoreError("더미 저장소를 읽을 수 없습니다.")

    def write(self, data: dict) -> None:
        """Atomically replace the store; callers pass data obtained from read()."""
        self._write(data)

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

    def upsert_menu(
        self,
        name: str,
        price: int,
        *,
        description: str | None = None,
        name_native: str | None = None,
        photo: bytes | None = None,
        photo_mime: str | None = None,
        menu_id: str | None = None,
        create_only: bool = False,
    ) -> dict:
        """같은 이름이면 가격을 고치고, 없으면 등록한다. description·name_native는 줄 때만 저장
        (메뉴 사진 등록 A-1)."""
        if not isinstance(name, str) or not normalize(name) or not valid_price(price):
            raise DummyStoreError("메뉴 이름과 양의 정수 가격(원)이 필요합니다.")
        extra = {
            k: " ".join(v.split())
            for k, v in (("description", description), ("name_native", name_native))
            if isinstance(v, str) and v.strip()
        }
        data = self.read()
        record = next((m for m in data["menus"] if normalize(m["name"]) == normalize(name)), None)
        if menu_id is not None:
            record = next((m for m in data["menus"] if m["id"] == menu_id), None)
            if record is None:
                raise DummyStoreError("기존 메뉴를 저장소에서 찾을 수 없습니다.")
        elif create_only and record is not None:
            raise DummyStoreError("같은 이름의 메뉴가 이미 있습니다. 사진 변경을 확인해 주세요.")
        previous_price = None
        action = "created"
        if record is None:
            record = {
                "id": str(uuid.uuid4()),
                "name": " ".join(name.split()),
                "price": price,
                "aliases": [],
            }
            data["menus"].append(record)
        else:
            action = "updated"
            previous_price = record["price"]
            record["price"] = price
        record.update(extra)
        if photo is not None:
            if photo_mime not in ("image/jpeg", "image/png") or not isinstance(photo, bytes):
                raise DummyStoreError("사진 형식이 올바르지 않습니다.")
            if self.path.parent.resolve() == SEED_PATH.parent.resolve():
                raise DummyStoreError("시드 파일 옆에는 사진을 저장할 수 없습니다.")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", record["id"]):
                raise DummyStoreError("메뉴 ID가 올바르지 않습니다.")
            photo_dir = self.path.parent / "photos"
            filename = record["id"] + (".png" if photo_mime == "image/png" else ".jpg")
            temporary = None
            try:
                photo_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    dir=photo_dir, suffix=".tmp", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(photo)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(photo_dir / filename)
            except OSError:
                raise DummyStoreError("사진을 저장할 수 없습니다.") from None
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            record["photo"] = f"photos/{filename}"
        self._write(data)
        saved = next(m for m in self.read()["menus"] if m["id"] == record["id"])
        result = {"ok": True, "action": action, "record": saved}
        if action == "updated":
            result["previous_price"] = previous_price
        return result
