"""Owner screen backend: menu photos go in over HTTP and come back in pending and place (#63)."""

from fastapi.testclient import TestClient

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app.server import create_app
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
    assert draft["name_ko"] == "밀푀유 케이크" and draft["price"] is None
    assert "price_missing" in draft["notes"]
    preview = api.get(draft["photo_url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert preview.content[:3] == b"\xff\xd8\xff"

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
    assert "밀푀유 케이크" in question and "맞나요" in question and "가격" in question
    assert "이대로 저장할까요" not in reply


def test_a_yes_without_a_price_saves_nothing_and_keeps_asking(tmp_path):
    api = client(tmp_path, chat=EditChat({"price": 8500}))
    session_id = start(api)
    before = send_photo(api, session_id, CAKE.read_bytes()).json()["place"]

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
