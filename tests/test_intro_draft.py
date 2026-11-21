"""가게 정보 입력 블록의 [추천 글 받기]: 가게 자료로 소개글 초안을 쓰고, 저장은 하지 않는다."""

import pytest
from fastapi.testclient import TestClient

from agenthan.agent import intro_draft
from agenthan.app import pilot
from agenthan.app.server import create_app


class Chat:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def __call__(self, messages, *, tools):
        self.requests.append((messages, tools))
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"role": "assistant", "content": self.reply}


def api(tmp_path, chat):
    app = create_app(make_assistant=lambda path: pilot.assistant(path, chat=chat), root=tmp_path)
    client = TestClient(app)
    sid = client.post("/assistant/sessions").json()["session_id"]
    return client, sid


def test_draft_uses_only_the_shop_data_and_does_not_save(tmp_path):
    chat = Chat('"이태원 골목에서 매일 아침 메도빅을 굽는 러시안 베이커리예요."')
    client, sid = api(tmp_path, chat)
    before = client.get(f"/assistant/sessions/{sid}/place").json()["place"]["business_info"]
    result = client.post(f"/assistant/sessions/{sid}/introduction/draft")
    assert result.status_code == 200
    assert result.json() == {"text": "이태원 골목에서 매일 아침 메도빅을 굽는 러시안 베이커리예요."}
    [(messages, tools)] = chat.requests
    assert tools == []
    facts = messages[-1]["content"]
    assert before["name"] in facts and "메도빅" in facts
    after = client.get(f"/assistant/sessions/{sid}/place").json()["place"]["business_info"]
    assert after == before  # 초안은 입력 칸에만 들어간다


@pytest.mark.parametrize("reply", [RuntimeError("HCX 429"), "   "])
def test_draft_failure_is_a_plain_message(tmp_path, reply):
    client, sid = api(tmp_path, Chat(reply))
    result = client.post(f"/assistant/sessions/{sid}/introduction/draft")
    assert result.status_code == 502
    assert "다시" in result.json()["detail"]


def test_draft_unknown_session(tmp_path):
    client, _ = api(tmp_path, Chat("x"))
    assert client.post("/assistant/sessions/nope/introduction/draft").status_code == 404


def test_clean_strips_quotes_and_label():
    assert intro_draft.clean("소개글: “좋은 가게예요.”\n") == "좋은 가게예요."
