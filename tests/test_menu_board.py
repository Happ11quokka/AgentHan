"""메뉴판 사진 → 메뉴 탭에 바로 등록, 설명·흰 배경 사진은 뒤에서 채운다 (agent/menu_board.py)."""

import base64
import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from agenthan.agent import menu_board
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app.server import create_app
from agenthan.core.contracts import MenuItem
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.illustration import pick_illustration
from agenthan.tools.dummy_store import DummyStore, DummyStoreError
from agenthan.tools.image_cutout import CutoutResult

KNOWLEDGE = FakeStoreKnowledge(
    [MenuItem(id="m01", name_ko="메도빅", name_native="Медовик", price=7500)]
)
NAMES = {
    "Ватрушка": {"name_ko": "바트루시카", "description_ko": "치즈를 올려 구운 동그란 빵."},
    "Пельмени": {"name_ko": "펠메니", "description_ko": "러시아식 만두."},
    "김밥": {"name_ko": "김밥", "description_ko": "밥과 채소를 김에 만 음식."},
    "Блюдо дня": {"name_ko": "오늘의 수프", "description_ko": "날마다 바뀌는 수프."},
}


RED, BLUE = (0, 0, 255), (255, 0, 0)  # BGR: 이름표 자리의 색 (FakeV3가 색으로 이름표를 읽는다)


def board_image():
    """흰 메뉴판 위에 음식 사진 칸 두 개(질감 있는 네모)와 그 아래 이름표(왼쪽 빨강 Медовик,
    오른쪽 파랑 Ватрушка)."""
    image = np.full((600, 800, 3), 255, np.uint8)
    rng = np.random.default_rng(1)
    for (x0, y0), color in zip(((60, 80), (460, 80)), (RED, BLUE), strict=True):
        image[y0 : y0 + 220, x0 : x0 + 260] = rng.integers(0, 255, (220, 260, 3), dtype=np.uint8)
        image[350:380, x0 : x0 + 260] = color
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


BOARD = board_image()


BOARD_LABELS = {"Медовик": RED, "Ватрушка": BLUE}


def label_by_color(data: bytes, labels: dict) -> str:
    """이름표 자리 조각에서 이름표 색이 가장 많이 보이는 이름. 아무 색도 없으면 빈 글자."""
    piece = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).astype(int)
    seen = {
        name: (np.abs(piece - color).sum(axis=2) < 120).mean() for name, color in labels.items()
    }
    name = max(seen, key=seen.get, default="")
    return name if name and seen[name] > 0.05 else ""


class FakeV3:
    """메뉴판 읽기 질문에는 board 답을, 메뉴 하나 질문에는 NAMES를 돌려준다. 이름표 읽기에는
    잘라 온 자리의 이름표 색(labels: 이름 → BGR)으로 답한다."""

    def __init__(self, board: dict | str, labels: dict = BOARD_LABELS, strip: list | None = None):
        self.board = board
        self.labels = labels
        # 조각 읽기마다 같은 답(합칠 때 겹친 줄은 하나로). 없으면 빈 줄
        self.strip = list(strip or [])
        self.calls = []

    def __call__(self, model, body):
        self.calls.append(body)
        content = body["messages"][-1]["content"]
        if isinstance(content, list):
            text = content[1]["text"]
            if text == menu_board.PHOTOS_PROMPT:  # 사진만 다시 읽기
                photos = self.board.get("photos", []) if isinstance(self.board, dict) else []
                return {"message": {"content": json.dumps(photos, ensure_ascii=False)}}
            if text.startswith("메뉴판 일부를 잘라 낸 그림이다"):  # 사진 위치 다시 묻기
                return {"message": {"content": '{"box": [0.2, 0.2, 0.8, 0.8]}'}}
            if text == menu_board.STRIP_PROMPT:  # 큰 가로 메뉴판의 세로 조각 읽기
                answer = json.dumps({"items": self.strip}, ensure_ascii=False)
                return {"message": {"content": answer}}
            if text == menu_board.LABEL_PROMPT:  # 사진 아래 이름표 읽기
                uri = content[0]["dataUri"]["data"]
                label = label_by_color(base64.b64decode(uri.split(",", 1)[1]), self.labels)
                return {"message": {"content": json.dumps({"label": label}, ensure_ascii=False)}}
            assert text == menu_board.BOARD_PROMPT
            answer = self.board if isinstance(self.board, str) else json.dumps(self.board)
            return {"message": {"content": answer}}
        name = content.rsplit("메뉴 원어 이름: ", 1)[1]
        return {"message": {"content": json.dumps(NAMES.get(name, {}), ensure_ascii=False)}}


def fake_cutout(data, **kwargs):
    return CutoutResult(b"\xff\xd8\xffwhite", "image/jpeg", True, "", 0.5)


ITEMS = {
    "kind": "menu_board",
    "items": [
        {"name": "Медовик", "price": 8000},
        {"name": "Ватрушка", "price": 8500},
        {"name": "김밥", "price": 4000},
        {"name": "Пельмени", "price": None},
    ],
    "photos": [
        {"box": [0.57, 0.13, 0.9, 0.5], "label": "Ватрушка"},
        {"box": [0.07, 0.13, 0.4, 0.5], "label": "Медовник"},  # 이름표를 조금 틀리게 읽어도 붙는다
        {"box": [0.1, 0.6, 0.3, 0.8], "label": None},  # 이름표 없는 사진은 버린다
        {"box": [0.4, 0.6, 0.6, 0.8], "label": "Борщ"},  # 맞는 메뉴가 없는 사진도 버린다
    ],
}


def test_read_board_returns_kind_items_and_boxes_in_one_call():
    fake = FakeV3(ITEMS)
    kind, items = menu_board.read_board(BOARD, call=fake)
    assert kind == "menu_board" and len(fake.calls) == 1
    assert [(i.name, i.price) for i in items] == [
        ("Медовик", 8000),
        ("Ватрушка", 8500),
        ("김밥", 4000),
        ("Пельмени", None),
    ]
    assert items[0].box == (0.07, 0.13, 0.4, 0.5) and items[1].box == (0.57, 0.13, 0.9, 0.5)
    assert items[2].box is None and items[3].box is None


def test_duplicate_names_are_read_once():
    answer = {
        "kind": "menu_board",
        "items": [{"name": "A", "price": 1}, {"name": " a ", "price": 1}],
    }
    assert len(menu_board.read_board(BOARD, call=FakeV3(answer))[1]) == 1


