"""음식 사진 등록은 사장님의 확인 없이 기존 메뉴를 덮어쓰지 않는다."""

import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from agenthan.agent import food_photo
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.core.contracts import MenuItem
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.tools.dummy_store import DummyStore
from agenthan.tools.image_cutout import CutoutResult

DATA = Path(__file__).parent / "data" / "food"
CAKE = DATA / "cake.jpg"
EMPTY = FakeStoreKnowledge([])


class FakeV3:
    def __init__(self, name="밀푀유 케이크", box=None, owner_name=""):
        self.name, self.box, self.owner_name = name, box, owner_name

    def __call__(self, model, body):
        content = body["messages"][-1]["content"]
        if isinstance(content, list):
            if content[1]["text"] == food_photo.CLASSIFY_PROMPT:
                answer = "food"
            else:
                answer = json.dumps(
                    {
                        "name_ko": self.name,
                        "name_native": "",
                        "candidates": ["허니 케이크", "나폴레옹"],
                        "description": "겹겹이 쌓은 케이크.",
                        "box": self.box,
                    },
                    ensure_ascii=False,
                )
        elif "사장님 말에서" in content:
            answer = json.dumps({"name": self.owner_name}, ensure_ascii=False)
        else:
            answer = '{"description": "꿀을 넣어 만든 메도빅입니다."}'
        return {"message": {"content": answer}}


class EditChat:
    def __init__(self, *edits):
        self.edits = iter(edits)
        self.pending = False

    def __call__(self, messages, *, tools):
        if self.pending:
            self.pending = False
            return {"role": "assistant", "content": "고쳤어요."}
        self.pending = True
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "edit_menu_draft",
                        "arguments": json.dumps(
                            {"number": 1, **next(self.edits)}, ensure_ascii=False
                        ),
                    },
                }
            ],
        }


def bot(tmp_path, *, fake=None, chat=None, knowledge=EMPTY):
    store = DummyStore(tmp_path / "store.json")
    data = store.read()
    data["menus"] = []
    store.write(data)
    return OwnerAssistant(
        store.path, knowledge=knowledge, clova_v3=fake or FakeV3(), chat=chat or EditChat()
    )


def test_unexpected_classification_is_other():
    assert (
        food_photo.classify_photo(
            CAKE.read_bytes(), call=lambda *args: {"message": {"content": "maybe food"}}
        )
        == "other"
    )


class NoChat:
    """The name the owner types in the card is set by code, not by the chat model."""

    def __call__(self, messages, *, tools):
        pytest.fail("메뉴 이름 입력은 모델을 부르지 않는다")


def store_with(assistant, *items):
    data = assistant.store.read()
    data["menus"] = [
        {
            "id": i.id,
            "name": i.name_ko,
            "price": i.price,
            "description": i.description,
            "aliases": list(i.aliases),
        }
        for i in items
    ]
    assistant.store.write(data)


def test_full_edit_approve_and_photo(tmp_path):
    assistant = bot(tmp_path, chat=EditChat({"name_ko": "메도빅"}, {"price": 8500}))
    before = CAKE.read_bytes()
    answer = assistant.ask_photo(before)
    assert all(
        x in answer for x in ["AI 추측: 밀푀유 케이크", "허니 케이크", "나폴레옹", "가격 확인 필요"]
    )
    assert "메뉴 이름을 입력해 주세요" in answer
    preview = Path(assistant.menu_form()[0]["photo"])
    assert preview.exists()
    assert "메뉴 이름을 입력해 주세요" in assistant.ask("네")
    assert assistant.store.read()["menus"] == [] and assistant.menu_drafts
    answer = assistant.ask("이거 메도빅이야")
    assert "메도빅" in answer and "꿀을 넣어 만든 메도빅" in answer
    assistant.ask("가격 8500원")
    assert "저장했어요" in assistant.ask("네")
    assert not preview.exists() and CAKE.read_bytes() == before
    record = assistant.store.read()["menus"][0]
    photo = (tmp_path / record["photo"]).read_bytes()
    image = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
    assert record["name"] == "메도빅" and record["price"] == 8500
    assert image.shape[:2] == (1024, 1024) and np.all(image[0, 0] > 245)


