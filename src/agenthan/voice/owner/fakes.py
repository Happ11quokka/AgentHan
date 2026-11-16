"""사장님 허브 가짜 (4단계 티켓 03). 테스트가 번역을 결정적으로 만든다.

- `FakeTranslator`: 표에 있는 글은 표 값으로, 없는 글은 `(ru) 원문`으로 옮긴다. 받은 호출을
  `calls`에 `(글들, 원어, 대상 언어)`로 남긴다. `fail=True`면 늘 `TranslateError`.
  `gate`를 주면 그 이벤트가 켜질 때까지 기다렸다가 답한다(번역이 늦게 오는 경우)
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from agenthan.voice.owner.translate import TranslateError


class FakeTranslator:
    def __init__(
        self,
        table: Mapping[str, str] | None = None,
        *,
        fail: bool = False,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.table = dict(table or {})
        self.fail = fail
        self.gate = gate
        self.calls: list[tuple[tuple[str, ...], str, str]] = []

    async def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        self.calls.append((tuple(texts), source, target))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise TranslateError("가짜 번역 실패", code="translate_fake")
        return [self.table.get(t, f"({target}) {t}") for t in texts]