@pytest.mark.parametrize(
    "box", [[0.5, 0.1, 0.4, 0.3], [10, 20, 300, 400], [0, 0, 0.01, 0.01], "x", [0, 0, 1]]
)
def test_bad_boxes_are_dropped(box):
    answer = {
        "kind": "menu_board",
        "items": [{"name": "A", "price": 1000}],
        "photos": [{"box": box, "label": "A"}],
    }
    [item] = menu_board.read_board(BOARD, call=FakeV3(answer))[1]
    assert item.box is None


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        ('{"kind": "food"}', "food"),
        ("other", "other"),
        ('{"kind": "menu_board", "items": []}', "other"),
    ],
)
def test_photos_that_are_not_boards(answer, kind):
    assert menu_board.read_board(BOARD, call=FakeV3(answer)) == (kind, [])


def test_big_photos_are_shrunk_for_reading():
    big = cv2.imencode(".jpg", np.zeros((3000, 4000, 3), np.uint8))[1].tobytes()
    fake = FakeV3(ITEMS)
    menu_board.read_board(big, call=fake)
    [whole] = [c for c in fake.calls
               if c["messages"][0]["content"][1]["text"] == menu_board.BOARD_PROMPT]
    uri = whole["messages"][0]["content"][0]["dataUri"]["data"]
    sent = cv2.imdecode(np.frombuffer(base64.b64decode(uri.split(",", 1)[1]), np.uint8), 1)
    assert max(sent.shape[:2]) == menu_board.READ_SIDE


def test_photo_regions_find_the_picture_cells_and_snap_a_loose_box():
    regions = menu_board.photo_regions(BOARD)
    loose = (0.05, 0.1, 0.45, 0.55)
    snapped = menu_board.snap(loose, regions)
    assert snapped != loose
    assert abs(snapped[0] - 60 / 800) < 0.03 and abs(snapped[2] - 320 / 800) < 0.03


# ---------------------------------------------------------------- 등록과 채우기


def run_board(tmp_path, answer=ITEMS):
    store = DummyStore(tmp_path / "store.json")
    store.write({"shop": {"name": "베이커리"}, "menus": [], "reviews": []})
    store.upsert_menu("메도빅", 7500, name_native="Медовик", description="꿀 케이크")
    fake = FakeV3(answer)
    kind, items = menu_board.read_board(BOARD, call=fake)
    result = menu_board.register(store, items, KNOWLEDGE)
    return store, fake, result


def menus(store):
    return {m["name"]: m for m in store.read()["menus"]}


def test_register_saves_right_away_and_marks_what_is_still_filling(tmp_path):
    store, _, result = run_board(tmp_path)
    assert result.updated == ["메도빅"] and result.no_price == ["Пельмени"]
    assert result.created == ["Ватрушка", "김밥"]
    saved = menus(store)
    assert saved["메도빅"]["price"] == 8000 and saved["메도빅"]["ai"] == ["price"]
    assert saved["메도빅"]["filling"] == ["photo"]  # 이름·설명은 이미 아는 메뉴라 그대로
    assert saved["Ватрушка"]["filling"] == ["name", "description", "photo"]
    assert saved["김밥"]["filling"] == ["description"]  # 한글 이름은 그대로 두고 설명만
    assert "Пельмени" not in saved


def test_fill_all_fills_names_descriptions_and_white_photos(tmp_path):
    store, fake, result = run_board(tmp_path)
    menu_board.fill_all(store, result.jobs, BOARD, "베이커리", call=fake, cutout=fake_cutout)()
    saved = menus(store)
    assert set(saved) == {"메도빅", "바트루시카", "김밥"}
    assert saved["바트루시카"]["description"] == "치즈를 올려 구운 동그란 빵."
    assert saved["바트루시카"]["name_native"] == "Ватрушка"
    assert sorted(saved["바트루시카"]["ai"]) == ["description", "name", "photo"]
    assert saved["메도빅"]["photo"] and saved["메도빅"]["photo_version"] == 1
    assert saved["메도빅"]["description"] == "꿀 케이크"
    assert saved["김밥"]["description"].startswith("밥과") and not saved["김밥"].get("photo")
    assert all(m["filling"] == [] for m in saved.values())


def test_owner_edits_win_over_late_background_values(tmp_path):
    store, fake, result = run_board(tmp_path)
    syrniki = next(j for j in result.jobs if j.item.name == "Ватрушка")
    store.patch_menu(syrniki.menu_id, {"name": "치즈 팬케이크"}, by_owner=True)
    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake, cutout=fake_cutout)()
    record = next(m for m in store.read()["menus"] if m["id"] == syrniki.menu_id)
    assert record["name"] == "치즈 팬케이크" and "name" not in record["ai"]
    assert record["description"] == "치즈를 올려 구운 동그란 빵."  # 고치지 않은 칸은 채운다


def test_a_failing_menu_does_not_stop_the_others_and_clears_filling(tmp_path):
    store, fake, result = run_board(tmp_path)

    def broken(data, **kwargs):
        raise ValueError("boom")

    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake, cutout=broken)()
    saved = menus(store)
    assert saved["바트루시카"]["description"] and not saved["바트루시카"].get("photo")
    assert all(m["filling"] == [] for m in saved.values())


# ---------------------------------------------------------------- 대표 그림


def picked(name):
    """저장소는 잠금 안에서 임베딩 없이 고른다(임베딩은 app.server.fill_illustrations가 밖에서)."""
    return pick_illustration(name)


def test_board_menus_get_the_illustration_of_their_final_localized_name(tmp_path):
    answer = {"kind": "menu_board", "items": [{"name": "Блюдо дня", "price": 6000}]}
    store, fake, result = run_board(tmp_path, answer)
    first = next(m for m in store.read()["menus"] if m["name"] == "Блюдо дня")
    assert first["illustration"] == picked("Блюдо дня")  # 등록하자마자 하나는 있다
    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake, cutout=fake_cutout)()
    soup = menus(store)["오늘의 수프"]
    assert soup["illustration"] == picked("오늘의 수프") != picked("Блюдо дня")
    assert "illustration" not in soup["ai"] + soup["filling"]  # AI 표시 대상이 아니다


