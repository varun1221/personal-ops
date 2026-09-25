"""The acceptance test: a write must not happen without approval.

Runs the real graph against the real MCP servers over stdio, with a scripted model
standing in for the LLM so the tool calls are deterministic. No API key needed.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import history
from agent.graph import build_graph
from agent.mcp_client import build_client, load_tools

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ScriptedModel:
    """Stands in for a chat model. build_graph only needs these two methods."""

    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self.received: list = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.received.append(messages)
        if self._responses:
            return self._responses.pop(0)
        return AIMessage(content="done")


@pytest.fixture
def temp_vault(tmp_path, monkeypatch) -> Path:
    """A throwaway copy of the fixture vault. The real vault is never touched."""
    vault = tmp_path / "vault"
    shutil.copytree(PROJECT_ROOT / "fixtures" / "vault", vault)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("OBSIDIAN_EVENTS_FOLDER", "Events")
    monkeypatch.setenv("OBSIDIAN_INBOX_NOTE", "Inbox.md")
    return vault


async def _graph_with(responses, include_gmail=False, include_memory=False):
    # Memory is left out unless a test calls it: every server is a subprocess,
    # and one nobody calls still adds about 0.4s to every test here.
    client = build_client(
        include_gmail=include_gmail, include_actions=True, include_memory=include_memory
    )
    tools = await load_tools(client)
    model = ScriptedModel(responses)
    return build_graph(tools, model, MemorySaver()), model


def _write_call(**args):
    return AIMessage(
        content="Scheduling that now.",
        tool_calls=[
            {"name": "create_calendar_event", "args": args, "id": "call_1", "type": "tool_call"}
        ],
    )


def _config():
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


async def test_write_pauses_for_approval(temp_vault):
    graph, _ = await _graph_with(
        [_write_call(title="Spec writing", date="2026-08-20", start_time="13:00", end_time="15:00")]
    )
    result = await graph.ainvoke({"messages": [HumanMessage("schedule it")]}, _config())

    pending = result.get("__interrupt__")
    assert pending, "a gated tool must interrupt before running"
    request = pending[0].value
    assert request["type"] == "approval_request"
    assert request["tool"] == "create_calendar_event"
    assert "Spec writing" in request["description"]
    # Nothing on disk yet.
    assert not list((temp_vault / "Events").glob("*Spec writing*"))


async def test_rejection_writes_nothing(temp_vault):
    graph, _ = await _graph_with(
        [
            _write_call(title="Spec writing", date="2026-08-20", start_time="13:00", end_time="15:00"),
            AIMessage(content="Understood, leaving it alone."),
        ]
    )
    config = _config()
    before = sorted(p.name for p in (temp_vault / "Events").glob("*.md"))

    await graph.ainvoke({"messages": [HumanMessage("schedule it")]}, config)
    result = await graph.ainvoke(
        Command(resume={"approved": False, "reason": "Thursday is full"}), config
    )

    after = sorted(p.name for p in (temp_vault / "Events").glob("*.md"))
    assert after == before, "a rejected write must leave the vault untouched"
    assert not result.get("__interrupt__")

    rejection = [m for m in result["messages"] if getattr(m, "name", None) == "create_calendar_event"]
    assert rejection and "REJECTED" in rejection[0].content
    assert "Thursday is full" in rejection[0].content


async def test_approval_writes_the_event(temp_vault):
    graph, _ = await _graph_with(
        [
            _write_call(title="Spec writing", date="2026-08-20", start_time="13:00", end_time="15:00"),
            AIMessage(content="Scheduled."),
        ]
    )
    config = _config()

    await graph.ainvoke({"messages": [HumanMessage("schedule it")]}, config)
    await graph.ainvoke(Command(resume={"approved": True}), config)

    created = temp_vault / "Events" / "2026-08-20 Spec writing.md"
    assert created.exists(), "an approved write must actually land"
    text = created.read_text()
    assert "title: Spec writing" in text
    assert 'startTime: "13:00"' in text


async def test_approved_write_runs_exactly_once(temp_vault):
    """Resuming re-runs the node from the top; side effects must not double up.

    The second write would hit the 'already exists' guard and report an error, so
    a duplicated dispatch shows up as a failed tool result rather than two notes.
    """
    graph, _ = await _graph_with(
        [
            _write_call(title="Spec writing", date="2026-08-20", start_time="13:00", end_time="15:00"),
            AIMessage(content="Scheduled."),
        ]
    )
    config = _config()

    await graph.ainvoke({"messages": [HumanMessage("schedule it")]}, config)
    result = await graph.ainvoke(Command(resume={"approved": True}), config)

    assert len(list((temp_vault / "Events").glob("*Spec writing*"))) == 1
    tool_results = [
        m for m in result["messages"] if getattr(m, "name", None) == "create_calendar_event"
    ]
    assert len(tool_results) == 1
    assert "already exists" not in tool_results[0].content


async def test_read_tools_are_not_gated(temp_vault):
    """Reads must flow straight through — gating them would make the agent useless."""
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Checking.",
                tool_calls=[
                    {
                        "name": "list_events",
                        "args": {"start_date": "2026-08-20", "end_date": "2026-08-20"},
                        "id": "call_r",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="You have three things Thursday."),
        ]
    )
    result = await graph.ainvoke({"messages": [HumanMessage("what's thursday")]}, _config())

    assert not result.get("__interrupt__"), "read tools must not require approval"
    assert "Design Review" in str(result["messages"])


async def test_working_set_populated_from_reads(temp_vault):
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Checking.",
                tool_calls=[
                    {
                        "name": "list_events",
                        "args": {"start_date": "2026-08-20", "end_date": "2026-08-20"},
                        "id": "call_r",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Three things."),
        ]
    )
    result = await graph.ainvoke({"messages": [HumanMessage("what's thursday")]}, _config())

    events = result["working_set"]["last_events"]
    assert [e["index"] for e in events] == [1, 2, 3]
    # "the second one" has to resolve to something specific.
    assert events[1]["title"] == "Design Review"
    assert events[1]["note_path"].endswith("Design Review.md")


async def test_add_task_appends_after_approval(temp_vault):
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Adding.",
                tool_calls=[
                    {
                        "name": "add_task",
                        "args": {"text": "Draft the retro", "due_date": "2026-08-21", "priority": "high"},
                        "id": "call_t",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Added."),
        ]
    )
    config = _config()

    await graph.ainvoke({"messages": [HumanMessage("add it")]}, config)
    await graph.ainvoke(Command(resume={"approved": True}), config)

    inbox = (temp_vault / "Inbox.md").read_text()
    assert "Draft the retro" in inbox
    assert "📅 2026-08-21" in inbox
    assert "⏫" in inbox


async def test_malformed_gated_call_never_prompts(temp_vault):
    """An approval prompt costs real attention; a doomed call must not spend it."""
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Moving it.",
                tool_calls=[
                    {
                        "name": "move_event",
                        "args": {"note_path": "Events/x.md", "title": "Thing"},  # no destination
                        "id": "call_bad",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="I need a new time."),
        ]
    )
    result = await graph.ainvoke({"messages": [HumanMessage("move it")]}, _config())

    assert not result.get("__interrupt__"), "must not ask about a call that cannot succeed"
    errors = [m for m in result["messages"] if getattr(m, "name", None) == "move_event"]
    assert errors and "needs a destination" in errors[0].content
    assert errors[0].status == "error"


async def test_precheck_lets_well_formed_calls_through(temp_vault):
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Moving it.",
                tool_calls=[
                    {
                        "name": "move_event",
                        "args": {
                            "note_path": "Events/x.md",
                            "title": "Thing",
                            "new_start_time": "16:00",
                        },
                        "id": "call_ok",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    result = await graph.ainvoke({"messages": [HumanMessage("move it")]}, _config())
    assert result.get("__interrupt__"), "a complete call must still ask"


async def test_precheck_requires_note_path_for_edits(temp_vault):
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Ticking.",
                tool_calls=[
                    {"name": "complete_task", "args": {"text": "Standup"},
                     "id": "c1", "type": "tool_call"}
                ],
            ),
            AIMessage(content="Need the path."),
        ]
    )
    result = await graph.ainvoke({"messages": [HumanMessage("tick it")]}, _config())
    assert not result.get("__interrupt__")
    errors = [m for m in result["messages"] if getattr(m, "name", None) == "complete_task"]
    assert errors and "note_path" in errors[0].content


async def test_each_answer_is_recorded_exactly_once(temp_vault):
    """Two gated calls mean two interrupts, so the node replays once more than it
    finishes. A decision recorded before the last interrupt would be recorded twice.
    """
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Adding both.",
                tool_calls=[
                    {"name": "add_task", "args": {"text": "Gym"}, "id": "a", "type": "tool_call"},
                    {"name": "add_task", "args": {"text": "Run"}, "id": "b", "type": "tool_call"},
                ],
            ),
            AIMessage(content="Left alone."),
        ]
    )
    config = _config()

    await graph.ainvoke({"messages": [HumanMessage("add gym and run")]}, config)
    await graph.ainvoke(Command(resume={"approved": False, "reason": "not today"}), config)
    await graph.ainvoke(Command(resume={"approved": False}), config)

    rejections = history.past_rejections()
    assert sorted(r.args["text"] for r in rejections) == ["Gym", "Run"]
    assert {r.reason for r in rejections} == {"not today", None}


async def test_the_agent_can_read_what_it_was_refused(temp_vault):
    graph, _ = await _graph_with(
        [
            AIMessage(
                content="Adding it.",
                tool_calls=[
                    {"name": "add_task", "args": {"text": "Gym"}, "id": "a", "type": "tool_call"}
                ],
            ),
            AIMessage(
                content="Checking.",
                tool_calls=[
                    {"name": "past_rejections", "args": {}, "id": "r", "type": "tool_call"}
                ],
            ),
            AIMessage(content="You said no to that before."),
        ],
        include_memory=True,
    )
    config = _config()

    await graph.ainvoke({"messages": [HumanMessage("add gym")]}, config)
    result = await graph.ainvoke(Command(resume={"approved": False, "reason": "rest day"}), config)

    [answer] = [m for m in result["messages"] if getattr(m, "name", None) == "past_rejections"]
    assert "rest day" in str(answer.content)
