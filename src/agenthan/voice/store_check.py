"""가게 데이터 검사 (5단계 티켓 06). 새 가게를 전화 주문에 붙이기 전에 데이터 실수를 잡는다.

가게마다 코드를 고치지 않으니(티켓 05) 가게 차이는 모두 데이터에 있다. 데이터가 틀리면 통화가
이상하게 말한다. 2026-11-11 쌀국수집 더미에서 곱빼기를 꼭 고를 것과 옵션("곱빼기 변경") 양쪽에
넣어 봇이 "곱빼기 곱빼기 변경"이라고 말했다.

- 오류(error): 그대로 두면 통화가 틀어진다. 서버는 뜨지 않는다
  - `unknown_menu`: 꼭 고를 것을 건 메뉴 id가 메뉴에 없다
  - `bad_default`: 기본값이 선택지에 없다
  - `too_few_choices`: 선택지가 둘보다 적다(고를 게 없다)
- 경고(warning): 헷갈리게 말하거나 엉뚱한 메뉴를 고를 수 있다. 로그에만 남긴다
  - `choice_is_option`: 꼭 고를 것의 값이 그 메뉴 옵션(또는 옵션 대상어)과 같다
  - `shared_name`: 두 메뉴가 같은 이름·다른 이름을 쓴다(띄어쓰기 무시)
  - `duplicate_option`: 한 메뉴에 같은 옵션이 두 번

명령으로도 돌린다(가게 JSON 하나, 오류가 있으면 종료 코드 1):

    uv run python -m agenthan.voice.store_check fixtures/phone/sample_pho_store.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agenthan.core.contracts import MenuItem
from agenthan.voice.dialog.options import option_target
from agenthan.voice.store_config import StoreConfig


class StoreDataError(ValueError):
    """가게 데이터에 오류가 있어 전화 주문을 띄우지 않는다."""


@dataclass(frozen=True)
class Problem:
    level: Literal["error", "warning"]
    code: str
    message: str


def check_store(menu: Iterable[MenuItem], config: StoreConfig) -> list[Problem]:
    items = list(menu)
    by_id = {item.id: item for item in items}
    problems: list[Problem] = []
    for menu_id, groups in config.required_options.items():
        item = by_id.get(menu_id)
        if item is None:
            problems.append(Problem("error", "unknown_menu",
                                    f"꼭 고를 것을 건 메뉴 id {menu_id!r}가 메뉴에 없어요"))
            continue
        for group in groups:
            problems.extend(_check_group(item, group))
    problems.extend(_shared_names(items))
    for item in items:
        names = [o.name for o in item.options]
        for name in sorted({n for n in names if names.count(n) > 1}):
            problems.append(Problem("warning", "duplicate_option",
                                    f"{item.name_ko}: 옵션 {name!r}이 두 번 있어요"))
    return problems


def _check_group(item, group) -> list[Problem]:  # type: ignore[no-untyped-def]
    out: list[Problem] = []
    names = [c.name for c in group.choices]
    where = f"{item.name_ko}의 꼭 고를 것 {group.name!r}"
    if len(names) < 2:
        out.append(Problem("error", "too_few_choices", f"{where}: 선택지가 둘 이상이어야 해요"))
    if group.default and group.default not in names:
        out.append(Problem("error", "bad_default",
                           f"{where}: 기본값 {group.default!r}이 선택지 {names}에 없어요"))
    for choice in names:
        for option in item.options:
            if choice in (option.name, option_target(option.name)):
                out.append(Problem(
                    "warning", "choice_is_option",
                    f"{where}의 값 {choice!r}이 옵션 {option.name!r}과 겹쳐요. "
                    "복창에서 같은 말을 두 번 할 수 있어요. 한쪽에만 두세요",
                ))
    return out


def _shared_names(items: Sequence[MenuItem]) -> list[Problem]:
    owners: dict[str, set[str]] = {}
    for item in items:
        for name in (item.name_ko, *item.aliases):
            owners.setdefault(name.replace(" ", ""), set()).add(item.name_ko)
    return [
        Problem("warning", "shared_name",
                f"{' · '.join(sorted(menus))}가 같은 이름 {key!r}을 써요. 손님 말이 어느 메뉴인지 "
                "못 고를 수 있어요")
        for key, menus in owners.items() if len(menus) > 1
    ]


def ensure_usable(menu: Iterable[MenuItem], config: StoreConfig, log) -> None:  # type: ignore[no-untyped-def]
    """서버 시작 때. 경고는 로그에 남기고, 오류가 하나라도 있으면 `StoreDataError`."""
    problems = check_store(menu, config)
    for p in problems:
        if p.level == "warning":
            log.warning("가게 데이터 경고 [%s] %s", p.code, p.message)
    errors = [p for p in problems if p.level == "error"]
    if errors:
        raise StoreDataError("가게 데이터 오류: " + " / ".join(p.message for p in errors))


def check_file(path: Path) -> list[Problem]:
    from agenthan.core.fakes import FakeStoreKnowledge

    raw = json.loads(path.read_text(encoding="utf-8"))
    return check_store(FakeStoreKnowledge.from_fixture(path).list_menu(),
                       StoreConfig.from_store_json(raw))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="가게 데이터 검사 (전화 주문)")
    parser.add_argument("store", type=Path, help="가게 JSON (store·menu·phone)")
    args = parser.parse_args(argv)
    try:
        problems = check_file(args.store)
    except (OSError, ValueError, KeyError, TypeError) as err:
        print(f"오류: 가게 JSON을 읽지 못했어요: {err}")
        return 1
    if not problems:
        print(f"{args.store}: 문제 없음")
        return 0
    for p in problems:
        print(f"{'오류' if p.level == 'error' else '경고'} [{p.code}] {p.message}")
    return 1 if any(p.level == "error" for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