def test_every_board_menu_has_an_illustration_even_with_a_cut_photo(tmp_path):
    store, fake, result = run_board(tmp_path)
    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake, cutout=fake_cutout)()
    saved = menus(store)
    assert saved["바트루시카"]["photo"] and saved["메도빅"]["photo"]
    for name, record in saved.items():
        assert record["illustration"] == picked(name)


def test_an_owner_rename_before_the_fill_keeps_the_owner_name_illustration(tmp_path):
    answer = {"kind": "menu_board", "items": [{"name": "Блюдо дня", "price": 6000}]}
    store, fake, result = run_board(tmp_path, answer)
    store.patch_menu(result.jobs[0].menu_id, {"name": "가나다 에이드"}, by_owner=True)
    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake, cutout=fake_cutout)()
    assert menus(store)["가나다 에이드"]["illustration"] == picked("가나다 에이드")


def test_menus_added_or_renamed_in_chat_get_an_illustration(tmp_path):
    from agenthan.tools.feature_tools import execute_feature_tool

    store, _, _ = run_board(tmp_path)
    execute_feature_tool(store, "upsert_menu", {"name": "가나다 에이드", "price": 5000})
    assert menus(store)["가나다 에이드"]["illustration"] == picked("가나다 에이드")
    execute_feature_tool(
        store, "upsert_menu", {"name": "가나다 에이드", "new_name": "가나다 수프"}
    )
    assert menus(store)["가나다 수프"]["illustration"] == picked("가나다 수프")
    store.upsert_menu("가나다 라떼", 6000)  # 사진 초안 승인으로 들어온 메뉴
    assert menus(store)["가나다 라떼"]["illustration"] == picked("가나다 라떼")


def test_store_paths_never_reach_the_embedding_step(tmp_path, monkeypatch):
    """저장소 잠금 안에서 그림을 고르는 길(메뉴판 등록·채우기의 이름 변경·대화 도구·메뉴 탭·사진
    승인)은 임베딩 단계에 닿지 않는다. 임베딩은 잠금 밖 app.server.fill_illustrations만 한다."""
    from agenthan.illustration import matcher
    from agenthan.tools.feature_tools import execute_feature_tool

    def no_embedding(*_args):
        pytest.fail("저장소 경로가 임베딩을 불렀어요")

    monkeypatch.setattr(matcher, "_nearest", no_embedding)
    store, fake, result = run_board(tmp_path)
    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake, cutout=fake_cutout)()
    execute_feature_tool(store, "upsert_menu", {"name": "쀍뷁", "price": 5000})
    execute_feature_tool(store, "upsert_menu", {"name": "쀍뷁", "new_name": "쀍뷁 둘"})
    menu_id = menus(store)["쀍뷁 둘"]["id"]
    store.patch_menu(menu_id, {"name": "쀍뷁 셋"}, by_owner=True)
    store.upsert_menu("쀍뷁 넷", 6000)
    assert menus(store)["쀍뷁 셋"]["illustration"] == "default_other"


def test_a_chat_menu_change_does_not_lose_a_concurrent_menu_tab_save(tmp_path):
    """대화의 메뉴 도구(_menu)는 읽고-고치고-쓰는 동안 저장소 잠금을 잡는다: 그 사이 들어온
    메뉴 탭 저장(백그라운드 채우기와 같은 잠금)은 기다렸다가 쓰이고, 덮여서 사라지지 않는다."""
    import threading

    from agenthan.tools.feature_tools import execute_feature_tool

    store, _, _ = run_board(tmp_path)
    kimbap = menus(store)["김밥"]["id"]
    real_read = store.read
    other = []

    def read():
        data = real_read()
        if not other:  # 대화 도구가 처음 읽은 직후, 메뉴 탭에서 김밥 가격을 고친다
            edit = {"price": 4700}
            job = threading.Thread(target=lambda: store.patch_menu(kimbap, edit, by_owner=True))
            other.append(job)
            other[0].start()
            other[0].join(timeout=0.3)  # 잠금이 있으면 여기서 끝나지 못한다
        return data

    store.read = read
    execute_feature_tool(store, "upsert_menu", {"name": "가나다 에이드", "price": 5000})
    other[0].join(timeout=5)
    saved = menus(store)
    assert saved["김밥"]["price"] == 4700
    assert saved["가나다 에이드"]["price"] == 5000


def test_a_price_change_keeps_the_illustration(tmp_path):
    from agenthan.tools.feature_tools import execute_feature_tool

    store, _, _ = run_board(tmp_path)
    data = store.read()
    record = next(m for m in data["menus"] if m["name"] == "김밥")
    record["illustration"] = "default_tea"  # 무엇이든 이름이 그대로면 다시 고르지 않는다
    store.write(data)
    execute_feature_tool(store, "upsert_menu", {"name": "김밥", "price": 4500})
    store.patch_menu(record["id"], {"name": "김밥", "price": 4800}, by_owner=True)
    assert menus(store)["김밥"]["illustration"] == "default_tea"


# ---------------------------------------------------------------- 저장소


def test_glossary_dishes_skip_the_model_and_get_a_checked_description(tmp_path):
    answer = {
        "kind": "menu_board",
        "items": [{"name": "Сырники", "price": 8500}, {"name": "콤포트", "price": 5000}],
    }
    store, fake, result = run_board(tmp_path, answer)
    saved = menus(store)
    assert saved["시르니키"]["name_native"] == "Сырники"
    assert saved["시르니키"]["description"].startswith("코티지치즈")
    assert sorted(saved["시르니키"]["ai"]) == ["description", "name"]
    assert saved["콤포트"]["ai"] == ["description"] and "음료" in saved["콤포트"]["description"]
    assert result.jobs == [] and len(fake.calls) == 1  # 읽기 한 번뿐, 설명 호출 없음


@pytest.mark.parametrize(
    ("read", "ko"),
    [("Медовник", "메도빅"), ("보르쉬", "보르시"), ("Pho", "쌀국수"), ("Пельмени", "펠메니")],
)
def test_glossary_lookup(read, ko):
    from agenthan.agent import dish_glossary

    assert dish_glossary.lookup(read).ko == ko


@pytest.mark.parametrize("read", ["Наличные", "김밥", "메도", ""])
def test_glossary_does_not_guess(read):
    from agenthan.agent import dish_glossary

    assert dish_glossary.lookup(read) is None


