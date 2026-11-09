"""카드 자유 문장 번역 (4단계 spec "번역"). 허브가 통화 id별로 쓴다.

    texts = CardTexts(translator, store=store, lexicon=RU)
    texts.known("call-1", "고수 빼 주세요")         # 아직이면 ""
    todo = texts.claim("call-1", ["고수 빼 주세요"])  # 캐시에 없고 진행 중도 아닌 것
    changed = await texts.fetch("call-1", todo)      # 번역해 캐시에 넣는다. 새 번역이 있으면 True

- 같은 문장은 통화 동안 한 번만 번역한다(캐시, 통화 id별). 번역이 버려진 문장(자리표시자·숫자
  불일치)도 ""로 캐시해 다시 보내지 않는다. 번역기 오류는 캐시하지 않는다(다음 카드 갱신 때 다시)
- 번역 전에 메뉴명·헷갈리는 재료 낱말을 가린다(`protect.py`). 메뉴명 표는 부를 때마다 가게
  메뉴(`list_menu`)에서 만든다: 이름·별칭 → 원어 이름(비면 한국어 이름)
- 번역기가 없으면(키 없음) 아무것도 하지 않는다. 카드는 원문만
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from agenthan.core.contracts import StoreKnowledge
from agenthan.voice.owner.lexicon import Lexicon
from agenthan.voice.owner.protect import mask, restore
from agenthan.voice.owner.translate import Translator

log = logging.getLogger(__name__)

SOURCE_LANG = "ko"


class CardTexts:
    def __init__(
        self, translator: Translator | None, *, store: StoreKnowledge, lexicon: Lexicon
    ) -> None:
        self._translator = translator
        self._store = store
        self._lexicon = lexicon
        self._done: dict[str, dict[str, str]] = {}  # 통화 id → 원문 → 번역("" = 버림)
        self._pending: dict[str, set[str]] = {}  # 통화 id → 번역 중인 원문

    def known(self, call_id: str, text: str) -> str:
        return self._done.get(call_id, {}).get(text, "")

    def claim(self, call_id: str, texts: Iterable[str]) -> list[str]:
        """번역할 문장을 맡는다: 비지 않고, 캐시에 없고, 진행 중이 아닌 것. 번역기가 없으면 []."""
        if self._translator is None:
            return []
        done = self._done.get(call_id, {})
        pending = self._pending.setdefault(call_id, set())
        todo = list(dict.fromkeys(t for t in texts if t and t not in done and t not in pending))
        pending.update(todo)
        return todo

    def release(self, call_id: str, texts: Iterable[str]) -> None:
        """맡았지만 번역하지 못할 문장을 놓는다(다음 카드 갱신 때 다시 맡을 수 있게)."""
        self._pending.get(call_id, set()).difference_update(texts)

    async def fetch(self, call_id: str, texts: list[str]) -> bool:
        """`claim`한 문장을 번역해 캐시에 넣는다. 쓸 만한 번역이 하나라도 생기면 True."""
        assert self._translator is not None
        names = self._menu_names()
        masked = [mask(t, names=names, terms=self._lexicon.terms) for t in texts]
        try:
            out = await self._translator.translate(
                [m.text for m in masked], SOURCE_LANG, self._lexicon.lang
            )
        except Exception as err:  # noqa: BLE001 — 번역은 덤이다. 어떤 실패든 원문만 둔다
            log.warning("번역 실패(%s): 원문만 보임, 문장 %d개", type(err).__name__, len(texts))
            return False
        finally:
            self._pending.get(call_id, set()).difference_update(texts)
        if len(out) != len(texts):
            log.warning("번역 개수가 달라 버림: 보냄 %d, 받음 %d", len(texts), len(out))
            return False
        done = self._done.setdefault(call_id, {})
        changed = False
        for text, m, tr in zip(texts, masked, out, strict=True):
            kept = restore(tr, m, numerals=self._lexicon.numerals)
            if kept is None:
                log.info("번역을 버림(자리표시자·숫자 불일치): %r → %r", m.text, tr)
            done[text] = kept or ""
            changed = changed or bool(kept)
        return changed

    def _menu_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for item in self._store.list_menu():
            owner = item.name_native or item.name_ko
            for ko in (item.name_ko, *item.aliases):
                names.setdefault(ko, owner)
        return names
