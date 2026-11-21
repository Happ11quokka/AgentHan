"""Local shop data in a JSON file. With AGENTHAN_DB_URL set, every write is also copied to the
assistant_store_docs table (agenthan.db), and a missing file is read back from that copy.
No publishing operations. `watch` lets the integrated server hear every save of one chat's
store (the phone takes its menu from it)."""

import copy
import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path

from agenthan.illustration import assign_illustration

SEED_PATH = Path(__file__).with_name("dummy_store_seed.json")
DEFAULT_STORE = Path(".agenthan/store.json")

log = logging.getLogger(__name__)


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


def _db_key(path: Path) -> str:
    return str(path.resolve())


def _db_copy(path: Path) -> dict | None:
    """AGENTHAN_DB_URL이 있으면 assistant_store_docs의 사본. 없거나 DB가 꺼져 있으면 None."""
    if not os.environ.get("AGENTHAN_DB_URL"):
        return None
    from agenthan.db.stores import load_assistant_doc

    return load_assistant_doc(_db_key(path))


def _db_save(path: Path, data: dict) -> None:
    """파일을 쓴 뒤 DB에도 한 벌(실패해도 파일은 이미 저장됨)."""
    if not os.environ.get("AGENTHAN_DB_URL"):
        return
    from agenthan.db.stores import save_assistant_doc

    save_assistant_doc(_db_key(path), data)


# 메뉴판 사진은 메뉴를 먼저 등록하고 설명·사진을 백그라운드에서 채운다. 그 쓰기와 대화 턴·
# 사장님 직접 수정이 같은 파일을 읽고-고치고-쓰므로 경로마다 잠금 하나로 줄 세운다.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()

# 메뉴 줄의 ai·filling에 들어가는 칸 이름. ai: AI가 채워 사장님 확인 전인 칸,
# filling: 지금 백그라운드에서 채우는 칸(polish: 흰 배경 사진을 이미지 모델로 다듬는 중).
MENU_FLAG_FIELDS = ("name", "price", "description", "photo", "polish")


# 저장 뒤 알림(전화 메뉴 티켓 02). 비서 대화의 가게 파일을 저장하면 통합 서버가 그 가게를
# 전화용 가게로 게시한다. 경로마다 등록하므로 미리보기용 임시 사본(`_dry_run`·`place.preview`)은
# 알리지 않는다.
_WATCHERS: dict[str, Callable[[dict], None]] = {}


def watch(path: str | Path, on_saved: Callable[[dict], None]) -> None:
    """이 경로의 가게 파일을 저장할 때마다 `on_saved(data)`를 부른다. 저장한 스레드에서, 파일과
    DB 사본을 쓴 뒤에 부른다. 알림이 실패해도 저장은 끝난 것이다(로그만 남긴다)."""
    with _LOCKS_GUARD:
        _WATCHERS[_db_key(Path(path).expanduser())] = on_saved


def _notify(path: Path, data: dict) -> None:
    on_saved = _WATCHERS.get(_db_key(path))
    if on_saved is None:
        return
    try:
        on_saved(data)
    except Exception:
        log.exception("가게 저장 알림 실패 (저장은 됨): %s", path)


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _flags(record: dict, key: str) -> list[str]:
    value = record.get(key)
    return [v for v in value if v in MENU_FLAG_FIELDS] if isinstance(value, list) else []


# 메뉴판 사진 채우기 한 번(진행 줄): 언제 시작했고 어느 메뉴·칸을 채우는지. 화면이 메뉴 탭을 다시
# 열어도(다른 탭에 갔다 와도) 걸린 초와 'N/M개 완료'를 처음부터 세지 않게 저장소에 둔다.
# {started_at: 초(epoch), menu_ids: [...], fields: ["<메뉴 id>/<칸>", ...]}. 다 채우면 지운다.
FILL_KEY = "menu_fill"


def _now() -> float:
    return time.time()


def track_fill(data: dict) -> None:
    """저장 직전: 채우는 칸(사진 다듬기 polish 제외)이 있으면 진행 중인 채우기에 더하고(없으면 지금
    시작), 하나도 없으면 채우기를 지운다. 늦게 사진을 찾은 메뉴도 같은 채우기에 들어간다."""
    busy = {}
    for record in data.get("menus", []):
        flags = [f for f in _flags(record, "filling") if f != "polish"]
        if flags:
            busy[record["id"]] = flags
    if not busy:
        data.pop(FILL_KEY, None)
        return
    run = data.get(FILL_KEY)
    if not isinstance(run, dict) or type(run.get("started_at")) not in (int, float):
        run = {"started_at": _now()}
    ids = [i for i in run.get("menu_ids") or [] if isinstance(i, str)]
    fields = {f for f in run.get("fields") or [] if isinstance(f, str)}
    for menu_id, flags in busy.items():
        if menu_id not in ids:
            ids.append(menu_id)
        fields |= {f"{menu_id}/{flag}" for flag in flags}
    data[FILL_KEY] = {"started_at": run["started_at"], "menu_ids": ids, "fields": sorted(fields)}