def test_candidate_and_cancel(tmp_path):
    assistant = bot(tmp_path)
    assistant.ask_photo(CAKE.read_bytes())
    path = Path(assistant.menu_form()[0]["photo"])
    assert "나폴레옹" in assistant.ask("2번 후보")
    assert assistant.menu_form()[0]["name_ko"] == "나폴레옹"
    assistant.ask("취소")
    assert not path.exists() and assistant.store.read()["menus"] == []


def test_the_ai_guess_is_never_the_name(tmp_path):
    assistant = bot(tmp_path, chat=NoChat())
    assistant.ask_photo(CAKE.read_bytes())
    [draft] = assistant.menu_drafts
    assert draft.name_ko == "" and draft.name_read == "밀푀유 케이크"
    assert "food_guess" in draft.notes and not draft.complete
    assert "메뉴 이름을 입력해 주세요" in assistant.ask("네")
    assert assistant.store.read()["menus"] == []
    answer = assistant.ask("메뉴 이름: 체리 파이")
    assert draft.name_ko == "체리 파이" and "food_guess" not in draft.notes
    assert "가격" in answer.splitlines()[-1] and assistant.store.read()["menus"] == []


def test_ai_candidates_do_not_pick_an_existing_menu(tmp_path):
    napoleon = MenuItem(id="m02", name_ko="나폴레옹", price=7000)
    assistant = bot(tmp_path, knowledge=FakeStoreKnowledge([napoleon]))
    store_with(assistant, napoleon)
    answer = assistant.ask_photo(CAKE.read_bytes())
    assert assistant.menu_drafts[0].possible_menu_id is None
    assert "붙일까요" not in answer


def test_a_new_menu_instead_of_the_matched_one(tmp_path):
    medovik = MenuItem(id="m01", name_ko="메도빅", price=7500, aliases=["꿀케이크"])
    assistant = bot(
        tmp_path, knowledge=FakeStoreKnowledge([medovik]), chat=EditChat({"price": 8500})
    )
    store_with(assistant, medovik)
    assistant.ask_photo(CAKE.read_bytes())
    answer = assistant.ask("메뉴 이름: 꿀케이크")
    assert "기존 메뉴 메도빅에 이 사진을 붙일까요?" in answer
    answer = assistant.ask("새 메뉴로 올려 줘")
    assert "붙일까요" not in answer and "가격" in answer.splitlines()[-1]
    assert assistant.menu_drafts[0].possible_menu_id is None
    assistant.ask("가격 8500원")
    assert "저장했어요" in assistant.ask("네")
    rows = {m["name"]: m for m in assistant.store.read()["menus"]}
    assert rows["꿀케이크"]["price"] == 8500 and rows["꿀케이크"]["photo"]
    assert rows["메도빅"]["price"] == 7500 and "photo" not in rows["메도빅"]


def test_a_new_menu_cannot_take_a_saved_name(tmp_path):
    medovik = MenuItem(id="m01", name_ko="메도빅", price=7500)
    assistant = bot(tmp_path, knowledge=FakeStoreKnowledge([medovik]), chat=NoChat())
    store_with(assistant, medovik)
    assistant.ask_photo(CAKE.read_bytes())
    assistant.ask("메뉴 이름: 메도빅")
    answer = assistant.ask("새 메뉴로 올려 줘")
    assert "같은 이름의 메뉴" in answer and "붙일까요" in answer
    assert assistant.menu_drafts[0].possible_menu_id == "m01"
    assert "photo" not in assistant.store.read()["menus"][0]


