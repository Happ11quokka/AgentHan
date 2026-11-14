"""One owner turn as a LangGraph StateGraph (ADR-0003).

The graph only wires the existing OwnerAssistant steps; every decision stays in those
methods. A shown write preview pauses the thread at ``wait_approval`` (``interrupt()``) and
the owner's next words resume it. The task ledger stays the source of truth: dispatch
re-reads it on every pass, so a pause the ledger no longer backs changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

if TYPE_CHECKING:
    from agenthan.agent.owner_assistant import OwnerAssistant

THREAD = {"configurable": {"thread_id": "owner"}}


class Turn(TypedDict):
    text: str
    answer: str
    approved: bool  # approval answered a clear yes/no this pass


def build(assistant: OwnerAssistant):
    """Compile the turn graph over one assistant, with its own in-memory checkpointer."""

    def waits(_: Turn) -> str:
        task = assistant.ledger.active()
        return "wait_approval" if task is not None and task.preview is not None else END

    def dispatch(_: Turn) -> dict:
        return {}

    def choose(_: Turn) -> str:
        task = assistant.ledger.active()
        if task is not None and task.kind == "photo":
            return "photo_reply"
        if task is not None and task.preview is not None:
            return "approval"
        return "route"

    def photo_reply(state: Turn) -> dict:
        return {"answer": assistant._menu_photo_reply(state["text"])}

    def approval(state: Turn) -> dict:
        answer = assistant.reply_to_approval(state["text"])
        return {"approved": False} if answer is None else {"answer": answer, "approved": True}

    def approved(state: Turn) -> str:
        # reply_to_approval returns None for anything but a clear yes/no; then the
        # utterance is an edit or an interjection and goes through route like any other.
        return waits(state) if state["approved"] else "route"

    def route(state: Turn) -> dict:
        # Route holds the chosen feature for respond; it is turn-local, not checkpointed.
        assistant._turn_route = assistant.route(state["text"])
        return {}

    def respond(state: Turn) -> dict:
        chosen, assistant._turn_route = assistant._turn_route, None
        return {"answer": assistant.respond(state["text"], chosen)}

    def wait_approval(state: Turn) -> dict:
        return {"text": interrupt(state["answer"])}

    graph = StateGraph(Turn)
    graph.add_node("dispatch", dispatch)
    graph.add_node("photo_reply", photo_reply)
    graph.add_node("approval", approval)
    graph.add_node("route", route)
    graph.add_node("respond", respond)
    graph.add_node("wait_approval", wait_approval)
    graph.add_edge(START, "dispatch")
    graph.add_conditional_edges("dispatch", choose, ["photo_reply", "approval", "route"])
    graph.add_edge("photo_reply", END)
    graph.add_conditional_edges("approval", approved, ["route", "wait_approval", END])
    graph.add_edge("route", "respond")
    graph.add_conditional_edges("respond", waits, ["wait_approval", END])
    graph.add_edge("wait_approval", "dispatch")
    return graph.compile(checkpointer=InMemorySaver())


def paused(turns) -> bool:
    """True while the thread waits at an interrupt for the owner's next words."""
    return any(task.interrupts for task in turns.get_state(THREAD).tasks)


def run(turns, text: str) -> str:
    """Run one owner utterance: resume a pause, or start a new pass from dispatch."""
    fresh = {"text": text, "answer": "", "approved": False}
    payload = Command(resume=text) if paused(turns) else fresh
    turns.invoke(payload, THREAD)
    return turns.get_state(THREAD).values["answer"]
