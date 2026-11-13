"""Session task ledger: every owner task the assistant is holding, in one place.

A task is one thing the owner asked for (change the phone number, register menus from a
photo, answer a review). It records which feature handles it, its status, the write waiting
for approval (with a version that changes whenever that preview changes or is dropped), and
the last question the assistant asked within it.

Tasks are kept on a stack: a new task or an interjection goes on top and the task below is
paused, not deleted. When the top task ends, the one below resumes. A paused task stops
getting resume notices after QUIET_AFTER owner turns, but it stays in the ledger and can be
resumed by id.

Slots (value + where it came from) are recorded here so later steps can check that a write
uses only values the owner said or a lookup confirmed. A slot changes only through set_slot,
which keeps the earlier value in the task's change log; an edit replaces only the slots it
names and every other confirmed value stays.
"""

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["collecting", "awaiting_approval", "paused", "done", "cancelled", "failed"]
OPEN = frozenset({"collecting", "awaiting_approval", "paused"})
# owner = the owner said it, lookup = confirmed from the store, rule = computed by code,
# ai_draft = written by a model (needs the owner's approval before it is saved).
Source = Literal["owner", "lookup", "rule", "ai_draft"]
QUIET_AFTER = 3


@dataclass
class Slot:
    value: object
    source: Source
    turn: int
    said_at: int = 0  # len(task.said) when set: later owner words start at this index.


@dataclass
class Task:
    id: str
    feature: str
    kind: str = "tool"  # tool | photo | review
    status: Status = "collecting"
    slots: dict[str, Slot] = field(default_factory=dict)
    said: list[str] = field(default_factory=list)  # Owner words within this task.
    preview: dict | None = None  # {"name", "arguments", "preview", "version"}
    version: int = 0
    data: object = None  # Kind-specific payload (menu photo drafts).
    prompt: str = ""  # Last thing the assistant said within this task.
    started: int = 0
    touched: int = 0
    paused_at: int | None = None
    held_status: Status | None = None  # Status to restore when resumed.
    summary: str = ""
    # (turn, slot, old value, new value, source) for every slot change, oldest first.
    changes: list[tuple] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN


class TaskLedger:
    def __init__(self):
        self.tasks: list[Task] = []
        self.stack: list[str] = []
        self.turn = 0
        self._next = 1

    def tick(self) -> int:
        """One owner turn passed."""
        self.turn += 1
        return self.turn

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def active(self) -> Task | None:
        return self.get(self.stack[-1]) if self.stack else None

    def open_tasks(self) -> list[Task]:
        return [t for t in self.tasks if t.is_open]

    def find_open(self, feature: str | None = None, kind: str | None = None) -> Task | None:
        """Most recent open task matching feature/kind."""
        for task_id in reversed(self.stack):
            task = self.get(task_id)
            if (feature is None or task.feature == feature) and (kind is None or task.kind == kind):
                return task
        return None

    def _pause(self, task: Task) -> None:
        if task.status != "paused":
            task.held_status = task.status
            task.status = "paused"
            task.paused_at = self.turn

    def _restore(self, task: Task) -> None:
        if task.status == "paused":
            task.status = task.held_status or "collecting"
            task.held_status = None

    def start(self, feature: str, kind: str = "tool", data: object = None) -> Task:
        """New task on top; the previous top is paused (kept)."""
        top = self.active()
        if top is not None:
            self._pause(top)
        task = Task(
            f"t{self._next}", feature, kind, data=data, started=self.turn, touched=self.turn
        )
        self._next += 1
        self.tasks.append(task)
        self.stack.append(task.id)
        return task

    def touch(self, task: Task) -> None:
        task.touched = self.turn
        task.paused_at = None

    def resume(self, task_id: str) -> Task | None:
        """Bring an open task to the top (pausing the current top)."""
        task = self.get(task_id)
        if task is None or not task.is_open:
            return None
        top = self.active()
        if top is not None and top is not task:
            self._pause(top)
        self.stack.remove(task.id)
        self.stack.append(task.id)
        self._restore(task)
        self.touch(task)
        return task

    def set_slot(self, task: Task, name: str, value: object, source: Source) -> bool:
        """Record one confirmed value; True when it is new or changed."""
        old = task.slots.get(name)
        if old is not None and old.value == value:
            return False
        task.slots[name] = Slot(value, source, self.turn, len(task.said))
        task.changes.append((self.turn, name, old.value if old else None, value, source))
        return True

    def set_preview(self, task: Task, preview: dict) -> None:
        task.version += 1
        task.preview = {**preview, "version": task.version}
        task.status = "awaiting_approval"
        self.touch(task)

    def drop_preview(self, task: Task) -> None:
        """The shown change is void (edited, failed); any approval of it no longer counts."""
        if task.preview is not None:
            task.preview = None
            task.version += 1
        if task.status == "awaiting_approval":
            task.status = "collecting"

    def close(self, task: Task, status: Status, summary: str = "") -> Task | None:
        """End a task. Returns the task that resumes on top, if any."""
        task.status = status
        task.preview = None
        task.summary = summary or task.summary
        if task.id in self.stack:
            self.stack.remove(task.id)
        top = self.active()
        if top is not None and top.status == "paused":
            self._restore(top)
            return top
        return None

    def quiet(self, task: Task) -> bool:
        """True when a held task has waited long enough to stop resume notices."""
        return task.paused_at is not None and self.turn - task.paused_at > QUIET_AFTER

    def log(self, feature: str | None = None, last: int | None = None) -> list[str]:
        """One line per finished task, oldest first.

        With last: the last N finished tasks, plus every finished task of feature however old
        (아까 아메리카노 얼마로 바꿨죠? after five other tasks)."""
        done = [t for t in self.tasks if not t.is_open]
        if last is not None:
            recent = {t.id for t in done[-last:]} if last else set()
            done = [t for t in done if t.id in recent or (feature and t.feature == feature)]
        return [
            f"{t.id} {t.feature}: {t.status}" + (f" ({t.summary})" if t.summary else "")
            for t in done
        ]
