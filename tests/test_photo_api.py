"""Owner screen backend: menu photos go in over HTTP and come back in pending and place (#63)."""

import base64

import cv2
import numpy as np
from fastapi.testclient import TestClient

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app.server import create_app
from agenthan.core.contracts import MenuItem
from agenthan.core.fakes import FakeStoreKnowledge
from test_food_photo import CAKE, EMPTY, EditChat, FakeV3
from test_menu_photo import JPEG, KNOWLEDGE, tool_call
from test_menu_photo import FakeChat as BoardChat
from test_menu_photo import FakeV3 as BoardV3


def client(tmp_path, *, fake=None, chat=None, knowledge=EMPTY):
    def make(path):
        return OwnerAssistant(
            path, knowledge=knowledge, clova_v3=fake or FakeV3(), chat=chat or EditChat()
        )

    return TestClient(create_app(make_assistant=make, root=tmp_path))


def start(api):
    return api.post("/assistant/sessions").json()["session_id"]


def start_place(api, session_id):
    return api.get(f"/assistant/sessions/{session_id}/place").json()["place"]


def send_photo(api, session_id, image, text=None):
    data = {"text": text} if text is not None else {}
    return api.post(
        f"/assistant/sessions/{session_id}/photos",
        files={"image": ("photo.jpg", image, "image/jpeg")},
        data=data,
    )


def say(api, session_id, text):
    return api.post(f"/assistant/sessions/{session_id}/messages", json={"text": text}).json()


