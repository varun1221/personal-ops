"""Graph state, including the working set that makes multi-turn reference work.

Chat history alone gets you surprisingly far and then fails in a way that is hard
to read: by turn three the model is picking the wrong "it" out of a wall of JSON.
`working_set` keeps the entities the conversation has actually established —
the dates looked at, the events listed, the notes read — in a small, explicit
place the prompt can restate every turn.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.graph.message import add_messages

# How many events/notes to carry forward. Enough for "the second one", small
# enough to restate in the prompt every turn.
MAX_TRACKED = 10


def merge_working_set(left: dict, right: dict) -> dict:
    """Reducer: later turns overwrite keys, they don't deep-merge."""
    return {**(left or {}), **(right or {})}


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    working_set: Annotated[dict[str, Any], merge_working_set]


def _payload(message: ToolMessage) -> Any:
    """Tool results arrive as a JSON string or as MCP content blocks."""
    content = message.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None


def extract_working_set(messages: list[AnyMessage]) -> dict[str, Any]:
    """Pull referenceable entities out of the most recent tool results.

    Only the newest results matter — this is short-term working memory, not a log.
    """
    updates: dict[str, Any] = {}

    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break  # Stop at the first non-tool message: only this turn's results.

        data = _payload(message)
        if not isinstance(data, dict):
            continue

        if "events" in data:
            updates["last_events"] = [
                {
                    "index": index,
                    "title": event.get("title"),
                    "date": event.get("date"),
                    "start_time": event.get("start_time"),
                    "note_path": event.get("note_path"),
                }
                for index, event in enumerate(data["events"][:MAX_TRACKED], start=1)
            ]
            if data.get("start_date"):
                updates["last_date_range"] = [data.get("start_date"), data.get("end_date")]

        if "tasks" in data:
            updates["last_tasks"] = [
                {
                    "index": index,
                    "description": task.get("description"),
                    "due": task.get("due"),
                    "note_path": task.get("note_path"),
                }
                for index, task in enumerate(data["tasks"][:MAX_TRACKED], start=1)
            ]

        if "results" in data:
            updates["last_notes"] = [
                result.get("note_path") for result in data["results"][:MAX_TRACKED]
            ]

        if "emails" in data:
            updates["last_emails"] = [
                {
                    "index": index,
                    "subject": email.get("subject"),
                    "from": email.get("from"),
                    "date": email.get("date"),
                    "thread_id": email.get("thread_id"),
                }
                for index, email in enumerate(data["emails"][:MAX_TRACKED], start=1)
            ]

        if data.get("note_path") and "content" in data:
            updates["last_note_read"] = data["note_path"]

    return updates


def render_working_set(working_set: dict[str, Any]) -> str:
    """Restate the working set for the prompt, compactly."""
    if not working_set:
        return ""

    lines = ["## What this conversation has established so far", ""]

    if date_range := working_set.get("last_date_range"):
        lines.append(f"Dates last looked at: {date_range[0]} to {date_range[1]}")

    if events := working_set.get("last_events"):
        lines.append("Events last listed (referenceable by position):")
        for event in events:
            when = event.get("start_time") or "all day"
            lines.append(
                f"  {event['index']}. {event['title']} — {event['date']} {when} "
                f"[{event['note_path']}]"
            )

    if tasks := working_set.get("last_tasks"):
        lines.append("Tasks last listed:")
        for task in tasks:
            due = f" (due {task['due']})" if task.get("due") else ""
            lines.append(f"  {task['index']}. {task['description']}{due}")

    if emails := working_set.get("last_emails"):
        lines.append("Emails last listed:")
        for email in emails:
            lines.append(f"  {email['index']}. {email['subject']} — from {email['from']}")

    if notes := working_set.get("last_notes"):
        lines.append(f"Notes last found: {', '.join(filter(None, notes))}")

    if note := working_set.get("last_note_read"):
        lines.append(f"Note last read in full: {note}")

    return "\n".join(lines)