def test_owner_patch_validates_and_clears_flags(tmp_path):
    store, _, _ = run_board(tmp_path)
    medovik = menus(store)["메도빅"]
    with pytest.raises(DummyStoreError):
        store.patch_menu(medovik["id"], {"price": 0}, by_owner=True)
    with pytest.raises(DummyStoreError):
        store.patch_menu(medovik["id"], {"name": "김밥"}, by_owner=True)
    record = store.patch_menu(medovik["id"], {"price": 9000}, by_owner=True)
    assert record["price"] == 9000 and record["ai"] == []
    assert store.patch_menu("nope", {"price": 1000}, by_owner=True) is None


def test_upsert_from_an_approved_chat_change_clears_the_ai_mark(tmp_path):
    store, _, _ = run_board(tmp_path)
    store.upsert_menu("메도빅", 8500)
    assert menus(store)["메도빅"]["ai"] == []


def test_a_chat_menu_change_clears_the_ai_mark_of_that_field_only(tmp_path):
    from agenthan.tools.feature_tools import execute_feature_tool

    store, _, _ = run_board(tmp_path)
    data = store.read()
    next(m for m in data["menus"] if m["name"] == "메도빅")["ai"] = ["price", "description"]
    store.write(data)
    execute_feature_tool(store, "upsert_menu", {"name": "메도빅", "price": 9000})
    assert menus(store)["메도빅"]["ai"] == ["description"]


def test_delete_menu(tmp_path):
    store, _, _ = run_board(tmp_path)
    assert store.delete_menu(menus(store)["김밥"]["id"])
    assert "김밥" not in menus(store) and not store.delete_menu("nope")


# ---------------------------------------------------------------- 비서·서버


def test_assistant_answers_at_once_and_leaves_the_fill_job(tmp_path, monkeypatch):
    monkeypatch.setattr(menu_board, "cutout_on_white", fake_cutout)
    store = DummyStore(tmp_path / "store.json")
    store.write({"shop": {"name": "베이커리"}, "menus": [], "reviews": []})
    bot = OwnerAssistant(tmp_path / "store.json", knowledge=KNOWLEDGE, clova_v3=FakeV3(ITEMS))
    reply = bot.ask_photo(BOARD)
    assert "메뉴 3개를 메뉴 탭에 올렸어요" in reply and "채우는 중" in reply
    assert "가격이 안 보이는 메뉴는 올리지 않았어요: Пельмени" in reply
    assert bot.ledger.find_open(kind="photo") is None  # 승인 카드 없음
    job = bot.take_photo_job()
    assert job is not None and bot.take_photo_job() is None


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(menu_board, "cutout_on_white", fake_cutout)

    def make(path):
        return OwnerAssistant(path, knowledge=FakeStoreKnowledge([]), clova_v3=FakeV3(ITEMS))

    return TestClient(create_app(make_assistant=make, root=tmp_path))


def wait_filled(api, sid):
    for _ in range(100):
        place = api.get(f"/assistant/sessions/{sid}/place").json()["place"]
        if not any(m["filling"] for m in place["menu"]):
            return place
        time.sleep(0.05)
    raise AssertionError("still filling")


def test_empty_menu_start_then_board_photo_fills_the_menu_tab(api):
    sid = api.post("/assistant/sessions?menu=empty").json()["session_id"]
    assert api.get(f"/assistant/sessions/{sid}/place").json()["place"]["menu"] == []
    body = api.post(
        f"/assistant/sessions/{sid}/photos", files={"image": ("m.jpg", BOARD, "image/jpeg")}
    ).json()
    assert body["pending"] == [] and len(body["place"]["menu"]) == 3
    # 올린 답에 이미 대표 그림 주소가 있다(사진 없는 메뉴는 새로고침 없이 그림이 뜬다)
    assert all(m["illustration_url"] for m in body["place"]["menu"])
    place = wait_filled(api, sid)
    rows = {m["name"]: m for m in place["menu"]}
    assert rows["바트루시카"]["photo_url"].endswith("?v=1")
    # 사진이 있어도 그림 주소는 온다(무엇을 보일지는 화면이 정한다)
    assert rows["바트루시카"]["illustration_url"]
    assert rows["김밥"]["photo_url"] is None and rows["김밥"]["illustration_url"]
    assert api.get(rows["바트루시카"]["photo_url"]).content == b"\xff\xd8\xffwhite"


def test_menu_tab_edits_and_deletes_without_a_chat_turn(api):
    sid = api.post("/assistant/sessions?menu=empty").json()["session_id"]
    api.post(f"/assistant/sessions/{sid}/photos", files={"image": ("m.jpg", BOARD, "image/jpeg")})
    place = wait_filled(api, sid)
    menu_id = next(m["id"] for m in place["menu"] if m["name"] == "김밥")
    url = f"/assistant/sessions/{sid}/menus/{menu_id}"

    body = api.patch(url, json={"price": 4500, "description": "참기름 김밥"}).json()
    row = next(m for m in body["place"]["menu"] if m["id"] == menu_id)
    assert (row["price"], row["description"], row["ai"]) == (4500, "참기름 김밥", [])
    assert api.patch(url, json={"price": 0}).status_code == 422
    assert api.patch(url, json={}).status_code == 422
    assert api.patch(f"/assistant/sessions/{sid}/menus/nope", json={"price": 1}).status_code == 404

    body = api.delete(url).json()
    assert all(m["id"] != menu_id for m in body["place"]["menu"])
    assert api.delete(url).status_code == 404


# ---------------------------------------------------------------- 2차: 사진 다듬기 (SEED Omni)


def filled_store(tmp_path):
    store, fake, result = run_board(tmp_path)
    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake, cutout=fake_cutout)()
    return store


def test_polish_target_needs_polish_words_and_a_menu_name():
    menus = [{"id": "1", "name": "메도빅"}, {"id": "2", "name": "메도빅 홀케이크"}]
    assert menu_board.polish_target("메도빅 사진 더 예쁘게 해 줘", menus)["id"] == "1"
    assert menu_board.polish_target("메도빅 홀케이크 사진 AI로 다듬어 줘", menus)["id"] == "2"
    assert menu_board.polish_target("메도빅 가격 9000원", menus) is None
    assert menu_board.polish_target("사진 예쁘게 해 줘", menus) is None


