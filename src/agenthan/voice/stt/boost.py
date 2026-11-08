"""CLOVA Speech 키워드 부스팅 단어 (티켓 10 #5, `research/s2-stt-misrecognition.md` Q1).

통화를 시작할 때 CONFIG `keywordBoosting.boostings`에 넣는다(`clova.build_config`). 가게 메뉴에서
만든다: 메뉴 이름, 별칭, 옵션 대상어("사워크림 빼기" → "사워크림").

- 요청별 단어(`boostings`)만 쓴다. 도메인 ID 방식(`useDomainBoostings` + `boostingIDs`)은 가게가
  바뀔 때마다 빌더 작업이 필요하고, 둘을 섞으면 요청이 실패한다(공식 문서)
- 가중치는 3. 실측 A/B에서 3은 "나폴내용"을 "나폴레옹"으로 고쳤고 5는 3보다 나은 점이 없었다.
  가중치가 크면 비슷한 소리를 키워드로 끌어온다(과부스팅)
- 한 음절 단어("샷")는 넣지 않는다. 비슷한 소리가 많아 과부스팅 위험이 크다
- 부스팅이 고친 음절은 `alignInfos` 신뢰도가 0.02~0.05로 떨어진다(실측 C3 "나폴레옹").
  그래서 메뉴 단어 신뢰도로 되묻는 규칙은 두지 않는다(2026-11 현재 없음, 어댑터는 `alignInfos`를
  읽지도 않는다). 그런 규칙을 넣게 되면 부스팅 단어와 정확히 같은 단어는 신뢰도를 보지 않아야
  한다. 안 그러면 부스팅이 맞게 고친 단어마다 되묻는다 (리서치 고칠 것 후보 #7)

이 모듈은 gRPC를 모른다(라우터가 어댑터 없이도 import한다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from agenthan.core.contracts import MenuItem
from agenthan.voice.dialog.options import option_target

BOOST_WEIGHT = 3.0
MAX_BOOST_WORDS = 500  # 빌더 권장 상한. 요청별 상한은 문서에 없다

_SEPARATOR = re.compile(r",")
_SPACES = re.compile(r"\s+")


def _clean(word: str) -> str:
    """콤마는 CLOVA 단어 구분자이고 앞뒤 공백도 키워드에 들어가서 뺀다."""
    return _SPACES.sub(" ", _SEPARATOR.sub("", word)).strip()


def menu_boost_words(menu: Iterable[MenuItem]) -> tuple[str, ...]:
    """메뉴 → 부스팅 단어. 메뉴 순서대로 이름 · 별칭 · 옵션 대상어, 겹치는 것은 한 번만."""
    words: dict[str, None] = {}
    for item in menu:
        targets = (option_target(o.name) for o in item.options)
        for word in (item.name_ko, *item.aliases, *targets):
            cleaned = _clean(word)
            if len(cleaned.replace(" ", "")) >= 2:
                words.setdefault(cleaned)
    return tuple(words)[:MAX_BOOST_WORDS]
