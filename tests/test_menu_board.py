"""메뉴판 사진 → 메뉴 탭에 바로 등록, 설명·흰 배경 사진은 뒤에서 채운다 (agent/menu_board.py)."""

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
from agenthan.tools.dummy_store import DummyStore, DummyStoreError
from agenthan.tools.image_cutout import CutoutResult

KNOWLEDGE = FakeStoreKnowledge(
    [MenuItem(id="m01", name_ko="메도빅", name_native="Медовик", price=7500)]
)
NAMES = {
    "Ватрушка": {"name_ko": "바트루시카", "description_ko": "치즈를 올려 구운 동그란 빵."},
    "Пельмени": {"name_ko": "펠메니", "description_ko": "러시아식 만두."},
    "김밥": {"name_ko": "김밥", "description_ko": "밥과 채소를 김에 만 음식."},
}


def board_image():
    """흰 메뉴판 위에 음식 사진 칸 두 개(질감 있는 네모)."""
    image = np.full((600, 800, 3), 255, np.uint8)
    rng = np.random.default_rng(1)
    for x0, y0 in ((60, 80), (460, 80)):
        image[y0 : y0 + 220, x0 : x0 + 260] = rng.integers(0, 255, (220, 260, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


BOARD = board_image()


class FakeV3:
    """메뉴판 읽기 질문에는 board 답을, 메뉴 하나 질문에는 NAMES를 돌려준다."""

    def __init__(self, board: dict | str):
        self.board = board
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
    import base64

    uri = fake.calls[0]["messages"][0]["content"][0]["dataUri"]["data"]
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
    place = wait_filled(api, sid)
    rows = {m["name"]: m for m in place["menu"]}
    assert rows["바트루시카"]["photo_url"].endswith("?v=1")
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
    """크림색 메뉴판 오른쪽 열에 음식 사진 두 장이 위아래로(그 아래 이름표 자리)."""
    image = np.full((800, 1000, 3), (205, 230, 245), np.uint8)  # BGR 크림색 종이
    rng = np.random.default_rng(2)
    for top in (120, 470):
        image[top : top + 220, 650:900] = rng.integers(0, 255, (220, 250, 3), dtype=np.uint8)
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

    class Labels(FakeV3):
        def __call__(self, model, body):
            content = body["messages"][-1]["content"]
            if isinstance(content, list) and content[1]["text"] == menu_board.LABEL_PROMPT:
                return {"message": {"content": '{"label": "떡볶이"}'}}
            return super().__call__(model, body)

    fake = Labels(answer)
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


def test_refine_box_maps_the_answer_back_and_keeps_the_box_on_failure():
    box = (0.5, 0.2, 0.7, 0.4)
    refined = menu_board.refine_box(BOARD, box, "김밥", call=FakeV3(ITEMS))
    assert refined != box and 0.38 <= refined[0] < refined[2] <= 0.82

    def nonsense(model, body):
        return {"message": {"content": "잘 모르겠어요"}}

    assert menu_board.refine_box(BOARD, box, "김밥", call=nonsense) == box
