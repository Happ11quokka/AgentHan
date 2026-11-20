"""Pilot store for the owner screen (#73).

Every chat starts from a copy of one made-up shop whose menus, reviews, inquiries and
stats tell one story, on a fixed day so "어제" and "이번 주" match the data. The
evaluation seed (tools/dummy_store_seed.json) is not touched.

With AGENTHAN_DATA_DIR set (e.g. data/clean), the shop, menus and reviews come from that
store data folder instead and the rest of the story is retold with its menus (store_data.py).
"""

import json
import os
import shutil
from datetime import date
from pathlib import Path

from agenthan.agent import review_delegate
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app import knowledge, store_data
from agenthan.rag import LiveShopKnowledge
from agenthan.tools import menu_index
from agenthan.tools.dummy_store import DummyStore

PILOT_SEED = Path(__file__).with_name("pilot_store.json")
PILOT_TODAY = date(2026, 11, 9)  # the Monday after the stats data's last week


def review_session(store: DummyStore, owner_lang: str = "ko", **kwargs):
    """The review helper on the pilot day (OwnerAssistant does not pass its own day)."""
    return review_delegate.new_session(store, owner_lang=owner_lang, today=PILOT_TODAY, **kwargs)


_knowledge_on = False


def use_knowledge() -> None:
    """Tools search menus with Part B search over each shop copy they work on (ADR-0008)."""
    global _knowledge_on
    if not _knowledge_on:
        menu_index.configure(knowledge.index_factory())
        _knowledge_on = True


def assistant(path: str | Path, **kwargs) -> OwnerAssistant:
    """An assistant over a fresh copy of the pilot shop. kwargs go to OwnerAssistant.

    Its store knowledge (menu photo matching) is Part B search over this chat's own shop file,
    rebuilt whenever the menus or reviews in that file change."""
    path = Path(path)
    folder = os.environ.get(store_data.DATA_DIR_ENV)
    use_knowledge()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if folder:
            pilot = json.loads(PILOT_SEED.read_text(encoding="utf-8"))
            DummyStore(path).write(store_data.pilot_seed(folder, pilot, PILOT_TODAY))
        else:
            shutil.copyfile(PILOT_SEED, path)
    kwargs.setdefault("knowledge", LiveShopKnowledge(DummyStore(path).read))
    return OwnerAssistant(path, today=PILOT_TODAY, review_session=review_session, **kwargs)
