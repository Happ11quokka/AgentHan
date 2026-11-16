"""Owner screen backend: menu photos go in over HTTP and come back in pending and place (#63)."""

from fastapi.testclient import TestClient

from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app.server import create_app
from test_food_photo import CAKE, EMPTY, EditChat, FakeV3


def client(tmp_path, *, fake=None, chat=None):
    def make(path):
        return OwnerAssistant(
            path, knowledge=EMPTY, clova_v3=fake or FakeV3(), chat=chat or EditChat()
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
