"""Pilot store for the owner screen (#73).

Every chat starts from a copy of one made-up shop whose menus, reviews, inquiries and
stats tell one story, on a fixed day so "어제" and "이번 주" match the data. The
evaluation seed (tools/dummy_store_seed.json) is not touched.
"""

import shutil
from datetime import date
from pathlib import Path

from agenthan.agent import review_delegate
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.tools.dummy_store import DummyStore

PILOT_SEED = Path(__file__).with_name("pilot_store.json")
PILOT_TODAY = date(2026, 11, 9)  # the Monday after the stats data's last week


def review_session(store: DummyStore, owner_lang: str = "ko", **kwargs):
    """The review helper on the pilot day (OwnerAssistant does not pass its own day)."""
    return review_delegate.new_session(store, owner_lang=owner_lang, today=PILOT_TODAY, **kwargs)


def assistant(path: str | Path, **kwargs) -> OwnerAssistant:
    """An assistant over a fresh copy of the pilot shop. kwargs go to OwnerAssistant."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PILOT_SEED, path)
    return OwnerAssistant(path, today=PILOT_TODAY, review_session=review_session, **kwargs)