def test_existing_requires_explicit_confirmation(tmp_path):
    item = MenuItem(id="known", name_ko="밀푀유 케이크", price=9000, description="기존 설명")
    assistant = bot(tmp_path, knowledge=FakeStoreKnowledge([item]), chat=NoChat())
    store_with(assistant, item)
    answer = assistant.ask_photo(CAKE.read_bytes())
    assert "붙일까요" not in answer  # The AI guess alone does not pick a saved menu.
    answer = assistant.ask("메뉴 이름: 밀푀유 케이크")
    assert "기존 메뉴 밀푀유 케이크에 이 사진을 붙일까요?" in answer
    assert "기존 메뉴 밀푀유 케이크에 이 사진을 붙일까요?" in assistant.ask("네")
    assert "photo" not in assistant.store.read()["menus"][0]
    assert "저장했어요" in assistant.ask("응, 밀푀유 케이크 사진 바꿔")
    rows = assistant.store.read()["menus"]
    assert len(rows) == 1 and rows[0]["id"] == "known" and rows[0]["price"] == 9000
    assert rows[0]["description"] == "기존 설명" and (tmp_path / rows[0]["photo"]).exists()


def test_owner_name_and_bad_box_fallback(tmp_path):
    assistant = bot(tmp_path, fake=FakeV3(owner_name="메도빅"))
    answer = assistant.ask_photo(CAKE.read_bytes(), "메도빅 올려 줘")
    assert "메도빅" in answer and "AI 추측" not in answer
    assistant.ask("취소")
    seen = []

    def cutout(data, *, box):
        seen.append(box)
        return CutoutResult(data, "image/jpeg", False, "실패", 0.0)

    draft = food_photo.draft_from_food(
        CAKE.read_bytes(), EMPTY, call=FakeV3(box=[0, 0, 0.1, 0.1]), cutout=cutout
    )
    assert seen == [None] and draft.photo == CAKE.read_bytes() and "cutout_failed" in draft.notes


@pytest.mark.network
def test_live_food_photo(record_property):
    if "CLOVASTUDIO_API_KEY" not in os.environ:
        pytest.skip("CLOVA key unavailable")
    image = CAKE.read_bytes()
    start = time.monotonic()
    assert food_photo.classify_photo(image) == "food"
    guess = food_photo.read_food_photo(image)
    assert guess.name_ko and guess.description
    draft = food_photo.draft_from_food(image, EMPTY)
    assert draft.name_read and not draft.name_ko and draft.photo
    elapsed = time.monotonic() - start
    record_property("elapsed_seconds", elapsed)
    print(f"HCX 음식 사진 전체 경과: {elapsed:.2f}초")


def test_read_retries_once_and_skips_empty_store_context():
    prompts = []
    answers = iter(["가게 정보가 없어 답할 수 없습니다.", json.dumps({"name_ko": "케이크"})])

    def call(model, body):
        prompts.append(body["messages"][0]["content"][1]["text"])
        return {"message": {"content": next(answers)}}

    guess = food_photo.read_food_photo(CAKE.read_bytes(), call=call)
    assert guess.name_ko == "케이크"
    assert len(prompts) == 2 and all("가게 정보(" not in p for p in prompts)
    prompts.clear()
    answers = iter([json.dumps({"name_ko": "메도빅"})])
    food_photo.read_food_photo(CAKE.read_bytes(), "러시안 베이커리", call=call)
    assert "가게 정보(참고용 자료): 러시안 베이커리" in prompts[0]


def test_read_gives_up_after_two_bad_answers():
    def call(model, body):
        return {"message": {"content": "형식 없는 답"}}

    with pytest.raises(food_photo.menu_photo.MenuPhotoError):
        food_photo.read_food_photo(CAKE.read_bytes(), call=call)


def test_owner_named_food_is_kept_when_the_text_call_misses(tmp_path):
    assistant = bot(tmp_path, fake=FakeV3(owner_name=""))  # The name call returns nothing.
    out = assistant.ask_photo(CAKE.read_bytes(), "이거 메도빅이에요")
    assert assistant.menu_drafts[0].name_ko == "메도빅" and "1. 메도빅" in out
    other = bot(tmp_path / "x", fake=FakeV3(owner_name=""))
    other.ask_photo(CAKE.read_bytes(), "이거 올려 줘")
    [guessed] = other.menu_drafts
    assert guessed.name_ko == "" and guessed.name_read == "밀푀유 케이크"
