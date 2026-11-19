"""가게 데이터 검사 (5단계 티켓 06). 새 가게를 붙이기 전에 데이터 실수를 잡는다."""

from __future__ import annotations

import json
from pathlib import Path

from agenthan.core.contracts import MenuItem, MenuOption
from agenthan.voice.store_check import check_store, main
from agenthan.voice.store_config import RequiredOption, StoreConfig

ROOT = Path(__file__).parents[2]
STORES = [ROOT / "fixtures" / "sample_store.json",
          ROOT / "fixtures" / "phone" / "sample_pho_store.json"]


def _item(id_: str, name: str, *options: str, aliases: tuple[str, ...] = ()) -> MenuItem:
    return MenuItem(id_, name, 1000, aliases=aliases,
                    options=tuple(MenuOption(o) for o in options))


def _req(name: str, *choices: str, default: str = "") -> RequiredOption:
    return RequiredOption(name, tuple(MenuOption(c) for c in choices), default)


def _codes(problems) -> list[tuple[str, str]]:
    return [(p.level, p.code) for p in problems]


def test_the_dummy_stores_have_no_problems() -> None:
    for path in STORES:
        raw = json.loads(path.read_text(encoding="utf-8"))
        from agenthan.core.fakes import FakeStoreKnowledge
        menu = FakeStoreKnowledge.from_fixture(path).list_menu()
        assert check_store(menu, StoreConfig.from_store_json(raw)) == [], path.name


def test_same_word_as_a_required_choice_and_an_option_is_warned() -> None:
    """2026-11-11 쌀국수집 실수: 곱빼기를 꼭 고를 것과 옵션("곱빼기 변경") 양쪽에 넣어
    봇이 "곱빼기 곱빼기 변경"이라고 말했다."""
    menu = [_item("p01", "쌀국수", "곱빼기 변경")]
    config = StoreConfig("가게", required_options={"p01": (_req("양", "보통", "곱빼기"),)})
    problems = check_store(menu, config)
    assert _codes(problems) == [("warning", "choice_is_option")]
    assert "곱빼기" in problems[0].message and "쌀국수" in problems[0].message


def test_required_choices_for_a_menu_that_does_not_exist_is_an_error() -> None:
    config = StoreConfig("가게", required_options={"m99": (_req("온도", "따뜻하게", "차갑게"),)})
    assert _codes(check_store([_item("m01", "커피")], config)) == [("error", "unknown_menu")]


def test_a_default_that_is_not_one_of_the_choices_is_an_error() -> None:
    config = StoreConfig("가게", required_options={
        "m01": (_req("온도", "따뜻하게", "차갑게", default="미지근하게"),)})
    assert _codes(check_store([_item("m01", "커피")], config)) == [("error", "bad_default")]


def test_required_choices_with_fewer_than_two_values_is_an_error() -> None:
    config = StoreConfig("가게", required_options={"m01": (_req("온도", "따뜻하게"),)})
    assert _codes(check_store([_item("m01", "커피")], config)) == [("error", "too_few_choices")]


def test_a_name_or_other_name_shared_by_two_menus_is_warned() -> None:
    menu = [_item("m01", "쌀국수"), _item("m02", "소고기 쌀국수", aliases=("쌀국수",))]
    assert _codes(check_store(menu, StoreConfig("가게"))) == [("warning", "shared_name")]


def test_the_same_option_twice_on_one_menu_is_warned() -> None:
    menu = [_item("m01", "반미", "고수 빼기", "고수 빼기")]
    assert _codes(check_store(menu, StoreConfig("가게"))) == [("warning", "duplicate_option")]


def test_the_command_prints_problems_and_fails_only_on_errors(tmp_path, capsys) -> None:
    store = {
        "store": {"name": "시험 가게"},
        "menu": [{"id": "m01", "name_ko": "쌀국수", "price": 1000,
                  "options": [{"name": "곱빼기 변경"}]}],
        "phone": {"required_options": {"m01": [{"name": "양", "choices": ["보통", "곱빼기"]}]}},
    }
    path = tmp_path / "store.json"
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "경고" in out and "곱빼기" in out

    store["phone"]["required_options"]["m99"] = store["phone"]["required_options"].pop("m01")
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    assert main([str(path)]) == 1
    assert "오류" in capsys.readouterr().out

    assert main([str(STORES[1])]) == 0
    assert "문제 없음" in capsys.readouterr().out