def test_food_photo_goes_from_upload_to_a_saved_menu_with_its_photo(tmp_path):
    api = client(tmp_path, chat=EditChat({"price": 8500}))
    session_id = start(api)

    body = send_photo(api, session_id, CAKE.read_bytes()).json()
    assert "밀푀유 케이크" in body["reply"]
    [waiting] = body["pending"]
    assert waiting["feature"] == "menu" and waiting["kind"] == "photo"
    [draft] = waiting["drafts"]
    # The AI guess is only a placeholder for the name the owner types in the card.
    assert draft["name_ko"] == "" and draft["name_guess"] == "밀푀유 케이크"
    assert draft["is_food"] is True and draft["price"] is None
    assert "price_missing" in draft["notes"]
    preview = api.get(draft["photo_url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert preview.content[:3] == b"\xff\xd8\xff"

    [draft] = say(api, session_id, "메뉴 이름: 밀푀유 케이크")["pending"][0]["drafts"]
    assert draft["name_ko"] == "밀푀유 케이크" and draft["change"] == "new"
    say(api, session_id, "가격 8500원")
    body = say(api, session_id, "네")
    assert "저장했어요" in body["reply"] and body["pending"] == []
    [saved] = [m for m in body["place"]["menu"] if m["name"] == "밀푀유 케이크"]
    assert saved["price"] == 8500
    photo = api.get(saved["photo_url"])
    assert photo.status_code == 200 and photo.content == preview.content


def test_pending_shows_what_saving_the_priced_draft_adds(tmp_path):
    api = client(tmp_path, chat=EditChat({"price": 8500}))
    session_id = start(api)
    send_photo(api, session_id, CAKE.read_bytes())
    say(api, session_id, "메뉴 이름: 밀푀유 케이크")

    [waiting] = say(api, session_id, "가격 8500원")["pending"]
    [added] = waiting["changes"]["menu"]["added"]
    [new] = [m for m in waiting["after"]["menu"] if m["id"] == added]
    assert new["name"] == "밀푀유 케이크" and new["price"] == 8500


def test_a_photo_that_is_not_a_menu_asks_for_another(tmp_path):
    api = client(tmp_path, fake=lambda model, body: {"message": {"content": "other"}})
    session_id = start(api)

    body = send_photo(api, session_id, CAKE.read_bytes()).json()
    assert "메뉴판이나 음식 사진" in body["reply"] and body["pending"] == []


def test_an_unreadable_file_is_answered_and_an_empty_one_rejected(tmp_path):
    api = client(tmp_path)
    session_id = start(api)

    body = send_photo(api, session_id, b"GIF89a not a menu").json()
    assert "JPG·PNG·WEBP" in body["reply"] and body["pending"] == []
    assert send_photo(api, session_id, b"").status_code == 422


def test_photos_are_only_served_inside_their_own_session(tmp_path):
    api = client(tmp_path)
    mine, other = start(api), start(api)
    draft = send_photo(api, mine, CAKE.read_bytes()).json()["pending"][0]["drafts"][0]

    assert api.get(draft["photo_url"]).status_code == 200
    assert api.get(draft["photo_url"].replace(mine, other)).status_code == 404
    assert api.get(f"/assistant/sessions/{mine}/photos/m01").status_code == 404
    assert api.get(f"/assistant/sessions/{mine}/photos/draft-9").status_code == 404


def test_food_photo_asks_the_name_and_the_price_in_one_question(tmp_path):
    api = client(tmp_path)
    session_id = start(api)

    reply = send_photo(api, session_id, CAKE.read_bytes()).json()["reply"]
    question = reply.splitlines()[-1]
    assert "메뉴 이름을 입력해 주세요" in question and "가격" in question
    assert "AI 추측: 밀푀유 케이크" in question
    assert "이대로 저장할까요" not in reply


def test_a_yes_without_a_price_saves_nothing_and_keeps_asking(tmp_path):
    api = client(tmp_path, chat=EditChat({"price": 8500}))
    session_id = start(api)
    before = send_photo(api, session_id, CAKE.read_bytes()).json()["place"]

    body = say(api, session_id, "네")
    assert "메뉴 이름을 입력해 주세요" in body["reply"]
    assert body["place"] == before and body["pending"][0]["changes"] == {}

    body = say(api, session_id, "메뉴 이름: 밀푀유 케이크")
    assert "가격은 얼마로 올릴까요" in body["reply"]
    body = say(api, session_id, "네")
    assert "가격은 얼마로 올릴까요" in body["reply"]
    assert body["place"] == before
    [waiting] = body["pending"]
    assert waiting["kind"] == "photo" and waiting["changes"] == {}

    body = say(api, session_id, "가격 8500원")
    assert "이대로 저장할까요" in body["reply"]
    assert body["pending"][0]["changes"]["menu"]["added"]

    body = say(api, session_id, "네")
    [saved] = [m for m in body["place"]["menu"] if m["name"] == "밀푀유 케이크"]
    assert saved["price"] == 8500 and saved["photo_url"]


def test_cancel_saves_nothing(tmp_path):
    api = client(tmp_path)
    session_id = start(api)
    before = send_photo(api, session_id, CAKE.read_bytes()).json()["place"]

    body = say(api, session_id, "취소")
    assert body["pending"] == [] and body["place"] == before


BOARD = """[{"name": "Медовик", "price": 8000}, {"name": "Наполеон", "price": 7000},
 {"name": "Сырники", "price": 8500}, {"name": "Чай", "price": 3000},
 {"name": "Пельмени", "price": null}]"""
BOARD_NAMES = {
    "Сырники": '{"name_ko": "시르니키", "description_ko": "러시아식 치즈 팬케이크."}',
    "Чай": '{"name_ko": "차이", "description_ko": "러시아식 홍차."}',
    "Пельмени": '{"name_ko": "펠메니", "description_ko": "러시아식 만두."}',
}


def test_menu_board_updates_existing_menus_and_adds_new_ones_in_one_yes(tmp_path):
    chat = BoardChat(
        tool_call({"number": 5, "price": 10000}), {"role": "assistant", "content": "고쳤어요."}
    )
    api = client(tmp_path, fake=BoardV3(BOARD, BOARD_NAMES), chat=chat, knowledge=KNOWLEDGE)
    session_id = start(api)

    body = send_photo(api, session_id, JPEG).json()
    rows = {d["name_ko"]: d for d in body["pending"][0]["drafts"]}
    assert (rows["메도빅"]["change"], rows["메도빅"]["previous_price"]) == ("price_change", 7500)
    assert (rows["나폴레옹"]["change"], rows["나폴레옹"]["previous_price"]) == ("unchanged", 7000)
    assert rows["시르니키"]["change"] == "new" and rows["시르니키"]["previous_price"] is None
    assert rows["차이"]["change"] == "new"
    assert "가격 변경 7,500 → 8,000" in body["reply"] and "변경 없음" in body["reply"]
    question = body["reply"].splitlines()[-1]
    assert "5번 펠메니" in question and "가격" in question
    assert "메도빅" not in question and "시르니키" not in question

    say(api, session_id, "5번 가격 10000원")
    body = say(api, session_id, "네")
    assert body["pending"] == []
    prices = {m["name"]: m["price"] for m in body["place"]["menu"]}
    assert prices == {
        "메도빅": 8000,
        "나폴레옹": 7000,
        "아메리카노": 4500,
        "블린": 9000,
        "시르니키": 8500,
        "차이": 3000,
        "펠메니": 10000,
    }


def test_food_photo_of_an_existing_menu_attaches_the_photo_without_asking_a_price(tmp_path):
    medovik = MenuItem(id="m01", name_ko="메도빅", price=7500)
    api = client(tmp_path, fake=FakeV3(name="메도빅"), knowledge=FakeStoreKnowledge([medovik]))
    session_id = start(api)
    count = len(start_place(api, session_id)["menu"])

    body = send_photo(api, session_id, CAKE.read_bytes()).json()
    [draft] = body["pending"][0]["drafts"]
    assert draft["change"] == "new" and draft["menu_name"] is None  # Not picked by the AI.

    body = say(api, session_id, "메뉴 이름: 메도빅")
    [draft] = body["pending"][0]["drafts"]
    assert (draft["change"], draft["previous_price"]) == ("photo_attach", 7500)
    assert draft["menu_name"] == "메도빅"  # The screen answers with "메도빅 사진 바꿔".
    question = body["reply"].splitlines()[-1]
    assert question.startswith("기존 메뉴 메도빅에 이 사진을 붙일까요?")
    assert "가격" not in question

    body = say(api, session_id, "네")
    assert body["reply"] == question  # A bare yes asks the same question; nothing is saved.
    assert not [m for m in body["place"]["menu"] if m["id"] == "m01"][0]["photo_url"]

    body = say(api, session_id, "응, 메도빅 사진 바꿔")
    assert body["pending"] == []
    [saved] = [m for m in body["place"]["menu"] if m["id"] == "m01"]
    assert (saved["name"], saved["price"]) == ("메도빅", 7500) and saved["photo_url"]
    assert len(body["place"]["menu"]) == count


def test_a_failed_background_cut_keeps_the_original_photo_and_says_so(tmp_path):
    _, plain = cv2.imencode(".jpg", np.full((200, 200, 3), 128, np.uint8))
    api = client(tmp_path)
    session_id = start(api)

    body = send_photo(api, session_id, plain.tobytes()).json()
    [draft] = body["pending"][0]["drafts"]
    assert "cutout_failed" in draft["notes"] and "배경 지우기 실패" in body["reply"]
    photo = api.get(draft["photo_url"])
    image = cv2.imdecode(np.frombuffer(photo.content, np.uint8), cv2.IMREAD_COLOR)
    # The original on a white square, not a cut-out: its gray middle survives.
    assert image.shape[:2] == (1024, 1024) and abs(int(image[512, 512, 0]) - 128) <= 3


def test_an_oversized_photo_is_answered(tmp_path):
    api = client(tmp_path)
    session_id = start(api)
    huge = b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024)

    body = send_photo(api, session_id, huge).json()
    assert "10MB" in body["reply"] and body["pending"] == []


def test_a_board_line_without_a_price_shows_no_change_until_every_line_is_filled(tmp_path):
    # A saved menu the store knowledge does not know yet: no known price fills the gap.
    board = '[{"name": "Медовик", "price": null}, {"name": "Сырники", "price": 8500}]'
    names = {**BOARD_NAMES, "Медовик": '{"name_ko": "메도빅", "description_ko": ""}'}
    api = client(tmp_path, fake=BoardV3(board, names), knowledge=EMPTY)
    session_id = start(api)

    [waiting] = send_photo(api, session_id, JPEG).json()["pending"]
    rows = {d["name_ko"]: d for d in waiting["drafts"]}
    assert (rows["메도빅"]["change"], rows["메도빅"]["previous_price"]) == (None, 7500)
    assert waiting["changes"] == {}  # Nothing is saved while line 1 has no price.


class SizeCheckingV3(FakeV3):
    """HCX-005 refuses images whose long side is over 2240px (docs/research/01)."""

    def __init__(self):
        super().__init__()
        self.sides = []

    def __call__(self, model, body):
        content = body["messages"][-1]["content"]
        if isinstance(content, list):
            uri = content[0]["dataUri"]["data"]
            raw = base64.b64decode(uri.split(",", 1)[1])
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            self.sides.append(max(image.shape[:2]))
        return super().__call__(model, body)


def phone_photo(width=4032, height=3024, ext=".png"):
    # Noise keeps the PNG large: a phone camera photo is this wide and often over 10MB.
    noise = np.random.default_rng(0).integers(0, 256, (height, width, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(ext, noise)
    assert ok
    return encoded.tobytes()


def test_a_big_phone_photo_is_shrunk_before_the_vision_model_reads_it(tmp_path):
    fake = SizeCheckingV3()
    api = client(tmp_path, fake=fake)
    session_id = start(api)
    image = phone_photo()
    assert len(image) > 10 * 1024 * 1024

    body = send_photo(api, session_id, image).json()

    assert "밀푀유 케이크" in body["reply"]
    assert fake.sides and max(fake.sides) <= 2240