class DummyStore:
    def __init__(self, path: str | Path = DEFAULT_STORE):
        self.path = Path(path).expanduser()
        if self.path.resolve() == SEED_PATH.resolve():
            raise DummyStoreError("시드 파일은 실행 저장소로 사용할 수 없습니다.")
        self.lock = _lock_for(self.path)

    @staticmethod
    def illustrate(record: dict) -> None:
        """새 메뉴의 대표 그림을 지금 이름으로 고른다. 저장소 잠금 안에서 불리므로 임베딩
        (네트워크)은 부르지 않는다: 정확한 이름·키워드·분류·이름 끝말만. 임베딩으로 더 맞는
        그림을 찾는 일은 잠금 밖에서 따로 한다(app.server.fill_illustrations)."""
        assign_illustration(record)

    @staticmethod
    def rename_menu(record: dict, name: str) -> bool:
        """메뉴 이름을 바꾼다(공백 정리). 이름이 실제로 바뀌었으면 대표 그림도 새 이름으로 다시
        고르고 True. 같은 이름 검사와 잠금은 부르는 쪽이 한다."""
        tidy = " ".join(name.split())
        if tidy == record["name"]:
            return False
        record["name"] = tidy
        DummyStore.illustrate(record)
        return True

    def read(self) -> dict:
        """Before the first valid write, read the seed without creating a file."""
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
            else:  # 파일이 없으면 DB 사본(재배포로 파일이 사라진 경우) → 없으면 시드
                data = _db_copy(self.path) or json.loads(SEED_PATH.read_text(encoding="utf-8"))
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
        track_fill(data)
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
            _db_save(self.path, data)
            _notify(self.path, data)
            return
        except OSError:
            pass
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        raise DummyStoreError("더미 저장소에 저장할 수 없습니다.")

    def _save_photo(self, record: dict, photo: bytes, photo_mime: str | None) -> None:
        """메뉴 사진 파일을 쓰고 record에 경로와 버전(화면 캐시를 깨는 값)을 적는다."""
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
            with tempfile.NamedTemporaryFile(dir=photo_dir, suffix=".tmp", delete=False) as stream:
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
        record["photo_version"] = int(record.get("photo_version") or 0) + 1

    def add_board_menus(self, items: list[dict]) -> list[dict]:
        """메뉴판 사진에서 읽은 메뉴를 승인 없이 바로 올린다(사장님이 메뉴 탭에서 고친다).

        items: {name, price, name_native?, description?, ai?, filling?}. 같은 이름이 이미 있으면
        새로 만들지 않고 가격만 맞춘다(바뀌면 ai에 price).
        돌려주는 줄마다 action(created|updated|unchanged)."""
        with self.lock:
            data = self.read()
            out = []
            for item in items:
                name, price = item.get("name"), item.get("price")
                if not isinstance(name, str) or not normalize(name) or not valid_price(price):
                    continue
                key = normalize(name)
                record = next((m for m in data["menus"] if normalize(m["name"]) == key), None)
                if record is None:
                    record = {
                        "id": str(uuid.uuid4()),
                        "name": " ".join(name.split()),
                        "price": price,
                        "aliases": [],
                    }
                    for key in ("name_native", "description"):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            record[key] = " ".join(value.split())
                    record["ai"] = [f for f in item.get("ai", []) if f in MENU_FLAG_FIELDS]
                    record["filling"] = [
                        f for f in item.get("filling", []) if f in MENU_FLAG_FIELDS
                    ]
                    # 이름을 현지화하는 메뉴는 채우기가 이름을 고칠 때 최종 이름으로 다시 고른다
                    self.illustrate(record)
                    data["menus"].append(record)
                    action = "created"
                else:
                    action = "unchanged"
                    if record["price"] != price:
                        record["price"] = price
                        record["ai"] = sorted(set(_flags(record, "ai")) | {"price"})
                        action = "updated"
                    wanted = [f for f in item.get("filling", []) if f == "photo"]
                    if wanted and not record.get("photo"):
                        record["filling"] = sorted(set(_flags(record, "filling")) | set(wanted))
                out.append({"action": action, "record": copy.deepcopy(record)})
            self._write(data)
            return out

    def patch_menu(
        self,
        menu_id: str,
        fields: dict,
        *,
        by_owner: bool,
        done: tuple[str, ...] = (),
        start: tuple[str, ...] = (),
        photo: bytes | None = None,
        photo_mime: str | None = None,
        photo_source: bytes | None = None,
    ) -> dict | None:
        """메뉴 한 줄의 이름·가격·설명·사진을 고친다. 없는 메뉴면 None.

        photo_source: 흰 배경으로 만들기 전 원본 조각(JPEG). 사진 다듬기(2차)가 다시 쓴다.

        by_owner=True: 사장님이 메뉴 탭에서 직접 고친 값. 그 칸의 AI 표시와 채우는 중 표시를
        지우고, 뒤에서 채우던 값이 늦게 와도 덮어쓰지 않는다.
        by_owner=False: 백그라운드 채우기. 아직 filling에 남은 칸만 쓰고(사장님이 먼저 고친
        칸은 건너뜀), 쓴 칸은 ai에 더한다. done: 값이 없어도 채우기를 끝낼 칸.
        start: 새로 채우기 시작할 칸(예: 사진 다듬기 polish)."""
        with self.lock:
            data = self.read()
            record = next((m for m in data["menus"] if m["id"] == menu_id), None)
            if record is None:
                return None
            ai, filling = set(_flags(record, "ai")), set(_flags(record, "filling"))
            values = {k: v for k, v in fields.items() if k in ("name", "price", "description")}
            if photo is not None:
                values["photo"] = photo
            if not by_owner:
                keep = filling | ({"photo"} if "polish" in filling else set())
                values = {k: v for k, v in values.items() if k in keep}
            if "name" in values:
                name = values["name"]
                if not isinstance(name, str) or not normalize(name):
                    raise DummyStoreError("메뉴 이름이 비어 있어요.")
                clash = any(
                    normalize(m["name"]) == normalize(name) and m["id"] != menu_id
                    for m in data["menus"]
                )
                if clash:
                    if by_owner:
                        raise DummyStoreError("같은 이름의 메뉴가 이미 있어요.")
                    values.pop("name")
                else:
                    self.rename_menu(record, name)
            if "price" in values:
                if not valid_price(values["price"]):
                    raise DummyStoreError("가격은 0보다 큰 정수(원)로 넣어 주세요.")
                record["price"] = values["price"]
            if "description" in values:
                text = values["description"]
                record["description"] = " ".join(text.split()) if isinstance(text, str) else ""
            if "photo" in values:
                self._save_photo(record, photo, photo_mime)
                if photo_source is not None:
                    source = self.path.parent / "photos" / f"{record['id']}-source.jpg"
                    try:
                        source.write_bytes(photo_source)
                        record["photo_source"] = f"photos/{source.name}"
                    except OSError:
                        pass  # 원본이 없으면 다듬기는 지금 사진으로 한다
            written = set(values)
            if by_owner:
                ai -= written
                filling -= written
            else:
                ai |= {k for k in written if k != "name" or values.get("name")}
                filling -= written | set(done)
                if "photo" in written:
                    filling.discard("polish")
            filling |= set(start)
            record["ai"] = sorted(ai)
            record["filling"] = sorted(filling)
            self._write(data)
            return copy.deepcopy(record)

    def delete_menu(self, menu_id: str) -> bool:
        """메뉴판에서 잘못 읽은 줄을 사장님이 지운다."""
        with self.lock:
            data = self.read()
            kept = [m for m in data["menus"] if m["id"] != menu_id]
            if len(kept) == len(data["menus"]):
                return False
            data["menus"] = kept
            self._write(data)
            return True

    def set_shop_info(self, name: str, category: str) -> dict:
        """처음 쓰는 사장님 온보딩(#94): 가게 이름·업종만 바꾸고 나머지 가게 정보는 둔다."""
        values = {"name": name, "category": category}
        if any(not isinstance(v, str) or not normalize(v) for v in values.values()):
            raise DummyStoreError("가게 이름과 업종이 필요합니다.")
        data = self.read()
        data["shop"].update({k: " ".join(v.split()) for k, v in values.items()})
        self._write(data)
        return data["shop"]

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
        (메뉴 사진 등록 A-1). 사장님이 승인한 값이라 그 칸의 AI 표시(ai)는 지운다."""
        with self.lock:
            return self._upsert_menu(
                name,
                price,
                description=description,
                name_native=name_native,
                photo=photo,
                photo_mime=photo_mime,
                menu_id=menu_id,
                create_only=create_only,
            )

    def _upsert_menu(
        self,
        name: str,
        price: int,
        *,
        description: str | None,
        name_native: str | None,
        photo: bytes | None,
        photo_mime: str | None,
        menu_id: str | None,
        create_only: bool,
    ) -> dict:
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
            self.illustrate(record)
            data["menus"].append(record)
        else:
            action = "updated"
            previous_price = record["price"]
            record["price"] = price
        record.update(extra)
        confirmed = {"name", "price"} | {k for k in extra if k == "description"}
        if photo is not None:
            self._save_photo(record, photo, photo_mime)
            confirmed.add("photo")
        if record.get("ai"):
            record["ai"] = [f for f in _flags(record, "ai") if f not in confirmed]
        self._write(data)
        saved = next(m for m in self.read()["menus"] if m["id"] == record["id"])
        result = {"ok": True, "action": action, "record": saved}
        if action == "updated":
            result["previous_price"] = previous_price
        return result
