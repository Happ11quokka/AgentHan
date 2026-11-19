"""HCX 튜닝 학습 데이터 (5단계 티켓 08): CLOVA 형식과 평가 가게 분리."""

from __future__ import annotations

import json
import re

from agenthan.voice.nlu.base import NluContext
from agenthan.voice.nlu.clova import ClovaClient
from agenthan.voice.nlu.hcx import ACT_TYPES, HcxNlu, tuned_system
from agenthan.voice.nlu.tuning_data import (
    MAX_ROW_CHARS,
    context,
    generate,
    load_stores,
    misheard,
    rows,
    write_jsonl,
)
from agenthan.voice.store_check import check_store

# 평가 가게(학습에 넣으면 안 되는 메뉴)
EVAL_WORDS = ("메도빅", "나폴레옹", "블린", "아메리카노", "사워크림", "쌀국수", "분짜", "반미",
              "연유 커피", "고수 빼기 | 곱")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_rows_follow_the_clova_format(tmp_path) -> None:
    data = rows(generate(300, seed=3))
    assert [list(r) for r in data[:1]] == [["System_Prompt", "C_ID", "T_ID", "Text", "Completion"]]
    assert [r["C_ID"] for r in data] == list(range(300))
    assert {r["T_ID"] for r in data} == {0}
    assert max(len(r["System_Prompt"]) + len(r["Text"]) + len(r["Completion"]) for r in data) \
        <= MAX_ROW_CHARS
    path = tmp_path / "train.jsonl"
    write_jsonl(path, data)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # BOM이 있으면 CLOVA가 1행 JSON 오류로 실패
    json.loads(raw.decode("utf-8").splitlines()[0])


def test_answers_are_valid_acts_grounded_in_the_utterance_and_the_store_menu() -> None:
    for sample in generate(400, seed=5):
        store = sample.store
        assert store is not None
        names = {m.name_ko for m in store.menu}
        for act in sample.acts:
            assert act["type"] in ACT_TYPES
            if act.get("menu_guess"):
                assert act["menu_guess"] in names
            if act.get("menu_text"):
                assert _norm(act["menu_text"]) in _norm(sample.text), sample
            if act.get("option_text"):
                assert _norm(act["option_text"]) in _norm(sample.text), sample


def test_no_menu_of_the_evaluation_stores_is_in_the_training_data() -> None:
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows(generate(1000, seed=11)))
    assert [w for w in EVAL_WORDS if w in text] == []


def test_the_training_stores_have_clean_data() -> None:
    for store in load_stores():
        assert check_store(store.menu, store.config) == [], store.name


def test_the_tuned_model_is_called_with_the_same_instruction_it_was_trained_on() -> None:
    sample = generate(1, seed=7)[0]
    ctx: NluContext = context(sample)
    nlu = HcxNlu(ClovaClient("k"), task_id="t-1")
    messages = nlu.payload(sample.text, ctx)["messages"]
    assert messages[0]["content"] == tuned_system(ctx) == rows([sample])[0]["System_Prompt"]
    assert "tools" not in nlu.payload(sample.text, ctx)


def test_misheard_names_change_a_little_and_stay_hangul() -> None:
    import random

    rng = random.Random(1)
    heard = {misheard("탄두리 치킨", rng) for _ in range(30)}
    assert "탄두리 치킨" not in heard or len(heard) > 1
    assert all(len(h.replace(" ", "")) == len("탄두리치킨") for h in heard)


def test_the_call_nlu_uses_the_tuned_task_only_when_it_is_set(monkeypatch) -> None:
    """`PHONE_NLU_TASK_ID`(5단계 티켓 08). 없으면 지금 프롬프트 그대로."""
    from agenthan.voice.call.session import make_nlu

    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "k")
    monkeypatch.delenv("PHONE_NLU_TASK_ID", raising=False)
    assert make_nlu("hcx")._task_id == ""
    monkeypatch.setenv("PHONE_NLU_TASK_ID", "5s8ppwmo")
    assert make_nlu("hcx")._task_id == "5s8ppwmo"