def test_without_a_gpu_server_polish_is_the_cpu_studio_touch_up(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTHAN_IMAGE_POLISH_URL", raising=False)
    store, fake, result = run_board(tmp_path)
    # 진짜 GrabCut으로 채워 원본 조각(photo_source)까지 남긴다
    menu_board.fill_all(store, result.jobs, BOARD, "", call=fake)()
    medovik = menus(store)["메도빅"]
    assert medovik["photo_source"].endswith("-source.jpg")
    message, job = menu_board.start_polish(store, medovik["id"])
    assert "보정하고 있어요" in message and menus(store)["메도빅"]["filling"] == ["polish"]
    job()
    after = menus(store)["메도빅"]
    assert after["filling"] == [] and after["photo_version"] == 2
    photo = (tmp_path / after["photo"]).read_bytes()
    image = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (1024, 1024, 3) and (image[5, 5] >= 250).all()  # 흰 배경 정사각형


def test_polish_replaces_the_photo_in_the_background(tmp_path):
    store = filled_store(tmp_path)
    medovik = menus(store)["메도빅"]
    seen = []

    def omni(photo, name):
        seen.append((photo, name))
        return b"\x89PNG\r\n\x1a\nomni", "image/png"

    message, job = menu_board.start_polish(store, medovik["id"], polish=omni)
    assert "1~2분" in message and menus(store)["메도빅"]["filling"] == ["polish"]
    assert menu_board.start_polish(store, medovik["id"], polish=omni)[1] is None  # 이미 다듬는 중
    job()
    after = menus(store)["메도빅"]
    # 흰 배경 사진이 아니라 메뉴판에서 자른 원본 조각으로 다시 만든다
    source = (tmp_path / medovik["photo_source"]).read_bytes()
    assert seen == [(source, "메도빅")] and source != b"\xff\xd8\xffwhite"
    assert (
        after["filling"] == [] and after["photo_version"] == 2 and after["photo"].endswith(".png")
    )


def test_a_failed_polish_keeps_the_first_photo(tmp_path):
    from agenthan.tools.image_polish import ImagePolishError

    store = filled_store(tmp_path)
    medovik = menus(store)["메도빅"]

    def broken(photo, name):
        raise ImagePolishError("down")

    _, job = menu_board.start_polish(store, medovik["id"], polish=broken)
    job()
    after = menus(store)["메도빅"]
    assert after["filling"] == [] and after["photo_version"] == 1


def test_polish_without_a_photo_asks_for_one(tmp_path):
    store = filled_store(tmp_path)
    message, job = menu_board.start_polish(store, menus(store)["김밥"]["id"], polish=lambda p, n: p)
    assert job is None and "사진이 없어요" in message


def test_polish_button_and_chat_both_start_the_touch_up(api, monkeypatch):
    monkeypatch.delenv("AGENTHAN_IMAGE_POLISH_URL", raising=False)
    sid = api.post("/assistant/sessions?menu=empty").json()["session_id"]
    api.post(f"/assistant/sessions/{sid}/photos", files={"image": ("m.jpg", BOARD, "image/jpeg")})
    place = wait_filled(api, sid)
    row = next(m for m in place["menu"] if m["photo_url"])
    body = api.post(f"/assistant/sessions/{sid}/menus/{row['id']}/polish").json()
    assert body["started"] is True and "보정하고 있어요" in body["message"]
    place = wait_filled(api, sid)
    assert next(m for m in place["menu"] if m["id"] == row["id"])["photo_url"].endswith("?v=2")
    assert api.post(f"/assistant/sessions/{sid}/menus/nope/polish").status_code == 404
    reply = api.post(
        f"/assistant/sessions/{sid}/messages", json={"text": f"{row['name']} 사진 더 예쁘게 해 줘"}
    ).json()["reply"]
    assert "보정하고 있어요" in reply
    wait_filled(api, sid)


# ---------------------------------------------------------------- 이름 옆 괄호 풀이


@pytest.mark.parametrize(
    ("raw", "name", "sub"),
    [
        ("메도빅 (꿀 케이크)", "메도빅", "꿀 케이크"),
        ("콤포트（과일 음료）", "콤포트", "과일 음료"),
        ("Борщ [суп]", "Борщ", "суп"),
        ("메도빅", "메도빅", ""),
        ("(세트)", "(세트)", ""),
    ],
)
def test_split_subtitle(raw, name, sub):
    assert menu_board.split_subtitle(raw) == (name, sub)


def test_names_with_a_subtitle_get_their_photo_and_glossary_entry(tmp_path):
    """'메도빅 (꿀 케이크)'처럼 괄호 풀이가 붙은 메뉴판(2026-11-21 실서버): 사진 이름표 '메도빅'과
    맞아 사진이 붙고, 사전으로 설명을 채우며, 한글 표기(블리니)는 그대로 둔다."""
    answer = {
        "kind": "menu_board",
        "items": [
            {"name": "메도빅 (꿀 케이크)", "price": 7500},
            {"name": "블리니 (러시아식 팬케이크)", "price": 9000},
            {"name": "머랭롤 (머랭 롤 케이크)", "price": 8500},
        ],
        "photos": [
            {"box": [0.66, 0.19, 0.9, 0.35], "label": "메도빅"},
            {"box": [0.66, 0.75, 0.9, 0.9], "label": "머랭롤"},
        ],
    }
    store, fake, result = run_board(tmp_path, answer)
    [first, second, third] = menu_board.read_board(BOARD, call=FakeV3(answer))[1]
    assert first.box and not second.box and third.box
    assert third.subtitle == "머랭 롤 케이크"
    saved = menus(store)
    assert set(saved) == {"메도빅", "블리니", "머랭롤"}
    assert saved["블리니"]["description"].startswith("얇게 부친")  # 사전 설명
    assert saved["블리니"]["ai"] == ["description"]  # 한글 이름은 사장님 표기 그대로
    assert saved["머랭롤"]["filling"] == ["description", "photo"]  # 사전에 없음 → 모델이 설명


def column_board():
    """크림색 메뉴판 오른쪽 열에 음식 사진 두 장이 위아래로, 그 아래 이름표(위 빨강, 아래 파랑)."""
    image = np.full((800, 1000, 3), (205, 230, 245), np.uint8)  # BGR 크림색 종이
    rng = np.random.default_rng(2)
    for top, color in zip((120, 470), (RED, BLUE), strict=True):
        image[top : top + 220, 650:900] = rng.integers(0, 255, (220, 250, 3), dtype=np.uint8)
        image[top + 240 : top + 260, 700:850] = color
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def test_photo_cells_are_measured_and_a_missed_photo_found_by_its_label(tmp_path):
    """첫 읽기는 사진 위치가 어긋나고 4장 중 1장을 자주 빠뜨렸다(2026-11-21 실측): 사진 열 안의
    칸을 영상으로 재고, 이름이 없는 칸은 아래 이름표를 읽어 메뉴에 붙인다."""
    board = column_board()
    answer = {
        "kind": "menu_board",
        "items": [{"name": "김밥", "price": 4000}, {"name": "떡볶이", "price": 5000}],
        # 김밥 사진 위치는 어긋나게(실제 0.15~0.425), 떡볶이 사진은 빠뜨림
        "photos": [{"box": [0.68, 0.2, 0.92, 0.45], "label": "김밥"}],
    }

    fake = FakeV3(answer, labels={"김밥": RED, "떡볶이": BLUE})
    runs = menu_board.photo_runs(board, (0.56, 1.0))
    assert len(runs) == 2
    assert abs(runs[0][1] - 0.15) < 0.03 and abs(runs[1][1] - 470 / 800) < 0.03
    assert abs(runs[0][0] - 0.65) < 0.03 and abs(runs[0][2] - 0.9) < 0.03

    store = DummyStore(tmp_path / "store.json")
    store.write({"shop": {"name": "분식"}, "menus": [], "reviews": []})
    _, items = menu_board.read_board(board, call=fake)
    result = menu_board.register(store, items, FakeStoreKnowledge([]))
    assert [r.item.name for r in result.no_photo] == ["떡볶이"]
    seen = []

    def cutout(data, **kwargs):
        seen.append(data)
        return fake_cutout(data)

    menu_board.fill_all(
        store, result.jobs, board, "", call=fake, cutout=cutout, no_photo=result.no_photo
    )()
    saved = menus(store)
    assert saved["김밥"]["photo"] and saved["떡볶이"]["photo"]
    assert all(m["filling"] == [] for m in saved.values())
    # 자른 조각이 사진 칸 그대로(크림색 종이가 거의 없다)
    for piece in seen:
        crop = cv2.imdecode(np.frombuffer(piece, np.uint8), cv2.IMREAD_COLOR)
        paper = np.all(np.abs(crop.astype(int) - (205, 230, 245)) < 12, axis=2).mean()
        assert paper < 0.1


def recovered_board(tmp_path, label="보르시"):
    """김밥(사진 있음) + 보르시(사전 메뉴라 처음엔 채울 칸이 없고, 첫 읽기가 사진을 빠뜨림)."""
    board = column_board()
    answer = {
        "kind": "menu_board",
        "items": [{"name": "김밥", "price": 4000}, {"name": "보르시", "price": 9000}],
        "photos": [{"box": [0.68, 0.2, 0.92, 0.45], "label": "김밥"}],
    }

    fake = FakeV3(answer, labels={"김밥": RED, label: BLUE})
    store = DummyStore(tmp_path / "store.json")
    store.write({"shop": {"name": "러시아 식당"}, "menus": [], "reviews": []})
    _, items = menu_board.read_board(board, call=fake)
    result = menu_board.register(store, items, FakeStoreKnowledge([]))
    return store, fake, result, board


def test_a_recovered_photo_that_crashes_does_not_spin_forever(tmp_path, caplog):
    """보르시처럼 첫 읽기가 사진을 빠뜨렸다가 이름표로 찾은 메뉴는 jobs에 없다(2026-11-21 서버 로그
    'fill … for 3 menus', 사진은 4장). 그 사진이 예상 못 한 오류로 죽어도 '채우는 중'이
    남지 않는다."""
    store, fake, result, board = recovered_board(tmp_path)
    assert [j.item.name for j in result.jobs] == ["김밥"]
    assert [r.item.name for r in result.no_photo] == ["보르시"]

    def crash(data, **kwargs):
        raise RuntimeError("onnxruntime")

    caplog.set_level("INFO", logger="agenthan.agent.menu_board")
    menu_board.fill_all(
        store, result.jobs, board, "", call=fake, cutout=crash, no_photo=result.no_photo
    )()
    assert all(m["filling"] == [] for m in menus(store).values())
    assert "menu board fill" in caplog.text and "for 2 menus" in caplog.text


def test_each_menu_shows_up_as_soon_as_its_own_photo_is_done(tmp_path):
    """다 된 칸부터 바로 보인다: 한 메뉴 사진이 늦어도 다른 메뉴 사진은 먼저 저장소에 쓴다."""
    import threading

    store, fake, result, board = recovered_board(tmp_path)
    lock, calls, early = threading.Lock(), [], []

    def cutout(data, **kwargs):
        with lock:
            first = not calls
            calls.append(data)
        if first:  # 첫 사진은 다른 메뉴 사진이 저장소에 보일 때까지 붙잡아 둔다
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not early:
                done = [n for n, m in menus(store).items() if m.get("photo")]
                early.extend(done)
                time.sleep(0.01)
        return fake_cutout(data)

    menu_board.fill_all(
        store, result.jobs, board, "", call=fake, cutout=cutout, no_photo=result.no_photo
    )()
    assert len(early) == 1  # 늦은 사진을 기다리는 동안 다른 한 메뉴는 이미 사진이 있었다
    saved = menus(store)
    assert saved["김밥"]["photo"] and saved["보르시"]["photo"]


def test_refine_box_maps_the_answer_back_and_keeps_the_box_on_failure():
    box = (0.5, 0.2, 0.7, 0.4)
    refined = menu_board.refine_box(BOARD, box, "김밥", call=FakeV3(ITEMS))
    assert refined != box and 0.38 <= refined[0] < refined[2] <= 0.82

    def nonsense(model, body):
        return {"message": {"content": "잘 모르겠어요"}}

    assert menu_board.refine_box(BOARD, box, "김밥", call=nonsense) == box


FOLK_NAMES = ["메도빅", "펠메니", "보르시", "머랭롤"]


def folk_board():
    """러시아 빵집 메뉴판(2026-11-21 실서버 모양): 오른쪽 열에 액자 칸 네 개(사진 + 그 아래
    이름표), 칸 사이·아래 민속 장식 띠, 판 아래 나무 식탁. 마지막 머랭롤 사진은 흰 크림·흰
    접시라 종이색과 거의 같다. → (메뉴판, 사진 칸들, 이름표 세로 범위들)"""
    width, height = 900, 1500
    image = np.full((height, width, 3), (40, 80, 130), np.uint8)  # 나무 식탁
    image[: height - 90] = (205, 230, 245)  # 크림색 종이
    rng = np.random.default_rng(4)
    photos, labels, top = [], [], 60
    for k in range(4):
        y0, y1 = top + 10, top + 160
        if k < 3:
            photo = rng.integers(0, 255, (150, 280, 3), dtype=np.uint8)
        else:
            photo = np.full((150, 280, 3), (225, 232, 238), np.uint8)
            cv2.ellipse(photo, (140, 80), (125, 60), 0, 0, 360, (250, 250, 250), -1)
            cv2.ellipse(photo, (140, 75), (90, 38), 0, 0, 360, (190, 215, 240), -1)
            for x in range(80, 210, 22):
                cv2.circle(photo, (x, 40), 6, (60, 40, 200), -1)
        image[y0:y1, 560:840] = photo
        cv2.putText(image, "NAME", (640, y1 + 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (40, 30, 150), 3)
        cv2.rectangle(image, (550, top), (850, y1 + 55), (40, 30, 150), 3)
        for x in range(520, 880, 45):  # 장식 띠: 열매와 잎
            cv2.circle(image, (x, y1 + 85), 8, (40, 40, 200), -1)
            cv2.ellipse(image, (x + 20, y1 + 85), (18, 7), 30, 0, 360, (40, 180, 230), -1)
        photos.append((560 / width, y0 / height, 840 / width, y1 / height))
        labels.append((y1 / height, (y1 + 55) / height))
        top = y1 + 115
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes(), photos, labels


def run_folk_board(tmp_path, monkeypatch, refined, unreadable=()):
    """첫 읽기는 사진 4장을 다 주지만 머랭롤 위치는 이름표 쪽으로 어긋난다. 이름표는 그 자리에
    실제로 있는 칸의 이름으로 읽힌다(이름표 자리가 아니거나 unreadable이면 빈 글자). refined:
    머랭롤 사진 위치를 다시 물었을 때 모델의 답. → (저장된 메뉴, 메뉴별 자른 위치, 실제 사진 칸)"""
    board, photos, labels = folk_board()
    answer = {
        "kind": "menu_board",
        "items": [{"name": n, "price": 8000 + 500 * i} for i, n in enumerate(FOLK_NAMES)],
        "photos": [
            {"box": [0.6, p[1] + 0.03, 0.95, p[3] + 0.03], "label": n}
            for p, n in zip(photos, FOLK_NAMES, strict=True)
        ],
    }
    answer["photos"][3]["box"] = [0.6, 0.7, 0.95, 0.8]

    def read_label(board, box, *, call):
        top = box[3]
        hits = [n for n, (a, b) in zip(FOLK_NAMES, labels, strict=True) if a - 0.01 <= top < b]
        return hits[0] if hits and hits[0] not in unreadable else ""

    monkeypatch.setattr(menu_board, "read_label", read_label)
    monkeypatch.setattr(menu_board, "refine_box", lambda board, box, label, call: refined)
    cropped = {}
    real_crop = menu_board.crop

    def crop(data, box, margin=0.02):
        piece = real_crop(data, box, margin)
        cropped[piece] = box
        return piece

    monkeypatch.setattr(menu_board, "crop", crop)
    store = DummyStore(tmp_path / "store.json")
    store.write({"shop": {"name": "러시아 빵집"}, "menus": [], "reviews": []})
    fake = FakeV3(answer)
    _, items = menu_board.read_board(board, call=fake)
    result = menu_board.register(store, items, FakeStoreKnowledge([]))
    menu_board.fill_all(
        store, result.jobs, board, "", call=fake, cutout=fake_cutout, no_photo=result.no_photo
    )()
    saved = menus(store)
    boxes = {}
    for name in FOLK_NAMES:
        source = saved[name].get("photo_source")
        if source:
            boxes[name] = cropped[(store.path.parent / source).read_bytes()]
    return saved, boxes, photos


def test_a_photo_the_cells_missed_is_not_cut_from_its_label_and_border(tmp_path, monkeypatch):
    """2026-11-21 실서버: 머랭롤 사진(흰 크림)을 사진 칸 재기가 놓쳤고, 첫 읽기 위치를 다시 물은
    답이 이름표·장식 띠·판 아래를 잡아 그 조각이 흰 배경 사진이 됐다. 사진 칸 간격으로 빠진
    칸을 찾아 이름표로 확인하고, 확인 못 한 모델 위치는 쓰지 않는다."""
    bad = (0.5, 0.68, 0.98, 0.78)  # 사진 아래 끝 + 이름표 + 장식 띠 (실서버 조각 모양)
    saved, boxes, photos = run_folk_board(tmp_path, monkeypatch, refined=bad)
    assert set(boxes) == set(FOLK_NAMES)
    for name, truth in zip(FOLK_NAMES, photos, strict=True):
        box = boxes[name]
        assert menu_board._iou(box, truth) > 0.6, (name, box, truth)
    assert all(m["filling"] == [] for m in saved.values())


def test_a_photo_cell_nobody_can_confirm_is_left_to_the_illustration(tmp_path, monkeypatch):
    """머랭롤 이름표를 못 읽으면(반사·흐림) 간격으로 짐작한 칸도, 다시 물은 위치도 확인할 수
    없다: 이름표·장식 띠 조각을 붙이지 않고 사진 없이 둔다(메뉴는 일러스트로 보인다)."""
    bad = (0.5, 0.68, 0.98, 0.78)
    saved, boxes, photos = run_folk_board(tmp_path, monkeypatch, refined=bad, unreadable={"머랭롤"})
    assert set(boxes) == {"메도빅", "펠메니", "보르시"}
    assert not saved["머랭롤"].get("photo") and saved["머랭롤"]["illustration"]
    assert all(m["filling"] == [] for m in saved.values())


def test_a_re_asked_photo_box_over_another_menus_label_is_not_used(tmp_path):
    """사진 칸을 잴 수 없는 판(사진이 가로로 퍼짐)에서는 위치를 다시 묻는다. 그 답 아래
    이름표가 다른 메뉴 이름이면 그 조각은 쓰지 않는다."""
    store, _, result = run_board(tmp_path)
    swapped = FakeV3(ITEMS, labels={"Медовик": BLUE, "Ватрушка": RED})
    menu_board.fill_all(store, result.jobs, BOARD, "베이커리", call=swapped, cutout=fake_cutout)()
    saved = menus(store)
    assert not saved["메도빅"].get("photo_source") and not saved["바트루시카"].get("photo")
    assert all(m["filling"] == [] for m in saved.values())

    store, fake, result = run_board(tmp_path / "ok")
    menu_board.fill_all(store, result.jobs, BOARD, "베이커리", call=fake, cutout=fake_cutout)()
    saved = menus(store)
    assert saved["메도빅"]["photo_source"] and saved["바트루시카"]["photo"]


# ---------------------------------------------------------------- 채우기 진행(menu_fill)


def test_the_fill_run_keeps_its_start_and_menus_until_everything_is_filled(tmp_path, monkeypatch):
    """진행 줄의 걸린 초와 'N/M개 완료'는 서버가 정한다: 메뉴 탭을 다시 열어도(다른 탭에 갔다 와도)
    처음부터 세지 않는다. 늦게 사진을 찾은 메뉴도 같은 일에 더하고, 다 채우면 지운다."""
    from agenthan.app import place
    from agenthan.tools import dummy_store

    clock = iter(range(1000, 1100))
    monkeypatch.setattr(dummy_store, "_now", lambda: float(next(clock)))
    store, fake, result, board = recovered_board(tmp_path)
    kimbap, borscht = result.jobs[0].menu_id, result.no_photo[0].menu_id
    run = store.read()["menu_fill"]
    assert run["started_at"] == 1000.0 and run["menu_ids"] == [kimbap]
    assert run["fields"] == [f"{kimbap}/description", f"{kimbap}/photo"]

    store.patch_menu(kimbap, {"description": "김밥 설명"}, by_owner=False)
    store.patch_menu(borscht, {}, by_owner=False, start=("photo",))  # 이름표로 사진을 찾음
    run = store.read()["menu_fill"]
    assert run["started_at"] == 1000.0  # 새로 시작하지 않는다
    assert run["menu_ids"] == [kimbap, borscht] and len(run["fields"]) == 3

    view = place.snapshot(store.read(), now=1024.6)["menu_fill"]
    assert view == {
        "started_at": 1000.0,
        "seconds": 24.6,
        "menu_ids": [kimbap, borscht],
        "fields": 3,
    }

    store.patch_menu(kimbap, {}, by_owner=False, done=("photo",))
    store.patch_menu(borscht, {}, by_owner=False, done=("photo",))
    assert "menu_fill" not in store.read()
    assert place.snapshot(store.read())["menu_fill"] is None


def test_a_polish_alone_is_not_a_fill_run(tmp_path):
    store, fake, result, board = recovered_board(tmp_path)
    menu_board.fill_all(
        store, result.jobs, board, "", call=fake, cutout=fake_cutout, no_photo=result.no_photo
    )()
    kimbap = result.jobs[0].menu_id
    store.patch_menu(kimbap, {}, by_owner=False, start=("polish",))
    assert "menu_fill" not in store.read()


def test_the_fill_run_is_not_a_change_to_approve():
    from agenthan.app import place

    before = {"menu": [], "menu_fill": None}
    fill = {"started_at": 1.0, "seconds": 2.0, "menu_ids": [], "fields": 0}
    after = {"menu": [], "menu_fill": fill}
    assert place.changes(before, after) == {}


# ---------------------------------------------------------------- 큰 가로 메뉴판 (2026-11-22)
# 메뉴 54개인 네 칸 메뉴판을 한 번에 읽자 HCX-005가 24개에서 멈췄다
# (오른쪽 베이커리 칸 통째로 빠짐).


def wide_board() -> bytes:
    return cv2.imencode(".jpg", np.full((1000, 1500, 3), 255, np.uint8))[1].tobytes()


def test_a_wide_board_is_also_read_in_strips_and_merged_in_column_order():
    whole = {"kind": "menu_board", "items": [
        {"name": "아메리카노 (아아)", "price": 4500}, {"name": "뱅쇼", "price": 6500}],
        "photos": []}
    strip = [{"name": "아메리카노", "price": 4500},
             {"name": "★대표 메도빅 (꿀 케이크) ……", "price": 7500},
             {"name": "* 인기 피로그 (러시아식 파이) …", "price": 4500},
             {"name": "뱅쇼 …", "price": 6500},
             {"name": "(고기 / 감자 / 양배추 중 선택)", "price": None}]
    fake = FakeV3(whole, strip=strip)
    kind, items = menu_board.read_board(wide_board(), call=fake)
    assert kind == "menu_board"
    assert [(i.name, i.price, i.subtitle) for i in items] == [
        ("아메리카노", 4500, "아아"),  # 조각과 전체에 다 있으면 하나로, 풀이는 채운다
        ("메도빅", 7500, "꿀 케이크"),  # 앞 표시·점선을 뗀다
        ("피로그", 4500, "러시아식 파이"),
        ("뱅쇼", 6500, ""),  # 조각이 놓친 줄은 전체 읽기에서
    ]
    texts = [c["messages"][0]["content"][1]["text"] for c in fake.calls]
    assert texts.count(menu_board.STRIP_PROMPT) == len(menu_board.STRIPS)


def test_a_small_or_tall_photo_is_read_once():
    tall = cv2.imencode(".jpg", np.full((1600, 1200, 3), 255, np.uint8))[1].tobytes()
    for image in (BOARD, tall):
        fake = FakeV3(ITEMS)
        menu_board.read_board(image, call=fake)
        assert len(fake.calls) == 1


def test_a_failed_strip_read_keeps_the_whole_read():
    class Broken(FakeV3):
        def __call__(self, model, body):
            if body["messages"][-1]["content"][1]["text"] == menu_board.STRIP_PROMPT:
                raise menu_board.ClovaV3Error("down")
            return super().__call__(model, body)

    whole = {"kind": "menu_board", "items": [{"name": "메도빅", "price": 7500}], "photos": []}
    items = menu_board.read_board(wide_board(), call=Broken(whole))[1]
    assert [i.name for i in items] == ["메도빅"]


def test_a_food_photo_ignores_the_strips():
    fake = FakeV3({"kind": "food"}, strip=[{"name": "메도빅", "price": 7500}])
    assert menu_board.read_board(wide_board(), call=fake) == ("food", [])
