"""번역기 포트 (4단계 spec "번역"). 사장님 허브가 쓰는 쪽에서 정의한다.

    texts = await translator.translate(["[1] 빼 주세요"], "ko", "ru")

- 문장 여러 개를 받아 같은 순서·같은 개수로 돌려준다. 언어 코드는 Papago 코드(ko·ru …)
- 실패(키 틀림·한도·시간 초과·응답 형식)는 `TranslateError`. 허브는 원문만 두고 넘어간다
- 구현은 Papago 어댑터 하나(`papago.py`). 테스트는 가짜(`fakes.FakeTranslator`)
- 메뉴명 보호·숫자 확인은 포트 밖(`protect.py`)에서 한다. 포트는 받은 글을 그대로 옮긴다
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from agenthan.voice.call.ports import PortError


class TranslateError(PortError):
    """번역 실패. 메시지에 키·서버 본문을 넣지 않는다."""


class Translator(Protocol):
    async def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        """`texts`를 `source` → `target`으로. 돌려주는 목록은 `texts`와 길이·순서가 같다."""
        ...
