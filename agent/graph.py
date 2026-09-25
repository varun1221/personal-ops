"""The agent graph: model -> gated tools -> model.

The shape is an ordinary ReAct cycle. Two things make it more than a tutorial:

1. A tool node that pauses for human approval before any tool that mutates the
   vault, using LangGraph's interrupt().
2. A working set updated from tool results, so later turns can say "the second
   one" and have it mean something.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

import history
from agent.mcp_client import GATED_TOOLS
from agent.prompts import system_prompt
from agent.state import AgentState, extract_working_set


def precheck(name: str, args: dict) -> str | None:
    """Reject obviously-malformed gated calls before asking the human about them.

    An approval prompt costs the user real attention. A call that validation will
    reject anyway must not spend it — a live run produced
    "Move ... to a new time" with no destination at all, which is not a decision
    anyone can meaningfully make. The tool still validates on its own; this only
    keeps doomed calls from reaching a prompt.
    """
    if name == "move_event" and not any(
        args.get(k) for k in ("new_date", "new_start_time", "new_end_time")
    ):
        return (
            "move_event needs a destination: pass new_date and/or new_start_time. "
            "Nothing was changed and the user was not asked."
        )
    if name == "create_calendar_event" and not args.get("date"):
        return "create_calendar_event needs a date (YYYY-MM-DD). Nothing was changed."
    if name in ("complete_task", "rename_entry", "delete_event") and not args.get("note_path"):
        return (
            f"{name} needs the note_path from a list_events or list_tasks result. "
            "Call one of those first. Nothing was changed."
        )
    return None


def describe_call(name: str, args: dict) -> str:
    """A human-readable one-liner for the approval prompt.

    This line is the whole basis on which the user approves or rejects, so it
    says what will change, not which function will run.
    """
    if name == "create_calendar_event":
        when = args.get("start_time")
        window = f"{when}–{args.get('end_time')}" if when else "all day"
        return f"Create event “{args.get('title')}” on {args.get('date')} ({window})"

    if name == "add_task":
        due = f", due {args['due_date']}" if args.get("due_date") else ""
        target = args.get("target_note") or "the inbox note"
        return f"Add task “{args.get('text')}”{due} to {target}"

    if name == "delete_event":
        what = args.get("title") or "an entry"
        when = f" at {args['start_time']}" if args.get("start_time") else ""
        return f"DELETE “{what}”{when} from {args.get('note_path')} — this removes it"

    if name == "complete_task":
        verb = "Tick off" if args.get("done", True) else "Re-open"
        when = f" at {args['start_time']}" if args.get("start_time") else ""
        return f"{verb} “{args.get('text')}”{when} in {args.get('note_path')}"

    if name == "rename_entry":
        return (
            f"Rename “{args.get('text')}” to “{args.get('new_text')}” "
            f"in {args.get('note_path')}"
        )

    if name == "move_event":
        what = args.get("title") or "the entry"
        current = f" (currently {args['start_time']})" if args.get("start_time") else ""
        parts = []
        if args.get("new_date"):
            parts.append(args["new_date"])
        if args.get("new_start_time"):
            window = args["new_start_time"]
            if args.get("new_end_time"):
                window += f"–{args['new_end_time']}"
            parts.append(window)
        return f"Move “{what}”{current} to {' '.join(parts) or 'a new time'}"

    return f"Run {name} with {json.dumps(args, default=str)}"


def build_graph(tools: list[BaseTool], model, checkpointer):
    tools_by_name = {tool.name: tool for tool in tools}
    model_with_tools = model.bind_tools(tools)

    async def call_model(state: AgentState) -> dict:
        messages = [
            SystemMessage(content=system_prompt(state.get("working_set", {}))),
            *state["messages"],
        ]
        response = await model_with_tools.ainvoke(messages)
        return {"messages": [response]}

    async def call_tools(state: AgentState, config: RunnableConfig) -> dict:
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []

        # Approvals first, dispatch second — deliberately, and this ordering is
        # load-bearing. interrupt() raises, and on resume LangGraph re-runs the
        # node from the top, replaying earlier interrupt() calls from their saved
        # answers. Anything executed *before* an interrupt therefore runs twice.
        # Collecting every decision before touching a tool keeps the side effects
        # on the far side of the last interrupt, so they happen exactly once.
        decisions: dict[str, dict] = {}
        rejected_early: dict[str, str] = {}
        for call in tool_calls:
            if call["name"] not in GATED_TOOLS:
                continue
            if problem := precheck(call["name"], call["args"]):
                rejected_early[call["id"]] = problem
                continue
            answer = interrupt(
                {
                    "type": "approval_request",
                    "tool": call["name"],
                    "args": call["args"],
                    "description": describe_call(call["name"], call["args"]),
                }
            )
            decisions[call["id"]] = _normalize_decision(answer)

        # Past the last interrupt, so each answer is recorded once however many
        # times this node replayed on the way here.
        thread_id = (config.get("configurable") or {}).get("thread_id")
        for call in tool_calls:
            if decision := decisions.get(call["id"]):
                history.record_approval(
                    call["name"],
                    call["args"],
                    describe_call(call["name"], call["args"]),
                    approved=decision["approved"],
                    reason=decision.get("reason"),
                    thread_id=thread_id,
                )

        messages: list[ToolMessage] = []
        for call in tool_calls:
            if problem := rejected_early.get(call["id"]):
                messages.append(
                    ToolMessage(
                        content=problem,
                        tool_call_id=call["id"],
                        name=call["name"],
                        status="error",
                    )
                )
                continue

            decision = decisions.get(call["id"])
            if decision is not None and not decision["approved"]:
                reason = decision.get("reason") or "no reason given"
                messages.append(
                    ToolMessage(
                        # Fed back to the model so it can adapt rather than
                        # silently retry the same rejected write.
                        content=(
                            f"REJECTED by the user: {reason}. "
                            f"Nothing was changed. Do not retry this unchanged — "
                            f"ask what they would prefer."
                        ),
                        tool_call_id=call["id"],
                        name=call["name"],
                        status="error",
                    )
                )
                continue

            tool = tools_by_name.get(call["name"])
            if tool is None:
                messages.append(
                    ToolMessage(
                        content=f"No such tool: {call['name']}",
                        tool_call_id=call["id"],
                        name=call["name"],
                        status="error",
                    )
                )
                continue

            try:
                result = await tool.ainvoke(call)
            except Exception as exc:
                result = ToolMessage(
                    content=f"{type(exc).__name__}: {exc}",
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                )
            messages.append(_as_tool_message(result, call))

        return {
            "messages": messages,
            "working_set": extract_working_set(messages),
        }

    def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", call_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    # Without a checkpointer there is no state to pause and resume, and
    # interrupt() fails at runtime. This is the single most common way
    # human-in-the-loop setups break.
    return graph.compile(checkpointer=checkpointer)


def _normalize_decision(answer: Any) -> dict:
    """Accept the several shapes a resume value plausibly arrives in."""
    if isinstance(answer, bool):
        return {"approved": answer, "reason": None}
    if isinstance(answer, str):
        return {"approved": answer.strip().lower() in ("approve", "approved", "yes", "y"), "reason": None}
    if isinstance(answer, dict):
        return {
            "approved": bool(answer.get("approved")),
            "reason": answer.get("reason"),
        }
    return {"approved": False, "reason": "unrecognized approval response"}


def _as_tool_message(result: Any, call: dict) -> ToolMessage:
    """Tools handed a ToolCall return a ToolMessage; be tolerant if one doesn't."""
    if isinstance(result, ToolMessage):
        return result
    if isinstance(result, list):
        # MCP content blocks.
        text = "".join(
            block.get("text", "")
            for block in result
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return ToolMessage(content=text or str(result), tool_call_id=call["id"], name=call["name"])
    return ToolMessage(content=str(result), tool_call_id=call["id"], name=call["name"])
